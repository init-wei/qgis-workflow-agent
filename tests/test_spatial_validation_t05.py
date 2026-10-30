"""T05 through the real system GIS CLI, with independently calculated rectangle expectations."""

import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest

from agent.planning.plan_schema import InputSpec, OutputSpec, PlanStep, TaskPlan, ToolCallPlan
from agent.spatial_validation import preflight_spatial_plan, validate_spatial_step


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/validate_spatial.py"


def inspect(request):
    result = subprocess.run(["/usr/bin/python3", str(CLI)], input=json.dumps(request),
                            text=True, capture_output=True, check=False, timeout=40)
    report = json.loads(result.stdout)
    assert result.returncode == (1 if report["status"] == "failed" else 0), result.stderr
    return report


def step(algorithm, parameters, inputs, output, *, facts=None):
    request = {"mode": "validate", "call": {"tool_name": "execute_processing", "arguments": {
        "algorithm": algorithm, "parameters": {**parameters, "OUTPUT": str(output)}}}, "input_paths": inputs,
        "output_paths": {"result": str(output)}}
    if facts is not None:
        request["input_facts"] = facts
    return inspect(request)


def plan(inputs, operations):
    steps, calls, outputs = [], [], []
    for number, (algorithm, refs, output, params) in enumerate(operations, 1):
        steps.append({"step_id": number, "input_refs": refs, "output_refs": [output]})
        calls.append({"step_id": number, "tool_name": "execute_processing", "arguments": {
            "algorithm": algorithm, "parameters": params}})
        outputs.append({"output_id": output, "path": params["OUTPUT"]})
    return {"mode": "preflight", "plan": {"required_inputs": [
        {"input_id": ref, "path": str(path)} for ref, path in inputs.items()],
        "steps": steps, "tool_calls": calls, "expected_outputs": outputs}}


@pytest.fixture(scope="module")
def gis(tmp_path_factory):
    root = tmp_path_factory.mktemp("t05-real-gis")
    subprocess.run(["/usr/bin/python3", str(ROOT / "scripts/build_t05_fixtures.py"),
                    "--output-dir", str(root / "inputs")], check=True, capture_output=True, text=True)
    inputs = root / "inputs"
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs.glob("*.gpkg")}
    worker = r'''
import os, sys, json, shutil
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
root = Path(sys.argv[1]); inputs = root / 'inputs'; profile = root / 'profile'; profile.mkdir()
os.environ['QGIS_CUSTOM_CONFIG_PATH'] = str(profile)
from qgis.core import QgsApplication
app = QgsApplication([], False, str(profile)); app.initQgis()
sys.path.append(str(Path(QgsApplication.pkgDataPath()) / 'python/plugins'))
from processing.core.Processing import Processing
Processing.initialize()
import processing
src = str(inputs / 't05_source.gpkg'); empty = str(inputs / 't05_empty_source.gpkg')
null = str(inputs / 't05_all_null_source.gpkg'); mask = str(inputs / 't05_clip_mask.gpkg')
far = str(inputs / 't05_far_mask.gpkg')
def run(name, algorithm, **params):
    out = str(root / (name + '.gpkg')); params['OUTPUT'] = out
    processing.run(algorithm, params); return out
clip = run('clip', 'native:clip', INPUT=src, OVERLAY=mask)
run('far', 'native:clip', INPUT=src, OVERLAY=far)
run('dissolve', 'native:dissolve', INPUT=clip, FIELD=['grp'], SEPARATE_DISJOINT=False)
run('all', 'native:dissolve', INPUT=clip, FIELD=[], SEPARATE_DISJOINT=False)
run('empty_group', 'native:dissolve', INPUT=empty, FIELD=['grp'], SEPARATE_DISJOINT=False)
run('empty_all', 'native:dissolve', INPUT=empty, FIELD=[], SEPARATE_DISJOINT=False)
run('empty_group_separate', 'native:dissolve', INPUT=empty, FIELD=['grp'], SEPARATE_DISJOINT=True)
run('empty_all_separate', 'native:dissolve', INPUT=empty, FIELD=[], SEPARATE_DISJOINT=True)
area = run('area', 'qgis:exportaddgeometrycolumns', INPUT=clip, CALC_METHOD=0)
run('area_stats', 'native:basicstatisticsforfields', INPUT_LAYER=area, FIELD_NAME='area')
run('basic', 'native:basicstatisticsforfields', INPUT_LAYER=clip, FIELD_NAME='value')
run('basic_empty', 'native:basicstatisticsforfields', INPUT_LAYER=empty, FIELD_NAME='value')
run('basic_null', 'native:basicstatisticsforfields', INPUT_LAYER=null, FIELD_NAME='value')
run('category', 'qgis:statisticsbycategories', INPUT=clip, VALUES_FIELD_NAME='value', CATEGORIES_FIELD_NAME=['grp'])
run('count', 'qgis:statisticsbycategories', INPUT=clip, CATEGORIES_FIELD_NAME=['grp'])
run('category_null', 'qgis:statisticsbycategories', INPUT=null, VALUES_FIELD_NAME='value', CATEGORIES_FIELD_NAME=['grp'])
from osgeo import gdal, ogr
null_group = str(root / 'null_group.gpkg'); shutil.copyfile(src, null_group)
null_ds = gdal.OpenEx(null_group, gdal.OF_VECTOR | gdal.OF_UPDATE); null_layer = null_ds.GetLayer(0)
for feature in null_layer:
    if feature.GetField('rid') == 1:
        feature.SetFieldNull('grp'); null_layer.SetFeature(feature)
null_ds.Close(); del null_layer, null_ds, feature
run('null_group_category', 'qgis:statisticsbycategories', INPUT=null_group, VALUES_FIELD_NAME='value', CATEGORIES_FIELD_NAME=['grp'])
run('null_group_count', 'qgis:statisticsbycategories', INPUT=null_group, CATEGORIES_FIELD_NAME=['grp'])
bad_type = str(root / 'wrong_type.gpkg'); bad_ds = ogr.GetDriverByName('GPKG').CreateDataSource(bad_type)
bad_layer = bad_ds.CreateLayer('wrong_type', geom_type=ogr.wkbNone)
for name, typ in [('count', ogr.OFTString), ('empty', ogr.OFTInteger), ('filled', ogr.OFTInteger), ('sum', ogr.OFTReal), ('min', ogr.OFTReal), ('max', ogr.OFTReal)]:
    bad_layer.CreateField(ogr.FieldDefn(name, typ))
bad_ds.Close(); del bad_layer, bad_ds
large = str(root / 'large_integer.gpkg'); large_ds = ogr.GetDriverByName('GPKG').CreateDataSource(large)
from osgeo import osr
srs = osr.SpatialReference(); srs.ImportFromEPSG(32650)
large_layer = large_ds.CreateLayer('large_integer', srs=srs, geom_type=ogr.wkbPoint)
large_layer.CreateField(ogr.FieldDefn('value', ogr.OFTInteger64))
f = ogr.Feature(large_layer.GetLayerDefn()); f.SetField('value', 9007199254740993)
f.SetGeometry(ogr.CreateGeometryFromWkt('POINT (500000 2)')); large_layer.CreateFeature(f)
large_ds.Close(); del large_layer, large_ds, f, srs
run('large_stats', 'native:basicstatisticsforfields', INPUT_LAYER=large, FIELD_NAME='value')
crs = {'type':'name','properties':{'name':'EPSG:32650'}}
def vector(name, typ, coordinates):
    path = root / (name + '.geojson')
    features = [{'type':'Feature', 'properties':{'grp':'A', 'rid':i + 1},
                 'geometry':{'type':typ, 'coordinates':c}} for i, c in enumerate(coordinates)]
    path.write_text(json.dumps({'type':'FeatureCollection','crs':crs,'features':features}))
    return str(path)
tiny = vector('tiny', 'Polygon', [[[[500000,0],[500001,0],[500001,0.000000005],[500000,0.000000005],[500000,0]]]])
run('tiny_area', 'qgis:exportaddgeometrycolumns', INPUT=tiny, CALC_METHOD=0)
micro = vector('micro', 'Polygon', [[[[500000,0],[500001,0],[500001,0.00000000000005],[500000,0.00000000000005],[500000,0]]]])
run('micro_area', 'qgis:exportaddgeometrycolumns', INPUT=micro, CALC_METHOD=0)
microline = vector('microline', 'LineString', [[[500000.5,0],[500000.5,0.0000000005]]])
micro_mask = vector('micro_mask', 'Polygon', [[[[500000,-1],[500001,-1],[500001,1],[500000,1],[500000,-1]]]])
run('microline_clip', 'native:clip', INPUT=microline, OVERLAY=micro_mask)
touch = vector('touch', 'Polygon', [[[[500008,1],[500010,1],[500010,3],[500008,3],[500008,1]]]])
run('touch_clip', 'native:clip', INPUT=src, OVERLAY=touch)
points = vector('points', 'Point', [[500002,2], [500004,2], [500002,2]])
lines = vector('lines', 'LineString', [[[500000,2],[500004,2]], [[500002,2],[500006,2]], [[500010,2],[500012,2]]])
for name, path in [('points', points), ('lines', lines)]:
    run(name + '_clip', 'native:clip', INPUT=path, OVERLAY=mask)
    run(name + '_union', 'native:dissolve', INPUT=path, FIELD=['grp'], SEPARATE_DISJOINT=False)
    run(name + '_separate', 'native:dissolve', INPUT=path, FIELD=['grp'], SEPARATE_DISJOINT=True)
buffer = run('empty_buffer', 'native:buffer', INPUT=src, DISTANCE=-10, SEGMENTS=8, END_CAP_STYLE=0,
             JOIN_STYLE=0, MITER_LIMIT=2, DISSOLVE=False, SEPARATE_DISJOINT=False)
run('buffer_clip', 'native:clip', INPUT=buffer, OVERLAY=mask)
run('buffer_stats', 'native:basicstatisticsforfields', INPUT_LAYER=buffer, FIELD_NAME='value')
run('buffer_dissolve', 'native:dissolve', INPUT=buffer, FIELD=['grp'], SEPARATE_DISJOINT=False)
app.exitQgis()
'''
    env = dict(os.environ, DATABASE_URL="", PYTHON_DOTENV_DISABLED="1")
    proc = subprocess.run(["/usr/bin/python3", "-c", worker, str(root)], env=env,
                          text=True, capture_output=True, check=False, timeout=90)
    assert proc.returncode == 0, proc.stderr
    yield root
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs.glob("*.gpkg")}
    assert not [p for p in root.rglob("*") if p.name.endswith(("-wal", "-shm", "-journal"))]


