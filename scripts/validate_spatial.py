"""Read-only spatial validation in the distribution Python/GDAL environment.

Input and output are one JSON document on standard streams.  This process is not a
QGIS Processing runner: it reads local vector files and uses independent checks.
Paths and their relationship to an approved plan are owned by the caller.
"""

import json
import math
import os
from pathlib import Path
import sqlite3
import sys

from osgeo import gdal, ogr, osr


gdal.UseExceptions()
osr.UseExceptions()
gdal.SetConfigOption("GDAL_PAM_ENABLED", "NO")
gdal.PushErrorHandler("CPLQuietErrorHandler")


class ValidationError(Exception):
    """A safe, actionable problem suitable for an execution record."""


def _report(status, *, checks=None, errors=None, facts=None):
    return {"status": status, "checks": checks or [], "errors": errors or [], "facts": facts or {}}


def _srs(value):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("A known CRS is required.")
    spatial_ref = osr.SpatialReference()
    try:
        spatial_ref.SetFromUserInput(value)
        spatial_ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    except Exception as exc:
        raise ValidationError("The requested target CRS is invalid or unavailable.") from exc
    if not (spatial_ref.IsGeographic() or spatial_ref.IsProjected()):
        raise ValidationError("A known geographic or projected CRS is required.")
    return spatial_ref


def _crs_label(spatial_ref):
    authority = spatial_ref.GetAuthorityName(None)
    code = spatial_ref.GetAuthorityCode(None)
    return f"{authority}:{code}" if authority and code else spatial_ref.ExportToWkt()


def _meter_crs(spatial_ref):
    if not spatial_ref or not spatial_ref.IsProjected() or not math.isclose(
        spatial_ref.GetLinearUnits(), 1.0, rel_tol=0.0, abs_tol=1e-9,
    ):
        raise ValidationError("Buffer requires a known projected CRS with metre units.")
    web_mercator = _srs("EPSG:3857")
    if spatial_ref.IsSame(web_mercator):
        raise ValidationError("Web Mercator is not a suitable distance CRS; choose a local metre projection.")



def _vertices(geometry):
    for index in range(geometry.GetPointCount()):
        yield geometry.GetX(index), geometry.GetY(index)
    for index in range(geometry.GetGeometryCount()):
        yield from _vertices(geometry.GetGeometryRef(index))


def _geographic_bounds(layer, spatial_ref):
    target = _srs("EPSG:4326")
    try:
        transform = osr.CoordinateTransformation(spatial_ref, target)
        bounds = None
        layer.ResetReading()
        for feature in layer:
            geometry = feature.GetGeometryRef()
            if geometry is None or geometry.IsEmpty():
                continue
            for x, y in _vertices(geometry):
                lon, lat, _ = transform.TransformPoint(x, y)
                if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
                    raise ValidationError("Input coordinates cannot be located in a valid geographic area.")
                if bounds is None:
                    bounds = [lon, lon, lat, lat]
                else:
                    bounds = [min(bounds[0], lon), max(bounds[1], lon),
                              min(bounds[2], lat), max(bounds[3], lat)]
        layer.ResetReading()
        return bounds
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError("Input coordinates cannot be checked against the projection area.") from exc


def _check_projection_area(spatial_ref, bounds):
    if bounds is None:
        return
    area = spatial_ref.GetAreaOfUse()
    if area is None:
        raise ValidationError("The projected CRS area of use is unavailable for this input.")
    west, east, south, north = bounds
    tolerance = 1e-7
    in_latitudes = south >= area.south_lat_degree - tolerance and north <= area.north_lat_degree + tolerance
    if area.west_lon_degree <= area.east_lon_degree:
        in_longitudes = west >= area.west_lon_degree - tolerance and east <= area.east_lon_degree + tolerance
    else:
        in_longitudes = west >= area.west_lon_degree - tolerance or east <= area.east_lon_degree + tolerance
    if not in_latitudes or not in_longitudes:
        raise ValidationError("Input coordinates fall outside the projected CRS area of use.")


def _path_layer(path, selected=None):
    if not isinstance(path, str) or not path or "://" in path:
        raise ValidationError("A local vector input is required.")
    parts = path.split("|")
    if len(parts) > 2 or (len(parts) == 2 and not parts[1].startswith("layername=")):
        raise ValidationError("Only an explicit vector layer name is supported.")
    embedded = parts[1][len("layername="):] if len(parts) == 2 else None
    if embedded is not None and not embedded:
        raise ValidationError("A non-empty vector layer name is required.")
    if selected and embedded and selected != embedded:
        raise ValidationError("The selected vector layer does not match the input reference.")
    return parts[0], selected or embedded


