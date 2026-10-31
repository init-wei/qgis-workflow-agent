#!/usr/bin/env python3
"""Freeze, materialize, and independently verify the finite T07 GIS cases.

Math expectations use Snyder's series, analytic regular polygons and shoelace
arithmetic. GDAL only writes the inputs and reads actual files; it never reruns
QGIS Processing to make expected results. This is not a general GIS verifier.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

from build_spatial_oracle import transverse_mercator

ROOT = Path(__file__).resolve().parents[1]
DEFINITION = ROOT / "tests/fixtures/gis/t07_benchmark.json"
ORACLE = ROOT / "tests/fixtures/gis/t07_benchmark_oracle.json"
GENERATOR = Path(__file__).resolve()
SNYDER_HELPER = ROOT / "scripts/build_spatial_oracle.py"
REQUIRED_SPECIAL_CATEGORIES = {
    "exceptions": {
        "missing_file",
        "unknown_crs",
        "ambiguous_units",
        "invalid_parameters",
        "unsupported_algorithm",
        "qgis_interrupt",
        "unapproved",
        "rejected",
        "stale_review",
        "duplicate_resume",
    },
    "browser": {
        "plan_restart",
        "risk_restart",
        "result_restart",
        "switch_session",
        "cancel_old_stream",
        "text_rendering",
        "deep_link",
    },
}


class VerificationFailure(ValueError):
    """Evidence proves a mismatch with this fixed case, not an inspector failure."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def shoelace(ring):
    # Translate the origin first so the small parcels avoid cancellation of large
    # UTM products. This computes signed geometry independently of GIS.
    x0, y0 = ring[0]
    points = [(x - x0, y - y0) for x, y in ring]
    return (
        abs(
            math.fsum(
                x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:])
            )
        )
        / 2
    )


def poly(local):
    return {
        "type": "Polygon",
        "coordinates": [[[500000 + x, 3300000 + y] for x, y in local]],
    }


def clip_to_rectangle(ring, mask_ring):
    """Intersect this fixture's straight-edged ring with an axis-aligned window."""
    xs = [point[0] for point in mask_ring]
    ys = [point[1] for point in mask_ring]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    if set(map(tuple, mask_ring[:-1])) != {
        (xmin, ymin),
        (xmax, ymin),
        (xmax, ymax),
        (xmin, ymax),
    }:
        raise ValueError("fixture mask is not the expected rectangle")
    vertices = [list(p) for p in ring[:-1]]
    for axis, bound, lower in [
        (0, xmin, True),
        (0, xmax, False),
        (1, ymin, True),
        (1, ymax, False),
    ]:
        if not vertices:
            break
        result = []
        previous = vertices[-1]
        previous_inside = previous[axis] >= bound if lower else previous[axis] <= bound
        for current in vertices:
            inside = current[axis] >= bound if lower else current[axis] <= bound
            if inside != previous_inside:
                ratio = (bound - previous[axis]) / (current[axis] - previous[axis])
                result.append(
                    [previous[i] + ratio * (current[i] - previous[i]) for i in (0, 1)]
                )
            if inside:
                result.append(current)
            previous, previous_inside = current, inside
        vertices = []
        for point in result:
            if not vertices or point != vertices[-1]:
                vertices.append(point)
        if len(vertices) > 1 and vertices[0] == vertices[-1]:
            vertices.pop()
    if len(vertices) < 3:
        return None
    vertices.append(vertices[0])
    if shoelace(vertices) == 0:
        return None
    return {"type": "Polygon", "coordinates": [vertices]}


def stats(values):
    nonnull = [v for v in values if v is not None]
    return {
        "count": len(values),
        "valid": len(nonnull),
        "null": len(values) - len(nonnull),
        "sum": math.fsum(nonnull),
        "min": min(nonnull),
        "max": max(nonnull),
    }


def build_oracle(definition_bytes):
    definition = json.loads(definition_bytes)
    datasets = definition["datasets"]
    points = {}
    flattening = 1 / 298.257223563
    for name, meridian, target, radius in [
        ("west_points", 111, "EPSG:32649", 60),
        ("east_points", 123, "EPSG:32651", 175),
    ]:
        features = []
        for feature in datasets[name]["features"]:
            lon, lat = feature["geometry"]["coordinates"]
            if abs(lon - meridian) >= 0.7 or not 0 < lat < 84:
                raise ValueError("point outside frozen Snyder/UTM domain")
            xy = transverse_mercator(
                lon,
                lat,
                semi_major_m=6378137.0,
                eccentricity_squared=flattening * (2 - flattening),
                central_meridian_deg=meridian,
                scale=0.9996,
                false_easting_m=500000,
                false_northing_m=0,
            )
            features.append(
                {
                    "properties": feature["properties"],
                    "input_coordinates": [lon, lat],
                    "projected_coordinates_m": [round(v, 6) for v in xy],
                    "buffer_vertices_m": [
                        [
                            round(xy[0] + radius * math.cos(2 * math.pi * i / 32), 6),
                            round(xy[1] + radius * math.sin(2 * math.pi * i / 32), 6),
                        ]
                        for i in range(32)
                    ],
                }
            )
        n = 32
        points[name] = {
            "target_crs": target,
            "central_meridian": meridian,
            "features": features,
            "buffer": {
                "radius_m": radius,
                "segments": 8,
                "vertices": n,
                "area_m2": n * radius**2 * math.sin(2 * math.pi / n) / 2,
                "edge_m": 2 * radius * math.sin(math.pi / n),
                "perimeter_m": 2 * n * radius * math.sin(math.pi / n),
            },
        }
    parcels = datasets["parcels"]["features"]
    areas = [shoelace(f["geometry"]["coordinates"][0]) for f in parcels]
    mask = datasets["left_mask"]["features"][0]["geometry"]["coordinates"][0]
    far_mask = datasets["far_mask"]["features"][0]["geometry"]["coordinates"][0]
    clips = []
    far_count = 0
    for parcel in parcels:
        ring = parcel["geometry"]["coordinates"][0]
        geometry = clip_to_rectangle(ring, mask)
        if geometry is not None:
            clips.append(
                {
                    "geometry": geometry,
                    "properties": parcel["properties"],
                    "area_m2": shoelace(geometry["coordinates"][0]),
                }
            )
        far_count += clip_to_rectangle(ring, far_mask) is not None
    shapes = {
        "A": poly([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]),
        "B": poly([(20, 0), (28, 0), (28, 6), (20, 6), (20, 0)]),
        "C": parcels[4]["geometry"],
    }
    area_by_group = {
        key: shoelace(value["coordinates"][0]) for key, value in shapes.items()
    }
    return {
        "schema_version": 1,
        "definition_sha256": digest(definition_bytes),
        "method": {
            "projection": "Snyder equations 8-9, 8-10, 3-21 with WGS84; no GIS/PROJ",
            "geometry": "straight-edge rectangle clipping and explicit union vertices; translated shoelace; no GIS Processing",
            "buffer": "32-gon area=N*r^2*sin(2*pi/N)/2; edge=2*r*sin(pi/N)",
            "scope": "only the frozen synthetic fixtures; not a general topology proof",
            "projection_source": "https://pubs.usgs.gov/pp/1395/report.pdf",
            "projection_helper_sha256": digest(SNYDER_HELPER.read_bytes()),
        },
        "tolerances": {
            "projected_axis_m": 0.02,
            "buffer_radius_m": 0.03,
            "buffer_edge_m": 0.001,
            "buffer_area_m2": 0.05,
            "buffer_perimeter_m": 0.005,
            "polygon_area_m2": 1e-7,
        },
        "points": points,
        "parcels": {"areas_m2": areas, "total_area_m2": math.fsum(areas)},
        "clip": {
            "features": clips,
            "far_feature_count": far_count,
            "empty_result_is_valid": True,
        },
        "dissolve": {
            "geometries": shapes,
            "group_area_m2": area_by_group,
            "total_area_m2": math.fsum(area_by_group.values()),
            "component_count": 3,
        },
        "statistics": {
            "score": stats([f["properties"]["score"] for f in parcels]),
            "area": stats(areas),
        },
    }