def edit_vector(path, action, condition="True"):
    script = """from osgeo import gdal, ogr
import sys
d = gdal.OpenEx(sys.argv[1], gdal.OF_VECTOR | gdal.OF_UPDATE)
l = d.GetLayer(0)
for f in l:
    if CONDITION:
        ACTION
        l.SetFeature(f)
        break
d.Close()
""".replace("CONDITION", condition).replace("ACTION", action)
    subprocess.run(["/usr/bin/python3", "-c", script, str(path)], check=True, capture_output=True)


def vector_rows(path):
    script = """from osgeo import ogr
import json,sys
d = ogr.Open(sys.argv[1]); l = d.GetLayer(0); fields = [f.name for f in l.schema]
rows = []
for f in l:
    g = f.GetGeometryRef()
    rows.append({'attributes': {name: f.GetField(name) for name in fields}, 'area': g.GetArea() if g else None, 'perimeter': g.Boundary().Length() if g else None, 'bounds': g.GetEnvelope() if g else None})
print(json.dumps(rows))
d = None
"""
    result = subprocess.run(["/usr/bin/python3", "-c", script, str(path)], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_clip_and_dissolve_actual_files_and_geometric_counterexamples(gis, tmp_path):
    source = gis / "inputs/t05_source.gpkg"
    mask = gis / "inputs/t05_clip_mask.gpkg"
    clip = gis / "clip.gpkg"
    params = {"INPUT": str(source), "OVERLAY": str(mask), "OUTPUT": str(clip)}
    report = step("native:clip", params, {"src": str(source), "mask": str(mask)}, clip)
    assert report["status"] == "passed", report
    assert report["facts"]["output"]["feature_count"] == 3
    assert {row["attributes"]["rid"]: row["area"] for row in vector_rows(clip)} == {1: 6, 2: 8, 3: 2}
    assert any(c["check"] == "analytic_rectangle_intersection" for c in report["checks"])
    dissolved = gis / "dissolve.gpkg"
    params = {"INPUT": str(clip), "FIELD": ["grp"], "SEPARATE_DISJOINT": False, "OUTPUT": str(dissolved)}
    report = step("native:dissolve", params, {"src": str(clip)}, dissolved)
    assert report["status"] == "passed", report
    assert report["facts"]["output"]["feature_count"] == 2
    assert {row["attributes"]["grp"]: row["area"] for row in vector_rows(dissolved)} == {"A": 10, "B": 2}
    assert any(c["check"] == "analytic_rectangle_union" for c in report["checks"])
    all_groups = gis / "all.gpkg"
    all_params = {"INPUT": str(clip), "FIELD": [], "SEPARATE_DISJOINT": False, "OUTPUT": str(all_groups)}
    all_result = step("native:dissolve", all_params, {"src": str(clip)}, all_groups)
    assert all_result["status"] == "passed", all_result
    assert (vector_rows(all_groups)[0]["area"], vector_rows(all_groups)[0]["perimeter"]) == (12, 16)

    bad = tmp_path / "wrong.gpkg"
    shutil.copyfile(dissolved, bad)
    edit_vector(bad, "f.SetField('grp', 'X')", "f.GetField('grp') == 'A'")
    before = hashlib.sha256(bad.read_bytes()).hexdigest()
    bad_result = step("native:dissolve", params, {"src": str(clip)}, bad)
    assert bad_result["status"] == "failed"
    assert hashlib.sha256(bad.read_bytes()).hexdigest() == before


def test_preflight_follows_fields_and_units_and_rejects_wrong_field(gis, tmp_path):
    source, mask = gis / "inputs/t05_source.gpkg", gis / "inputs/t05_clip_mask.gpkg"
    clip, area, result = (tmp_path / p for p in ("clip.gpkg", "area.gpkg", "stats.gpkg"))
    operations = [
        ("native:clip", ["source", "mask"], "clip", {"INPUT": str(source), "OVERLAY": str(mask), "OUTPUT": str(clip)}),
        ("qgis:exportaddgeometrycolumns", ["clip"], "area", {"INPUT": str(clip), "CALC_METHOD": 0, "OUTPUT": str(area)}),
        ("native:basicstatisticsforfields", ["area"], "stats", {"INPUT_LAYER": str(area), "FIELD_NAME": "area", "OUTPUT": str(result)}),
    ]
    request = plan({"source": source, "mask": mask}, operations)
    assert inspect(request)["status"] == "passed"
    request["plan"]["tool_calls"][-1]["arguments"]["parameters"]["FIELD_NAME"] = "wrong"
    assert inspect(request)["status"] == "failed"
    assert not any(p.exists() for p in (clip, area, result))


def test_basic_and_category_statistics_match_actual_input_including_nulls(gis, tmp_path):
    clip = gis / "clip.gpkg"
    basic = gis / "basic.gpkg"
    params = {"INPUT_LAYER": str(clip), "FIELD_NAME": "value", "OUTPUT": str(basic)}
    report = step("native:basicstatisticsforfields", params, {"in": str(clip)}, basic)
    assert report["status"] == "passed", report
    statistics = report["facts"]["statistics"]
    assert {key: statistics[key] for key in ("feature_count", "valid_value_count", "null_value_count", "sum", "min", "max", "unit")} == {
        "feature_count": 3, "valid_value_count": 2, "null_value_count": 1, "sum": 15, "min": 5, "max": 10, "unit": None}
    assert statistics["empty"] is False and statistics["no_valid_values"] is False

    category = gis / "category.gpkg"
    params = {"INPUT": str(clip), "VALUES_FIELD_NAME": "value", "CATEGORIES_FIELD_NAME": ["grp"], "OUTPUT": str(category)}
    report = step("qgis:statisticsbycategories", params, {"in": str(clip)}, category)
    assert report["status"] == "passed", report
    stats = report["facts"]["statistics"]
    groups = {item["group"]["grp"]: item for item in stats["groups"]}
    assert stats["feature_count"] == 3 and stats["valid_value_count"] == 2 and stats["null_value_count"] == 1
    assert (groups["A"]["feature_count"], groups["A"]["valid_value_count"], groups["A"]["null_value_count"], groups["A"]["sum"]) == (2, 1, 1, 10)
    assert (groups["B"]["feature_count"], groups["B"]["valid_value_count"], groups["B"]["sum"]) == (1, 1, 5)
    assert not stats["omitted_groups"]

    count = gis / "count.gpkg"
    params.pop("VALUES_FIELD_NAME")
    params["OUTPUT"] = str(count)
    report = step("qgis:statisticsbycategories", params, {"in": str(clip)}, count)
    assert report["status"] == "passed", report
    stats = report["facts"]["statistics"]
    assert stats["feature_count"] == 3 and stats["valid_value_count"] is None and stats["sum"] is None
    assert {item["group"]["grp"]: item["feature_count"] for item in stats["groups"]} == {"A": 2, "B": 1}

    bad = tmp_path / "bad_stats.gpkg"
    shutil.copyfile(basic, bad)
    with sqlite3.connect(bad) as conn:
        table = conn.execute("SELECT table_name FROM gpkg_contents").fetchone()[0]
        conn.execute(f'UPDATE "{table}" SET "sum" = 16')
    before = hashlib.sha256(bad.read_bytes()).hexdigest()
    params = {"INPUT_LAYER": str(clip), "FIELD_NAME": "value", "OUTPUT": str(basic)}
    assert step("native:basicstatisticsforfields", params, {"in": str(clip)}, bad)["status"] == "failed"
    assert hashlib.sha256(bad.read_bytes()).hexdigest() == before
    assert step("native:basicstatisticsforfields", params, {"in": str(clip)}, clip)["status"] == "failed"
    assert step("native:basicstatisticsforfields", params, {"in": str(clip)}, count)["status"] == "failed"


def test_empty_all_null_and_dissolve_placeholder_are_distinct(gis):
    empty = gis / "inputs/t05_empty_source.gpkg"
    null = gis / "inputs/t05_all_null_source.gpkg"
    far, mask, src = gis / "far.gpkg", gis / "inputs/t05_far_mask.gpkg", gis / "inputs/t05_source.gpkg"
    report = step("native:clip", {"INPUT": str(src), "OVERLAY": str(mask), "OUTPUT": str(far)},
                  {"src": str(src), "mask": str(mask)}, far)
    assert report["status"] == "passed" and report["facts"]["output"]["feature_count"] == 0
    for name, source, count, missing in (("basic_empty", empty, 0, 0), ("basic_null", null, 4, 4)):
        output = gis / f"{name}.gpkg"
        report = step("native:basicstatisticsforfields", {"INPUT_LAYER": str(source), "FIELD_NAME": "value", "OUTPUT": str(output)},
                      {"in": str(source)}, output)
        assert report["status"] == "passed", report
        stats = report["facts"]["statistics"]
        assert (stats["feature_count"], stats["valid_value_count"], stats["null_value_count"], stats["sum"], stats["min"], stats["max"]) == (count, 0, missing, 0, None, None)
        assert stats["empty"] == (count == 0) and stats["no_valid_values"] is True
    output = gis / "category_null.gpkg"
    report = step("qgis:statisticsbycategories", {"INPUT": str(null), "VALUES_FIELD_NAME": "value", "CATEGORIES_FIELD_NAME": ["grp"], "OUTPUT": str(output)},
                  {"in": str(null)}, output)
    assert report["status"] == "passed", report
    stats = report["facts"]["statistics"]
    assert stats["feature_count"] == 4 and stats["groups"] == [] and stats["no_valid_values"]
    assert {item["group"]["grp"]: item["feature_count"] for item in stats["omitted_groups"]} == {"A": 2, "B": 1, "C": 1}
    for fields, name, status, separate in ((["grp"], "empty_group", "passed", False),
                                            ([], "empty_all", "failed", False),
                                            (["grp"], "empty_group_separate", "passed", True),
                                            ([], "empty_all_separate", "failed", True)):
        output = gis / f"{name}.gpkg"
        before = hashlib.sha256(output.read_bytes()).hexdigest()
        report = step("native:dissolve", {"INPUT": str(empty), "FIELD": fields, "SEPARATE_DISJOINT": separate, "OUTPUT": str(output)},
                      {"in": str(empty)}, output)
        assert report["status"] == status, report
        assert hashlib.sha256(output.read_bytes()).hexdigest() == before
        if status == "failed":
            assert report["facts"]["output"]["feature_count"] == 1
            assert "placeholder" in report["errors"][0]


def test_area_attributes_and_units_are_proved_before_statistics(gis, tmp_path):
    clip, output = gis / "clip.gpkg", gis / "area.gpkg"
    params = {"INPUT": str(clip), "CALC_METHOD": 0, "OUTPUT": str(output)}
    report = step("qgis:exportaddgeometrycolumns", params, {"in": str(clip)}, output)
    assert report["status"] == "passed", report
    assert report["facts"]["field_units"] == {"area": "m2", "perimeter": "m"}
    result = gis / "area_stats.gpkg"
    basic = {"INPUT_LAYER": str(output), "FIELD_NAME": "area", "OUTPUT": str(result)}
    stats = step("native:basicstatisticsforfields", basic, {"result": str(output)}, result,
                 facts={"result": report["facts"]})
    assert stats["status"] == "passed", stats
    stats = stats["facts"]["statistics"]
    assert (stats["feature_count"], stats["sum"], stats["min"], stats["max"], stats["unit"]) == (3, 16, 2, 8, "m2")
    without_provenance = step("native:basicstatisticsforfields", basic, {"result": str(output)}, result)
    assert without_provenance["facts"]["statistics"]["unit"] is None
    modified_facts = {**report["facts"], "algorithm": "native:clip"}
    modified = step("native:basicstatisticsforfields", basic, {"result": str(output)}, result,
                    facts={"result": modified_facts})
    assert modified["facts"]["statistics"]["unit"] is None
    bad = tmp_path / "bad_area.gpkg"
    shutil.copyfile(output, bad)
    edit_vector(bad, "f.SetField('area', 999.0)")
    assert step("qgis:exportaddgeometrycolumns", params, {"in": str(clip)}, bad)["status"] == "failed"


def test_layer_observation_is_checked_against_actual_source(gis):
    source = gis / "inputs/t05_source.gpkg"
    obs = {"id": "observed-layer-id", "source": str(source) + "|layername=source", "crs": "EPSG:32650",
           "geometry": "Polygon", "feature_count": 3, "fields": ["fid", "rid", "grp", "value"]}
    request = {"mode": "validate", "call": {"tool_name": "add_layer", "arguments": {}},
               "input_paths": {"source": str(source)}, "output_paths": {}, "observation": obs}
    report = inspect(request)
    assert report["status"] == "passed", report
    assert report["facts"]["statistics"]["kind"] == "feature_count"
    assert report["facts"]["statistics"]["feature_count"] == 3
    for key, wrong in (("feature_count", 2), ("geometry", "LineString"), ("crs", "EPSG:4326"),
                       ("source", str(gis / "inputs/t05_clip_mask.gpkg")), ("fields", ["rid"]), ("id", "")):
        changed = {**request, "observation": {**obs, key: wrong}}
        assert inspect(changed)["status"] == "failed", key


def test_preflight_refuses_collision_wrong_types_crs_table_and_format(gis, tmp_path):
    source = gis / "inputs/t05_source.gpkg"
    collision = gis / "inputs/t05_collision_source.gpkg"
    output = tmp_path / "unused.gpkg"
    helper = {"INPUT": str(collision), "CALC_METHOD": 0, "OUTPUT": str(output)}
    req = plan({"source": collision}, [("qgis:exportaddgeometrycolumns", ["source"], "out", helper)])
    assert inspect(req)["status"] == "failed"
    basic = {"INPUT_LAYER": str(source), "FIELD_NAME": "grp", "OUTPUT": str(output)}
    req = plan({"source": source}, [("native:basicstatisticsforfields", ["source"], "out", basic)])
    assert inspect(req)["status"] == "failed"
    basic["FIELD_NAME"] = "value"
    basic["OUTPUT"] = str(tmp_path / "stats.csv")
    req = plan({"source": source}, [("native:basicstatisticsforfields", ["source"], "out", basic)])
    assert inspect(req)["status"] == "failed"
    category = {"INPUT": str(source), "VALUES_FIELD_NAME": "wrong", "CATEGORIES_FIELD_NAME": ["grp"], "OUTPUT": str(output)}
    req = plan({"source": source}, [("qgis:statisticsbycategories", ["source"], "out", category)])
    assert inspect(req)["status"] == "failed"
    req = plan({"source": gis / "basic.gpkg"}, [("native:basicstatisticsforfields", ["source"], "out", basic)])
    assert inspect(req)["status"] == "failed"
    geojson = tmp_path / "degrees.geojson"
    geojson.write_text(json.dumps({"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
                                   "features": [{"type": "Feature", "properties": {"id": 1}, "geometry": {"type": "Polygon", "coordinates": [[[1, 1], [2, 1], [2, 2], [1, 2], [1, 1]]]}}]}))
    clip = {"INPUT": str(source), "OVERLAY": str(geojson), "OUTPUT": str(output)}
    req = plan({"source": source, "overlay": geojson}, [("native:clip", ["source", "overlay"], "out", clip)])
    assert inspect(req)["status"] == "failed"
    assert not output.exists()


def test_sidecars_are_rejected_without_changing_evidence(gis, tmp_path):
    source, output = gis / "clip.gpkg", gis / "basic.gpkg"
    copied = tmp_path / "source.gpkg"
    shutil.copyfile(source, copied)
    sidecar = Path(str(copied) + "-wal")
    sidecar.write_bytes(b"unverified sidecar")
    before = hashlib.sha256(copied.read_bytes()).hexdigest()
    params = {"INPUT_LAYER": str(copied), "FIELD_NAME": "value", "OUTPUT": str(output)}
    report = step("native:basicstatisticsforfields", params, {"in": str(copied)}, output)
    assert report["status"] == "failed" and "sidecar" in report["errors"][0].lower()
    assert sidecar.read_bytes() == b"unverified sidecar"
    assert hashlib.sha256(copied.read_bytes()).hexdigest() == before
    sidecar.unlink()
    sidecar.symlink_to(tmp_path / "missing-journal-target")
    report = step("native:basicstatisticsforfields", params, {"in": str(copied)}, output)
    assert report["status"] == "failed" and sidecar.is_symlink()
    sidecar.unlink()


def test_point_line_clip_and_dissolve_use_real_nonpolygon_geometry(gis, tmp_path):
    mask = gis / "inputs/t05_clip_mask.gpkg"
    for family, clipped_count in (("points", 3), ("lines", 2)):
        source = gis / f"{family}.geojson"
        clip = gis / f"{family}_clip.gpkg"
        params = {"INPUT": str(source), "OVERLAY": str(mask), "OUTPUT": str(clip)}
        report = step("native:clip", params, {"in": str(source), "mask": str(mask)}, clip)
        assert report["status"] == "passed", report
        assert report["facts"]["output"]["feature_count"] == clipped_count
        for separate, expected in ((False, 1), (True, 2)):
            output = gis / f"{family}_{'separate' if separate else 'union'}.gpkg"
            params = {"INPUT": str(source), "FIELD": ["grp"], "SEPARATE_DISJOINT": separate, "OUTPUT": str(output)}
            report = step("native:dissolve", params, {"in": str(source)}, output)
            assert report["status"] == "passed", report
            assert report["facts"]["output"]["feature_count"] == expected
    bad = tmp_path / "wrong_clip.gpkg"
    shutil.copyfile(gis / "lines_clip.gpkg", bad)
    edit_vector(bad, "f.SetGeometry(ogr.CreateGeometryFromWkt('MULTILINESTRING ((500001 2,500003 2))'))")
    params = {"INPUT": str(gis / 'lines.geojson'), "OVERLAY": str(mask), "OUTPUT": str(gis / 'lines_clip.gpkg')}
    assert step("native:clip", params, {"in": str(gis / 'lines.geojson'), "mask": str(mask)}, bad)["status"] == "failed"


def test_trusted_buffer_empty_geometry_is_distinct_from_unverified_null(gis):
    source, buffer = gis / "inputs/t05_source.gpkg", gis / "empty_buffer.gpkg"
    params = {"INPUT": str(source), "OUTPUT": str(buffer), "DISTANCE": -10, "SEGMENTS": 8,
              "END_CAP_STYLE": 0, "JOIN_STYLE": 0, "MITER_LIMIT": 2, "DISSOLVE": False, "SEPARATE_DISJOINT": False}
    report = inspect({"mode": "validate", "call": {"tool_name": "execute_processing", "distance_unit": "m",
                      "arguments": {"algorithm": "native:buffer", "parameters": params}},
                      "input_paths": {"source": str(source)}, "output_paths": {"result": str(buffer)}})
    assert report["status"] == "passed", report
    assert report["facts"]["output"]["empty_geometries"] == 3
    stats = gis / "buffer_stats.gpkg"
    basic = {"INPUT_LAYER": str(buffer), "FIELD_NAME": "value", "OUTPUT": str(stats)}
    no_proof = step("native:basicstatisticsforfields", basic, {"result": str(buffer)}, stats)
    assert no_proof["status"] == "failed"
    proven = step("native:basicstatisticsforfields", basic, {"result": str(buffer)}, stats,
                  facts={"result": report["facts"]})
    assert proven["status"] == "passed", proven
    assert proven["facts"]["statistics"]["feature_count"] == 3
    assert proven["facts"]["statistics"]["sum"] == 15
    mask, clipped = gis / "inputs/t05_clip_mask.gpkg", gis / "buffer_clip.gpkg"
    clip = {"INPUT": str(buffer), "OVERLAY": str(mask), "OUTPUT": str(clipped)}
    proven_clip = step("native:clip", clip, {"result": str(buffer), "mask": str(mask)}, clipped,
                       facts={"result": report["facts"]})
    assert proven_clip["status"] == "passed", proven_clip
    assert proven_clip["facts"]["output"]["feature_count"] == 0
    dissolved = gis / "buffer_dissolve.gpkg"
    dissolve = {"INPUT": str(buffer), "FIELD": ["grp"], "SEPARATE_DISJOINT": False, "OUTPUT": str(dissolved)}
    unproven_union = step("native:dissolve", dissolve, {"result": str(buffer)}, dissolved,
                          facts={"result": report["facts"]})
    assert unproven_union["status"] == "failed" and "empty" in unproven_union["errors"][0].lower()
    observation = {"id": "verified-buffer", "source": str(buffer), "crs": "EPSG:32650", "geometry": "MultiPolygon",
                   "feature_count": 3, "fields": ["fid", "rid", "grp", "value"]}
    load = {"mode": "validate", "call": {"tool_name": "add_layer", "arguments": {}},
            "input_paths": {"result": str(buffer)}, "output_paths": {}, "observation": observation}
    assert inspect(load)["status"] == "failed"
    load["input_facts"] = {"result": report["facts"]}
    assert inspect(load)["status"] == "passed"
    wrong_facts = {**report["facts"], "output": {**report["facts"]["output"], "empty_geometries": 1}}
    assert step("native:basicstatisticsforfields", basic, {"result": str(buffer)}, stats,
                facts={"result": wrong_facts})["status"] == "failed"


def test_polygon_boundary_touch_is_valid_empty_clip(gis):
    source, mask, output = gis / "inputs/t05_source.gpkg", gis / "touch.geojson", gis / "touch_clip.gpkg"
    params = {"INPUT": str(source), "OVERLAY": str(mask), "OUTPUT": str(output)}
    report = step("native:clip", params, {"source": str(source), "mask": str(mask)}, output)
    assert report["status"] == "passed", report
    assert report["facts"]["output"]["feature_count"] == 0


def test_category_wrong_count_and_missing_row_cannot_pass(gis, tmp_path):
    clip, actual = gis / "clip.gpkg", gis / "category.gpkg"
    params = {"INPUT": str(clip), "VALUES_FIELD_NAME": "value", "CATEGORIES_FIELD_NAME": ["grp"], "OUTPUT": str(actual)}
    wrong = tmp_path / "wrong_category.gpkg"
    shutil.copyfile(actual, wrong)
    with sqlite3.connect(wrong) as conn:
        table = conn.execute("SELECT table_name FROM gpkg_contents").fetchone()[0]
        conn.execute(f'UPDATE "{table}" SET count=2 WHERE grp=?', ("A",))
    assert step("qgis:statisticsbycategories", params, {"in": str(clip)}, wrong)["status"] == "failed"
    missing = tmp_path / "missing_category.gpkg"
    shutil.copyfile(actual, missing)
    with sqlite3.connect(missing) as conn:
        table = conn.execute("SELECT table_name FROM gpkg_contents").fetchone()[0]
        conn.execute(f'DELETE FROM "{table}" WHERE grp=?', ("B",))
    assert step("qgis:statisticsbycategories", params, {"in": str(clip)}, missing)["status"] == "failed"


def test_preflight_rejects_loading_planned_nonspatial_table(gis, tmp_path):
    source, table = gis / "inputs/t05_source.gpkg", tmp_path / "table.gpkg"
    params = {"INPUT_LAYER": str(source), "FIELD_NAME": "value", "OUTPUT": str(table)}
    request = plan({"source": source}, [("native:basicstatisticsforfields", ["source"], "table", params)])
    request["plan"]["steps"].append({"step_id": 2, "input_refs": ["table"], "output_refs": []})
    request["plan"]["tool_calls"].append({"step_id": 2, "tool_name": "add_layer", "arguments": {
        "path": str(table), "layer_type": "vector"}})
    result = inspect(request)
    assert result["status"] == "failed"
    assert not table.exists()


def test_geometry_helper_rejects_nonpolygons_geographic_or_wrong_calculation(gis, tmp_path):
    output = tmp_path / "area.gpkg"
    source = gis / "inputs/t05_source.gpkg"
    params = {"INPUT": str(source), "CALC_METHOD": 1, "OUTPUT": str(output)}
    assert inspect(plan({"source": source}, [("qgis:exportaddgeometrycolumns", ["source"], "out", params)]))["status"] == "failed"
    points = gis / "points.geojson"
    params = {"INPUT": str(points), "CALC_METHOD": 0, "OUTPUT": str(output)}
    assert inspect(plan({"source": points}, [("qgis:exportaddgeometrycolumns", ["source"], "out", params)]))["status"] == "failed"
    degrees = tmp_path / "degrees.geojson"
    degrees.write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [[[115, 1], [116, 1], [116, 2], [115, 2], [115, 1]]]}}]}))
    params = {"INPUT": str(degrees), "CALC_METHOD": 0, "OUTPUT": str(output)}
    assert inspect(plan({"source": degrees}, [("qgis:exportaddgeometrycolumns", ["source"], "out", params)]))["status"] == "failed"
    assert not output.exists()