def _open_layer(path, selected=None, *, table=False):
    filename, layer_name = _path_layer(path, selected)
    if not Path(filename).is_file():
        raise ValidationError("A referenced local vector file is unavailable.")
    if Path(filename).suffix.lower() == ".gpkg" and any(
        os.path.lexists(filename + suffix) for suffix in ("-wal", "-shm", "-journal")
    ):
        raise ValidationError("GeoPackage has an SQLite sidecar and is not independently verifiable.")
    try:
        dataset = gdal.OpenEx(filename, gdal.OF_VECTOR | gdal.OF_READONLY,
                              allowed_drivers=["GeoJSON", "GPKG"])
        if dataset is None or dataset.GetLayerCount() == 0:
            raise ValidationError("The file has no supported vector layer.")
        if layer_name:
            layer = dataset.GetLayerByName(layer_name)
            if layer is None:
                raise ValidationError("The selected vector layer was not found.")
        elif dataset.GetLayerCount() == 1:
            layer = dataset.GetLayerByIndex(0)
        else:
            raise ValidationError("Select one layer in a multi-layer vector file.")
        spatial_ref = layer.GetSpatialRef()
        if table:
            if Path(filename).suffix.lower() != ".gpkg" or ogr.GT_Flatten(layer.GetGeomType()) != ogr.wkbNone:
                raise ValidationError("Statistics require a non-spatial GeoPackage table.")
            if spatial_ref is not None:
                raise ValidationError("A non-spatial statistics table must not have a CRS.")
            try:
                with sqlite3.connect(Path(filename).resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as connection:
                    metadata = connection.execute(
                        "SELECT data_type, srs_id FROM gpkg_contents WHERE table_name = ?", (layer.GetName(),),
                    ).fetchone()
                if metadata is None or metadata[0] != "attributes" or metadata[1] not in (None, 0, -1):
                    raise ValidationError("A non-spatial statistics table has an invalid type or CRS metadata.")
            except sqlite3.Error as exc:
                raise ValidationError("The GeoPackage table metadata cannot be verified.") from exc
            return dataset, layer, None
        if ogr.GT_Flatten(layer.GetGeomType()) == ogr.wkbNone:
            raise ValidationError("A spatial vector input is required, not a table.")
        if spatial_ref is None:
            raise ValidationError("The vector layer has no reliable CRS.")
        spatial_ref = spatial_ref.Clone()
        spatial_ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        if (not (spatial_ref.IsGeographic() or spatial_ref.IsProjected())
                or spatial_ref.GetAuthorityCode(None) == "0"):
            raise ValidationError("The vector layer has no reliable CRS.")
        return dataset, layer, spatial_ref
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError("The referenced GeoJSON or GeoPackage could not be read.") from exc


def _layer_info(layer, *, allow_empty=False):
    layer_type = layer.GetGeomType()
    if ogr.GT_HasZ(layer_type) or ogr.GT_HasM(layer_type):
        raise ValidationError("Only 2D geometries are supported.")
    definition = layer.GetLayerDefn()
    fields = [definition.GetFieldDefn(i).GetName() for i in range(definition.GetFieldCount())]
    count = 0
    empty_geometries = 0
    layer.ResetReading()
    for feature in layer:
        geometry = feature.GetGeometryRef()
        if geometry is None or geometry.IsEmpty():
            if not allow_empty:
                raise ValidationError("A feature has missing or empty geometry.")
            empty_geometries += 1
            count += 1
            continue
        if ogr.GT_HasZ(geometry.GetGeometryType()) or ogr.GT_HasM(geometry.GetGeometryType()):
            raise ValidationError("Only 2D geometries are supported.")
        if not geometry.IsValid():
            raise ValidationError("A feature has invalid geometry.")
        count += 1
    layer.ResetReading()
    field_types = {}
    for i in range(definition.GetFieldCount()):
        field = definition.GetFieldDefn(i)
        field_types[field.GetName()] = "Boolean" if field.GetSubType() == ogr.OFSTBoolean else field.GetTypeName()
    return {"layer": layer.GetName(), "feature_count": count, "fields": fields,
            "field_types": field_types, "geometry": ogr.GeometryTypeToName(layer_type).replace(" ", ""),
            "empty_geometries": empty_geometries}


def _buffer_parameters(parameters):
    required = ("DISTANCE", "SEGMENTS", "END_CAP_STYLE", "JOIN_STYLE", "MITER_LIMIT", "DISSOLVE")
    if not isinstance(parameters, dict) or any(key not in parameters for key in required):
        raise ValidationError("Buffer needs DISTANCE, SEGMENTS, END_CAP_STYLE, JOIN_STYLE, MITER_LIMIT and DISSOLVE.")
    distance = parameters["DISTANCE"]
    segments = parameters["SEGMENTS"]
    end_cap = parameters["END_CAP_STYLE"]
    join = parameters["JOIN_STYLE"]
    miter = parameters["MITER_LIMIT"]
    dissolve = parameters["DISSOLVE"]
    separate = parameters.get("SEPARATE_DISJOINT", False)
    if (isinstance(distance, bool) or not isinstance(distance, (int, float))
            or not math.isfinite(distance)):
        raise ValidationError("Buffer DISTANCE must be a finite distance in metres.")
    if distance and abs(distance) < 1e-6:
        raise ValidationError("Buffer distances below one micrometre cannot be verified reliably.")
    if isinstance(segments, bool) or not isinstance(segments, int) or not 1 <= segments <= 10000:
        raise ValidationError("Buffer SEGMENTS must be an integer between 1 and 10000.")
    if isinstance(end_cap, bool) or not isinstance(end_cap, int) or end_cap not in (0, 1, 2):
        raise ValidationError("Buffer END_CAP_STYLE must be 0, 1 or 2.")
    if isinstance(join, bool) or not isinstance(join, int) or join not in (0, 1, 2):
        raise ValidationError("Buffer JOIN_STYLE must be 0, 1 or 2.")
    if (isinstance(miter, bool) or not isinstance(miter, (int, float))
            or not math.isfinite(miter) or miter <= 0):
        raise ValidationError("Buffer MITER_LIMIT must be a finite positive number.")
    if not isinstance(dissolve, bool) or not isinstance(separate, bool):
        raise ValidationError("Buffer DISSOLVE and SEPARATE_DISJOINT must be Boolean.")
    return {"distance": float(distance), "segments": segments, "end_cap": end_cap,
            "join": join, "miter": float(miter), "dissolve": dissolve, "separate": separate}


def _field_list(value, *, name, empty=False):
    if not isinstance(value, list) or (not empty and not value) or any(
        not isinstance(item, str) or not item for item in value
    ) or len(set(value)) != len(value):
        raise ValidationError(f"{name} must be a {'possibly empty' if empty else 'non-empty'} list of distinct field names.")
    return value


def _numeric_field(schema, field):
    if not isinstance(field, str) or not field or field not in schema["field_types"]:
        raise ValidationError(f"Numeric field {field!r} is not present in the input schema.")
    if schema["field_types"][field] not in ("Integer", "Integer64", "Real"):
        raise ValidationError(f"Field {field!r} has no reliable numeric type.")


def _fields_exist(schema, fields):
    if any(field not in schema["field_types"] for field in fields):
        raise ValidationError("A selected field is absent from the input schema.")


def _polygon_schema(schema):
    if schema["geometry"] not in ("Polygon", "MultiPolygon"):
        raise ValidationError("This operation requires known 2D polygonal input.")


def _spatial_schema(schema):
    if schema["geometry"] not in ("Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"):
        raise ValidationError("This operation requires a known 2D point, line or polygon geometry type.")


def _gpkg_output(params):
    output = params.get("OUTPUT")
    if not isinstance(output, str) or Path(output).suffix.lower() != ".gpkg" or "|" in output:
        raise ValidationError("A new GeoPackage OUTPUT is required for this operation.")


def _geometry_parameters(params, schema):
    if type(params.get("CALC_METHOD")) is not int or params["CALC_METHOD"] != 0:
        raise ValidationError("Geometry attributes require CALC_METHOD=0 in the layer CRS.")
    _polygon_schema(schema)
    if any(field.lower() in ("area", "perimeter") for field in schema["fields"]):
        raise ValidationError("Existing area or perimeter field conflicts with geometry attributes.")


def _stat_parameters(algorithm, params, schema):
    _gpkg_output(params)
    if algorithm == "native:basicstatisticsforfields":
        field = params.get("FIELD_NAME")
        _numeric_field(schema, field)
        return field, []
    groups = _field_list(params.get("CATEGORIES_FIELD_NAME"), name="CATEGORIES_FIELD_NAME")
    _fields_exist(schema, groups)
    reserved = {"count", "unique", "min", "max", "range", "sum", "mean", "median", "stddev", "minority", "majority", "q1", "q3", "iqr"}
    if any(field.lower() in reserved for field in groups):
        raise ValidationError("Category fields conflict with statistics output columns.")
    if any(schema["field_types"][field] not in ("Integer", "Integer64", "Real", "String") for field in groups):
        raise ValidationError("Category field has an unsupported or unknown type.")
    value = params.get("VALUES_FIELD_NAME")
    if "VALUES_FIELD_NAME" in params:
        _numeric_field(schema, value)
    return value, groups


def _match_parameter_refs(params, input_refs, references, names):
    if len(input_refs) != len(names):
        raise ValidationError("Unsupported input combination: the operation requires its declared input references.")
    matches = []
    for name in names:
        value = params.get(name)
        candidates = [ref for ref in input_refs if references.get(ref) == value]
        if len(candidates) != 1 or candidates[0] in matches:
            raise ValidationError(f"{name} does not identify one declared input reference.")
        matches.append(candidates[0])
    return matches


def _preflight(plan, input_facts=None):
    if not isinstance(plan, dict):
        raise ValidationError("A structured task plan is required.")
    refs, inputs = {}, []
    input_facts = input_facts or {}
    original_sources = {source.get("input_id"): source for source in plan.get("required_inputs", [])}
    references = {source.get("input_id"): source.get("path") for source in plan.get("required_inputs", [])}
    references.update({output.get("output_id"): output.get("path") for output in plan.get("expected_outputs", [])})
    for source in plan.get("required_inputs", []):
        ref = source.get("input_id")
        dataset, layer, spatial_ref = _open_layer(source.get("path"), source.get("layer_name"))
        try:
            info = _verified_source_schema(layer, ref, input_facts)
            refs[ref] = {"srs": spatial_ref, "geographic_bounds": _geographic_bounds(layer, spatial_ref),
                         **info, "field_units": _verified_field_units(ref, input_facts), "kind": "spatial"}
            inputs.append({"ref": ref, "crs": _crs_label(spatial_ref), **info})
        finally:
            dataset.Close()
    steps = {step.get("step_id"): step for step in plan.get("steps", [])}
    checks, review_steps = [], []
    algorithms = ("native:reprojectlayer", "native:buffer", "native:clip", "native:dissolve",
                  "qgis:exportaddgeometrycolumns", "qgis:statisticsbycategories", "native:basicstatisticsforfields")
    for call in plan.get("tool_calls", []):
        step_id = call.get("step_id")
        step = steps.get(step_id)
        if step is None:
            raise ValidationError("A planned call has no matching step.")
        output_refs = step.get("output_refs", [])
        if call.get("tool_name") != "execute_processing":
            if call.get("tool_name") == "add_layer" and (call.get("arguments") or {}).get("layer_type", "vector") == "vector":
                input_refs = step.get("input_refs", [])
                if (len(input_refs) != 1 or input_refs[0] not in refs or refs[input_refs[0]] is None
                        or refs[input_refs[0]].get("kind") != "spatial"
                        or (call.get("arguments") or {}).get("path") != references.get(input_refs[0])):
                    raise ValidationError(f"Step {step_id} needs one spatial layer with a known CRS.")
                source = refs[input_refs[0]]
                checks.append({"step_id": step_id, "check": "load_spatial_layer", "crs": _crs_label(source["srs"])})
                review_steps.append({"step_id": step_id, "tool": "add_layer", "algorithm": None,
                                     "input_crs": [{"ref": input_refs[0], "crs": _crs_label(source["srs"])}],
                                     "output_crs": _crs_label(source["srs"]), "input_kind": "spatial",
                                     "output_kind": "layer", "geometry": source["geometry"], "field": None,
                                     "field_type": None, "group_fields": [], "unit": None})
            for ref in output_refs:
                refs[ref] = None
            continue
        args = call.get("arguments", {})
        algorithm = args.get("algorithm")
        params = args.get("parameters")
        if algorithm not in algorithms:
            raise ValidationError(f"Step {step_id}: spatial validation for this Processing algorithm is not yet available.")
        if not isinstance(params, dict):
            raise ValidationError(f"Step {step_id} processing parameters are missing.")
        input_refs = step.get("input_refs", [])
        if len(output_refs) != 1 or any(ref not in refs or refs[ref] is None for ref in input_refs):
            raise ValidationError(f"Step {step_id} needs known input and one output reference.")
        if algorithm == "native:clip":
            source_id, overlay_id = _match_parameter_refs(params, input_refs, references, ("INPUT", "OVERLAY"))
            source_ref, overlay = refs[source_id], refs[overlay_id]
            if source_ref["kind"] != "spatial" or overlay["kind"] != "spatial":
                raise ValidationError("Clip requires spatial inputs with known CRS.")
            _polygon_schema(overlay)
            _spatial_schema(source_ref)
            if not source_ref["srs"].IsSame(overlay["srs"]):
                raise ValidationError("Clip input and overlay must have the same CRS.")
        else:
            if len(input_refs) != 1:
                raise ValidationError(f"Step {step_id} needs one known input reference.")
            source_ref = refs[input_refs[0]]
            if algorithm not in ("native:reprojectlayer", "native:buffer"):
                input_name = "INPUT_LAYER" if algorithm == "native:basicstatisticsforfields" else "INPUT"
                _match_parameter_refs(params, input_refs, references, (input_name,))
        if algorithm not in ("native:reprojectlayer", "native:buffer") and params.get("OUTPUT") != references.get(output_refs[0]):
            raise ValidationError(f"Step {step_id} OUTPUT does not match its declared output reference.")
        if source_ref["kind"] != "spatial":
            raise ValidationError("A spatial input with known CRS is required, not a statistics table.")
        result = {**source_ref, "field_types": dict(source_ref["field_types"]),
                  "fields": list(source_ref["fields"]), "field_units": {}}
        field, field_type, groups, unit = None, None, [], None
        if algorithm == "native:reprojectlayer":
            target_ref = _srs(params.get("TARGET_CRS"))
            result["srs"] = target_ref
            checks.append({"step_id": step_id, "check": "target_crs", "crs": _crs_label(target_ref)})
        elif algorithm == "native:buffer":
            if call.get("distance_unit") != "m":
                raise ValidationError(f"Step {step_id} needs an explicitly reviewed buffer distance unit of m.")
            _buffer_parameters(params)
            _meter_crs(source_ref["srs"])
            _check_projection_area(source_ref["srs"], source_ref["geographic_bounds"])
            result["geometry"] = "MultiPolygon"
            unit = "m"
            checks.append({"step_id": step_id, "check": "buffer_metre_crs", "crs": _crs_label(source_ref["srs"])})
        elif algorithm == "native:dissolve":
            fields = _field_list(params.get("FIELD"), name="FIELD", empty=True)
            _fields_exist(source_ref, fields)
            groups = fields
            if not isinstance(params.get("SEPARATE_DISJOINT"), bool):
                raise ValidationError("Dissolve requires explicit Boolean SEPARATE_DISJOINT.")
            _spatial_schema(source_ref)
        elif algorithm == "qgis:exportaddgeometrycolumns":
            _geometry_parameters(params, source_ref)
            _meter_crs(source_ref["srs"])
            _check_projection_area(source_ref["srs"], source_ref["geographic_bounds"])
            result["fields"].extend(["area", "perimeter"])
            result["field_types"].update({"area": "Real", "perimeter": "Real"})
            result["field_units"] = {"area": "m2", "perimeter": "m"}
            field, field_type, unit = "area", "Real", "m2"
        elif algorithm in ("native:basicstatisticsforfields", "qgis:statisticsbycategories"):
            field, groups = _stat_parameters(algorithm, params, source_ref)
            field_type = source_ref["field_types"].get(field)
            unit = source_ref["field_units"].get(field)
            if input_refs[0] in original_sources:
                _preflight_stat_values(original_sources[input_refs[0]], field, groups)
            result = {"kind": "table", "srs": None, "fields": [], "field_types": {}, "field_units": {}}
            checks.append({"step_id": step_id, "check": "statistics_fields", "field": field,
                           "group_fields": groups, "unit": source_ref["field_units"].get(field)})
        if algorithm in ("native:clip", "native:dissolve", "qgis:exportaddgeometrycolumns"):
            _gpkg_output(params)
            checks.append({"step_id": step_id, "check": "spatial_schema", "crs": _crs_label(source_ref["srs"]),
                           "fields": result["fields"], "field_units": result["field_units"]})
        refs[output_refs[0]] = result
        review_steps.append({"step_id": step_id, "tool": "execute_processing", "algorithm": algorithm,
                             "input_crs": [{"ref": ref, "crs": _crs_label(refs[ref]["srs"])} for ref in input_refs],
                             "output_crs": _crs_label(result["srs"]) if result["srs"] else None,
                             "input_kind": "spatial", "output_kind": result["kind"],
                             "geometry": result.get("geometry") if result["kind"] == "spatial" else None,
                             "field": field, "field_type": field_type, "group_fields": groups, "unit": unit})
    return _report("passed", checks=checks, facts={"inputs": inputs, "review_steps": review_steps})



def _feature_values(feature, fields):
    # JSON provides a stable hash key while retaining nulls, lists and field names.
    values = []
    for name in fields:
        value = feature.GetField(name)
        if isinstance(value, bytes):
            value = {"binary_hex": value.hex()}
        values.append(value)
    return json.dumps(values, sort_keys=True, ensure_ascii=False, allow_nan=False, default=str)


def _features(layer, fields):
    features = []
    layer.ResetReading()
    for feature in layer:
        geometry = feature.GetGeometryRef()
        features.append((_feature_values(feature, fields), geometry.Clone() if geometry else None))
    layer.ResetReading()
    return features


def _point_close(first, second, tolerance):
    return (all(math.isfinite(value) for value in (*first, *second))
            and abs(first[0] - second[0]) <= tolerance
            and abs(first[1] - second[1]) <= tolerance)


def _sequence_matches(expected, actual, tolerance, is_ring):
    if len(expected) != len(actual):
        return False
    if not expected:
        return True
    if is_ring:
        if (len(expected) < 4 or not _point_close(expected[0], expected[-1], tolerance)
                or not _point_close(actual[0], actual[-1], tolerance)):
            return False
        expected, actual = expected[:-1], actual[:-1]
        for candidate in (actual, list(reversed(actual))):
            for index, point in enumerate(candidate):
                if _point_close(expected[0], point, tolerance) and all(
                    _point_close(value, candidate[(index + offset) % len(candidate)], tolerance)
                    for offset, value in enumerate(expected)
                ):
                    return True
        return False
    return any(all(_point_close(a, b, tolerance) for a, b in zip(expected, candidate))
               for candidate in (actual, list(reversed(actual))))


def _transformed_geometry_matches(source, actual, transform, tolerance):
    if (ogr.GT_Flatten(source.GetGeometryType()) != ogr.GT_Flatten(actual.GetGeometryType())
            or source.GetPointCount() != actual.GetPointCount()
            or source.GetGeometryCount() != actual.GetGeometryCount()):
        return False
    if source.GetPointCount():
        expected_points = [transform.TransformPoint(source.GetX(i), source.GetY(i))[:2]
                           for i in range(source.GetPointCount())]
        actual_points = [(actual.GetX(i), actual.GetY(i)) for i in range(actual.GetPointCount())]
        return _sequence_matches(expected_points, actual_points, tolerance,
                                 source.GetGeometryName().upper() == "LINEARRING")
    children = [actual.GetGeometryRef(i) for i in range(actual.GetGeometryCount())]
    for index in range(source.GetGeometryCount()):
        child = source.GetGeometryRef(index)
        # A polygon's first ring is its exterior; its holes and multi-geometries may reorder.
        choices = range(min(1, len(children))) if source.GetGeometryName() == "POLYGON" and index == 0 else range(len(children))
        match = next((i for i in choices if _transformed_geometry_matches(child, children[i], transform, tolerance)), None)
        if match is None:
            return False
        children.pop(match)
    return True


def _coordinates_tolerance(spatial_ref):
    if spatial_ref.IsProjected():
        return 0.02 / spatial_ref.GetLinearUnits(), "target units (equivalent to 0.02 metres)"
    return 1e-7, "degrees"


def _reprojection(source_layer, source_srs, output_layer, output_srs, params, checks):
    target_srs = _srs(params.get("TARGET_CRS"))
    if not output_srs.IsSame(target_srs):
        raise ValidationError("Output CRS does not match the requested target CRS.")
    checks.append({"check": "target_crs", "crs": _crs_label(target_srs)})
    source_fields = _layer_info(source_layer)["fields"]
    output_fields = _layer_info(output_layer)["fields"]
    if any(field not in output_fields for field in source_fields):
        raise ValidationError("Output is missing one or more original fields.")
    sources = _features(source_layer, source_fields)
    outputs = _features(output_layer, source_fields)
    if len(sources) != len(outputs):
        raise ValidationError("Output feature count does not match the input.")
    checks.append({"check": "feature_count", "count": len(sources)})
    tolerance, unit = _coordinates_tolerance(target_srs)
    transform = osr.CoordinateTransformation(source_srs, target_srs)
    pending = {}
    for key, geometry in outputs:
        pending.setdefault(key, []).append(geometry)
    for index, (key, geometry) in enumerate(sources, 1):
        candidates = pending.get(key, [])
        match = next((i for i, candidate in enumerate(candidates)
                      if _transformed_geometry_matches(geometry, candidate, transform, tolerance)), None)
        if match is None:
            raise ValidationError(f"Output attributes or transformed coordinates differ from input feature {index}.")
        candidates.pop(match)
    checks.append({"check": "attributes_and_transformed_vertices", "feature_count": len(sources),
                   "absolute_tolerance": tolerance, "units": unit,
                   "transform": "OSR traditional GIS axis order"})



def _polygonal(geometry):
    return ogr.GT_Flatten(geometry.GetGeometryType()) in (ogr.wkbPolygon, ogr.wkbMultiPolygon)


def _area_tolerance(area):
    return max(1e-12, abs(area) * 1e-6)


def _distance_tolerance(distance):
    return max(1e-9, min(0.001, abs(distance) * 1e-6))


def _buffer_shape(source, parameters):
    options = [
        "QUADRANT_SEGMENTS=" + str(parameters["segments"]),
        "ENDCAP_STYLE=" + ("ROUND", "FLAT", "SQUARE")[parameters["end_cap"]],
        "JOIN_STYLE=" + ("ROUND", "MITRE", "BEVEL")[parameters["join"]],
        "MITRE_LIMIT=" + str(parameters["miter"]),
    ]
    try:
        result = source.Buffer(parameters["distance"], options)
    except Exception as exc:
        raise ValidationError("Could not compute the independent buffer comparison.") from exc
    if result is None or not _polygonal(result) or (not result.IsEmpty() and not result.IsValid()):
        raise ValidationError("The requested buffer produces an unsupported geometry.")
    return result


def _same_area_geometry(expected, actual):
    if expected.IsEmpty():
        return actual is None or actual.IsEmpty()
    if actual is None or actual.IsEmpty() or not _polygonal(actual):
        return False
    if abs(expected.GetArea() - actual.GetArea()) > _area_tolerance(expected.GetArea()):
        return False
    difference = expected.SymDifference(actual)
    return difference is not None and difference.GetArea() <= _area_tolerance(expected.GetArea())


def _single_polygon(geometry):
    kind = ogr.GT_Flatten(geometry.GetGeometryType())
    if kind == ogr.wkbPolygon:
        return geometry
    if kind == ogr.wkbMultiPolygon and geometry.GetGeometryCount() == 1:
        return geometry.GetGeometryRef(0)
    return None


def _point_circle(source, output, parameters):
    if (ogr.GT_Flatten(source.GetGeometryType()) != ogr.wkbPoint
            or parameters["end_cap"] != 0 or parameters["distance"] <= 0):
        return None
    polygon = _single_polygon(output)
    if polygon is None or polygon.GetGeometryCount() != 1:
        return False
    ring = polygon.GetGeometryRef(0)
    count = 4 * parameters["segments"]
    if ring.GetPointCount() != count + 1:
        return False
    radius = parameters["distance"]
    tolerance = _distance_tolerance(radius)
    lengths = []
    for index in range(count):
        x, y = ring.GetX(index), ring.GetY(index)
        if abs(math.hypot(x - source.GetX(), y - source.GetY()) - radius) > tolerance:
            return False
        lengths.append(math.hypot(ring.GetX(index + 1) - x, ring.GetY(index + 1) - y))
    edge = 2 * radius * math.sin(math.pi / count)
    if any(abs(length - edge) > tolerance * 2 for length in lengths):
        return False
    analytic_area = count * radius ** 2 * math.sin(2 * math.pi / count) / 2
    return abs(output.GetArea() - analytic_area) <= _area_tolerance(analytic_area)


def _analytic_simple_shape(source, output, parameters):
    """Independently check known line and rectangular polygon area relationships."""
    kind = ogr.GT_Flatten(source.GetGeometryType())
    radius = parameters["distance"]
    segments = parameters["segments"]
    circle_area = 2 * segments * radius ** 2 * math.sin(math.pi / (2 * segments))
    if radius <= 0 and kind in (ogr.wkbPoint, ogr.wkbMultiPoint, ogr.wkbLineString, ogr.wkbMultiLineString):
        return output is None or output.IsEmpty()
    if kind in (ogr.wkbPoint, ogr.wkbMultiPoint) and parameters["end_cap"] == 1:
        return output is None or output.IsEmpty()
    if kind == ogr.wkbPoint and parameters["end_cap"] == 2:
        if output is None or output.IsEmpty():
            return False
        expected_envelope = (source.GetX() - radius, source.GetX() + radius,
                             source.GetY() - radius, source.GetY() + radius)
        tolerance = _distance_tolerance(radius)
        return (abs(output.GetArea() - 4 * radius ** 2) <= _area_tolerance(4 * radius ** 2)
                and all(abs(actual - expected) <= tolerance
                        for actual, expected in zip(output.GetEnvelope(), expected_envelope)))
    if kind == ogr.wkbLineString and source.GetPointCount() == 2:
        length = math.hypot(source.GetX(1) - source.GetX(0), source.GetY(1) - source.GetY(0))
        if length == 0:
            return False
        cap_area = (circle_area, 0.0, 4 * radius ** 2)[parameters["end_cap"]]
        expected_area = 2 * radius * length + cap_area
        return abs(output.GetArea() - expected_area) <= _area_tolerance(expected_area)
    if kind == ogr.wkbPolygon and source.GetGeometryCount() == 1:
        ring = source.GetGeometryRef(0)
        xmin, xmax, ymin, ymax = source.GetEnvelope()
        width, height = xmax - xmin, ymax - ymin
        corners = {(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)}
        if ring.GetPointCount() == 5 and set((ring.GetX(i), ring.GetY(i)) for i in range(4)) == corners:
            if radius <= 0:
                expected_area = max(0.0, width + 2 * radius) * max(0.0, height + 2 * radius)
                if expected_area == 0:
                    return output is None or output.IsEmpty()
                return output is not None and abs(output.GetArea() - expected_area) <= _area_tolerance(expected_area)
            corner_area = (circle_area, 4 * radius ** 2, 2 * radius ** 2)[parameters["join"]]
            # Very small miter limits bevel even a right angle; fall back to the shape comparison.
            if parameters["join"] == 1 and parameters["miter"] < math.sqrt(2):
                return None
            expected_area = width * height + 2 * radius * (width + height) + corner_area
            return abs(output.GetArea() - expected_area) <= _area_tolerance(expected_area)
    return None



def _maximum_vertex_distance(source, output, parameters):
    """Check a necessary distance bound independently of the GEOS buffer result."""
    if parameters["distance"] <= 0:
        return True
    radius = parameters["distance"]
    factor = max(1.0, math.sqrt(2) if parameters["end_cap"] == 2 else 1.0,
                 parameters["miter"] if parameters["join"] == 1 else 1.0)
    tolerance = _distance_tolerance(radius)
    point = ogr.Geometry(ogr.wkbPoint)
    for x, y in _vertices(output):
        point.SetPoint_2D(0, x, y)
        distance = source.Distance(point)
        if not math.isfinite(distance) or distance > radius * factor + tolerance:
            return False
    return True


def _analytic_dissolved(sources, output, parameters):
    if len(sources) == 1:
        return _analytic_simple_shape(sources[0][1], output, parameters)
    radius = parameters["distance"]
    if radius <= 0:
        return None
    geometries = [geometry for _, geometry in sources]
    if (parameters["end_cap"] == 1
            and all(ogr.GT_Flatten(geometry.GetGeometryType()) == ogr.wkbLineString
                    and geometry.GetPointCount() == 2 for geometry in geometries)):
        horizontal = all(g.GetY(0) == g.GetY(1) == geometries[0].GetY(0) for g in geometries)
        vertical = all(g.GetX(0) == g.GetX(1) == geometries[0].GetX(0) for g in geometries)
        if horizontal or vertical:
            intervals = sorted((min(g.GetX(0), g.GetX(1)), max(g.GetX(0), g.GetX(1)))
                               if horizontal else (min(g.GetY(0), g.GetY(1)), max(g.GetY(0), g.GetY(1)))
                               for g in geometries)
            first, last = intervals[0]
            total = 0.0
            for start, end in intervals[1:]:
                if start <= last:
                    last = max(last, end)
                else:
                    total += last - first
                    first, last = start, end
            total += last - first
            expected_area = 2 * radius * total
            return abs(output.GetArea() - expected_area) <= _area_tolerance(expected_area)
    if (parameters["end_cap"] == 0
            and all(ogr.GT_Flatten(g.GetGeometryType()) == ogr.wkbPoint for g in geometries)):
        if all(math.hypot(a.GetX() - b.GetX(), a.GetY() - b.GetY()) >= 2 * radius
               for index, a in enumerate(geometries) for b in geometries[index + 1:]):
            segments = parameters["segments"]
            expected_area = len(geometries) * 2 * segments * radius ** 2 * math.sin(math.pi / (2 * segments))
            return abs(output.GetArea() - expected_area) <= _area_tolerance(expected_area)
    return None


def _buffer(source_layer, source_srs, output_layer, output_srs, raw_parameters, checks):
    parameters = _buffer_parameters(raw_parameters)
    _meter_crs(source_srs)
    _check_projection_area(source_srs, _geographic_bounds(source_layer, source_srs))
    if not source_srs.IsSame(output_srs):
        raise ValidationError("Buffer output CRS does not match the actual input CRS.")
    checks.append({"check": "buffer_crs_and_units", "crs": _crs_label(source_srs), "units": "metres"})
    source_fields = _layer_info(source_layer)["fields"]
    output_fields = _layer_info(output_layer, allow_empty=True)["fields"]
    if any(field not in output_fields for field in source_fields):
        raise ValidationError("Buffer output is missing one or more original fields.")
    sources = _features(source_layer, source_fields)
    outputs = _features(output_layer, source_fields)
    if not parameters["dissolve"] and len(sources) != len(outputs):
        raise ValidationError("Buffer output feature count does not match the input.")
    if not sources:
        if outputs:
            raise ValidationError("An empty input requires an empty buffer output.")
        checks.append({"check": "empty_input_empty_output", "feature_count": 0})
        return
    if not outputs and not parameters["dissolve"]:
        raise ValidationError("Buffer output is missing features.")
    if any(geometry is not None and not geometry.IsEmpty() and not _polygonal(geometry)
           for _, geometry in outputs):
        raise ValidationError("Buffer output must contain valid polygonal geometry.")
    analytic_count = 0
    independently_empty_count = 0
    if not parameters["dissolve"]:
        pending = {}
        for key, geometry in outputs:
            pending.setdefault(key, []).append(geometry)
        for index, (key, source) in enumerate(sources, 1):
            expected = _buffer_shape(source, parameters)
            candidates = pending.get(key, [])
            match = next((i for i, candidate in enumerate(candidates) if _same_area_geometry(expected, candidate)), None)
            if match is None:
                raise ValidationError(f"Buffer geometry, distance, options or attributes differ for input feature {index}.")
            actual = candidates.pop(match)
            simple_check = _analytic_simple_shape(source, actual, parameters)
            if expected.IsEmpty():
                if simple_check is not True:
                    raise ValidationError(f"Empty buffer for input feature {index} cannot be independently established.")
                independently_empty_count += 1
                analytic_count += 1
                continue
            if parameters["distance"] > 0:
                if not source.Difference(actual).IsEmpty():
                    raise ValidationError(f"Buffer does not cover input feature {index}.")
                if not _maximum_vertex_distance(source, actual, parameters):
                    raise ValidationError(f"Buffer exceeds the permitted distance for input feature {index}.")
            elif not actual.Difference(source).IsEmpty():
                raise ValidationError(f"Non-positive buffer extends outside input feature {index}.")
            point_check = _point_circle(source, actual, parameters)
            if point_check is False or simple_check is False:
                raise ValidationError(f"Buffer distance or analytic area is incorrect for input feature {index}.")
            analytic_count += int(point_check is not None or simple_check is not None)
    else:
        expected = None
        for index, (_, source) in enumerate(sources, 1):
            buffered = _buffer_shape(source, parameters)
            if buffered.IsEmpty():
                if _analytic_simple_shape(source, None, parameters) is not True:
                    raise ValidationError(f"Empty buffer for input feature {index} cannot be independently established.")
                independently_empty_count += 1
                continue
            expected = buffered if expected is None else expected.Union(buffered)
        allowed_values = {key for key, _ in sources}
        if any(key not in allowed_values for key, _ in outputs):
            raise ValidationError("Dissolved buffer attributes do not correspond to an input feature.")
        if expected is None:
            if any(geometry is not None and not geometry.IsEmpty() for _, geometry in outputs):
                raise ValidationError("An independently empty dissolved buffer has non-empty output.")
            if len(outputs) > 1:
                raise ValidationError("An empty dissolved buffer has too many output features.")
        else:
            if not parameters["separate"] and len(outputs) != 1:
                raise ValidationError("A dissolved buffer must have one output feature.")
            if parameters["separate"]:
                expected_parts = expected.GetGeometryCount() if ogr.GT_Flatten(expected.GetGeometryType()) == ogr.wkbMultiPolygon else 1
                if len(outputs) != expected_parts:
                    raise ValidationError("Dissolved separated buffer feature count is incorrect.")
            actual = None
            for _, geometry in outputs:
                if geometry is None or geometry.IsEmpty():
                    raise ValidationError("A non-empty dissolved buffer has an empty feature.")
                actual = geometry if actual is None else actual.Union(geometry)
            if actual is None or not _same_area_geometry(expected, actual):
                raise ValidationError("Dissolved buffer geometry, distance or options are incorrect.")
            if parameters["distance"] > 0:
                all_sources = None
                for index, (_, source) in enumerate(sources, 1):
                    if not source.Difference(actual).IsEmpty():
                        raise ValidationError(f"Dissolved buffer does not cover input feature {index}.")
                    all_sources = source.Clone() if all_sources is None else all_sources.Union(source)
                if not _maximum_vertex_distance(all_sources, actual, parameters):
                    raise ValidationError("Dissolved buffer exceeds the permitted distance.")
            analytic = _analytic_dissolved(sources, actual, parameters)
            if analytic is False:
                raise ValidationError("Dissolved buffer does not match the independent analytic area.")
            if analytic is True:
                checks.append({"check": "analytic_dissolved_area", "input_features": len(sources)})
    checks.append({"check": "buffer_geometry_and_attributes", "input_features": len(sources),
                   "output_features": len(outputs), "distance_metres": parameters["distance"],
                   "segments_per_quadrant": parameters["segments"], "end_cap_style": parameters["end_cap"],
                   "join_style": parameters["join"], "miter_limit": parameters["miter"],
                   "dissolve": parameters["dissolve"], "separate_disjoint": parameters["separate"],
                   "area_tolerance": "max(1e-12 square metres, 1e-6 of expected area)",
                   "comparison": "OGR/GEOS shape and independent coverage / analytic checks",
                   "independent_analytic_features": analytic_count})
    if independently_empty_count:
        checks.append({"check": "independently_verified_empty_geometry", "features_checked": independently_empty_count})
    if analytic_count:
        checks.append({"check": "analytic_distance_and_area", "features_checked": analytic_count,
                       "radius_tolerance_metres": _distance_tolerance(parameters["distance"])})


def _rectangle(geometry):
    if geometry is None or geometry.IsEmpty():
        return None
    polygon = _single_polygon(geometry)
    if polygon is None or polygon.GetGeometryCount() != 1:
        return None
    ring = polygon.GetGeometryRef(0)
    xmin, xmax, ymin, ymax = polygon.GetEnvelope()
    corners = {(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)}
    if (xmin >= xmax or ymin >= ymax or ring.GetPointCount() != 5
            or set((ring.GetX(i), ring.GetY(i)) for i in range(4)) != corners):
        return None
    points = [(ring.GetX(i), ring.GetY(i)) for i in range(5)]
    if points[0] != points[-1] or any(x1 != x2 and y1 != y2
                                      for (x1, y1), (x2, y2) in zip(points, points[1:])):
        return None
    return xmin, xmax, ymin, ymax


def _rectangle_union_area(rectangles):
    xs = sorted({x for xmin, xmax, _, _ in rectangles for x in (xmin, xmax)})
    area = 0.0
    for left, right in zip(xs, xs[1:]):
        intervals = sorted((ymin, ymax) for xmin, xmax, ymin, ymax in rectangles
                           if xmin < right and xmax > left)
        if not intervals:
            continue
        start, end = intervals[0]
        covered = 0.0
        for low, high in intervals[1:]:
            if low > end:
                covered += end - start
                start, end = low, high
            else:
                end = max(end, high)
        area += (right - left) * (covered + end - start)
    return area


def _measurement_tolerance(geometry, measure, *, area=False):
    """Bound coordinate roundoff, capped at 1e-8 relative with no absolute floor.

    Ring area uses coordinate differences/products; length uses coordinate
    differences and hypot. Account for vertex count and each coordinate's ULP,
    rather than only the final measurement's ULP (which misses cancellation at
    large eastings/northings). The relative cap keeps thin/tiny geometries from
    acquiring a tolerance comparable to their whole area/length.
    """
    xmin, xmax, ymin, ymax = geometry.GetEnvelope()
    dx = math.ulp(max(abs(xmin), abs(xmax)))
    dy = math.ulp(max(abs(ymin), abs(ymax)))
    pending, vertices = [geometry], 0
    while pending:
        part = pending.pop()
        vertices += part.GetPointCount()
        pending.extend(part.GetGeometryRef(i) for i in range(part.GetGeometryCount()))
    coordinate_error = dx * (ymax - ymin) + dy * (xmax - xmin) if area else dx + dy
    return min(abs(measure) * 1e-8, max(8 * math.ulp(measure), 8 * vertices * coordinate_error))


def _measurement_equal(actual, expected, geometry, *, area=False):
    return (_finite_number(actual) and _finite_number(expected) and (actual == 0) == (expected == 0)
            and abs(actual - expected) <= _measurement_tolerance(geometry, expected, area=area))


def _same_geometry(expected, actual):
    if expected is None or expected.IsEmpty():
        return actual is None or actual.IsEmpty()
    if actual is None or actual.IsEmpty():
        return False
    if expected.GetDimension() != actual.GetDimension():
        return False
    if expected.Equals(actual):
        return True
    difference = expected.SymDifference(actual)
    if difference is None:
        return False
    if expected.GetDimension() == 2:
        if not _polygonal(actual):
            return False
        # Identical coverage can have different ring ordering and hence area
        # rounding. A zero symmetric difference is stronger than scalar area.
        if difference.IsEmpty() or difference.GetArea() == 0:
            return True
        return (_measurement_equal(actual.GetArea(), expected.GetArea(), expected, area=True)
                and difference.GetArea() <= _measurement_tolerance(expected, expected.GetArea(), area=True))
    if expected.GetDimension() == 1:
        return (_measurement_equal(actual.Length(), expected.Length(), expected)
                and difference.Length() <= _measurement_tolerance(expected, expected.Length()))
    return False


def _source_schema(layer, allow_empty=False):
    info = _layer_info(layer, allow_empty=allow_empty)
    return {**info, "kind": "spatial"}


def _verified_source_schema(layer, ref, input_facts):
    """Only a SHA-bound, verified buffer may account for an empty input geometry."""
    info = _source_schema(layer, allow_empty=True)
    if info["empty_geometries"]:
        producer = input_facts.get(ref, {}) if isinstance(input_facts, dict) else {}
        output = producer.get("output", {}) if isinstance(producer, dict) else {}
        if (producer.get("algorithm") != "native:buffer" or not isinstance(output, dict)
                or output.get("ref") != ref or output.get("feature_count") != info["feature_count"]
                or output.get("empty_geometries") != info["empty_geometries"]):
            raise ValidationError("Empty input geometry lacks a verified buffer producer and matching facts.")
    return info


def _verified_field_units(ref, input_facts):
    producer = input_facts.get(ref, {}) if isinstance(input_facts, dict) else {}
    if (not isinstance(producer, dict) or producer.get("algorithm") != "qgis:exportaddgeometrycolumns"
            or not isinstance(producer.get("output"), dict) or producer["output"].get("ref") != ref
            or not isinstance(producer.get("field_units"), dict)):
        return {}
    return {field: unit for field, unit in {"area": "m2", "perimeter": "m"}.items()
            if producer["field_units"].get(field) == unit}


def _matching_geometry(source, output):
    families = {"Point": "point", "MultiPoint": "point", "LineString": "line", "MultiLineString": "line",
                "Polygon": "polygon", "MultiPolygon": "polygon"}
    if not families.get(source.get("geometry")) or families.get(source["geometry"]) != families.get(output.get("geometry")):
        raise ValidationError("Output geometry type or dimension does not match the source geometry family.")


def _matching_crs(source, output):
    if not source.IsSame(output):
        raise ValidationError("Output CRS does not match the actual input CRS.")


def _check_source_fields(source, output):
    for name, field_type in source["field_types"].items():
        if output["field_types"].get(name) != field_type:
            raise ValidationError(f"Output is missing field {name!r} or has a different field type.")


def _clip(source_layer, source_srs, overlay_layer, overlay_srs, output_layer, output_srs, checks,
          source_info, overlay_info):
    source, overlay, output = source_info, overlay_info, _source_schema(output_layer)
    _spatial_schema(source)
    _polygon_schema(overlay)
    _matching_crs(source_srs, overlay_srs)
    _matching_crs(source_srs, output_srs)
    _check_source_fields(source, output)
    _matching_geometry(source, output)
    sources = _features(source_layer, source["fields"])
    masks = [geometry for _, geometry in _features(overlay_layer, overlay["fields"])
             if geometry is not None and not geometry.IsEmpty()]
    outputs = _features(output_layer, source["fields"])
    pending = {}
    for key, geometry in outputs:
        pending.setdefault(key, []).append(geometry)
    mask_union = None
    for geometry in masks:
        if not _polygonal(geometry):
            raise ValidationError("Clip overlay must contain polygon geometries.")
        mask_union = geometry if mask_union is None else mask_union.Union(geometry)
    rectangles = [_rectangle(mask) for mask in masks]
    analytic = 0
    expected_count = 0
    for key, geometry in sources:
        if geometry is None or geometry.IsEmpty():
            continue
        intersection = geometry.Intersection(mask_union) if mask_union else None
        if intersection is None or intersection.IsEmpty() or intersection.GetDimension() != geometry.GetDimension():
            continue
        expected_count += 1
        candidates = pending.get(key, [])
        match = next((i for i, candidate in enumerate(candidates) if _same_geometry(intersection, candidate)), None)
        if match is None:
            raise ValidationError("Clip geometry or original attributes do not match the input/overlay intersection.")
        actual = candidates.pop(match)
        rectangle = _rectangle(geometry)
        if rectangle is not None and all(item is not None for item in rectangles):
            parts = []
            for xmin, xmax, ymin, ymax in rectangles:
                a, b = max(xmin, rectangle[0]), min(xmax, rectangle[1])
                c, d = max(ymin, rectangle[2]), min(ymax, rectangle[3])
                if a < b and c < d:
                    parts.append((a, b, c, d))
            area = _rectangle_union_area(parts)
            if abs(actual.GetArea() - area) > _area_tolerance(area):
                raise ValidationError("Clip does not match independent rectangle-intersection area.")
            if parts:
                bounds = (min(p[0] for p in parts), max(p[1] for p in parts),
                          min(p[2] for p in parts), max(p[3] for p in parts))
                if any(abs(a - b) > 1e-8 for a, b in zip(actual.GetEnvelope(), bounds)):
                    raise ValidationError("Clip does not match independent rectangle-intersection bounds.")
            analytic += 1
    if any(candidates for candidates in pending.values()) or len(outputs) != expected_count:
        raise ValidationError("Clip output has extra or missing features.")
    checks.append({"check": "clip_intersection_and_attributes", "input_features": len(sources),
                   "output_features": len(outputs),
                   "comparison": "OGR/GEOS intersection and source attributes; GEOS may also be used by QGIS"})
    if analytic:
        checks.append({"check": "analytic_rectangle_intersection", "features_checked": analytic})


def _geometry_parts(geometry):
    kind = ogr.GT_Flatten(geometry.GetGeometryType())
    if kind in (ogr.wkbMultiPoint, ogr.wkbMultiLineString, ogr.wkbMultiPolygon):
        return [geometry.GetGeometryRef(index) for index in range(geometry.GetGeometryCount())]
    if kind in (ogr.wkbPoint, ogr.wkbLineString, ogr.wkbPolygon):
        return [geometry]
    raise ValidationError("A mixed or unsupported geometry cannot be verified.")


def _linear_part_count(geometry):
    """Count maximal non-branching chains in GEOS-noded linework."""
    edges = []
    adjacency = {}
    for line in _geometry_parts(geometry):
        if ogr.GT_Flatten(line.GetGeometryType()) != ogr.wkbLineString or line.GetPointCount() < 2:
            raise ValidationError("Dissolve linework has an unsupported component.")
        first = (line.GetX(0), line.GetY(0))
        last = (line.GetX(line.GetPointCount() - 1), line.GetY(line.GetPointCount() - 1))
        index = len(edges)
        edges.append((first, last))
        adjacency.setdefault(first, []).append(index)
        adjacency.setdefault(last, []).append(index)
    used = set()
    count = 0
    def walk(start, index):
        node = start
        while index not in used:
            used.add(index)
            a, b = edges[index]
            node = b if node == a else a
            if len(adjacency[node]) != 2:
                break
            next_edges = [candidate for candidate in adjacency[node] if candidate not in used]
            if not next_edges:
                break
            index = next_edges[0]
    for node, indices in adjacency.items():
        if len(indices) != 2:
            for index in indices:
                if index not in used:
                    walk(node, index)
                    count += 1
    for index, (node, _) in enumerate(edges):
        if index not in used:
            walk(node, index)
            count += 1
    return count


def _dissolve(source_layer, source_srs, output_layer, output_srs, params, checks, source_info):
    source, output = source_info, _layer_info(output_layer, allow_empty=True)
    _spatial_schema(source)
    _matching_crs(source_srs, output_srs)
    _check_source_fields(source, output)
    _matching_geometry(source, output)
    fields = _field_list(params.get("FIELD"), name="FIELD", empty=True)
    _fields_exist(source, fields)
    separate = params.get("SEPARATE_DISJOINT")
    if not isinstance(separate, bool):
        raise ValidationError("Dissolve requires explicit Boolean SEPARATE_DISJOINT.")
    source_features, output_features = _features(source_layer, source["fields"]), _features(output_layer, source["fields"])
    if not source_features:
        if output_features:
            raise ValidationError("Empty input produced a dissolve placeholder; it is not a valid empty result.")
        checks.append({"check": "empty_input_empty_output", "feature_count": 0})
        return
    indices = [source["fields"].index(field) for field in fields]
    def group_key(key):
        values = json.loads(key)
        return json.dumps([values[index] for index in indices], sort_keys=True, ensure_ascii=False)
    groups = {}
    for key, geometry in source_features:
        if geometry is None or geometry.IsEmpty():
            raise ValidationError("Dissolve cannot prove a group union with empty input geometry.")
        _geometry_parts(geometry)
        groups.setdefault(group_key(key), []).append((key, geometry))
    results = {}
    for key, geometry in output_features:
        if geometry is None or geometry.IsEmpty():
            raise ValidationError("Dissolve output contains missing or empty geometry.")
        _geometry_parts(geometry)
        group = group_key(key)
        if group not in groups or key not in {member[0] for member in groups[group]}:
            raise ValidationError("Dissolve attributes must come together from a member of the same group.")
        results.setdefault(group, []).append(geometry)
    analytic = 0
    for group, members in groups.items():
        expected = None
        rectangles = []
        for _, geometry in members:
            if expected is not None and expected.GetDimension() != geometry.GetDimension():
                raise ValidationError("Dissolve cannot verify mixed geometry dimensions.")
            expected = geometry if expected is None else expected.Union(geometry)
            rectangles.append(_rectangle(geometry))
        actual_parts = results.get(group, [])
        dimension = expected.GetDimension()
        expected_parts = _linear_part_count(expected) if dimension == 1 else len(_geometry_parts(expected))
        if len(actual_parts) != (expected_parts if separate else 1):
            raise ValidationError("Dissolve group or separated part count is incorrect.")
        if separate and any(len(_geometry_parts(part)) != 1 for part in actual_parts):
            raise ValidationError("Each separated dissolve output must have one component.")
        actual = None
        for geometry in actual_parts:
            actual = geometry if actual is None else actual.Union(geometry)
        if not _same_geometry(expected, actual):
            raise ValidationError("Dissolve group geometry does not match its input union.")
        if dimension == 2 and abs(sum(item.GetArea() for item in actual_parts) - actual.GetArea()) > _area_tolerance(actual.GetArea()):
            raise ValidationError("Dissolve separated outputs overlap or duplicate area.")
        if dimension == 1 and abs(sum(item.Length() for item in actual_parts) - actual.Length()) > _distance_tolerance(actual.Length()):
            raise ValidationError("Dissolve separated outputs overlap or duplicate linework.")
        if all(rectangle is not None for rectangle in rectangles):
            area = _rectangle_union_area(rectangles)
            if abs(actual.GetArea() - area) > _area_tolerance(area):
                raise ValidationError("Dissolve area differs from the independent rectangle union.")
            bounds = (min(rectangle[0] for rectangle in rectangles), max(rectangle[1] for rectangle in rectangles),
                      min(rectangle[2] for rectangle in rectangles), max(rectangle[3] for rectangle in rectangles))
            if any(abs(a - b) > 1e-8 for a, b in zip(actual.GetEnvelope(), bounds)):
                raise ValidationError("Dissolve bounds differ from the independent rectangle union.")
            analytic += 1
    checks.append({"check": "dissolve_groups_union_and_member_attributes", "input_features": len(source_features),
                   "output_features": len(output_features), "groups": len(groups),
                   "comparison": "OGR/GEOS union; GEOS may also be used by QGIS"})
    if analytic:
        checks.append({"check": "analytic_rectangle_union", "groups_checked": analytic})


def _geometry_attributes(source_layer, source_srs, output_layer, output_srs, params, checks):
    source, output = _source_schema(source_layer), _source_schema(output_layer)
    _geometry_parameters(params, source)
    _meter_crs(source_srs)
    _check_projection_area(source_srs, _geographic_bounds(source_layer, source_srs))
    _matching_crs(source_srs, output_srs)
    _check_source_fields(source, output)
    _matching_geometry(source, output)
    if any(output["field_types"].get(name) != "Real" for name in ("area", "perimeter")):
        raise ValidationError("Computed area and perimeter must be real-valued fields.")
    sources = _features(source_layer, source["fields"])
    output_layer.ResetReading()
    pending = {}
    for feature in output_layer:
        key = _feature_values(feature, source["fields"])
        geom = feature.GetGeometryRef()
        values = (feature.GetField("area"), feature.GetField("perimeter"))
        pending.setdefault(key, []).append((geom.Clone() if geom else None, values))
    output_layer.ResetReading()
    if len(sources) != output["feature_count"]:
        raise ValidationError("Geometry attributes output feature count differs from input.")
    for key, source_geom in sources:
        if not _polygonal(source_geom):
            raise ValidationError("Geometry attributes require polygon geometry.")
        candidates = pending.get(key, [])
        index = next((i for i, (geom, _) in enumerate(candidates) if _same_geometry(source_geom, geom)), None)
        if index is None:
            raise ValidationError("Geometry attributes changed source geometry or attributes.")
        _, (area, perimeter) = candidates.pop(index)
        boundary = source_geom.Boundary()
        if (boundary is None or not _measurement_equal(area, source_geom.GetArea(), source_geom, area=True)
                or not _measurement_equal(perimeter, boundary.Length(), source_geom)):
            raise ValidationError("Computed area or perimeter does not match the input geometry.")
    checks.append({"check": "geometry_attributes_planar_measurements", "feature_count": len(sources),
                   "area_unit": "m2", "perimeter_unit": "m",
                   "tolerance": "coordinate ULP and vertex count; capped at 1e-8 relative; zero/nonzero distinct"})


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _require_exact_additions(values, expected):
    # TwoSum recovers the rounding residual of each binary64 addition. A
    # representable final integer/zero alone says nothing about lost low bits
    # earlier in the source stream (e.g. 2^53 + 1 - 2^53).
    running = 0.0
    for value in values:
        value = float(value)
        total = running + value
        if not math.isfinite(total):
            raise ValidationError("Source accumulation has insufficient floating-point precision.")
        recovered = total - running
        residual = (running - (total - recovered)) + (value - recovered)
        if residual != 0:
            raise ValidationError("Source accumulation has insufficient floating-point precision.")
        running = total
    if running != expected:
        raise ValidationError("Source accumulation has insufficient floating-point precision.")


def _sum_tolerance(values, expected):
    # Standard sequential binary64 addition error: gamma_n * sum(abs(x)),
    # gamma_n = n*u/(1-n*u), u=2^-53. fsum supplies the independent reference;
    # eight result ULPs cover final rounding/serialization, not accumulated error.
    # Integer/zero sums must also prove their intermediate additions exact.
    # Singletons need no rounding allowance. At subnormal scales an ULP may
    # exceed the relative budget; require exact additions instead of allowing
    # the ULP term to override the cap.
    rounding = 8 * math.ulp(expected)
    relative_limit = abs(expected) * 1e-9
    if len(values) <= 1 or isinstance(expected, int) or expected == 0 or rounding > relative_limit:
        _require_exact_additions(values, expected)
        return 0.0
    nu = len(values) * 2.0 ** -53
    try:
        error = nu / (1 - nu) * math.fsum(abs(value) for value in values) if nu < 1 else math.inf
    except (OverflowError, ValueError):
        error = math.inf
    if not math.isfinite(error) or error > relative_limit:
        raise ValidationError("Source sum has insufficient floating-point precision (severe cancellation or accumulation).")
    return min(relative_limit, max(rounding, error))


def _sum_equal(actual, expected, values):
    return (_finite_number(actual) and (actual == 0) == (expected == 0)
            and abs(actual - expected) <= _sum_tolerance(values, expected))


def _table_rows(layer):
    definition = layer.GetLayerDefn()
    fields = {}
    for i in range(definition.GetFieldCount()):
        field = definition.GetFieldDefn(i)
        fields[field.GetName()] = "Boolean" if field.GetSubType() == ogr.OFSTBoolean else field.GetTypeName()
    rows = []
    layer.ResetReading()
    for feature in layer:
        rows.append({name: feature.GetField(name) for name in fields})
    layer.ResetReading()
    return fields, rows


def _summary(values, total, numeric):
    if not numeric:
        return {"feature_count": total, "valid_value_count": None, "null_value_count": None,
                "sum": None, "min": None, "max": None}
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value)
           for value in values):
        raise ValidationError("The source contains a non-finite or non-numeric value.")
    try:
        if values and all(isinstance(value, int) and not isinstance(value, bool) for value in values):
            total_value = sum(values)
            if any(int(float(value)) != value for value in [*values, total_value]):
                raise ValidationError("Integer statistics exceed reliable double precision.")
        else:
            total_value = math.fsum(values)
    except (OverflowError, ValueError) as exc:
        raise ValidationError("The source numeric sum cannot be represented reliably.") from exc
    if not math.isfinite(total_value):
        raise ValidationError("The source numeric sum cannot be represented reliably.")
    _sum_tolerance(values, total_value)
    return {"feature_count": total, "valid_value_count": len(values), "null_value_count": total - len(values),
            "sum": total_value, "min": min(values) if values else None, "max": max(values) if values else None}


