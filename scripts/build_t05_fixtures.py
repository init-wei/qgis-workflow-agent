#!/usr/bin/env python3
"""Check the fixed T05 rectangle oracle or materialize its inputs using GDAL.

The oracle uses only axis-aligned rectangle intervals and ordinary arithmetic.
It is specific to this small development sample, not a general GIS topology
validator. The separate materialization branch imports system GDAL/OGR and
never runs Processing or computes the oracle from a GIS operation.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFINITION_PATH = ROOT / "tests/fixtures/gis/t05_workflows.json"
ORACLE_PATH = ROOT / "tests/fixtures/gis/t05_workflow_oracle.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def area(bounds: list[float]) -> float:
    xmin, ymin, xmax, ymax = bounds
    if xmin >= xmax or ymin >= ymax:
        raise ValueError("rectangle must have positive width and height")
    return (xmax - xmin) * (ymax - ymin)


def perimeter(bounds: list[float]) -> float:
    area(bounds)
    xmin, ymin, xmax, ymax = bounds
    return 2 * ((xmax - xmin) + (ymax - ymin))


def intersection(left: list[float], right: list[float]) -> list[float] | None:
    area(left)
    area(right)
    bounds = [max(left[0], right[0]), max(left[1], right[1]),
              min(left[2], right[2]), min(left[3], right[3])]
    return bounds if bounds[0] < bounds[2] and bounds[1] < bounds[3] else None


def rectangle_union_area(rectangles: list[list[float]]) -> float:
    """Sum disjoint vertical slabs; only 2D axis-aligned rectangles apply."""
    for rectangle in rectangles:
        area(rectangle)
    x_coordinates = sorted({x for bounds in rectangles for x in (bounds[0], bounds[2])})
    total = 0
    for left, right in zip(x_coordinates, x_coordinates[1:]):
        intervals = sorted((b[1], b[3]) for b in rectangles if b[0] < right and b[2] > left)
        covered = 0
        if intervals:
            start, end = intervals[0]
            for next_start, next_end in intervals[1:]:
                if next_start > end:
                    covered += end - start
                    start, end = next_start, next_end
                else:
                    end = max(end, next_end)
            covered += end - start
        total += (right - left) * covered
    return total


def rectangle_union(rectangles: list[list[float]]) -> dict:
    """Describe a union only when it fills its rectangular envelope."""
    if not rectangles:
        raise ValueError("cannot form a nonempty rectangle from an empty union")
    bounds = [min(b[0] for b in rectangles), min(b[1] for b in rectangles),
              max(b[2] for b in rectangles), max(b[3] for b in rectangles)]
    union_area = rectangle_union_area(rectangles)
    if union_area != area(bounds):
        raise ValueError("this fixture union does not fill its rectangular envelope")
    return {"bounds": bounds, "area_m2": union_area, "perimeter_m": perimeter(bounds)}


def number_statistics(features: list[dict]) -> dict:
    values = [feature["properties"]["value"] for feature in features]
    valid = [value for value in values if value is not None]
    return {
        "row_count": len(values),
        "valid_value_count": len(valid),
        "null_value_count": len(values) - len(valid),
        "qgis_table_sum": sum(valid),
        "business_sum": sum(valid) if valid else None,
        "min": min(valid) if valid else None,
        "max": max(valid) if valid else None,
    }


def group_counts(features: list[dict], *, only_valid: bool = False) -> dict:
    counts = {}
    for feature in features:
        properties = feature["properties"]
        if only_valid and properties["value"] is None:
            continue
        group = properties["grp"]
        counts[group] = counts.get(group, 0) + 1
    return dict(sorted(counts.items()))


def build_oracle(definition_bytes: bytes) -> dict:
    """Build the expected values from this definition without loading GIS."""
    definition = json.loads(definition_bytes)
    datasets = definition["datasets"]
    source = datasets["source"]["features"]
    empty_source = datasets["empty_source"]["features"]
    clip_bounds = datasets["clip_mask"]["features"][0]["bounds"]
    far_bounds = datasets["far_mask"]["features"][0]["bounds"]
    all_null = datasets["all_null_source"]["features"]
    clipped = []
    for feature in source:
        bounds = intersection(feature["bounds"], clip_bounds)
        if bounds is not None:
            clipped.append({"bounds": bounds, "properties": feature["properties"]})
    far_clipped = [f for f in source if intersection(f["bounds"], far_bounds) is not None]
    if far_clipped:
        raise ValueError("far mask must be disjoint from this fixture's source")
    groups = sorted({f["properties"]["grp"] for f in clipped})
    by_group = {}
    for group in groups:
        members = [f for f in clipped if f["properties"]["grp"] == group]
        by_group[group] = {
            "geometry": rectangle_union([f["bounds"] for f in members]),
            "allowed_source_attributes": [f["properties"] for f in members],
        }
    all_bounds = [f["bounds"] for f in clipped]
    return {
        "oracle_version": 1,
        "definition_path": "tests/fixtures/gis/t05_workflows.json",
        "definition_sha256": sha256(definition_bytes),
        "purpose": "T05 development sample, not the T07 holdout set",
        "method": "Exact rectangle interval intersection and x-slab union; arithmetic only, no GIS/PROJ/GEOS or production validator",
        "scope": "These finite 2D axis-aligned rectangles only; not proof of arbitrary polygon topology",
        "crs": "EPSG:32650",
        "units": {"coordinates": "m", "area": "m2", "perimeter": "m"},
        "absolute_tolerances": {"coordinates_m": 1e-8, "area_m2": 1e-7, "perimeter_m": 1e-7},
        "source": {
            "feature_count": len(source),
            "feature_area_sum_m2": sum(area(f["bounds"]) for f in source),
            "union_area_m2": rectangle_union_area([f["bounds"] for f in source]),
            "value_statistics": number_statistics(source),
            "group_feature_counts": group_counts(source),
            "group_valid_value_counts": group_counts(source, only_valid=True),
        },
        "clip": {
            "feature_count": len(clipped),
            "features": [{"properties": f["properties"], "bounds": f["bounds"],
                          "area_m2": area(f["bounds"]), "perimeter_m": perimeter(f["bounds"])}
                         for f in clipped],
            "feature_area_sum_m2": sum(area(f["bounds"]) for f in clipped),
            "union_area_m2": rectangle_union_area(all_bounds),
            "value_statistics": number_statistics(clipped),
        },
        "clip_dissolve_by_grp": {
            "feature_count": len(groups),
            "groups": by_group,
            "attribute_rule": "Non-group attributes must come together from one member, not from a sum; do not depend on FID, row order or ring orientation.",
        },
        "clip_dissolve_all": {
            "feature_count": 1,
            "geometry": rectangle_union(all_bounds),
            "allowed_source_attributes": [f["properties"] for f in clipped],
            "geometry_rule": "Compare equivalent polygonal coverage, not FID, component order, ring orientation or Polygon versus equivalent MultiPolygon.",
        },
        "far_clip": {
            "feature_count": 0,
            "schema": definition["schemas"][datasets["source"]["schema"]],
            "value_statistics": number_statistics(far_clipped),
            "empty_result_is_valid": True,
        },
        "empty_source": {
            "value_statistics": number_statistics(empty_source),
            "group_feature_counts": group_counts(empty_source),
            "group_valid_value_counts": group_counts(empty_source, only_valid=True),
        },
        "all_null_source": {
            "value_statistics": number_statistics(all_null),
            "group_feature_counts": group_counts(all_null),
            "group_valid_value_counts": group_counts(all_null, only_valid=True),
            "numeric_category_note": "QGIS numeric-category results omit groups with no valid values; an empty result does not mean the input has no groups.",
        },
        "provider_observations": {
            "qgis_version": "3.40.15-Bratislava",
            "statistics": "Numeric categories omit groups with no non-NULL values. For zero valid values QGIS table SUM is 0 and MIN/MAX are NULL; scalar MIN/MAX may be NaN. A zero SUM does not imply an observed zero.",
            "empty_source_dissolve_grouped": {"feature_count": 0, "is_valid_empty": True},
            "empty_source_dissolve_all": {
                "feature_count": 1,
                "geometry": "empty placeholder",
                "source_attributes": "NULL (a provider-generated FID can be populated)",
                "expected_spatial_validation": "failed",
                "deliverable": False,
                "note": "Observed with SEPARATE_DISJOINT false and true; preserve the completed provider output for diagnosis, do not rewrite it into a zero-row success.",
            },
            "geometry_field_collision": "With existing area and perimeter fields, QGIS 3.40.15 retained the original 999 values and added area_2/perimeter_2 in the separate probe. Reject collisions or verify an explicit new-field mapping; never assume area names the computed value.",
        },
    }


def check_oracle(definition_bytes: bytes, oracle_bytes: bytes) -> dict:
    expected = json.loads(oracle_bytes)
    if sha256(definition_bytes) != expected["definition_sha256"]:
        raise ValueError("input definition SHA-256 mismatch")
    calculated = build_oracle(definition_bytes)
    if calculated != expected:
        raise ValueError("checked-in oracle differs from independent interval arithmetic")
    return calculated


def materialize(definition_bytes: bytes, output_dir: Path, command: list[str]) -> dict:
    # This is deliberately the only GIS-dependent branch; uv/offline oracle checks
    # do not need osgeo. GDAL is provided by the system Python installation.
    from osgeo import gdal, ogr, osr

    gdal.UseExceptions()
    ogr.UseExceptions()
    definition = json.loads(definition_bytes)
    oracle_bytes = ORACLE_PATH.read_bytes()
    generator_bytes = Path(__file__).read_bytes()
    check_oracle(definition_bytes, oracle_bytes)
    output_dir = Path(os.path.abspath(output_dir))
    # A new private directory is reserved atomically; existing directories, files
    # and symlinks are refused. On failure partial files are left for inspection.
    os.mkdir(output_dir, 0o700)
    driver = ogr.GetDriverByName("GPKG")
    if driver is None:
        raise RuntimeError("GDAL GeoPackage driver is unavailable")
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(32650)
    crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    field_types = {"Integer": ogr.OFTInteger, "String": ogr.OFTString, "Real": ogr.OFTReal}
    files = {}
    for dataset_name, data in definition["datasets"].items():
        filename = data["filename"]
        if Path(filename).name != filename:
            raise ValueError("output filename must be a single path component")
        path = output_dir / filename
        if os.path.lexists(path):
            raise FileExistsError(f"refusing to overwrite {path}")
        dataset = driver.CreateDataSource(str(path))
        if dataset is None:
            raise RuntimeError(f"could not create {path}")
        layer = dataset.CreateLayer(data["layer"], srs=crs, geom_type=ogr.wkbPolygon,
                                    options=["SPATIAL_INDEX=NO"])
        schema = definition["schemas"][data["schema"]]
        for field in schema:
            field_definition = ogr.FieldDefn(field["name"], field_types[field["type"]])
            field_definition.SetNullable(field["nullable"])
            layer.CreateField(field_definition)
        for item in data["features"]:
            xmin, ymin, xmax, ymax = item["bounds"]
            area(item["bounds"])
            ring = ogr.Geometry(ogr.wkbLinearRing)
            for x, y in [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin)]:
                ring.AddPoint_2D(x, y)
            polygon = ogr.Geometry(ogr.wkbPolygon)
            polygon.AddGeometry(ring)
            feature = ogr.Feature(layer.GetLayerDefn())
            for field in schema:
                name = field["name"]
                value = item["properties"][name]
                if value is None:
                    feature.SetFieldNull(feature.GetFieldIndex(name))
                else:
                    feature.SetField(name, value)
            feature.SetGeometry(polygon)
            layer.CreateFeature(feature)
            feature = None
            polygon = None
            ring = None
        layer = None
        dataset = None
        for suffix in ("-wal", "-shm", "-journal"):
            if os.path.lexists(str(path) + suffix):
                raise RuntimeError(f"SQLite transaction sidecar remains: {path}{suffix}")
        files[dataset_name] = {
            "filename": filename,
            "layer": data["layer"],
            "geometry_type": "2D Polygon",
            "crs": definition["coordinate_reference_system"],
            "fields": schema,
            "feature_count": len(data["features"]),
            "sha256": sha256(path.read_bytes()),
        }
    if DEFINITION_PATH.read_bytes() != definition_bytes:
        raise RuntimeError("input definition changed during materialization")
    if ORACLE_PATH.read_bytes() != oracle_bytes:
        raise RuntimeError("oracle changed during materialization")
    if Path(__file__).read_bytes() != generator_bytes:
        raise RuntimeError("generator changed during materialization")
    manifest = {
        "manifest_version": 1,
        "definition_path": "tests/fixtures/gis/t05_workflows.json",
        "definition_sha256": sha256(definition_bytes),
        "oracle_path": "tests/fixtures/gis/t05_workflow_oracle.json",
        "oracle_sha256": sha256(oracle_bytes),
        "generator_sha256": sha256(generator_bytes),
        "gdal_version": gdal.VersionInfo("RELEASE_NAME"),
        "generation_command": command,
        "byte_reproducibility": "GeoPackage contents can include driver-generated timestamps; each actual file SHA-256 is recorded here.",
        "datasets": files,
    }
    with (output_dir / "manifest.json").open("x", encoding="utf-8") as file:
        json.dump(manifest, file, indent=2, ensure_ascii=False)
        file.write("\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check-oracle", action="store_true", help="check the oracle using only stdlib arithmetic")
    action.add_argument("--print-oracle", action="store_true", help="print the arithmetic oracle as JSON")
    action.add_argument("--output-dir", type=Path, help="create inputs in a new directory using system GDAL")
    arguments = parser.parse_args()
    definition_bytes = DEFINITION_PATH.read_bytes()
    if arguments.print_oracle:
        print(json.dumps(build_oracle(definition_bytes), indent=2, ensure_ascii=False))
    elif arguments.check_oracle:
        check_oracle(definition_bytes, ORACLE_PATH.read_bytes())
        print("T05 input definition and arithmetic oracle match")
    else:
        command = [sys.executable, str(Path(__file__).resolve()), "--output-dir", str(arguments.output_dir)]
        manifest = materialize(definition_bytes, arguments.output_dir, command)
        print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