def test_null_group_value_and_missing_numeric_category_are_not_confused(gis):
    source = gis / "null_group.gpkg"
    numeric = gis / "null_group_category.gpkg"
    params = {"INPUT": str(source), "VALUES_FIELD_NAME": "value", "CATEGORIES_FIELD_NAME": ["grp"], "OUTPUT": str(numeric)}
    report = step("qgis:statisticsbycategories", params, {"source": str(source)}, numeric)
    assert report["status"] == "passed", report
    stats = report["facts"]["statistics"]
    assert {row["group"]["grp"]: row["sum"] for row in stats["groups"]} == {None: 10, "B": 5}
    assert [(row["group"]["grp"], row["feature_count"], row["null_value_count"]) for row in stats["omitted_groups"]] == [("A", 1, 1)]
    count = gis / "null_group_count.gpkg"
    params.pop("VALUES_FIELD_NAME")
    report = step("qgis:statisticsbycategories", params, {"source": str(source)}, count)
    assert report["status"] == "passed", report
    assert {row["group"]["grp"]: row["feature_count"] for row in report["facts"]["statistics"]["groups"]} == {None: 1, "A": 1, "B": 1}


def test_nonspatial_table_rejects_wrong_metadata_and_column_type(gis, tmp_path):
    source, output = gis / "clip.gpkg", gis / "basic.gpkg"
    params = {"INPUT_LAYER": str(source), "FIELD_NAME": "value", "OUTPUT": str(output)}
    bad = tmp_path / "bad_crs.gpkg"
    shutil.copyfile(output, bad)
    with sqlite3.connect(bad) as conn:
        conn.execute("UPDATE gpkg_contents SET srs_id = 32650")
    report = step("native:basicstatisticsforfields", params, {"source": str(source)}, bad)
    assert report["status"] == "failed" and "CRS" in report["errors"][0], report
    report = step("native:basicstatisticsforfields", params, {"source": str(source)}, gis / "wrong_type.gpkg")
    assert report["status"] == "failed" and "type" in report["errors"][0].lower(), report