def _preflight_stat_values(source, field, groups):
    dataset, layer, _ = _open_layer(source.get("path"), source.get("layer_name"))
    try:
        values, count, buckets = [], 0, {}
        layer.ResetReading()
        for feature in layer:
            count += 1
            group = {name: feature.GetField(name) for name in groups}
            if any(isinstance(value, float) and not math.isfinite(value) for value in group.values()):
                raise ValidationError("The source contains a non-finite category value.")
            value = feature.GetField(field) if field else None
            if value is not None:
                values.append(value)
            if groups and field is not None:
                key = json.dumps(group, sort_keys=True, ensure_ascii=False, allow_nan=False)
                bucket = buckets.setdefault(key, {"values": [], "count": 0})
                bucket["count"] += 1
                if value is not None:
                    bucket["values"].append(value)
        _summary(values, count, field is not None)
        for bucket in buckets.values():
            _summary(bucket["values"], bucket["count"], True)
    finally:
        dataset.Close()


def _statistics(source_layer, table_layer, algorithm, params, source_ref, input_facts, checks, schema):
    field, group_fields = _stat_parameters(algorithm, params, schema)
    numeric = field is not None
    table_fields, rows = _table_rows(table_layer)
    expected_fields = {"count": ("Integer", "Integer64")}
    if numeric:
        expected_fields.update({name: ("Real",) for name in ("sum", "min", "max")})
    if algorithm == "native:basicstatisticsforfields":
        expected_fields.update({"empty": ("Integer", "Integer64"), "filled": ("Integer", "Integer64")})
    for name in group_fields:
        expected_fields[name] = (schema["field_types"][name],)
    if any(table_fields.get(name) not in types for name, types in expected_fields.items()):
        raise ValidationError("Statistics table is missing required columns or has incorrect column types.")
    groups, values = {}, []
    source_layer.ResetReading()
    count = 0
    for feature in source_layer:
        count += 1
        group = {name: feature.GetField(name) for name in group_fields}
        key = json.dumps(group, sort_keys=True, ensure_ascii=False, allow_nan=False)
        bucket = groups.setdefault(key, {"group": group, "values": [], "count": 0})
        bucket["count"] += 1
        value = feature.GetField(field) if numeric else None
        if value is not None:
            bucket["values"].append(value)
            values.append(value)
    source_layer.ResetReading()
    total = _summary(values, count, numeric)
    seen = set()
    result_groups, omitted = [], []
    if group_fields:
        for row in rows:
            group = {name: row[name] for name in group_fields}
            key = json.dumps(group, sort_keys=True, ensure_ascii=False, allow_nan=False)
            if key not in groups or key in seen:
                raise ValidationError("Statistics table contains an unexpected or duplicate category.")
            bucket = groups[key]
            summary = _summary(bucket["values"], bucket["count"], numeric)
            expected_count = summary["valid_value_count"] if numeric else summary["feature_count"]
            if row["count"] != expected_count or (numeric and (
                    not _sum_equal(row["sum"], summary["sum"], bucket["values"])
                    or row["min"] != summary["min"] or row["max"] != summary["max"])):
                raise ValidationError("Category statistics do not match independently counted input values.")
            if numeric and expected_count == 0:
                raise ValidationError("A numeric category with no valid values must not appear as a QGIS result.")
            seen.add(key)
            result_groups.append({"group": group, **summary,
                                  "valid_value_count": row["count"] if numeric else None,
                                  "feature_count": summary["feature_count"] if numeric else row["count"],
                                  "sum": row["sum"] if numeric else None,
                                  "min": row["min"] if numeric else None, "max": row["max"] if numeric else None})
        for key, bucket in groups.items():
            if key not in seen:
                summary = _summary(bucket["values"], bucket["count"], numeric)
                if not numeric or summary["valid_value_count"]:
                    raise ValidationError("Statistics table is missing a populated category.")
                omitted.append({"group": bucket["group"], **summary, "sum": None, "min": None, "max": None})
        if numeric:
            total.update({"valid_value_count": sum(row["count"] for row in rows),
                          "sum": math.fsum(row["sum"] for row in rows) if rows else None,
                          "min": min(row["min"] for row in rows) if rows else None,
                          "max": max(row["max"] for row in rows) if rows else None})
    else:
        if len(rows) != 1:
            raise ValidationError("Basic statistics must produce exactly one table row.")
        row = rows[0]
        expected = {"count": total["valid_value_count"], "filled": total["valid_value_count"],
                    "empty": total["null_value_count"], "sum": total["sum"], "min": total["min"], "max": total["max"]}
        if any((not _sum_equal(row[name], value, values) if name == "sum" else row[name] != value)
               for name, value in expected.items()):
            raise ValidationError("Basic statistics do not match independently counted input values.")
        total.update({"valid_value_count": row["count"], "null_value_count": row["empty"],
                      "sum": row["sum"], "min": row["min"], "max": row["max"]})
    unit = _verified_field_units(source_ref, input_facts).get(field)
    statistics = {"kind": "categorical" if group_fields else "numeric", "field": field,
                  "group_fields": group_fields, **total, "unit": unit, "empty": count == 0,
                  "no_valid_values": bool(numeric and not values), "groups": result_groups, "omitted_groups": omitted}
    checks.append({"check": "statistics_recomputed_from_input", "input_features": count,
                   "table_rows": len(rows), "omitted_groups": len(omitted),
                   "sum_tolerance": "max(8 result ULP, gamma_n * sum(abs(input))); hard 1e-9 relative cap",
                   "exact_sum_cases": "singletons; integers, zero and subnormal-scale sums require exact source-stream additions",
                   "exact_comparisons": "counts, integer sums, min/max, zero/nonzero"})
    return statistics