def check_definition(definition, oracle):
    scenarios = definition["scenarios"]
    families = {"load", "reproject", "buffer", "clip", "dissolve", "statistics"}
    ids = {f"{family}-{n}" for family in families for n in (1, 2)}
    if (
        len(scenarios) != 12
        or {s["id"] for s in scenarios} != ids
        or len(definition["dev_references"]) != 6
        or {s["family"] for s in definition["dev_references"]} != families
        or len(definition["exceptions"]) < 8
        or not definition["browser"]
    ):
        raise ValueError(
            "definition scenarios or separate reference groups are incomplete"
        )
    request_ids, texts = set(), set()
    for scene in scenarios:
        if (
            scene["family"] not in families
            or scene["id"].split("-")[0] != scene["family"]
        ):
            raise ValueError("scenario family mismatch")
        if len(scene["requests"]) != 2 or not scene["inputs"]:
            raise ValueError("each holdout scenario needs two requests and inputs")
        if any(key not in definition["datasets"] for key in scene["inputs"]):
            raise ValueError("unknown dataset in scenario")
        for request, suffix in zip(scene["requests"], ("a", "b")):
            if (
                request["id"] != scene["id"] + "-" + suffix
                or request["id"] in request_ids
                or request["text"] in texts
            ):
                raise ValueError("requests must have distinct IDs and wording")
            if set(re.findall(r"\{([^{}]+)\}", request["text"])) != set(
                scene["inputs"]
            ):
                raise ValueError("request file placeholders differ from inputs")
            request_ids.add(request["id"])
            texts.add(request["text"])
    special_ids = set()
    for group, required in REQUIRED_SPECIAL_CATEGORIES.items():
        cases = definition[group]
        missing = required - {case.get("category") for case in cases}
        if missing:
            raise ValueError(
                f"missing {group} special case categories: {', '.join(sorted(missing))}"
            )
        for case in cases:
            identity = case.get("id")
            if not isinstance(identity, str) or not identity or identity in special_ids:
                raise ValueError("special case IDs must be distinct and nonempty")
            special_ids.add(identity)
            if case.get("status") != "not_run":
                raise ValueError(
                    "unrun special cases must remain not_run in the definition"
                )
            if not all(
                isinstance(case.get(key), str) and case[key].strip()
                for key in ("category", "input", "expected")
            ):
                raise ValueError(
                    f"special case {identity} lacks a fixed input or expected result"
                )
            actions = case.get("actions")
            if (
                not isinstance(actions, list)
                or not actions
                or any(
                    not isinstance(action, str) or not action.strip()
                    for action in actions
                )
            ):
                raise ValueError(f"special case {identity} lacks executable actions")
            execution = case.get("execution")
            if not isinstance(execution, dict) or not all(
                isinstance(execution.get(key), str) and execution[key].strip()
                for key in ("kind", "command", "source", "scope")
            ):
                raise ValueError(
                    f"special case {identity} lacks an execution method and scope"
                )
            if execution["kind"] not in {
                "http_or_adapter_seam",
                "system_gdal_seam",
                "real_browser",
            }:
                raise ValueError(
                    f"special case {identity} has an unknown execution kind"
                )
            source = Path(execution["source"])
            if (
                source.is_absolute()
                or ".." in source.parts
                or not (ROOT / source).is_file()
            ):
                raise ValueError(
                    f"special case {identity} has no repository execution source"
                )
            metrics = case.get("metrics")
            if (
                not isinstance(metrics, list)
                or len(set(metrics)) != len(metrics)
                or not set(metrics) <= {"false_success", "unapproved_execution"}
            ):
                raise ValueError(
                    f"special case {identity} has invalid metric declarations"
                )
    if (
        oracle["parcels"]["areas_m2"] != [50, 50, 24, 24, 24]
        or oracle["parcels"]["total_area_m2"] != 172
        or [x["area_m2"] for x in oracle["clip"]["features"]] != [37.5, 12.5]
        or oracle["clip"]["far_feature_count"] != 0
        or oracle["dissolve"]["group_area_m2"] != {"A": 100, "B": 48, "C": 24}
        or oracle["statistics"]["score"]
        != {"count": 5, "valid": 4, "null": 1, "sum": 14.75, "min": -4.5, "max": 12.25}
    ):
        raise ValueError("arithmetic differs from the fixed case")


def checked(definition_path=DEFINITION, oracle_path=ORACLE):
    source = Path(definition_path).read_bytes()
    stored = json.loads(Path(oracle_path).read_bytes())
    if digest(source) != stored["definition_sha256"]:
        raise ValueError("definition SHA-256 mismatch")
    calculated = build_oracle(source)
    if calculated != stored:
        raise ValueError("frozen oracle differs from independent arithmetic")
    definition = json.loads(source)
    check_definition(definition, calculated)
    return definition, calculated


def no_sidecars(path):
    for suffix in ("-wal", "-shm", "-journal"):
        if os.path.lexists(str(path) + suffix):
            raise VerificationFailure(f"SQLite sidecar present: {path}{suffix}")


def file_hash(path):
    path = Path(path)
    no_sidecars(path)
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise VerificationFailure(f"missing or empty regular file: {path}") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
        raise VerificationFailure(f"missing or empty regular file: {path}")
    if not info.st_mode & 0o444:
        raise PermissionError(f"file is not readable: {path}")
    before = path.stat()
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"file changed while reading: {path}")
    no_sidecars(path)
    return h.hexdigest(), after.st_size