def test_public_spatial_adapter_runs_system_preflight_and_readback(gis, tmp_path):
    source, mask, output = gis / "inputs/t05_source.gpkg", gis / "inputs/t05_clip_mask.gpkg", tmp_path / "clip.gpkg"
    call = ToolCallPlan(step_id=1, tool_name="execute_processing", arguments={"algorithm": "native:clip", "parameters": {
        "INPUT": str(source), "OVERLAY": str(mask), "OUTPUT": str(output)}})
    task = TaskPlan(task_id="t05-spatial", task_summary="Clip", risk_level="low", review_status="approved",
                    required_inputs=[InputSpec(input_id="source", description="source", path=str(source)),
                                     InputSpec(input_id="mask", description="mask", path=str(mask))],
                    expected_outputs=[OutputSpec(output_id="clip", description="clip", path=str(output))],
                    steps=[PlanStep(step_id=1, description="clip", input_refs=["source", "mask"], output_refs=["clip"],
                                    risk_level="low", requires_review=False)], tool_calls=[call])
    assert asyncio.run(preflight_spatial_plan(task))["status"] == "passed"
    assert not output.exists()
    shutil.copyfile(gis / "clip.gpkg", output)
    result = asyncio.run(validate_spatial_step(call, input_paths={"source": str(source), "mask": str(mask)},
                                               output_paths={"clip": str(output)}))
    assert result["status"] == "passed", result
    assert result["facts"]["output"]["feature_count"] == 3