def _layer_observation(request):
    inputs, outputs, observation = request.get("input_paths"), request.get("output_paths"), request.get("observation")
    if not isinstance(inputs, dict) or len(inputs) != 1 or outputs != {} or not isinstance(observation, dict):
        raise ValidationError("Layer validation needs one actual input and its observed layer data.")
    ref, path = next(iter(inputs.items()))
    dataset, layer, srs = _open_layer(path)
    try:
        info = _verified_source_schema(layer, ref, request.get("input_facts") or {})
        obs_source, obs_layer = _path_layer(observation.get("source"))
        in_source, in_layer = _path_layer(path)
        if dataset.GetLayerCount() > 1 and not obs_layer:
            raise ValidationError("A multi-layer source must identify the actually loaded layer.")
        if Path(obs_source).resolve() != Path(in_source).resolve() or (obs_layer and obs_layer != layer.GetName()) or (in_layer and in_layer != layer.GetName()):
            raise ValidationError("Loaded layer source or selected layer differs from the actual input.")
        if not isinstance(observation.get("id"), str) or not observation["id"]:
            raise ValidationError("The loaded layer has no actual layer ID.")
        if not _srs(observation.get("crs")).IsSame(srs):
            raise ValidationError("Loaded layer CRS differs from the actual input.")
        if type(observation.get("feature_count")) is not int or observation["feature_count"] != info["feature_count"]:
            raise ValidationError("Loaded layer feature count differs from the actual input.")
        if observation.get("geometry", "").replace(" ", "").lower() != info["geometry"].lower():
            raise ValidationError("Loaded layer geometry differs from the actual 2D input.")
        fields = observation.get("fields")
        fid = layer.GetFIDColumn()
        allowed_fields = [info["fields"]]
        if fid and fid not in info["fields"]:
            allowed_fields.append([fid, *info["fields"]])
        if fields not in allowed_fields:
            raise ValidationError("Loaded layer fields differ from the actual input.")
        stats = {"kind": "feature_count", "field": None, "group_fields": [],
                 "feature_count": info["feature_count"], "valid_value_count": None, "null_value_count": None,
                 "sum": None, "min": None, "max": None, "unit": None, "empty": info["feature_count"] == 0,
                 "no_valid_values": False, "groups": [], "omitted_groups": []}
        return _report("passed", checks=[{"check": "loaded_layer_matches_input"}],
                       facts={"input": {"ref": ref, "crs": _crs_label(srs), **info}, "statistics": stats})
    finally:
        dataset.Close()

