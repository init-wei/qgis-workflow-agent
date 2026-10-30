"""Read-only spatial validation in the distribution Python/GDAL environment.

Input and output are one JSON document on standard streams.  This process is not a
QGIS Processing runner: it reads local vector files and uses independent checks.
Paths and their relationship to an approved plan are owned by the caller.
"""

import json
import math
from pathlib import Path
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


def _open_layer(path, selected=None):
    filename, layer_name = _path_layer(path, selected)
    if not Path(filename).is_file():
        raise ValidationError("A referenced local vector file is unavailable.")
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
    return {"layer": layer.GetName(), "feature_count": count, "fields": fields,
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


def _preflight(plan):
    if not isinstance(plan, dict):
        raise ValidationError("A structured task plan is required.")
    refs = {}
    inputs = []
    for source in plan.get("required_inputs", []):
        ref = source.get("input_id")
        dataset, layer, spatial_ref = _open_layer(source.get("path"), source.get("layer_name"))
        try:
            info = _layer_info(layer)
            refs[ref] = {"srs": spatial_ref, "geographic_bounds": _geographic_bounds(layer, spatial_ref)}
            inputs.append({"ref": ref, "crs": _crs_label(spatial_ref), **info})
        finally:
            dataset.Close()
    steps = {step.get("step_id"): step for step in plan.get("steps", [])}
    checks = []
    for call in plan.get("tool_calls", []):
        step_id = call.get("step_id")
        step = steps.get(step_id)
        if step is None:
            raise ValidationError("A planned call has no matching step.")
        output_refs = step.get("output_refs", [])
        if call.get("tool_name") != "execute_processing":
            for ref in output_refs:
                refs[ref] = None
            continue
        args = call.get("arguments", {})
        algorithm = args.get("algorithm")
        params = args.get("parameters")
        if algorithm not in ("native:reprojectlayer", "native:buffer"):
            raise ValidationError(f"Step {step_id}: spatial validation for this Processing algorithm is not yet available.")
        input_refs = step.get("input_refs", [])
        if len(input_refs) != 1 or len(output_refs) != 1 or input_refs[0] not in refs:
            raise ValidationError(f"Step {step_id} needs one known input and one output reference.")
        source_ref = refs[input_refs[0]]
        if source_ref is None:
            raise ValidationError(f"Step {step_id} input CRS cannot be established before execution.")
        if not isinstance(params, dict):
            raise ValidationError(f"Step {step_id} processing parameters are missing.")
        if algorithm == "native:reprojectlayer":
            target_ref = _srs(params.get("TARGET_CRS"))
            refs[output_refs[0]] = {"srs": target_ref, "geographic_bounds": source_ref["geographic_bounds"]}
            checks.append({"step_id": step_id, "check": "target_crs", "crs": _crs_label(target_ref)})
        else:
            if call.get("distance_unit") != "m":
                raise ValidationError(f"Step {step_id} needs an explicitly reviewed buffer distance unit of m.")
            _buffer_parameters(params)
            _meter_crs(source_ref["srs"])
            _check_projection_area(source_ref["srs"], source_ref["geographic_bounds"])
            refs[output_refs[0]] = source_ref
            checks.append({"step_id": step_id, "check": "buffer_metre_crs", "crs": _crs_label(source_ref["srs"])})
    return _report("passed", checks=checks, facts={"inputs": inputs})



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


def _validate(request):
    call = request.get("call") or {}
    args = call.get("arguments") or {}
    algorithm = args.get("algorithm")
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
            result = _preflight(request.get("plan"))
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