def test_inexact_integer_statistics_are_rejected(gis):
    source, output = gis / "large_integer.gpkg", gis / "large_stats.gpkg"
    params = {"INPUT_LAYER": str(source), "FIELD_NAME": "value", "OUTPUT": str(output)}
    report = step("native:basicstatisticsforfields", params, {"source": str(source)}, output)
    assert report["status"] == "failed" and "precision" in report["errors"][0].lower(), report


def _create_review_counterexamples(gis, folder):
    """Create independent OGR fixtures and observe a real QGIS multi-layer selection."""
    script = r'''
import json, os, shutil, sys
from pathlib import Path
from osgeo import gdal, ogr, osr
root, shared = Path(sys.argv[1]), Path(sys.argv[2])
source = root / 'small.gpkg'; shutil.copyfile(shared / 'clip.gpkg', source)
d = gdal.OpenEx(str(source), gdal.OF_VECTOR | gdal.OF_UPDATE); l = d.GetLayer(0)
for f in l:
    if f.GetField('rid') == 1: f.SetField('value', 1e-9)
    if f.GetField('rid') == 2: f.SetFieldNull('value')
    if f.GetField('rid') == 3: f.SetField('value', 2e-9)
    l.SetFeature(f)
d.Close(); del d, l, f
original = ogr.Open(str(shared / 'inputs/t05_source.gpkg')); original_layer = original.GetLayer(0)
d = ogr.GetDriverByName('GPKG').CreateDataSource(str(root / 'empty_point.gpkg'))
l = d.CreateLayer('empty_point', srs=original_layer.GetSpatialRef(), geom_type=ogr.wkbPoint)
for field in original_layer.schema: l.CreateField(field)
d.Close(); original.Close(); del d, l, original, original_layer
srs = osr.SpatialReference(); srs.ImportFromEPSG(32650)
d = ogr.GetDriverByName('GPKG').CreateDataSource(str(root / 'multi.gpkg'))
for name, value, x in [('first', 'wrong', 500000), ('second', 'approved', 500001)]:
    l = d.CreateLayer(name, srs=srs, geom_type=ogr.wkbPoint)
    l.CreateField(ogr.FieldDefn('name', ogr.OFTString))
    f = ogr.Feature(l.GetLayerDefn()); f.SetField('name', value)
    f.SetGeometry(ogr.CreateGeometryFromWkt('POINT (%s 2)' % x)); l.CreateFeature(f)
    del f, l
d.Close(); del d, srs
os.environ['QT_QPA_PLATFORM'] = 'offscreen'; profile = root / 'profile'; profile.mkdir()
os.environ['QGIS_CUSTOM_CONFIG_PATH'] = str(profile)
from qgis.core import QgsApplication, QgsVectorLayer, QgsWkbTypes
app = QgsApplication([], False, str(profile)); app.initQgis()
def observation(path):
    layer = QgsVectorLayer(path, 'test', 'ogr')
    result = {'source': layer.source(), 'id': layer.id(), 'crs': layer.crs().authid(),
              'feature_count': layer.featureCount(), 'geometry': QgsWkbTypes.displayString(layer.wkbType()),
              'fields': [field.name() for field in layer.fields()]}
    return result
print(json.dumps({'bare': observation(str(root / 'multi.gpkg')),
                  'selected': observation(str(root / 'multi.gpkg') + '|layername=second')}))
app.exitQgis()
'''
    run = subprocess.run(["/usr/bin/python3", "-c", script, str(folder), str(gis)],
                         text=True, capture_output=True, check=True, timeout=60,
                         env=dict(os.environ, DATABASE_URL="", PYTHON_DOTENV_DISABLED="1"))
    return json.loads(run.stdout)


def _set_stats(path, **values):
    with sqlite3.connect(path) as conn:
        table = conn.execute("SELECT table_name FROM gpkg_contents").fetchone()[0]
        assignments = ", ".join(f'"{name}" = ?' for name in values)
        conn.execute(f'UPDATE "{table}" SET {assignments}', tuple(values.values()))


def test_review_small_statistics_are_compared_and_facts_come_from_table(gis, tmp_path):
    _create_review_counterexamples(gis, tmp_path)
    source, table = tmp_path / "small.gpkg", tmp_path / "actual.gpkg"
    shutil.copyfile(gis / "basic.gpkg", table)
    params = {"INPUT_LAYER": str(source), "FIELD_NAME": "value", "OUTPUT": str(table)}
    _set_stats(table, sum=0.0, min=0.0, max=0.0)
    wrong = step("native:basicstatisticsforfields", params, {"src": str(source)}, table)
    assert wrong["status"] == "failed", wrong
    _set_stats(table, sum=3e-9, min=1e-9, max=2e-9)
    correct = step("native:basicstatisticsforfields", params, {"src": str(source)}, table)
    assert correct["status"] == "passed", correct
    assert correct["facts"]["statistics"]["sum"] == 3e-9
    assert correct["facts"]["statistics"]["min"] == 1e-9
    assert correct["facts"]["statistics"]["max"] == 2e-9
    _set_stats(table, sum=0.0, min=1e-9, max=2e-9)
    assert step("native:basicstatisticsforfields", params, {"src": str(source)}, table)["status"] == "failed"
    _set_stats(table, sum=3e-9, min=math.nextafter(1e-9, math.inf), max=2e-9)
    assert step("native:basicstatisticsforfields", params, {"src": str(source)}, table)["status"] == "failed"


def test_review_zero_row_outputs_must_keep_geometry_family(gis, tmp_path):
    _create_review_counterexamples(gis, tmp_path)
    source, far, empty, wrong = (gis / "inputs/t05_source.gpkg", gis / "inputs/t05_far_mask.gpkg",
                                 gis / "inputs/t05_empty_source.gpkg", tmp_path / "empty_point.gpkg")
    clip = {"INPUT": str(source), "OVERLAY": str(far), "OUTPUT": str(wrong)}
    assert step("native:clip", clip, {"src": str(source), "mask": str(far)}, wrong)["status"] == "failed"
    dissolve = {"INPUT": str(empty), "FIELD": ["grp"], "SEPARATE_DISJOINT": False, "OUTPUT": str(wrong)}
    assert step("native:dissolve", dissolve, {"src": str(empty)}, wrong)["status"] == "failed"