def _validate_t05(request, algorithm, params):
    input_paths, output_paths = request.get("input_paths"), request.get("output_paths")
    if not isinstance(input_paths, dict) or not isinstance(output_paths, dict) or len(output_paths) != 1:
        raise ValidationError("Spatial validation needs actual input and output references.")
    if algorithm == "native:clip":
        source_ref, overlay_ref = _match_parameter_refs(params, list(input_paths), input_paths, ("INPUT", "OVERLAY"))
    else:
        input_name = "INPUT_LAYER" if algorithm == "native:basicstatisticsforfields" else "INPUT"
        source_ref, = _match_parameter_refs(params, list(input_paths), input_paths, (input_name,))
        overlay_ref = None
    _gpkg_output(params)
    output_ref, output_path = next(iter(output_paths.items()))
    if params.get("OUTPUT") != output_path:
        raise ValidationError("The actual output does not match the approved output reference.")
    datasets = []
    try:
        dataset, source, source_srs = _open_layer(input_paths[source_ref])
        datasets.append(dataset)
        overlay = overlay_srs = None
        if overlay_ref is not None:
            dataset, overlay, overlay_srs = _open_layer(input_paths[overlay_ref])
            datasets.append(dataset)
        table = algorithm in ("native:basicstatisticsforfields", "qgis:statisticsbycategories")
        dataset, output, output_srs = _open_layer(output_path, table=table)
        datasets.append(dataset)
        input_facts = request.get("input_facts") or {}
        source_info = _verified_source_schema(source, source_ref, input_facts)
        overlay_info = _verified_source_schema(overlay, overlay_ref, input_facts) if overlay is not None else None
        if table:
            output_fields, rows = _table_rows(output)
            output_info = {"layer": output.GetName(), "feature_count": len(rows),
                           "fields": list(output_fields), "field_types": output_fields, "geometry": "NoGeometry"}
        else:
            output_info = _layer_info(output, allow_empty=algorithm == "native:dissolve")
        facts = {"algorithm": algorithm, "input": {"ref": source_ref, "crs": _crs_label(source_srs), **source_info},
                 "output": {"ref": output_ref, "crs": _crs_label(output_srs) if output_srs else None, **output_info}}
        if overlay is not None:
            facts["overlay"] = {"ref": overlay_ref, "crs": _crs_label(overlay_srs), **overlay_info}
        checks = []
        try:
            if algorithm == "native:clip":
                _clip(source, source_srs, overlay, overlay_srs, output, output_srs, checks, source_info, overlay_info)
            elif algorithm == "native:dissolve":
                _dissolve(source, source_srs, output, output_srs, params, checks, source_info)
            elif algorithm == "qgis:exportaddgeometrycolumns":
                _geometry_attributes(source, source_srs, output, output_srs, params, checks)
                facts["field_units"] = {"area": "m2", "perimeter": "m"}
            else:
                facts["statistics"] = _statistics(source, output, algorithm, params, source_ref,
                                                  input_facts, checks, source_info)
            return _report("passed", checks=checks, facts=facts)
        except ValidationError as exc:
            return _report("failed", checks=checks, errors=[str(exc)], facts=facts)
    finally:
        for dataset in reversed(datasets):
            dataset.Close()


