"""T05 rectangle inputs and independent arithmetic, without a GIS validator."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.build_t05_fixtures import (
    DEFINITION_PATH,
    ORACLE_PATH,
    ROOT,
    build_oracle,
    check_oracle,
    intersection,
    rectangle_union_area,
)

SCRIPT = ROOT / "scripts/build_t05_fixtures.py"
SYSTEM_PYTHON = "/usr/bin/python3"


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_definition_contains_the_fixed_source_masks_and_edge_cases():
    definition = load_json(DEFINITION_PATH)
    assert definition["coordinate_reference_system"] == "EPSG:32650"
    assert definition["geometry_type"] == "Polygon"
    assert definition["geometry_dimensions"] == 2
    assert definition["schemas"]["source"] == [
        {"name": "rid", "type": "Integer", "nullable": True},
        {"name": "grp", "type": "String", "nullable": True},
        {"name": "value", "type": "Real", "nullable": True},
    ]
    datasets = definition["datasets"]
    assert datasets["source"]["features"] == [
        {"bounds": [500000, 0, 500004, 4], "properties": {"rid": 1, "grp": "A", "value": 10}},
        {"bounds": [500002, 0, 500006, 4], "properties": {"rid": 2, "grp": "A", "value": None}},
        {"bounds": [500006, 0, 500008, 4], "properties": {"rid": 3, "grp": "B", "value": 5}},
    ]
    assert datasets["clip_mask"]["features"][0]["bounds"] == [500001, 1, 500007, 3]
    assert datasets["far_mask"]["features"][0]["bounds"] == [500020, 1, 500021, 3]
    assert datasets["empty_source"]["schema"] == "source"
    assert datasets["empty_source"]["features"] == []
    all_null = datasets["all_null_source"]["features"]
    assert len(all_null) == 4
    assert [f["properties"]["grp"] for f in all_null] == ["A", "A", "B", "C"]
    assert all(f["properties"]["value"] is None for f in all_null)
    collision = datasets["collision_source"]["features"]
    assert len(collision) == 3
    assert all(f["properties"]["area"] == f["properties"]["perimeter"] == 999 for f in collision)
    for original, changed in zip(datasets["source"]["features"], collision):
        assert changed["bounds"] == original["bounds"]
        assert {key: changed["properties"][key] for key in ("rid", "grp", "value")} == original["properties"]
    assert len({d["filename"] for d in datasets.values()}) == len(datasets)


def test_hand_checked_clip_and_union_numbers_are_fixed():
    oracle = load_json(ORACLE_PATH)
    assert oracle["source"]["feature_area_sum_m2"] == 16 + 16 + 8 == 40
    assert oracle["source"]["union_area_m2"] == 8 * 4 == 32
    assert oracle["clip"]["feature_count"] == 3
    assert [f["bounds"] for f in oracle["clip"]["features"]] == [
        [500001, 1, 500004, 3],
        [500002, 1, 500006, 3],
        [500006, 1, 500007, 3],
    ]
    assert [f["area_m2"] for f in oracle["clip"]["features"]] == [6, 8, 2]
    assert oracle["clip"]["feature_area_sum_m2"] == 16
    assert oracle["clip"]["union_area_m2"] == 12
    grouped = oracle["clip_dissolve_by_grp"]
    assert grouped["feature_count"] == 2
    assert grouped["groups"]["A"]["geometry"] == {
        "bounds": [500001, 1, 500006, 3], "area_m2": 10, "perimeter_m": 14,
    }
    assert grouped["groups"]["B"]["geometry"] == {
        "bounds": [500006, 1, 500007, 3], "area_m2": 2, "perimeter_m": 6,
    }
    assert grouped["groups"]["A"]["allowed_source_attributes"] == [
        {"rid": 1, "grp": "A", "value": 10},
        {"rid": 2, "grp": "A", "value": None},
    ]
    assert oracle["clip_dissolve_all"]["feature_count"] == 1
    assert oracle["clip_dissolve_all"]["geometry"] == {
        "bounds": [500001, 1, 500007, 3], "area_m2": 12, "perimeter_m": 16,
    }
    assert intersection([0, 0, 2, 2], [2, 0, 4, 2]) is None
    assert rectangle_union_area([[0, 0, 2, 2], [1, 1, 3, 3]]) == 7


def test_counts_nulls_and_provider_edge_cases_stay_distinct():
    oracle = load_json(ORACLE_PATH)
    assert oracle["source"]["value_statistics"] == {
        "row_count": 3, "valid_value_count": 2, "null_value_count": 1,
        "qgis_table_sum": 15, "business_sum": 15, "min": 5, "max": 10,
    }
    assert oracle["source"]["group_feature_counts"] == {"A": 2, "B": 1}
    assert oracle["source"]["group_valid_value_counts"] == {"A": 1, "B": 1}
    no_values = {
        "row_count": 0, "valid_value_count": 0, "null_value_count": 0,
        "qgis_table_sum": 0, "business_sum": None, "min": None, "max": None,
    }
    assert oracle["far_clip"]["feature_count"] == 0
    assert oracle["far_clip"]["empty_result_is_valid"] is True
    assert oracle["far_clip"]["value_statistics"] == no_values
    assert oracle["empty_source"]["value_statistics"] == no_values
    assert oracle["empty_source"]["group_feature_counts"] == {}
    assert oracle["empty_source"]["group_valid_value_counts"] == {}
    assert oracle["all_null_source"]["value_statistics"] == {
        **no_values, "row_count": 4, "null_value_count": 4,
    }
    assert oracle["all_null_source"]["group_feature_counts"] == {"A": 2, "B": 1, "C": 1}
    assert oracle["all_null_source"]["group_valid_value_counts"] == {}
    provider = oracle["provider_observations"]
    assert provider["empty_source_dissolve_grouped"] == {"feature_count": 0, "is_valid_empty": True}
    assert provider["empty_source_dissolve_all"]["feature_count"] == 1
    assert provider["empty_source_dissolve_all"]["expected_spatial_validation"] == "failed"
    assert provider["empty_source_dissolve_all"]["deliverable"] is False


def test_oracle_rejects_changed_input_and_needs_no_gis_import():
    definition = DEFINITION_PATH.read_bytes()
    oracle = ORACLE_PATH.read_bytes()
    assert check_oracle(definition, oracle) == build_oracle(definition)
    with pytest.raises(ValueError, match="input definition SHA-256 mismatch"):
        check_oracle(definition + b" ", oracle)
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--check-oracle"],
        text=True, capture_output=True, check=True,
    )
    assert "arithmetic oracle match" in completed.stdout


# This process is intentionally independent of the materializer and reads every
# generated file with system OGR. It checks coordinates and attributes rather
# than invoking Processing or using OGR to compute expected overlay results.
OGR_INSPECTOR = r'''
from osgeo import ogr, osr
import json, sys
ogr.UseExceptions()
result = {}
for path in sys.argv[1:]:
    ds = ogr.Open(path, 0)
    assert ds is not None and ds.GetLayerCount() == 1
    layer = ds.GetLayer(0)
    srs = layer.GetSpatialRef()
    expected = osr.SpatialReference()
    expected.ImportFromEPSG(32650)
    expected.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    fields = layer.GetLayerDefn()
    data = {"layer": layer.GetName(), "crs_matches": bool(srs and srs.IsSame(expected)),
            "geometry_type": ogr.GeometryTypeToName(layer.GetGeomType()),
            "fields": [{"name": fields.GetFieldDefn(i).GetName(),
                        "type": fields.GetFieldDefn(i).GetFieldTypeName(fields.GetFieldDefn(i).GetType()),
                        "nullable": bool(fields.GetFieldDefn(i).IsNullable())}
                       for i in range(fields.GetFieldCount())], "features": []}
    for feature in layer:
        geom = feature.GetGeometryRef()
        assert geom is not None
        ring = geom.GetGeometryRef(0)
        data["features"].append({"properties": {fields.GetFieldDefn(i).GetName(): feature.GetField(i)
                                                for i in range(fields.GetFieldCount())},
                                 "geometry_type": ogr.GeometryTypeToName(geom.GetGeometryType()),
                                 "dimension": geom.GetCoordinateDimension(),
                                 "has_z": bool(ogr.GT_HasZ(geom.GetGeometryType())),
                                 "has_m": bool(ogr.GT_HasM(geom.GetGeometryType())),
                                 "rings": geom.GetGeometryCount(),
                                 "coordinates": [list(ring.GetPoint_2D(i)) for i in range(ring.GetPointCount())]})
    result[path] = data
print(json.dumps(result))
'''


@pytest.mark.external
def test_gdal_materialization_readback_manifest_and_no_overwrite(tmp_path):
    available = subprocess.run(
        [SYSTEM_PYTHON, "-c", "from osgeo import gdal, ogr, osr; assert ogr.GetDriverByName('GPKG')"],
        text=True, capture_output=True,
    )
    if available.returncode:
        pytest.skip("system Python GDAL/OGR with GeoPackage driver unavailable: " + available.stderr.strip())
    before = digest(DEFINITION_PATH)
    output_dir = tmp_path / "new-inputs"
    command = [SYSTEM_PYTHON, str(SCRIPT), "--output-dir", str(output_dir)]
    completed = subprocess.run(command, text=True, capture_output=True, check=True)
    manifest = load_json(output_dir / "manifest.json")
    assert json.loads(completed.stdout) == manifest
    assert manifest["definition_sha256"] == before
    assert manifest["oracle_sha256"] == digest(ORACLE_PATH)
    assert manifest["generator_sha256"] == digest(SCRIPT)
    assert manifest["generation_command"] == command
    assert manifest["gdal_version"]
    definition = load_json(DEFINITION_PATH)
    paths = [output_dir / data["filename"] for data in definition["datasets"].values()]
    inspected = subprocess.run(
        [SYSTEM_PYTHON, "-c", OGR_INSPECTOR, *map(str, paths)],
        text=True, capture_output=True, check=True,
    )
    actual = json.loads(inspected.stdout)
    for name, data in definition["datasets"].items():
        path = output_dir / data["filename"]
        record = manifest["datasets"][name]
        observed = actual[str(path)]
        assert record["sha256"] == digest(path)
        assert record["feature_count"] == len(data["features"])
        assert record["fields"] == definition["schemas"][data["schema"]]
        assert observed["layer"] == data["layer"]
        assert observed["crs_matches"] is True
        assert observed["geometry_type"] == "Polygon"
        assert observed["fields"] == definition["schemas"][data["schema"]]
        assert len(observed["features"]) == len(data["features"])
        by_attributes = {json.dumps(f["properties"], sort_keys=True): f for f in observed["features"]}
        for feature in data["features"]:
            got = by_attributes[json.dumps(feature["properties"], sort_keys=True)]
            assert got["geometry_type"] == "Polygon"
            assert got["dimension"] == 2
            assert not got["has_z"] and not got["has_m"]
            assert got["rings"] == 1
            xmin, ymin, xmax, ymax = feature["bounds"]
            coordinates = got["coordinates"]
            assert len(coordinates) == 5 and coordinates[0] == coordinates[-1]
            assert {tuple(point) for point in coordinates} == {
                (xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax),
            }
            assert all((a[0] == b[0]) != (a[1] == b[1]) for a, b in zip(coordinates, coordinates[1:]))
        for suffix in ("-wal", "-shm", "-journal"):
            assert not Path(str(path) + suffix).exists()
    snapshot = {path.name: digest(path) for path in output_dir.iterdir()}
    again = subprocess.run(command, text=True, capture_output=True)
    assert again.returncode != 0
    assert {path.name: digest(path) for path in output_dir.iterdir()} == snapshot
    existing_empty_dir = tmp_path / "existing-empty-dir"
    existing_empty_dir.mkdir()
    refusal = subprocess.run([SYSTEM_PYTHON, str(SCRIPT), "--output-dir", str(existing_empty_dir)], capture_output=True)
    assert refusal.returncode != 0 and not list(existing_empty_dir.iterdir())
    existing_file = tmp_path / "existing-file"
    existing_file.write_text("do not overwrite", encoding="utf-8")
    refusal = subprocess.run([SYSTEM_PYTHON, str(SCRIPT), "--output-dir", str(existing_file)], capture_output=True)
    assert refusal.returncode != 0 and existing_file.read_text() == "do not overwrite"
    dangling_target = tmp_path / "must-not-be-created"
    symlink = tmp_path / "dangling-symlink"
    symlink.symlink_to(dangling_target)
    refusal = subprocess.run([SYSTEM_PYTHON, str(SCRIPT), "--output-dir", str(symlink)], capture_output=True)
    assert refusal.returncode != 0 and not dangling_target.exists()
    assert digest(DEFINITION_PATH) == before