def test_review_multilayer_observation_requires_actual_selected_layer(gis, tmp_path):
    observations = _create_review_counterexamples(gis, tmp_path)
    approved = str(tmp_path / "multi.gpkg") + "|layername=second"
    request = {"mode": "validate", "call": {"tool_name": "add_layer", "arguments": {"path": approved}},
               "input_paths": {"src": approved}, "output_paths": {}, "observation": observations["bare"]}
    assert inspect(request)["status"] == "failed"
    request["observation"] = observations["selected"]
    assert inspect(request)["status"] == "passed"


def test_review_preflight_facts_expose_crs_field_type_and_unit(gis, tmp_path):
    source, mask = gis / "inputs/t05_source.gpkg", gis / "inputs/t05_clip_mask.gpkg"
    clip, area, stats = (tmp_path / name for name in ("clip.gpkg", "area.gpkg", "stats.gpkg"))
    operations = [
        ("native:clip", ["source", "mask"], "clip", {"INPUT": str(source), "OVERLAY": str(mask), "OUTPUT": str(clip)}),
        ("qgis:exportaddgeometrycolumns", ["clip"], "area", {"INPUT": str(clip), "CALC_METHOD": 0, "OUTPUT": str(area)}),
        ("native:basicstatisticsforfields", ["area"], "stats", {"INPUT_LAYER": str(area), "FIELD_NAME": "area", "OUTPUT": str(stats)}),
    ]
    report = inspect(plan({"source": source, "mask": mask}, operations))
    assert report["status"] == "passed", report
    reviewed = report["facts"]["review_steps"]
    assert len(reviewed) == 3
    assert reviewed[0]["input_crs"] == [{"ref": "source", "crs": "EPSG:32650"}, {"ref": "mask", "crs": "EPSG:32650"}]
    assert {key: reviewed[1][key] for key in ("field", "field_type", "unit", "output_crs")} == {
        "field": "area", "field_type": "Real", "unit": "m2", "output_crs": "EPSG:32650"}
    assert {key: reviewed[2][key] for key in ("field", "field_type", "unit", "output_crs", "output_kind", "geometry")} == {
        "field": "area", "field_type": "Real", "unit": "m2", "output_crs": None, "output_kind": "table", "geometry": None}
    assert not any(path.exists() for path in (clip, area, stats))


def test_review_preflight_rejects_nonfinite_source_before_write(gis, tmp_path):
    source, output = tmp_path / "infinity.gpkg", tmp_path / "stats.gpkg"
    shutil.copyfile(gis / "clip.gpkg", source)
    edit_vector(source, "f.SetField('value', float('inf'))")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    params = {"INPUT_LAYER": str(source), "FIELD_NAME": "value", "OUTPUT": str(output)}
    report = inspect(plan({"source": source}, [("native:basicstatisticsforfields", ["source"], "stats", params)]))
    assert report["status"] == "failed", report
    assert not output.exists() and hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_review_geometry_measurements_reject_small_nonzero_replaced_with_zero(gis, tmp_path):
    source, output = gis / "tiny.geojson", gis / "tiny_area.gpkg"
    params = {"INPUT": str(source), "CALC_METHOD": 0, "OUTPUT": str(output)}
    real = step("qgis:exportaddgeometrycolumns", params, {"src": str(source)}, output)
    assert real["status"] == "passed", real
    wrong = tmp_path / "zero_area.gpkg"
    shutil.copyfile(output, wrong)
    edit_vector(wrong, "f.SetField('area', 0.0)")
    report = step("qgis:exportaddgeometrycolumns", params, {"src": str(source)}, wrong)
    assert report["status"] == "failed", report


def test_review_category_numbers_come_from_checked_table(gis, tmp_path):
    _create_review_counterexamples(gis, tmp_path)
    source, output = tmp_path / "small.gpkg", tmp_path / "category.gpkg"
    shutil.copyfile(gis / "category.gpkg", output)
    # Two values permit accumulation rounding; a singleton now compares exactly.
    edit_vector(source, "f.SetField('value', 1e-9)", "f.GetField('rid') == 2")
    actual_group_sum = math.nextafter(2e-9, math.inf)
    with sqlite3.connect(output) as conn:
        table = conn.execute("SELECT table_name FROM gpkg_contents").fetchone()[0]
        conn.execute(f'UPDATE "{table}" SET count=2, sum=?, min=?, max=? WHERE grp=?', (actual_group_sum, 1e-9, 1e-9, "A"))
        conn.execute(f'UPDATE "{table}" SET sum=?, min=?, max=? WHERE grp=?', (2e-9, 2e-9, 2e-9, "B"))
    params = {"INPUT": str(source), "VALUES_FIELD_NAME": "value", "CATEGORIES_FIELD_NAME": ["grp"], "OUTPUT": str(output)}
    report = step("qgis:statisticsbycategories", params, {"src": str(source)}, output)
    assert report["status"] == "passed", report
    stats = report["facts"]["statistics"]
    assert next(group for group in stats["groups"] if group["group"]["grp"] == "A")["sum"] == actual_group_sum
    assert stats["sum"] == math.fsum([actual_group_sum, 2e-9])
    with sqlite3.connect(output) as conn:
        conn.execute(f'UPDATE "{table}" SET sum=0, min=0, max=0 WHERE grp=?', ("A",))
    assert step("qgis:statisticsbycategories", params, {"src": str(source)}, output)["status"] == "failed"


def test_review_preflight_consumed_buffer_and_verified_units(gis, tmp_path):
    source, buffer = gis / "inputs/t05_source.gpkg", gis / "empty_buffer.gpkg"
    buffer_params = {"INPUT": str(source), "OUTPUT": str(buffer), "DISTANCE": -10, "SEGMENTS": 8,
                     "END_CAP_STYLE": 0, "JOIN_STYLE": 0, "MITER_LIMIT": 2, "DISSOLVE": False, "SEPARATE_DISJOINT": False}
    proof = inspect({"mode": "validate", "call": {"tool_name": "execute_processing", "distance_unit": "m",
                     "arguments": {"algorithm": "native:buffer", "parameters": buffer_params}},
                     "input_paths": {"src": str(source)}, "output_paths": {"buffer": str(buffer)}})
    assert proof["status"] == "passed", proof
    output = tmp_path / "stats.gpkg"
    params = {"INPUT_LAYER": str(buffer), "FIELD_NAME": "value", "OUTPUT": str(output)}
    request = plan({"buffer": buffer}, [("native:basicstatisticsforfields", ["buffer"], "stats", params)])
    assert inspect(request)["status"] == "failed"
    request["input_facts"] = {"buffer": proof["facts"]}
    report = inspect(request)
    assert report["status"] == "passed", report
    assert report["facts"]["review_steps"][0]["unit"] is None
    area = gis / "area.gpkg"
    area_params = {"INPUT": str(gis / 'clip.gpkg'), "CALC_METHOD": 0, "OUTPUT": str(area)}
    area_proof = step("qgis:exportaddgeometrycolumns", area_params, {"src": str(gis / 'clip.gpkg')}, area)
    params = {"INPUT_LAYER": str(area), "FIELD_NAME": "area", "OUTPUT": str(output)}
    request = plan({"result": area}, [("native:basicstatisticsforfields", ["result"], "stats", params)])
    request["input_facts"] = {"result": area_proof["facts"]}
    report = inspect(request)
    assert report["status"] == "passed", report
    assert report["facts"]["review_steps"][0]["unit"] == "m2"


def test_review_tiny_geometry_cannot_be_displaced_without_detection(gis, tmp_path):
    source, output = gis / "micro.geojson", gis / "micro_area.gpkg"
    params = {"INPUT": str(source), "CALC_METHOD": 0, "OUTPUT": str(output)}
    good = step("qgis:exportaddgeometrycolumns", params, {"src": str(source)}, output)
    assert good["status"] == "passed", good
    displaced = tmp_path / "displaced.gpkg"
    shutil.copyfile(output, displaced)
    shifted = ("POLYGON ((500000 0.00000000000005,500001 0.00000000000005,"
               "500001 0.0000000000001,500000 0.0000000000001,500000 0.00000000000005))")
    edit_vector(displaced, f"f.SetGeometry(ogr.CreateGeometryFromWkt({shifted!r}))")
    report = step("qgis:exportaddgeometrycolumns", params, {"src": str(source)}, displaced)
    assert report["status"] == "failed", report