def _validate(request):
    call = request.get("call") or {}
    args = call.get("arguments") or {}
    algorithm = args.get("algorithm")
    if call.get("tool_name") == "add_layer":
        return _layer_observation(request)
    if call.get("tool_name") == "execute_processing" and algorithm in (
        "native:clip", "native:dissolve", "qgis:exportaddgeometrycolumns",
        "qgis:statisticsbycategories", "native:basicstatisticsforfields",
    ):
        params = args.get("parameters")
        if not isinstance(params, dict):
            raise ValidationError("Processing parameters are missing.")
        return _validate_t05(request, algorithm, params)
    if call.get("tool_name") != "execute_processing" or algorithm not in ("native:reprojectlayer", "native:buffer"):
        return _report("not_applicable", facts={"algorithm": algorithm})
    if algorithm == "native:buffer" and call.get("distance_unit") != "m":
        raise ValidationError("Buffer distance unit must be explicitly reviewed as m.")
    input_paths = request.get("input_paths")
    output_paths = request.get("output_paths")
    if not isinstance(input_paths, dict) or not isinstance(output_paths, dict) or len(input_paths) != 1 or len(output_paths) != 1:
        raise ValidationError("Spatial validation needs one actual input and one actual output reference.")
    input_ref, input_path = next(iter(input_paths.items()))
    output_ref, output_path = next(iter(output_paths.items()))
    in_dataset, in_layer, in_srs = _open_layer(input_path)
    try:
        out_dataset, out_layer, out_srs = _open_layer(output_path)
        try:
            in_info = _layer_info(in_layer)
            out_info = _layer_info(out_layer, allow_empty=(algorithm == "native:buffer"))
            facts = {"algorithm": algorithm,
                     "input": {"ref": input_ref, "crs": _crs_label(in_srs), **in_info},
                     "output": {"ref": output_ref, "crs": _crs_label(out_srs), **out_info}}
            checks = []
            try:
                if algorithm == "native:reprojectlayer":
                    _reprojection(in_layer, in_srs, out_layer, out_srs, args.get("parameters") or {}, checks)
                else:
                    _buffer(in_layer, in_srs, out_layer, out_srs, args.get("parameters") or {}, checks)
                return _report("passed", checks=checks, facts=facts)
            except ValidationError as exc:
                return _report("failed", checks=checks, errors=[str(exc)], facts=facts)
        finally:
            out_dataset.Close()
    finally:
        in_dataset.Close()


def main():
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValidationError("A structured spatial inspection request is required.")
        if request.get("mode") == "preflight":
            result = _preflight(request.get("plan"), request.get("input_facts"))
        elif request.get("mode") == "validate":
            result = _validate(request)
        else:
            raise ValidationError("Unsupported spatial inspection mode.")
    except ValidationError as exc:
        result = _report("failed", errors=[str(exc)])
    except Exception:
        result = _report("failed", errors=["Spatial inspection could not complete."])
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result["status"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