def materialize(output_dir, command):
    os.environ["GDAL_PAM_ENABLED"] = "NO"
    from osgeo import gdal, ogr, osr

    gdal.UseExceptions()
    ogr.UseExceptions()
    definition, _ = checked()
    sources = [
        DEFINITION.read_bytes(),
        ORACLE.read_bytes(),
        GENERATOR.read_bytes(),
        SNYDER_HELPER.read_bytes(),
    ]
    target = Path(os.path.abspath(output_dir))
    os.mkdir(target, 0o700)
    driver = ogr.GetDriverByName("GPKG")
    if driver is None:
        raise ValueError("GDAL GeoPackage driver unavailable")
    fields = {"Integer": ogr.OFTInteger, "Real": ogr.OFTReal, "String": ogr.OFTString}
    files = {}
    for name, item in definition["datasets"].items():
        filename = item["filename"]
        if Path(filename).name != filename or Path(filename).suffix != ".gpkg":
            raise ValueError("unsafe fixture filename")
        path = target / filename
        ds = driver.CreateDataSource(str(path))
        srs = osr.SpatialReference()
        srs.SetFromUserInput(item["crs"])
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        geom_type = {"Point": ogr.wkbPoint, "Polygon": ogr.wkbPolygon}[
            item["geometry_type"]
        ]
        layer = ds.CreateLayer(
            item["layer"], srs, geom_type, options=["SPATIAL_INDEX=NO"]
        )
        for field in item["fields"]:
            fd = ogr.FieldDefn(field["name"], fields[field["type"]])
            fd.SetNullable(field["nullable"])
            if layer.CreateField(fd) != 0:
                raise ValueError("cannot write field")
        for row in item["features"]:
            feature = ogr.Feature(layer.GetLayerDefn())
            for field in item["fields"]:
                key, value = field["name"], row["properties"][field["name"]]
                if value is None:
                    feature.SetFieldNull(feature.GetFieldIndex(key))
                else:
                    feature.SetField(key, value)
            geometry = ogr.CreateGeometryFromJson(json.dumps(row["geometry"]))
            feature.SetGeometry(geometry)
            if layer.CreateFeature(feature) != 0:
                raise ValueError("cannot write feature")
            feature = geometry = None
        layer = ds = None
        sha, size = file_hash(path)
        files[name] = {
            k: item[k] for k in ("filename", "layer", "crs", "geometry_type", "fields")
        }
        files[name].update(
            feature_count=len(item["features"]), sha256=sha, size_bytes=size
        )
    if sources != [
        DEFINITION.read_bytes(),
        ORACLE.read_bytes(),
        GENERATOR.read_bytes(),
        SNYDER_HELPER.read_bytes(),
    ]:
        raise ValueError(
            "fixture definition, oracle, generator, or Snyder helper changed during materialization"
        )
    manifest = {
        "manifest_version": 1,
        "definition_path": str(DEFINITION.relative_to(ROOT)),
        "definition_sha256": digest(sources[0]),
        "oracle_path": str(ORACLE.relative_to(ROOT)),
        "oracle_sha256": digest(sources[1]),
        "generator_sha256": digest(sources[2]),
        "snyder_helper_sha256": digest(sources[3]),
        "gdal_version": gdal.VersionInfo("RELEASE_NAME"),
        "generation_command": command,
        "byte_reproducibility": "GeoPackage timestamps may vary; actual file hashes are authoritative",
        "datasets": files,
    }
    with (target / "manifest.json").open("x", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return manifest


def selected_path(raw):
    if not isinstance(raw, str) or not raw or "://" in raw or "\\" in raw:
        raise VerificationFailure("invalid local file reference")
    parts = raw.split("|")
    if len(parts) > 2 or (
        len(parts) == 2 and (not parts[1].startswith("layername=") or not parts[1][10:])
    ):
        raise VerificationFailure("invalid selected layer")
    return parts[0], parts[1][10:] if len(parts) == 2 else None


def safe_path(raw, root):
    filename, selection = selected_path(raw)
    path = Path(filename)
    if ".." in path.parts:
        raise VerificationFailure("path traversal")
    if not path.is_absolute():
        path = ROOT / path
    path = Path(os.path.abspath(path))
    root = Path(os.path.abspath(root))
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise VerificationFailure("path outside controlled directory") from exc
    if path.resolve() != path:
        raise VerificationFailure("symlink in controlled path")
    current = root
    if current.is_symlink():
        raise VerificationFailure("symlink in controlled path")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise VerificationFailure("symlink in controlled path")
    return path, selection


def check_geopackage(path, *, table):
    """Prove basic format errors using read-only SQLite, not GDAL error text."""
    import sqlite3

    no_sidecars(path)
    with Path(path).open("rb") as stream:
        if stream.read(16) != b"SQLite format 3\x00":
            raise VerificationFailure(
                "GeoPackage is missing the SQLite format 3 header"
            )
    # GeoPackage 1.0/1.1 used GP10/GP11; newer versions use GPKG.
    # https://www.geopackage.org/spec/ (requirements 1-2, note K4)
    allowed_ids = {
        int.from_bytes(value, "big") for value in (b"GPKG", b"GP10", b"GP11")
    }
    required = {
        "gpkg_contents": {"table_name", "data_type", "srs_id"},
        "gpkg_spatial_ref_sys": {
            "srs_name",
            "srs_id",
            "organization",
            "organization_coordsys_id",
            "definition",
        },
    }
    if not table:
        required["gpkg_geometry_columns"] = {
            "table_name",
            "column_name",
            "geometry_type_name",
            "srs_id",
            "z",
            "m",
        }
    try:
        with sqlite3.connect(
            Path(path).resolve().as_uri() + "?mode=ro&immutable=1", uri=True
        ) as db:
            application_id = db.execute("PRAGMA application_id").fetchone()[0]
            if application_id not in allowed_ids:
                raise VerificationFailure(
                    "SQLite file is not identified as a GeoPackage"
                )
            check = db.execute("PRAGMA quick_check(1)").fetchone()
            if check != ("ok",):
                raise VerificationFailure("SQLite reports a corrupt GeoPackage")
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if not set(required) <= tables:
                raise VerificationFailure("GeoPackage is missing required core tables")
            for name, columns in required.items():
                actual = {row[1] for row in db.execute(f"PRAGMA table_info({name})")}
                if not columns <= actual:
                    raise VerificationFailure(
                        f"GeoPackage has an incomplete core table: {name}"
                    )
    except sqlite3.DatabaseError as exc:
        # Only SQLite's explicit NOTADB/CORRUPT codes prove malformed bytes.
        # IOERR, CANTOPEN, permissions, resource failures and unknown errors stay
        # unavailable and cannot be turned into a false-success finding.
        code = getattr(exc, "sqlite_errorcode", None)
        if isinstance(code, int) and code & 0xFF in (
            sqlite3.SQLITE_NOTADB,
            sqlite3.SQLITE_CORRUPT,
        ):
            raise VerificationFailure(
                "SQLite reports invalid or corrupt database bytes"
            ) from exc
        raise
    finally:
        no_sidecars(path)


def read_layer(path, selection=None, table=False):
    from osgeo import gdal, ogr

    no_sidecars(path)
    check_geopackage(path, table=table)
    ds = gdal.OpenEx(
        str(path), gdal.OF_VECTOR | gdal.OF_READONLY, allowed_drivers=["GPKG"]
    )
    if ds is None:
        raise RuntimeError("GDAL could not open the GeoPackage")
    if ds.GetLayerCount() != 1:
        raise VerificationFailure("expected one readable GeoPackage layer")
    layer = ds.GetLayerByIndex(0)
    if selection and layer.GetName() != selection:
        raise VerificationFailure("selected layer mismatch")
    srs = layer.GetSpatialRef()
    raw_geometry_type = layer.GetGeomType()
    geometry_type = ogr.GT_Flatten(raw_geometry_type)
    if table:
        if geometry_type != ogr.wkbNone or srs is not None:
            raise VerificationFailure("statistics result is not a nonspatial table")
    elif geometry_type == ogr.wkbNone or srs is None:
        raise VerificationFailure("spatial layer lacks geometry or CRS")
    elif ogr.GT_HasZ(raw_geometry_type) or ogr.GT_HasM(raw_geometry_type):
        raise VerificationFailure("spatial layer must be 2D, including an empty result")
    schema = layer.GetLayerDefn()
    names = [schema.GetFieldDefn(i).GetName() for i in range(schema.GetFieldCount())]
    field_types = {
        schema.GetFieldDefn(i).GetName(): schema.GetFieldDefn(i).GetFieldTypeName(
            schema.GetFieldDefn(i).GetType()
        )
        for i in range(schema.GetFieldCount())
    }
    rows = []
    for feature in layer:
        geometry = feature.GetGeometryRef()
        rows.append(
            (feature.items(), geometry.Clone() if geometry is not None else None)
        )
    result = {
        "fields": names,
        "field_types": field_types,
        "rows": rows,
        "name": layer.GetName(),
        "srs": srs.Clone() if srs else None,
        "geometry_type": geometry_type,
    }
    feature = layer = ds = None
    no_sidecars(path)
    return result


def check_crs(layer, code):
    from osgeo import osr

    actual = layer["srs"]
    if actual is None:
        raise VerificationFailure("missing spatial CRS")
    expected = osr.SpatialReference()
    expected.SetFromUserInput(code)
    actual.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    expected.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    if not actual.IsSame(expected):
        raise VerificationFailure(f"wrong CRS, expected {code}")
    if code != "EPSG:4326" and (
        not actual.IsProjected() or abs(actual.GetLinearUnits() - 1) > 1e-10
    ):
        raise VerificationFailure("projected output must have metre units")


def close(a, b, tolerance=1e-7):
    return (
        isinstance(a, (int, float))
        and not isinstance(a, bool)
        and math.isfinite(a)
        and abs(a - b) <= tolerance
    )


def check_fields(layer, schema):
    for field in schema:
        name, kind = field["name"], field["type"]
        allowed = {
            "Integer": ("Integer", "Integer64"),
            "Real": ("Real",),
            "String": ("String",),
        }[kind]
        if layer["field_types"].get(name) not in allowed:
            raise VerificationFailure(f"field missing or wrong type: {name}")


def geom_equal(actual, expected_json):
    from osgeo import ogr

    if actual is None or actual.IsEmpty() or not actual.IsValid():
        raise VerificationFailure("missing, empty or invalid geometry")
    if ogr.GT_HasZ(actual.GetGeometryType()) or ogr.GT_HasM(actual.GetGeometryType()):
        raise VerificationFailure("geometry must be 2D")
    expected = ogr.CreateGeometryFromJson(json.dumps(expected_json))
    if expected is None:
        raise ValueError("invalid frozen geometry")
    if ogr.GT_Flatten(expected.GetGeometryType()) == ogr.wkbPoint:
        if ogr.GT_Flatten(actual.GetGeometryType()) != ogr.wkbPoint:
            raise VerificationFailure("expected point geometry")
        if any(
            not close(a, b, 1e-9)
            for a, b in zip(actual.GetPoint_2D(), expected.GetPoint_2D())
        ):
            raise VerificationFailure("point coordinate mismatch")
        return
    if ogr.GT_Flatten(actual.GetGeometryType()) not in (
        ogr.wkbPolygon,
        ogr.wkbMultiPolygon,
    ):
        raise VerificationFailure("expected polygonal geometry")
    difference = actual.SymmetricDifference(expected)
    if (
        difference is None
        or difference.GetArea() > 1e-7
        or abs(actual.GetArea() - expected.GetArea()) > 1e-7
    ):
        raise VerificationFailure("polygon coverage differs from frozen vertices")


def attributes(actual, expected):
    if any(
        key not in actual or actual[key] != value for key, value in expected.items()
    ):
        raise VerificationFailure("source attributes differ")


def check_input(layer, data):
    check_crs(layer, data["crs"])
    check_fields(layer, data["fields"])
    if len(layer["rows"]) != len(data["features"]):
        raise VerificationFailure("input feature count mismatch")
    key = data["fields"][0]["name"]
    expected = {feature["properties"][key]: feature for feature in data["features"]}
    if len(expected) != len(data["features"]):
        raise ValueError("input identity is not unique")
    seen = set()
    for props, geometry in layer["rows"]:
        identity = props.get(key)
        if identity in seen or identity not in expected:
            raise VerificationFailure(
                "unexpected or duplicate input attribute identity"
            )
        seen.add(identity)
        attributes(props, expected[identity]["properties"])
        geom_equal(geometry, expected[identity]["geometry"])


def point_results(layer, point_oracle):
    from osgeo import ogr

    check_crs(layer, point_oracle["target_crs"])
    expected = {f["properties"]["pid"]: f for f in point_oracle["features"]}
    if len(layer["rows"]) != len(expected):
        raise VerificationFailure("projected point count mismatch")
    seen = set()
    for props, geometry in layer["rows"]:
        pid = props.get("pid")
        if pid not in expected or pid in seen:
            raise VerificationFailure("projected point ID mismatch")
        seen.add(pid)
        attributes(props, expected[pid]["properties"])
        if (
            geometry is None
            or ogr.GT_Flatten(geometry.GetGeometryType()) != ogr.wkbPoint
            or ogr.GT_HasZ(geometry.GetGeometryType())
            or ogr.GT_HasM(geometry.GetGeometryType())
        ):
            raise VerificationFailure("projected result must contain 2D points")
        if any(
            not close(x, y, 0.02)
            for x, y in zip(
                geometry.GetPoint_2D(), expected[pid]["projected_coordinates_m"]
            )
        ):
            raise VerificationFailure(
                "projected point coordinates differ from Snyder oracle"
            )


def buffer_results(layer, point_oracle):
    from osgeo import ogr

    check_crs(layer, point_oracle["target_crs"])
    expected = {f["properties"]["pid"]: f for f in point_oracle["features"]}
    model = point_oracle["buffer"]
    if len(layer["rows"]) != len(expected):
        raise VerificationFailure("buffer feature count mismatch")
    seen = set()
    for props, geometry in layer["rows"]:
        pid = props.get("pid")
        if pid not in expected or pid in seen:
            raise VerificationFailure("buffer ID mismatch")
        seen.add(pid)
        attributes(props, expected[pid]["properties"])
        if geometry is None or geometry.IsEmpty() or not geometry.IsValid():
            raise VerificationFailure("invalid buffer geometry")
        kind = ogr.GT_Flatten(geometry.GetGeometryType())
        if ogr.GT_HasZ(geometry.GetGeometryType()) or ogr.GT_HasM(
            geometry.GetGeometryType()
        ):
            raise VerificationFailure("buffer must be 2D")
        if kind == ogr.wkbMultiPolygon:
            if geometry.GetGeometryCount() != 1:
                raise VerificationFailure("buffer must have one polygon part")
            geometry = geometry.GetGeometryRef(0)
        elif kind != ogr.wkbPolygon:
            raise VerificationFailure("buffer must be polygonal")
        if geometry.GetGeometryCount() != 1:
            raise VerificationFailure("buffer must contain one ring and no holes")
        ring = geometry.GetGeometryRef(0)
        if ring.GetPointCount() != 33:
            raise VerificationFailure("buffer must have 32 sides")
        coords = [ring.GetPoint_2D(i) for i in range(33)]
        cx, cy = expected[pid]["projected_coordinates_m"]
        if coords[0] != coords[-1] or any(
            not close(math.hypot(x - cx, y - cy), model["radius_m"], 0.03)
            for x, y in coords[:-1]
        ):
            raise VerificationFailure("buffer center or radius mismatch")
        vertices = expected[pid]["buffer_vertices_m"]
        if not any(
            all(
                math.hypot(
                    coords[i][0] - vertices[(offset + direction * i) % 32][0],
                    coords[i][1] - vertices[(offset + direction * i) % 32][1],
                )
                <= 0.03
                for i in range(32)
            )
            for direction in (1, -1)
            for offset in range(32)
        ):
            raise VerificationFailure(
                "buffer vertex orientation or coordinates mismatch"
            )
        if any(
            not close(math.hypot(a[0] - b[0], a[1] - b[1]), model["edge_m"], 0.001)
            for a, b in zip(coords, coords[1:])
        ):
            raise VerificationFailure(
                "buffer polygon edges are not the expected regular 32-gon"
            )
        if not close(geometry.GetArea(), model["area_m2"], 0.05) or not close(
            ring.Length(), model["perimeter_m"], 0.005
        ):
            raise VerificationFailure("buffer area or perimeter mismatch")


def polygon_results(layer, expected_rows, *, grouped=False):
    check_crs(layer, "EPSG:32649")
    if len(layer["rows"]) != len(expected_rows):
        raise VerificationFailure("polygon result feature count mismatch")
    key = "district" if grouped else "parcel_id"
    expected = {item["key"]: item for item in expected_rows}
    seen = set()
    for props, geometry in layer["rows"]:
        identity = props.get(key)
        if identity in seen or identity not in expected:
            raise VerificationFailure(
                "polygon result has unexpected or duplicate identity"
            )
        seen.add(identity)
        item = expected[identity]
        if not any(
            all(props.get(k) == v for k, v in allowed.items())
            for allowed in item["properties"]
        ):
            raise VerificationFailure(
                "polygon attributes do not come from one valid source feature"
            )
        geom_equal(geometry, item["geometry"])
        if not close(geometry.GetArea(), item["area_m2"]):
            raise VerificationFailure("polygon area differs from shoelace oracle")


def statistics_results(layer, expected):
    if len(layer["rows"]) != 1:
        raise VerificationFailure("statistics must contain one nonspatial row")
    props, geom = layer["rows"][0]
    if geom is not None:
        raise VerificationFailure("statistics table has geometry")
    for key in ("count", "filled", "empty"):
        if layer["field_types"].get(key) not in ("Integer", "Integer64"):
            raise VerificationFailure("invalid statistics count type")
    for key in ("sum", "min", "max"):
        if layer["field_types"].get(key) != "Real":
            raise VerificationFailure("invalid statistics numeric type")
    values = {
        "count": expected["valid"],
        "filled": expected["valid"],
        "empty": expected["null"],
        "sum": expected["sum"],
        "min": expected["min"],
        "max": expected["max"],
    }
    if any(not close(props.get(key), value, 1e-9) for key, value in values.items()):
        raise VerificationFailure(
            "statistics differ from frozen count, NULL and numeric oracle"
        )
    if props["count"] + props["empty"] != expected["count"]:
        raise VerificationFailure("statistics total count mismatch")


def check_evidence_format(evidence, scene):
    """Reject incomplete reports as unavailable, before deciding any mismatch."""
    if not isinstance(evidence, dict):
        raise ValueError("evidence is not an object")
    execution, uploads = evidence.get("execution"), evidence.get("uploads")
    if not isinstance(execution, dict) or not isinstance(uploads, dict):
        raise ValueError("execution and uploads must be objects")
    if set(uploads) != set(scene["inputs"]):
        raise ValueError("upload evidence is missing or has unexpected datasets")
    for item in uploads.values():
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("file_id"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", item["file_id"])
            or not isinstance(item.get("filename"), str)
            or not isinstance(item.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
            or isinstance(item.get("size"), bool)
            or not isinstance(item.get("size"), int)
        ):
            raise ValueError("upload evidence lacks its identity, checksum or size")
    plan, attempts = execution.get("plan"), execution.get("attempts")
    if not isinstance(plan, dict) or not isinstance(attempts, list) or not attempts:
        raise ValueError("execution plan or attempts are absent")
    inputs = plan.get("required_inputs")
    if not isinstance(inputs, list) or any(
        not isinstance(item, dict)
        or any(
            not isinstance(item.get(key), str) or not item[key]
            for key in ("file_id", "path", "sha256")
        )
        for item in inputs
    ):
        raise ValueError("execution input references are incomplete")
    for attempt in attempts:
        if (
            not isinstance(attempt, dict)
            or not isinstance(attempt.get("tool_name"), str)
            or not isinstance(attempt.get("tool_arguments"), dict)
            or not isinstance(attempt.get("status"), str)
            or not isinstance(attempt.get("observation"), dict)
            or not isinstance(attempt["observation"].get("status"), str)
            or not isinstance(attempt.get("artifacts"), list)
        ):
            raise ValueError("execution attempt is incomplete")
        for artifact in attempt["artifacts"]:
            if not isinstance(artifact, dict):
                raise ValueError("artifact evidence is not an object")
            if artifact.get("kind") == "path" and (
                not isinstance(artifact.get("path"), str)
                or not isinstance(artifact.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"])
                or isinstance(artifact.get("size_bytes"), bool)
                or not isinstance(artifact.get("size_bytes"), int)
            ):
                raise ValueError("file artifact evidence lacks path, checksum or size")
        if attempt["tool_name"] == "execute_processing":
            args = attempt["tool_arguments"]
            if not isinstance(args.get("algorithm"), str) or not isinstance(
                args.get("parameters"), dict
            ):
                raise ValueError("processing attempt arguments are incomplete")
            params = args["parameters"]
            source = (
                "INPUT_LAYER"
                if args["algorithm"] == "native:basicstatisticsforfields"
                else "INPUT"
            )
            if not isinstance(params.get(source), str) or not isinstance(
                params.get("OUTPUT"), str
            ):
                raise ValueError("processing attempt input or output is missing")
        elif attempt["tool_name"] == "add_layer":
            arguments = attempt["tool_arguments"]
            if any(
                not isinstance(arguments.get(key), str) or not arguments[key]
                for key in ("path", "name", "layer_type")
            ):
                raise ValueError("loaded-layer call arguments are incomplete")
            data = attempt["observation"].get("data")
            if not isinstance(data, dict) or any(
                data.get(key) is None
                for key in (
                    "id",
                    "name",
                    "source",
                    "crs",
                    "geometry",
                    "feature_count",
                    "fields",
                )
            ):
                raise ValueError("loaded-layer echo is incomplete")
            if (
                any(
                    not isinstance(data[key], str) or not data[key]
                    for key in ("id", "name", "source", "crs", "geometry")
                )
                or isinstance(data["feature_count"], bool)
                or not isinstance(data["feature_count"], int)
                or not isinstance(data["fields"], list)
            ):
                raise ValueError("loaded-layer echo has invalid field types")


def verify_result(manifest_path, scenario_id, evidence, workspace):
    os.environ["GDAL_PAM_ENABLED"] = "NO"
    report = {"status": "unavailable", "empty": False, "checks": [], "errors": []}
    checks = report["checks"]
    observed_hashes = []
    try:
        from osgeo import gdal, ogr

        gdal.UseExceptions()
        ogr.UseExceptions()
        definition, oracle = checked()
        manifest_path = Path(manifest_path)
        manifest = json.loads(manifest_path.read_bytes())
        required_hashes = {
            "definition_sha256": digest(DEFINITION.read_bytes()),
            "oracle_sha256": digest(ORACLE.read_bytes()),
            "generator_sha256": digest(GENERATOR.read_bytes()),
            "snyder_helper_sha256": digest(SNYDER_HELPER.read_bytes()),
        }
        if any(manifest.get(k) != v for k, v in required_hashes.items()):
            raise ValueError("manifest provenance hash mismatch")
        if set(manifest.get("datasets", {})) != set(definition["datasets"]):
            raise ValueError("manifest dataset inventory mismatch")
        scene = next(
            (s for s in definition["scenarios"] if s["id"] == scenario_id), None
        )
        if scene is None:
            raise ValueError("unknown holdout scenario; dev references are separate")
        check_evidence_format(evidence, scene)
        execution, uploads = evidence["execution"], evidence["uploads"]
        task_id, revision = execution["task_id"], execution["plan_revision"]
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", task_id):
            raise ValueError("invalid business task ID")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("invalid plan revision")
        plan = execution["plan"]
        if (
            execution.get("status") != "success"
            or plan.get("task_id") != task_id
            or plan.get("revision") != revision
        ):
            raise ValueError(
                "business execution is not successful for this task and revision"
            )
        checks.append("business task and revision")
        workspace = Path(os.path.abspath(workspace))
        upload_root = workspace / "shared/uploads"
        output_root = workspace / "shared/outputs" / task_id / f"r{revision}"
        if set(uploads) != set(scene["inputs"]):
            raise ValueError("uploaded datasets do not match scenario inputs")
        if len(plan.get("required_inputs", [])) != len(scene["inputs"]):
            raise ValueError("business plan has unexpected input references")
        input_paths = {}
        for key in scene["inputs"]:
            spec, metadata, upload = (
                definition["datasets"][key],
                manifest["datasets"][key],
                uploads[key],
            )
            for field in ("filename", "layer", "crs", "geometry_type", "fields"):
                if metadata.get(field) != spec[field]:
                    raise ValueError("manifest schema differs from definition")
            if metadata.get("feature_count") != len(spec["features"]):
                raise ValueError("manifest feature count differs from definition")
            path = manifest_path.parent / metadata["filename"]
            try:
                source_hash, source_size = file_hash(path)
            except VerificationFailure as exc:
                raise ValueError(
                    f"materialized source cannot be verified: {exc}"
                ) from exc
            if source_hash != metadata.get("sha256"):
                raise ValueError("materialized source checksum mismatch")
            observed_hashes.append((path, source_hash, source_size))
            file_id = upload.get("file_id")
            if (
                not isinstance(file_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]+", file_id)
                or upload.get("sha256") != metadata["sha256"]
                or upload.get("filename") != metadata["filename"]
            ):
                raise VerificationFailure(
                    "upload record does not match fixed materialized source"
                )
            matching = [
                item
                for item in plan.get("required_inputs", [])
                if item.get("file_id") == file_id
            ]
            if len(matching) != 1 or matching[0].get("sha256") != metadata["sha256"]:
                raise VerificationFailure(
                    "business plan does not bind this uploaded source and checksum"
                )
            raw = matching[0].get("path")
            input_path, selection = safe_path(raw, upload_root)
            if input_path.name != file_id + ".gpkg" or selection != metadata["layer"]:
                raise VerificationFailure(
                    "business input is not the registered upload and layer"
                )
            sha, size = file_hash(input_path)
            if sha != metadata["sha256"] or size != upload.get("size"):
                raise VerificationFailure("uploaded source differs from its record")
            input_paths[key] = input_path
            observed_hashes.append((input_path, sha, size))
            check_input(read_layer(input_path, selection), spec)
        checks.append("fixed inputs, hashes, fields, attributes, CRS and geometry")
        attempts = execution.get("attempts")
        if not isinstance(attempts, list) or any(
            a.get("status") != "success"
            or a.get("observation", {}).get("status") != "success"
            for a in attempts
        ):
            raise ValueError("a business attempt is absent or unsuccessful")
        if any(
            a.get("tool_name") not in ("execute_processing", "add_layer")
            for a in attempts
        ):
            raise VerificationFailure("unexpected business tool call")
        processing = [a for a in attempts if a.get("tool_name") == "execute_processing"]
        loads = [a for a in attempts if a.get("tool_name") == "add_layer"]
        expected = scene["expected"]
        if [
            a.get("tool_arguments", {}).get("algorithm") for a in processing
        ] != expected["algorithms"]:
            raise VerificationFailure("actual processing algorithms or order differ")
        if len(loads) != len(expected["loads"]) or len(attempts) != len(
            processing
        ) + len(loads):
            raise VerificationFailure("actual load count or tool count differs")
        if loads and attempts[-len(loads) :] != loads:
            raise VerificationFailure("loads must follow all processing")
        outputs = []
        for index, attempt in enumerate(processing):
            args = attempt["tool_arguments"]
            params = args.get("parameters", {})
            candidates = [
                a for a in attempt.get("artifacts", []) if a.get("kind") == "path"
            ]
            if len(candidates) != 1:
                raise ValueError(
                    "processing attempt has no unique recorded file artifact"
                )
            artifact = candidates[0]
            path, selection = safe_path(artifact.get("path"), output_root)
            if selection is not None or len(path.relative_to(output_root).parts) != 2:
                raise VerificationFailure("artifact is not a task-owned output file")
            if (
                path.name != expected["output_filenames"][index]
                or path.suffix.lower() != ".gpkg"
            ):
                raise VerificationFailure("output name or format differs from request")
            output_param, output_layer = safe_path(params.get("OUTPUT"), output_root)
            if output_layer or output_param != path:
                raise VerificationFailure("actual OUTPUT and artifact path differ")
            sha, size = file_hash(path)
            if sha != artifact.get("sha256") or size != artifact.get("size_bytes"):
                raise VerificationFailure(
                    "artifact checksum or size differs from business record"
                )
            if path in outputs:
                raise VerificationFailure("output file reused")
            outputs.append(path)
            observed_hashes.append((path, sha, size))
        checks.append("actual ordered calls, task-owned outputs and recorded hashes")

        # Establish the chain from actual call arguments, rather than inferring it
        # from tool success or a model-written description.
        primary = input_paths[scene["inputs"][0]]
        for index, attempt in enumerate(processing):
            algo = expected["algorithms"][index]
            params = attempt["tool_arguments"]["parameters"]
            raw_input = (
                params.get("INPUT_LAYER")
                if algo == "native:basicstatisticsforfields"
                else params.get("INPUT")
            )
            input_filename, input_layer = selected_path(raw_input)
            actual_input = Path(input_filename)
            if not actual_input.is_absolute():
                actual_input = workspace / actual_input
            actual_input = Path(os.path.abspath(actual_input))
            wanted_input = outputs[index - 1] if index else primary
            if actual_input != wanted_input:
                raise VerificationFailure(
                    "processing input is not its expected source or prior artifact"
                )
            if input_layer and read_layer(wanted_input)["name"] != input_layer:
                raise VerificationFailure("processing input selects a different layer")
            if algo == "native:clip":
                overlay_filename, overlay_layer = selected_path(params.get("OVERLAY"))
                overlay = Path(overlay_filename)
                if not overlay.is_absolute():
                    overlay = workspace / overlay
                if Path(os.path.abspath(overlay)) != input_paths[scene["inputs"][1]]:
                    raise VerificationFailure("clip mask differs from selected upload")
                if overlay_layer and read_layer(overlay)["name"] != overlay_layer:
                    raise VerificationFailure("clip mask selects a different layer")
            elif algo == "native:reprojectlayer":
                if params.get("TARGET_CRS") != expected["target_crs"]:
                    raise VerificationFailure("target CRS parameter mismatch")
            elif algo == "native:buffer":
                if (
                    not close(params.get("DISTANCE"), expected["distance_m"], 0)
                    or params.get("SEGMENTS") != expected["segments"]
                    or params.get("DISSOLVE") is not False
                ):
                    raise VerificationFailure(
                        "buffer distance, segments or dissolve parameter mismatch"
                    )
            elif algo == "native:dissolve":
                if (
                    params.get("FIELD") != expected["group_fields"]
                    or params.get("SEPARATE_DISJOINT", False) is not False
                ):
                    raise VerificationFailure(
                        "dissolve grouping or multipart parameter mismatch"
                    )
            elif algo == "qgis:exportaddgeometrycolumns":
                if params.get("CALC_METHOD") != expected["calculation_method"]:
                    raise VerificationFailure("geometry calculation method differs")
            elif (
                algo == "native:basicstatisticsforfields"
                and params.get("FIELD_NAME") != expected["field"]
            ):
                raise VerificationFailure("statistics field mismatch")
        checks.append("actual input, processing parameters and output lineage")

        source_key = scene["inputs"][0]
        source_schema = definition["datasets"][source_key]["fields"]
        if scene["family"] in ("reproject", "buffer"):
            point_oracle = oracle["points"][source_key]
            projected = read_layer(outputs[0])
            check_fields(projected, source_schema)
            point_results(projected, point_oracle)
            if scene["family"] == "buffer":
                buffered = read_layer(outputs[1])
                check_fields(buffered, source_schema)
                buffer_results(buffered, point_oracle)
            checks.append(
                "Snyder coordinates and regular 32-sided buffer"
                if scene["family"] == "buffer"
                else "Snyder projected coordinates"
            )
        elif scene["id"] == "clip-1":
            actual = read_layer(outputs[0])
            check_fields(actual, source_schema)
            expected_rows = [
                {
                    "key": v["properties"]["parcel_id"],
                    "properties": [v["properties"]],
                    "geometry": v["geometry"],
                    "area_m2": v["area_m2"],
                }
                for v in oracle["clip"]["features"]
            ]
            polygon_results(actual, expected_rows)
            checks.append("frozen clip vertices, source attributes and shoelace areas")
        elif scene["id"] == "clip-2":
            actual = read_layer(outputs[0])
            check_fields(actual, source_schema)
            check_crs(actual, "EPSG:32649")
            if actual["rows"] or actual["geometry_type"] not in (
                ogr.wkbPolygon,
                ogr.wkbMultiPolygon,
            ):
                raise VerificationFailure(
                    "far clip is not a schema-preserving zero-row polygon layer"
                )
            checks.append("valid zero-row clip with original schema and CRS")
            report["empty"] = True
        elif scene["id"] == "dissolve-1":
            actual = read_layer(outputs[0])
            check_fields(actual, source_schema)
            features = definition["datasets"]["parcels"]["features"]
            expected_rows = [
                {
                    "key": key,
                    "properties": [
                        f["properties"]
                        for f in features
                        if f["properties"]["district"] == key
                    ],
                    "geometry": shape,
                    "area_m2": oracle["dissolve"]["group_area_m2"][key],
                }
                for key, shape in oracle["dissolve"]["geometries"].items()
            ]
            polygon_results(actual, expected_rows, grouped=True)
            checks.append("grouped frozen coverage and allowed source attributes")
        elif scene["id"] == "dissolve-2":
            actual = read_layer(outputs[0])
            check_fields(actual, source_schema)
            check_crs(actual, "EPSG:32649")
            if len(actual["rows"]) != 1:
                raise VerificationFailure("all dissolve must have one feature")
            props, geometry = actual["rows"][0]
            source_properties = [
                f["properties"] for f in definition["datasets"]["parcels"]["features"]
            ]
            if not any(
                all(props.get(k) == v for k, v in p.items()) for p in source_properties
            ):
                raise VerificationFailure(
                    "all dissolve attributes do not come from one input"
                )
            shapes = oracle["dissolve"]["geometries"]
            target = {
                "type": "MultiPolygon",
                "coordinates": [shapes[k]["coordinates"] for k in ("A", "B", "C")],
            }
            geom_equal(geometry, target)
            if (
                ogr.GT_Flatten(geometry.GetGeometryType()) != ogr.wkbMultiPolygon
                or geometry.GetGeometryCount() != 3
                or not close(geometry.GetArea(), 172)
            ):
                raise VerificationFailure(
                    "all dissolve must preserve three disjoint parts and total area"
                )
            checks.append(
                "three disjoint frozen components and allowed source attributes"
            )
        elif scene["id"] == "statistics-1":
            statistics_results(
                read_layer(outputs[0], table=True), oracle["statistics"]["score"]
            )
            checks.append("score count, valid, NULL, sum, min and max")
        elif scene["id"] == "statistics-2":
            actual = read_layer(outputs[0])
            check_fields(actual, source_schema)
            parcels = definition["datasets"]["parcels"]["features"]
            rows = [
                {
                    "key": f["properties"]["parcel_id"],
                    "properties": [f["properties"]],
                    "geometry": f["geometry"],
                    "area_m2": area,
                }
                for f, area in zip(parcels, oracle["parcels"]["areas_m2"])
            ]
            polygon_results(actual, rows)
            if actual["field_types"].get("area") != "Real":
                raise VerificationFailure("missing calculated area field")
            expected_area = {row["key"]: row["area_m2"] for row in rows}
            if any(
                not close(props.get("area"), expected_area[props["parcel_id"]])
                for props, _ in actual["rows"]
            ):
                raise VerificationFailure("individual calculated areas differ")
            statistics_results(
                read_layer(outputs[1], table=True), oracle["statistics"]["area"]
            )
            checks.append("source geometry, per-feature m2 and area statistics")
        elif scene["family"] != "load":
            raise ValueError("unsupported scenario")

        for attempt, load in zip(loads, expected["loads"]):
            data = attempt.get("observation", {}).get("data") or {}
            source = primary if load["source"] == "input" else outputs[-1]
            raw_source = data.get("source")
            actual_source, selected_layer = selected_path(raw_source)
            path = Path(actual_source)
            if not path.is_absolute():
                path = workspace / path
            arguments = attempt.get("tool_arguments", {})
            name = arguments.get("name")
            called_path, called_layer = selected_path(arguments.get("path"))
            called_path = Path(called_path)
            if not called_path.is_absolute():
                called_path = workspace / called_path
            if (
                Path(os.path.abspath(path)) != source
                or not data.get("id")
                or data.get("name") != load["name"]
                or name != load["name"]
                or Path(os.path.abspath(called_path)) != source
                or arguments.get("layer_type") != "vector"
            ):
                raise VerificationFailure("loaded layer source, ID, or name mismatch")
            source_layer = read_layer(source)
            if any(
                layer and source_layer["name"] != layer
                for layer in (selected_layer, called_layer)
            ):
                raise VerificationFailure(
                    "loaded layer selection differs from the source file"
                )
            if load["source"] == "input":
                count = len(definition["datasets"][source_key]["features"])
                crs = definition["datasets"][source_key]["crs"]
                geometry = definition["datasets"][source_key]["geometry_type"]
            else:
                count = len(oracle["points"][source_key]["features"])
                crs = oracle["points"][source_key]["target_crs"]
                geometry = "Polygon" if scene["family"] == "buffer" else "Point"
            reported_geometry = str(data.get("geometry", "")).replace(" ", "").lower()
            actual_geometry = (
                ogr.GeometryTypeToName(source_layer["geometry_type"])
                .replace(" ", "")
                .lower()
            )
            if (
                data.get("feature_count") != count
                or data.get("crs") != crs
                or reported_geometry != actual_geometry
                or geometry.lower() not in reported_geometry
                or not set(f["name"] for f in source_schema).issubset(
                    set(data.get("fields") or [])
                )
            ):
                raise VerificationFailure(
                    "loaded layer CRS, geometry, count, or fields mismatch"
                )
        if loads:
            checks.append(
                "actual loaded layer source, name, CRS, geometry, count and fields"
            )
        for path, sha, size in observed_hashes:
            if file_hash(path) != (sha, size):
                raise ValueError(
                    "an input or artifact changed during independent verification"
                )
        checks.append("unchanged input and output file checksums")
        report["status"] = "passed"
    except VerificationFailure as exc:
        report["status"] = "failed"
        report["empty"] = False
        report["errors"].append(f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        report["status"] = "unavailable"
        report["empty"] = False
        report["errors"].append(f"{type(exc).__name__}: {exc}")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--definition", type=Path, default=DEFINITION)
    validate.add_argument("--oracle", type=Path, default=ORACLE)
    generate = sub.add_parser("materialize")
    generate.add_argument("--output-dir", type=Path, required=True)
    verify = sub.add_parser("verify-result")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--scenario", required=True)
    verify.add_argument("--execution-json", type=Path, required=True)
    verify.add_argument("--output-json", type=Path, required=True)
    verify.add_argument(
        "--workspace-root",
        type=Path,
        default=ROOT,
        help="workspace containing shared/uploads and shared/outputs (default repository)",
    )
    args = parser.parse_args()
    try:
        if args.action == "validate":
            definition, _ = checked(args.definition, args.oracle)
            result = {
                "status": "passed",
                "definition_sha256": digest(args.definition.read_bytes()),
                "oracle_sha256": digest(args.oracle.read_bytes()),
                "special_cases": {
                    group: {
                        "defined": len(definition[group]),
                        "not_run": sum(
                            case["status"] == "not_run" for case in definition[group]
                        ),
                    }
                    for group in ("exceptions", "browser")
                },
            }
        elif args.action == "materialize":
            result = materialize(
                args.output_dir, [sys.executable, str(GENERATOR), *sys.argv[1:]]
            )
        else:
            try:
                evidence = json.loads(args.execution_json.read_bytes())
                result = verify_result(
                    args.manifest, args.scenario, evidence, args.workspace_root
                )
            except Exception as exc:
                result = {
                    "status": "unavailable",
                    "empty": False,
                    "checks": [],
                    "errors": [f"{type(exc).__name__}: {exc}"],
                }
            with args.output_json.open("x", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
                f.write("\n")
        print(json.dumps(result, ensure_ascii=False))
        return {"failed": 1, "unavailable": 2}.get(result.get("status"), 0)
    except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
        print(f"{parser.prog}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