def test_review_preflight_load_buffer_and_category_fields(gis, tmp_path):
    source, buffer = gis / "inputs/t05_source.gpkg", tmp_path / "buffer.gpkg"
    params = {"INPUT": str(source), "OUTPUT": str(buffer), "DISTANCE": 5, "SEGMENTS": 8,
              "END_CAP_STYLE": 0, "JOIN_STYLE": 0, "MITER_LIMIT": 2, "DISSOLVE": False, "SEPARATE_DISJOINT": False}
    request = plan({"source": source}, [("native:buffer", ["source"], "buffer", params)])
    request["plan"]["tool_calls"][0]["distance_unit"] = "m"
    request["plan"]["steps"].append({"step_id": 2, "input_refs": ["buffer"], "output_refs": []})
    request["plan"]["tool_calls"].append({"step_id": 2, "tool_name": "add_layer", "arguments": {
        "path": str(buffer), "layer_type": "vector"}})
    report = inspect(request)
    assert report["status"] == "passed", report
    buffer_review, load_review = report["facts"]["review_steps"]
    assert buffer_review["unit"] == "m" and buffer_review["output_crs"] == "EPSG:32650"
    assert load_review["tool"] == "add_layer" and load_review["algorithm"] is None
    assert load_review["input_crs"] == [{"ref": "buffer", "crs": "EPSG:32650"}]
    assert load_review["output_kind"] == "layer" and load_review["field"] is None
    output = tmp_path / "categories.gpkg"
    params = {"INPUT": str(source), "VALUES_FIELD_NAME": "value", "CATEGORIES_FIELD_NAME": ["grp"], "OUTPUT": str(output)}
    report = inspect(plan({"source": source}, [("qgis:statisticsbycategories", ["source"], "out", params)]))
    reviewed = report["facts"]["review_steps"][0]
    assert (reviewed["field"], reviewed["field_type"], reviewed["group_fields"], reviewed["unit"]) == ("value", "Real", ["grp"], None)
    assert not buffer.exists() and not output.exists()


def test_review_tiny_line_cannot_be_displaced_without_detection(gis, tmp_path):
    source, mask, output = gis / "microline.geojson", gis / "micro_mask.geojson", gis / "microline_clip.gpkg"
    params = {"INPUT": str(source), "OVERLAY": str(mask), "OUTPUT": str(output)}
    good = step("native:clip", params, {"src": str(source), "mask": str(mask)}, output)
    assert good["status"] == "passed", good
    displaced = tmp_path / "displaced_line.gpkg"
    shutil.copyfile(output, displaced)
    shifted = "MULTILINESTRING ((500000.5 0.0000000005,500000.5 0.000000001))"
    edit_vector(displaced, f"f.SetGeometry(ogr.CreateGeometryFromWkt({shifted!r}))")
    report = step("native:clip", params, {"src": str(source), "mask": str(mask)}, displaced)
    assert report["status"] == "failed", report


def test_category_precision_before_write_checks_each_group(tmp_path):
    script = r'''
import sys
from pathlib import Path
from osgeo import ogr, osr
root = Path(sys.argv[1]); srs = osr.SpatialReference(); srs.ImportFromEPSG(32650)
for name, increment in [('unsafe', 1), ('safe', 2)]:
    ds = ogr.GetDriverByName('GPKG').CreateDataSource(str(root / (name + '.gpkg')))
    layer = ds.CreateLayer('input', srs=srs, geom_type=ogr.wkbPoint)
    layer.CreateField(ogr.FieldDefn('grp', ogr.OFTString)); layer.CreateField(ogr.FieldDefn('value', ogr.OFTInteger64))
    for i, (group, value) in enumerate([('A', 2**53), ('A', increment), ('B', -(2**53)), ('B', -increment)]):
        feature = ogr.Feature(layer.GetLayerDefn()); feature.SetField('grp', group); feature.SetField('value', value)
        feature.SetGeometry(ogr.CreateGeometryFromWkt('POINT (500000 %d)' % i)); layer.CreateFeature(feature)
    ds.Close()
'''
    subprocess.run(["/usr/bin/python3", "-c", script, str(tmp_path)], check=True, capture_output=True, text=True)
    for name, status in (("unsafe", "failed"), ("safe", "passed")):
        source, output = tmp_path / f"{name}.gpkg", tmp_path / f"{name}_stats.gpkg"
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        params = {"INPUT": str(source), "VALUES_FIELD_NAME": "value", "CATEGORIES_FIELD_NAME": ["grp"], "OUTPUT": str(output)}
        result = inspect(plan({"src": source}, [("qgis:statisticsbycategories", ["src"], "out", params)]))
        assert result["status"] == status, result
        if status == "failed":
            assert "precision" in result["errors"][0].lower()
        assert not output.exists() and hashlib.sha256(source.read_bytes()).hexdigest() == before
        assert not [p for p in tmp_path.iterdir() if p.name.endswith(("-wal", "-shm", "-journal"))]


@pytest.fixture(scope="module")
def roundoff_gis(tmp_path_factory):
    root = tmp_path_factory.mktemp("t05-roundoff-gis")
    worker = r'''
import math, os, random, sys
from pathlib import Path
from osgeo import ogr, osr
root = Path(sys.argv[1]); profile = root / 'profile'; profile.mkdir()
os.environ['QT_QPA_PLATFORM'] = 'offscreen'; os.environ['QGIS_CUSTOM_CONFIG_PATH'] = str(profile)
srs = osr.SpatialReference(); srs.ImportFromEPSG(32650)
def create(name, shapes):
    ds = ogr.GetDriverByName('GPKG').CreateDataSource(str(root / (name + '.gpkg')))
    layer = ds.CreateLayer(name, srs=srs, geom_type=ogr.wkbPolygon)
    layer.CreateField(ogr.FieldDefn('rid', ogr.OFTInteger)); layer.CreateField(ogr.FieldDefn('grp', ogr.OFTString))
    for i, points in enumerate(shapes):
        ring = ogr.Geometry(ogr.wkbLinearRing)
        for x, y in points + [points[0]]: ring.AddPoint_2D(x, y)
        geom = ogr.Geometry(ogr.wkbPolygon); geom.AddGeometry(ring)
        f = ogr.Feature(layer.GetLayerDefn()); f.SetField('rid', i); f.SetField('grp', str(i % 4))
        f.SetGeometry(geom); layer.CreateFeature(f)
    ds.Close()
random.seed(98765); shapes = []
for i in range(80):
    x = 500000 + random.uniform(-15, 15); y = 3000000 + random.uniform(-15, 15)
    radius = random.uniform(1, 8); n = random.randint(3, 9); phase = random.random() * 6.283
    shapes.append([(x + radius * math.cos(phase + 2 * math.pi * k / n),
                    y + radius * math.sin(phase + 2 * math.pi * k / n)) for k in range(n)])
create('irregular', shapes)
create('mask', [[(499990.123,2999984.234),(500015.345,2999997.456),
                 (500009.567,3000013.678),(499985.789,3000004.891)]])
create('octagon', [[(500000 + 100 * math.cos(2 * math.pi * i / 8),
                     3300000 + 100 * math.sin(2 * math.pi * i / 8)) for i in range(8)]])
for count in (100, 1000):
    ds = ogr.GetDriverByName('GPKG').CreateDataSource(str(root / ('values%d.gpkg' % count)))
    layer = ds.CreateLayer('input', srs=srs, geom_type=ogr.wkbPoint)
    layer.CreateField(ogr.FieldDefn('value', ogr.OFTReal)); layer.CreateField(ogr.FieldDefn('grp', ogr.OFTString))
    for i in range(count):
        f = ogr.Feature(layer.GetLayerDefn()); f.SetField('value', 0.1); f.SetField('grp', 'A')
        f.SetGeometry(ogr.CreateGeometryFromWkt('POINT (500000 3000000)')); layer.CreateFeature(f)
    ds.Close()
before = {p.name: p.read_bytes() for p in root.glob('*.gpkg')}
from qgis.core import QgsApplication
app = QgsApplication([], False, str(profile)); app.initQgis()
sys.path.append(str(Path(QgsApplication.pkgDataPath()) / 'python/plugins'))
from processing.core.Processing import Processing
Processing.initialize(); import processing
def run(name, algorithm, **params):
    path = str(root / (name + '.gpkg')); processing.run(algorithm, dict(params, OUTPUT=path)); return path
clip = run('clip', 'native:clip', INPUT=str(root / 'irregular.gpkg'), OVERLAY=str(root / 'mask.gpkg'))
run('dissolve', 'native:dissolve', INPUT=clip, FIELD=['grp'], SEPARATE_DISJOINT=False)
run('irregular_area', 'qgis:exportaddgeometrycolumns', INPUT=clip, CALC_METHOD=0)
run('octagon_area', 'qgis:exportaddgeometrycolumns', INPUT=str(root / 'octagon.gpkg'), CALC_METHOD=0)
for count in (100, 1000):
    source = str(root / ('values%d.gpkg' % count))
    run('basic%d' % count, 'native:basicstatisticsforfields', INPUT_LAYER=source, FIELD_NAME='value')
    run('category%d' % count, 'qgis:statisticsbycategories', INPUT=source,
        VALUES_FIELD_NAME='value', CATEGORIES_FIELD_NAME=['grp'])
app.exitQgis()
assert before == {name: (root / name).read_bytes() for name in before}
'''
    proc = subprocess.run(["/usr/bin/python3", "-c", worker, str(root)], text=True, capture_output=True,
                          check=False, timeout=90, env=dict(os.environ, DATABASE_URL="", PYTHON_DOTENV_DISABLED="1"))
    assert proc.returncode == 0, proc.stderr
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("*.gpkg")}
    yield root
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("*.gpkg")}
    assert not [p for p in root.rglob("*") if p.name.endswith(("-wal", "-shm", "-journal"))]


@pytest.mark.parametrize("operation", ["clip", "dissolve", "irregular_area", "octagon_area"])
def test_roundoff_normal_non_axis_aligned_geometry_passes(roundoff_gis, operation):
    root = roundoff_gis
    output = root / f"{operation}.gpkg"
    if operation == "clip":
        source, mask = root / "irregular.gpkg", root / "mask.gpkg"
        algorithm, params = "native:clip", {"INPUT": str(source), "OVERLAY": str(mask)}
        inputs = {"src": str(source), "mask": str(mask)}
    elif operation == "dissolve":
        source = root / "clip.gpkg"
        algorithm, params = "native:dissolve", {"INPUT": str(source), "FIELD": ["grp"], "SEPARATE_DISJOINT": False}
        inputs = {"src": str(source)}
    else:
        source = root / ("octagon.gpkg" if operation == "octagon_area" else "clip.gpkg")
        algorithm, params = "qgis:exportaddgeometrycolumns", {"INPUT": str(source), "CALC_METHOD": 0}
        inputs = {"src": str(source)}
    report = step(algorithm, params, inputs, output)
    assert report["status"] == "passed", report
    if operation == "octagon_area":
        # Independent regular-octagon area, not a second Processing run.
        area = vector_rows(output)[0]["attributes"]["area"]
        assert area == pytest.approx(2 * math.sqrt(2) * 100 ** 2, rel=1e-11)


@pytest.mark.parametrize("count", [100, 1000])
@pytest.mark.parametrize("categorized", [False, True])
def test_roundoff_decimal_sum_passes_and_retains_actual_table(roundoff_gis, tmp_path, count, categorized):
    root = roundoff_gis
    source = root / f"values{count}.gpkg"
    output = root / f"{'category' if categorized else 'basic'}{count}.gpkg"
    if categorized:
        algorithm = "qgis:statisticsbycategories"
        params = {"INPUT": str(source), "VALUES_FIELD_NAME": "value", "CATEGORIES_FIELD_NAME": ["grp"]}
    else:
        algorithm = "native:basicstatisticsforfields"
        params = {"INPUT_LAYER": str(source), "FIELD_NAME": "value"}
    actual = vector_rows(output)[0]["attributes"]["sum"]
    assert actual == pytest.approx(count * 0.1, rel=1e-12)
    report = step(algorithm, params, {"src": str(source)}, output)
    assert report["status"] == "passed", report
    assert report["facts"]["statistics"]["sum"] == actual
    if categorized:
        assert report["facts"]["statistics"]["groups"][0]["sum"] == actual
    for name, value in (("sum", count * 0.1 + 1e-5), ("sum", 0.0),
                        ("min", math.nextafter(0.1, math.inf)), ("max", math.nextafter(0.1, math.inf))):
        wrong = tmp_path / f"bad_{name}_{value}.gpkg"
        shutil.copyfile(output, wrong)
        _set_stats(wrong, **{name: value})
        assert step(algorithm, params, {"src": str(source)}, wrong)["status"] == "failed"


@pytest.mark.parametrize("field", ["area", "perimeter"])
def test_roundoff_geometry_measurements_still_reject_errors(roundoff_gis, tmp_path, field):
    source = roundoff_gis / "octagon.gpkg"
    output = roundoff_gis / "octagon_area.gpkg"
    actual = vector_rows(output)[0]["attributes"][field]
    params = {"INPUT": str(source), "CALC_METHOD": 0}
    for i, value in enumerate((0.0, actual * (1 + 1e-7))):
        wrong = tmp_path / f"bad_{i}.gpkg"
        shutil.copyfile(output, wrong)
        edit_vector(wrong, f"f.SetField({field!r}, {value!r})")
        assert step("qgis:exportaddgeometrycolumns", params, {"src": str(source)}, wrong)["status"] == "failed"


@pytest.mark.parametrize("categorized", [False, True])
def test_roundoff_unreliable_cancellation_rejected_before_writing(roundoff_gis, tmp_path, categorized):
    source, output = tmp_path / "cancellation.gpkg", tmp_path / "stats.gpkg"
    shutil.copyfile(roundoff_gis / "values100.gpkg", source)
    edit_vector(source, "f.SetField('value', 1e16)", "f.GetFID() == 1")
    edit_vector(source, "f.SetField('value', -1e16)", "f.GetFID() == 2")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    if categorized:
        algorithm = "qgis:statisticsbycategories"
        params = {"INPUT": str(source), "VALUES_FIELD_NAME": "value", "CATEGORIES_FIELD_NAME": ["grp"], "OUTPUT": str(output)}
    else:
        algorithm = "native:basicstatisticsforfields"
        params = {"INPUT_LAYER": str(source), "FIELD_NAME": "value", "OUTPUT": str(output)}
    report = inspect(plan({"src": source}, [(algorithm, ["src"], "out", params)]))
    assert report["status"] == "failed", report
    assert "precision" in report["errors"][0].lower()
    assert not output.exists() and hashlib.sha256(source.read_bytes()).hexdigest() == before


@pytest.fixture(scope="module")
def accumulation_gis(tmp_path_factory):
    root = tmp_path_factory.mktemp("t05-accumulation-gis")
    worker = r'''
import sys
from pathlib import Path
from osgeo import ogr, osr
root = Path(sys.argv[1]); srs = osr.SpatialReference(); srs.ImportFromEPSG(32650)
cases = [
    ('real_bad_zero', ogr.OFTReal, [1e16, 1., -1e16, -1.]),
    ('integer_bad', ogr.OFTInteger64, [2**53, 1, -(2**53)]),
    ('real_good_zero', ogr.OFTReal, [0.1, 0.1, -0.1, -0.1]),
    ('integer_good_zero', ogr.OFTInteger64, [2**53, 2, -(2**53), -2]),
    ('integer_good', ogr.OFTInteger64, [2**53, 2]),
    ('all_zero', ogr.OFTReal, [0., 0.]),
    ('empty', ogr.OFTReal, []),
    ('subnormal_one', ogr.OFTReal, [5e-324]),
    ('subnormal_two', ogr.OFTReal, [5e-324, 5e-324]),
]
for name, typ, values in cases:
    ds = ogr.GetDriverByName('GPKG').CreateDataSource(str(root / (name + '.gpkg')))
    layer = ds.CreateLayer('source', srs=srs, geom_type=ogr.wkbPoint)
    layer.CreateField(ogr.FieldDefn('value', typ)); layer.CreateField(ogr.FieldDefn('grp', ogr.OFTString))
    for i, value in enumerate(values):
        f = ogr.Feature(layer.GetLayerDefn()); f.SetField('value', value); f.SetField('grp', 'A')
        f.SetGeometry(ogr.CreateGeometryFromWkt('POINT (500000 %d)' % i)); layer.CreateFeature(f)
    ds.Close()
'''
    subprocess.run(["/usr/bin/python3", "-c", worker, str(root)], check=True, text=True, capture_output=True)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("*.gpkg")}
    yield root
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("*.gpkg")}
    assert not [p for p in root.rglob("*") if p.name.endswith(("-wal", "-shm", "-journal"))]


@pytest.mark.parametrize("categorized", [False, True])
@pytest.mark.parametrize("name", ["real_bad_zero", "integer_bad", "real_good_zero", "integer_good_zero",
                                  "integer_good", "all_zero", "empty", "subnormal_one", "subnormal_two"])
def test_accumulation_precision_is_checked_before_writing(accumulation_gis, tmp_path, name, categorized):
    source, output = accumulation_gis / f"{name}.gpkg", tmp_path / "statistics.gpkg"
    if categorized:
        algorithm = "qgis:statisticsbycategories"
        params = {"INPUT": str(source), "VALUES_FIELD_NAME": "value", "CATEGORIES_FIELD_NAME": ["grp"], "OUTPUT": str(output)}
    else:
        algorithm = "native:basicstatisticsforfields"
        params = {"INPUT_LAYER": str(source), "FIELD_NAME": "value", "OUTPUT": str(output)}
    report = inspect(plan({"src": source}, [(algorithm, ["src"], "out", params)]))
    assert report["status"] == ("failed" if name in ("real_bad_zero", "integer_bad") else "passed"), report
    if report["status"] == "failed":
        assert "precision" in report["errors"][0].lower()
    assert not output.exists()


def test_subnormal_sum_preserves_supported_scale_without_large_ulp_errors(accumulation_gis, gis, tmp_path):
    for name, expected in (("subnormal_one", 5e-324), ("subnormal_two", 1e-323)):
        source = accumulation_gis / f"{name}.gpkg"
        output = tmp_path / f"{name}_table.gpkg"
        shutil.copyfile(gis / "basic.gpkg", output)
        count = 1 if name == "subnormal_one" else 2
        _set_stats(output, count=count, empty=0, filled=count, sum=expected, min=5e-324, max=5e-324)
        params = {"INPUT_LAYER": str(source), "FIELD_NAME": "value"}
        report = step("native:basicstatisticsforfields", params, {"src": str(source)}, output)
        assert report["status"] == "passed", report
        assert report["facts"]["statistics"]["sum"] == expected
        for wrong_sum in (0.0, 4.4e-323, math.nextafter(expected, math.inf)):
            _set_stats(output, sum=wrong_sum)
            report = step("native:basicstatisticsforfields", params, {"src": str(source)}, output)
            assert report["status"] == "failed", report
