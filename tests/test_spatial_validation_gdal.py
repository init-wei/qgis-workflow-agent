"""Spatial results checked through the public module and system GDAL, without QGIS."""

import asyncio
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import subprocess

import pytest

from agent.planning.plan_schema import InputSpec, OutputSpec, PlanStep, TaskPlan, ToolCallPlan
from agent.spatial_validation import preflight_spatial_plan, validate_spatial_step


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]


def chain(source, projected, buffered, *, target="EPSG:32650", distance=100):
    steps = [
        PlanStep(step_id=1, description="Project", input_refs=["source"], output_refs=["projected"],
                 risk_level="low", requires_review=False),
        PlanStep(step_id=2, description="Buffer", input_refs=["projected"], output_refs=["buffered"],
                 risk_level="low", requires_review=False),
    ]
    calls = [
        ToolCallPlan(step_id=1, tool_name="execute_processing", arguments={
            "algorithm": "native:reprojectlayer", "parameters": {
                "INPUT": str(source), "TARGET_CRS": target, "OUTPUT": str(projected),
            },
        }),
        ToolCallPlan(step_id=2, tool_name="execute_processing", distance_unit="m", arguments={
            "algorithm": "native:buffer", "parameters": {
                "INPUT": str(projected), "OUTPUT": str(buffered), "DISTANCE": distance,
                "SEGMENTS": 8, "END_CAP_STYLE": 0, "JOIN_STYLE": 0,
                "MITER_LIMIT": 2.0, "DISSOLVE": False, "SEPARATE_DISJOINT": False,
            },
        }),
    ]
    return TaskPlan(
        task_id="spatial", task_summary="Project and buffer", steps=steps,
        risk_level="low", review_status="approved",
        required_inputs=[InputSpec(input_id="source", description="Source", path=str(source))],
        expected_outputs=[OutputSpec(output_id="projected", description="Projected", path=str(projected)),
                          OutputSpec(output_id="buffered", description="Buffer", path=str(buffered))],
        tool_calls=calls,
    )


def test_preflight_rejects_future_geographic_buffer_without_writing(tmp_path):
    source = ROOT / "tests/fixtures/gis/phase11_points.geojson"
    projected, buffered = tmp_path / "projected.gpkg", tmp_path / "buffer.gpkg"
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="(?i)(metre|meter)"):
        asyncio.run(preflight_spatial_plan(chain(source, projected, buffered, target="EPSG:4326")))

    assert not projected.exists() and not buffered.exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert list(tmp_path.iterdir()) == []


def write_geojson(path, crs, features):
    path.write_text(json.dumps({
        "type": "FeatureCollection", "name": path.stem,
        "crs": {"type": "name", "properties": {"name": crs}}, "features": features,
    }))


def in_utm_zone_50(features):
    """Translate the analytic planar examples into a realistic UTM 50N location."""
    shifted = deepcopy(features)
    def translate(coordinates):
        if isinstance(coordinates[0], (int, float)):
            return [coordinates[0] + 500000, coordinates[1] + 4000000]
        return [translate(item) for item in coordinates]
    for feature in shifted:
        geometry = feature["geometry"]
        if geometry is not None:
            geometry["coordinates"] = translate(geometry["coordinates"])
    return shifted


def point_feature(attributes, coordinates):
    return {"type": "Feature", "properties": attributes,
            "geometry": {"type": "Point", "coordinates": coordinates}}


def oracle():
    return json.loads((ROOT / "tests/fixtures/gis/reprojection-buffer-oracle.json").read_text())


def test_reprojection_checks_each_known_coordinate_and_rejects_plausible_wrong_file(tmp_path):
    data = oracle()
    source = ROOT / data["input"]["path"]
    projected, buffered = tmp_path / "projected.geojson", tmp_path / "buffered.geojson"
    plan = chain(source, projected, buffered)
    rows = data["reprojection"]["features"]
    features = [point_feature(row["attributes"], row["expected_coordinates_m"]) for row in rows]
    write_geojson(projected, data["reprojection"]["target_crs"], features)
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    async def validate():
        return await validate_spatial_step(plan.tool_calls[0], input_paths={"source": str(source)},
                                           output_paths={"projected": str(projected)})

    report = asyncio.run(validate())
    assert report["status"] == "passed", report
    assert report["facts"]["input"]["feature_count"] == 3
    assert report["facts"]["output"]["crs"] == "EPSG:32650"

    features[1]["geometry"]["coordinates"][0] += 20
    write_geojson(projected, "EPSG:32650", features)
    wrong = asyncio.run(validate())
    assert wrong["status"] == "failed"
    assert wrong["errors"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_three_point_buffers_match_independent_radius_and_area_and_reject_wrong_radius(tmp_path):
    data = oracle()
    rows = data["reprojection"]["features"]
    projected, buffered = tmp_path / "projected.geojson", tmp_path / "buffered.geojson"
    plan = chain(ROOT / data["input"]["path"], projected, buffered)
    write_geojson(projected, "EPSG:32650", [
        point_feature(row["attributes"], row["expected_coordinates_m"]) for row in rows
    ])
    expected = data["buffer"]["expected_per_feature"]

    def circles(radius):
        features = []
        for row in rows:
            x, y = row["expected_coordinates_m"]
            vertices = [[x + radius * math.cos(2 * math.pi * n / 32),
                         y + radius * math.sin(2 * math.pi * n / 32)] for n in range(32)]
            vertices.append(vertices[0])
            features.append({"type": "Feature", "properties": row["attributes"],
                             "geometry": {"type": "Polygon", "coordinates": [vertices]}})
        return features

    assert expected["area_m2"] == pytest.approx(31214.451523, abs=0.001)
    multi_features = circles(100)
    for feature in multi_features:
        feature["geometry"] = {"type": "MultiPolygon", "coordinates": [feature["geometry"]["coordinates"]]}
    write_geojson(buffered, "EPSG:32650", multi_features)

    async def validate():
        return await validate_spatial_step(plan.tool_calls[1], input_paths={"projected": str(projected)},
                                           output_paths={"buffered": str(buffered)})

    report = asyncio.run(validate())
    assert report["status"] == "passed", report
    assert report["facts"]["output"]["feature_count"] == 3
    write_geojson(buffered, "EPSG:32650", circles(95))
    wrong = asyncio.run(validate())
    assert wrong["status"] == "failed"
    assert wrong["errors"]


def test_line_flat_cap_polygon_mitre_and_dissolve_use_expected_analytic_shapes(tmp_path):
    source, output = tmp_path / "input.geojson", tmp_path / "result.geojson"
    plan = chain(source, tmp_path / "unused.geojson", output)
    call = plan.tool_calls[1]
    call.arguments["parameters"].update({"END_CAP_STYLE": 1, "JOIN_STYLE": 1})

    def validate():
        return asyncio.run(validate_spatial_step(call, input_paths={"source": str(source)},
                                                 output_paths={"result": str(output)}))

    line = {"type": "Feature", "properties": {"id": 7},
            "geometry": {"type": "LineString", "coordinates": [[0, 0], [1000, 0]]}}
    rectangle = {"type": "Feature", "properties": {"id": 7}, "geometry": {
        "type": "Polygon", "coordinates": [[[0, -100], [1000, -100],
                                                [1000, 100], [0, 100], [0, -100]]]}}
    write_geojson(source, "EPSG:32650", in_utm_zone_50([line]))
    write_geojson(output, "EPSG:32650", in_utm_zone_50([rectangle]))
    report = validate()
    assert report["status"] == "passed", report

    square = {"type": "Feature", "properties": {"id": 7}, "geometry": {
        "type": "Polygon", "coordinates": [[[0, 0], [200, 0], [200, 200], [0, 200], [0, 0]]]}}
    buffered_square = {"type": "Feature", "properties": {"id": 7}, "geometry": {
        "type": "Polygon", "coordinates": [[[-100, -100], [300, -100], [300, 300],
                                                [-100, 300], [-100, -100]]]}}
    write_geojson(source, "EPSG:32650", in_utm_zone_50([square]))
    write_geojson(output, "EPSG:32650", in_utm_zone_50([buffered_square]))
    report = validate()
    assert report["status"] == "passed", report

    line2 = {"type": "Feature", "properties": {"id": 8},
             "geometry": {"type": "LineString", "coordinates": [[500, 0], [1500, 0]]}}
    dissolved = {"type": "Feature", "properties": {"id": 7}, "geometry": {
        "type": "Polygon", "coordinates": [[[0, -100], [1500, -100],
                                                [1500, 100], [0, 100], [0, -100]]]}}
    call.arguments["parameters"]["DISSOLVE"] = True
    write_geojson(source, "EPSG:32650", in_utm_zone_50([line, line2]))
    write_geojson(output, "EPSG:32650", in_utm_zone_50([dissolved]))
    report = validate()
    assert report["status"] == "passed", report
    assert any(check["check"] == "analytic_dissolved_area" for check in report["checks"])
    dissolved["geometry"]["coordinates"][0][1][0] = 1400
    write_geojson(output, "EPSG:32650", in_utm_zone_50([dissolved]))
    assert validate()["status"] == "failed"


def test_preflight_accepts_meter_projection_and_rejects_unverifiable_later_algorithm(tmp_path):
    source = ROOT / "tests/fixtures/gis/phase11_points.geojson"
    plan = chain(source, tmp_path / "projected.gpkg", tmp_path / "buffered.gpkg")
    report = asyncio.run(preflight_spatial_plan(plan))
    assert report["status"] == "passed"
    assert report["facts"]["inputs"][0]["crs"] == "EPSG:4326"

    plan.tool_calls[1].arguments["algorithm"] = "native:clip"
    with pytest.raises(ValueError, match="(?i)(not yet|unavailable|unsupported)"):
        asyncio.run(preflight_spatial_plan(plan))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("change", ["wrong_crs", "missing_field", "wrong_attribute", "missing_file"])
def test_reprojection_rejects_readable_but_wrong_outputs_and_missing_file(tmp_path, change):
    data = oracle()
    source = ROOT / data["input"]["path"]
    output = tmp_path / "projected.geojson"
    plan = chain(source, output, tmp_path / "buffered.geojson")
    features = [point_feature(row["attributes"].copy(), row["expected_coordinates_m"][:])
                for row in data["reprojection"]["features"]]
    if change == "missing_field":
        for feature in features:
            feature["properties"].pop("group")
    if change == "wrong_attribute":
        features[0]["properties"]["value"] = 100
    if change != "missing_file":
        write_geojson(output, "EPSG:4326" if change == "wrong_crs" else "EPSG:32650", features)
    report = asyncio.run(validate_spatial_step(plan.tool_calls[0], input_paths={"source": str(source)},
                                               output_paths={"projected": str(output)}))
    assert report["status"] == "failed"
    assert report["errors"]


@pytest.mark.parametrize("change", ["wrong_crs", "missing_field", "wrong_attribute"])
def test_buffer_rejects_mismatched_crs_or_attributes(tmp_path, change):
    source, output = tmp_path / "line.geojson", tmp_path / "buffer.geojson"
    plan = chain(source, tmp_path / "unused.geojson", output)
    call = plan.tool_calls[1]
    call.arguments["parameters"]["END_CAP_STYLE"] = 1
    line = {"type": "Feature", "properties": {"id": 7, "name": "road"},
            "geometry": {"type": "LineString", "coordinates": [[0, 0], [1000, 0]]}}
    properties = {"id": 7, "name": "road"}
    if change == "wrong_attribute":
        properties["name"] = "wrong"
    if change == "missing_field":
        properties.pop("name")
    rectangle = {"type": "Feature", "properties": properties, "geometry": {
        "type": "Polygon", "coordinates": [[[0, -100], [1000, -100],
                                                [1000, 100], [0, 100], [0, -100]]]}}
    write_geojson(source, "EPSG:32650", in_utm_zone_50([line]))
    write_geojson(output, "EPSG:4326" if change == "wrong_crs" else "EPSG:32650", in_utm_zone_50([rectangle]))
    report = asyncio.run(validate_spatial_step(call, input_paths={"source": str(source)},
                                               output_paths={"result": str(output)}))
    assert report["status"] == "failed"
    assert report["errors"]


@pytest.mark.parametrize("target", ["EPSG:2263", "EPSG:3857", "not-a-crs"])
def test_preflight_rejects_bad_or_unsuitable_future_crs(tmp_path, target):
    source = ROOT / "tests/fixtures/gis/phase11_points.geojson"
    plan = chain(source, tmp_path / "projected.gpkg", tmp_path / "buffer.gpkg", target=target)
    with pytest.raises(ValueError):
        asyncio.run(preflight_spatial_plan(plan))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("parameter,value", [
    ("DISTANCE", "100"), ("DISTANCE", True),
    ("SEGMENTS", 0), ("SEGMENTS", 3.2), ("END_CAP_STYLE", 4), ("END_CAP_STYLE", 0.0),
    ("JOIN_STYLE", -1), ("JOIN_STYLE", 0.0), ("MITER_LIMIT", 0), ("DISSOLVE", "false"),
])
def test_bad_buffer_parameters_are_rejected_before_all_writes(tmp_path, parameter, value):
    source = ROOT / "tests/fixtures/gis/phase11_points.geojson"
    plan = chain(source, tmp_path / "projected.gpkg", tmp_path / "buffer.gpkg")
    plan.tool_calls[1].arguments["parameters"][parameter] = value
    with pytest.raises(ValueError):
        asyncio.run(preflight_spatial_plan(plan))
    assert list(tmp_path.iterdir()) == []


def test_negative_polygon_buffer_preserves_exact_square_erosion_and_rejects_wrong_size(tmp_path):
    source, output = tmp_path / "square.geojson", tmp_path / "eroded.geojson"
    call = chain(source, tmp_path / "unused.geojson", output).tool_calls[1]
    call.arguments["parameters"]["DISTANCE"] = -10
    square = {"type": "Feature", "properties": {"id": 7}, "geometry": {
        "type": "Polygon", "coordinates": [[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]]}}
    inner = {"type": "Feature", "properties": {"id": 7}, "geometry": {
        "type": "Polygon", "coordinates": [[[10, 10], [90, 10], [90, 90], [10, 90], [10, 10]]]}}
    write_geojson(source, "EPSG:32650", in_utm_zone_50([square]))
    write_geojson(output, "EPSG:32650", in_utm_zone_50([inner]))

    def validate():
        return asyncio.run(validate_spatial_step(call, input_paths={"source": str(source)},
                                                 output_paths={"result": str(output)}))

    report = validate()
    assert report["status"] == "passed", report
    inner["geometry"]["coordinates"][0] = [[15, 15], [85, 15], [85, 85], [15, 85], [15, 15]]
    write_geojson(output, "EPSG:32650", in_utm_zone_50([inner]))
    assert validate()["status"] == "failed"


@pytest.mark.parametrize("geometry", [
    {"type": "Point", "coordinates": [0, 0]},
    {"type": "LineString", "coordinates": [[0, 0], [100, 0]]},
])
def test_negative_point_or_line_buffer_is_an_independently_known_empty_result(tmp_path, geometry):
    source, output = tmp_path / "input.geojson", tmp_path / "empty.geojson"
    call = chain(source, tmp_path / "unused.geojson", output).tool_calls[1]
    call.arguments["parameters"]["DISTANCE"] = -10
    write_geojson(source, "EPSG:32650", in_utm_zone_50([{"type": "Feature", "properties": {"id": 1},
                                          "geometry": geometry}]))
    write_geojson(output, "EPSG:32650", [{"type": "Feature", "properties": {"id": 1},
                                          "geometry": None}])
    report = asyncio.run(validate_spatial_step(call, input_paths={"source": str(source)},
                                               output_paths={"result": str(output)}))
    assert report["status"] == "passed", report
    assert report["facts"]["output"]["empty_geometries"] == 1
    assert any(check["check"] == "independently_verified_empty_geometry" for check in report["checks"])


def test_buffer_distance_unit_must_be_explicitly_reviewed_before_writes(tmp_path):
    source = ROOT / "tests/fixtures/gis/phase11_points.geojson"
    plan = chain(source, tmp_path / "projected.gpkg", tmp_path / "buffer.gpkg")
    plan.tool_calls[1].distance_unit = None
    with pytest.raises(ValueError, match="(?i)(unit|metre|meter)"):
        asyncio.run(preflight_spatial_plan(plan))
    assert list(tmp_path.iterdir()) == []


def test_preflight_rejects_wrong_utm_zone_even_though_its_units_are_metres(tmp_path):
    source = ROOT / "tests/fixtures/gis/phase11_points.geojson"
    plan = chain(source, tmp_path / "projected.gpkg", tmp_path / "buffer.gpkg", target="EPSG:32601")
    with pytest.raises(ValueError, match="(?i)(area|range|zone)"):
        asyncio.run(preflight_spatial_plan(plan))
    assert list(tmp_path.iterdir()) == []


def create_geopackage(path, *, with_crs=True, layer_names=("points",), geometry="point"):
    script = r"""
import sys
from osgeo import ogr, osr
ogr.UseExceptions()
osr.UseExceptions()
ds = ogr.GetDriverByName("GPKG").CreateDataSource(sys.argv[1])
srs = None
if sys.argv[2] == "yes":
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32650)
for name in sys.argv[4].split(","):
    layer = ds.CreateLayer(name, srs=srs, geom_type=ogr.wkbPoint if sys.argv[3] == "point" else ogr.wkbPolygon)
    layer.CreateField(ogr.FieldDefn("id", ogr.OFTInteger))
ds = None
"""
    subprocess.run(["/usr/bin/python3", "-c", script, str(path), "yes" if with_crs else "no",
                    geometry, ",".join(layer_names)], check=True, capture_output=True, text=True, timeout=15)


def test_empty_geopackage_is_legitimate_and_missing_output_is_failure(tmp_path):
    source, projected, buffered = (tmp_path / "input.gpkg", tmp_path / "projected.gpkg",
                                   tmp_path / "buffer.gpkg")
    create_geopackage(source)
    create_geopackage(projected)
    create_geopackage(buffered, geometry="polygon")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    plan = chain(source, projected, buffered)
    plan.tool_calls[0].arguments["parameters"]["TARGET_CRS"] = "EPSG:32650"
    assert asyncio.run(preflight_spatial_plan(plan))["status"] == "passed"
    reproject = asyncio.run(validate_spatial_step(plan.tool_calls[0], input_paths={"source": str(source)},
                                                  output_paths={"projected": str(projected)}))
    buffer = asyncio.run(validate_spatial_step(plan.tool_calls[1], input_paths={"projected": str(projected)},
                                               output_paths={"buffered": str(buffered)}))
    assert reproject["status"] == buffer["status"] == "passed", (reproject, buffer)
    assert buffer["facts"]["output"]["feature_count"] == 0
    assert any(check["check"] == "empty_input_empty_output" for check in buffer["checks"])
    absent = asyncio.run(validate_spatial_step(plan.tool_calls[1], input_paths={"projected": str(projected)},
                                               output_paths={"buffered": str(tmp_path / "missing.gpkg")}))
    assert absent["status"] == "failed"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["buffer.gpkg", "input.gpkg", "projected.gpkg"]


def test_unset_geopackage_crs_and_unselected_multilayer_fail_before_writes(tmp_path):
    missing_crs = tmp_path / "missing-crs.gpkg"
    create_geopackage(missing_crs, with_crs=False)
    output, buffer = tmp_path / "projected.gpkg", tmp_path / "buffer.gpkg"
    with pytest.raises(ValueError, match="(?i)crs"):
        asyncio.run(preflight_spatial_plan(chain(missing_crs, output, buffer)))
    multi = tmp_path / "multiple.gpkg"
    create_geopackage(multi, layer_names=("points", "other"))
    with pytest.raises(ValueError, match="(?i)select.*layer"):
        asyncio.run(preflight_spatial_plan(chain(multi, output, buffer)))
    selected = chain(multi, output, buffer)
    selected.required_inputs[0].layer_name = "points"
    assert asyncio.run(preflight_spatial_plan(selected))["status"] == "passed"
    assert not output.exists() and not buffer.exists()


def test_system_cli_returns_nonzero_json_on_invalid_request():
    proc = subprocess.run(["/usr/bin/python3", str(ROOT / "scripts/validate_spatial.py")],
                          input="not json", text=True, capture_output=True, timeout=15)
    report = json.loads(proc.stdout)
    assert proc.returncode != 0
    assert report["status"] == "failed"
    assert report["errors"]


def test_unknown_complex_erosion_empty_is_not_falsely_accepted(tmp_path):
    source, output = tmp_path / "triangle.geojson", tmp_path / "empty.geojson"
    call = chain(source, tmp_path / "unused.geojson", output).tool_calls[1]
    call.arguments["parameters"]["DISTANCE"] = -100
    triangle = {"type": "Feature", "properties": {"id": 1}, "geometry": {
        "type": "Polygon", "coordinates": [[[0, 0], [30, 0], [0, 30], [0, 0]]]}}
    write_geojson(source, "EPSG:32650", in_utm_zone_50([triangle]))
    write_geojson(output, "EPSG:32650", [{"type": "Feature", "properties": {"id": 1},
                                          "geometry": None}])
    report = asyncio.run(validate_spatial_step(call, input_paths={"source": str(source)},
                                               output_paths={"result": str(output)}))
    assert report["status"] == "failed"
    assert any("independently" in error for error in report["errors"])


def test_positive_point_with_flat_cap_has_explained_empty_output(tmp_path):
    source, output = tmp_path / "point.geojson", tmp_path / "empty.geojson"
    call = chain(source, tmp_path / "unused.geojson", output).tool_calls[1]
    call.arguments["parameters"]["END_CAP_STYLE"] = 1
    point = {"type": "Feature", "properties": {"id": 1},
             "geometry": {"type": "Point", "coordinates": [0, 0]}}
    write_geojson(source, "EPSG:32650", in_utm_zone_50([point]))
    write_geojson(output, "EPSG:32650", [{"type": "Feature", "properties": {"id": 1},
                                          "geometry": None}])
    report = asyncio.run(validate_spatial_step(call, input_paths={"source": str(source)},
                                               output_paths={"result": str(output)}))
    assert report["status"] == "passed", report
    assert any(check["check"] == "independently_verified_empty_geometry" for check in report["checks"])


def test_actual_buffer_input_outside_crs_area_of_use_is_rejected(tmp_path):
    source, output = tmp_path / "outside.geojson", tmp_path / "buffer.geojson"
    call = chain(source, tmp_path / "unused.geojson", output).tool_calls[1]
    # An easting of zero is outside the stated EPSG:32650 zone, even though its units are metres.
    line = {"type": "Feature", "properties": {"id": 1},
            "geometry": {"type": "LineString", "coordinates": [[0, 0], [100, 0]]}}
    rectangle = {"type": "Feature", "properties": {"id": 1}, "geometry": {
        "type": "Polygon", "coordinates": [[[0, -100], [100, -100], [100, 100], [0, 100], [0, -100]]]}}
    write_geojson(source, "EPSG:32650", [line])
    write_geojson(output, "EPSG:32650", [rectangle])
    report = asyncio.run(validate_spatial_step(call, input_paths={"source": str(source)},
                                               output_paths={"result": str(output)}))
    assert report["status"] == "failed"
    assert any("area of use" in error for error in report["errors"])


def test_reprojection_verifies_line_and_polygon_vertices_and_geometry(tmp_path):
    source, output = tmp_path / "mixed.geojson", tmp_path / "reprojected.geojson"
    call = chain(source, output, tmp_path / "unused.geojson").tool_calls[0]
    call.arguments["parameters"]["TARGET_CRS"] = "EPSG:32650"
    line = {"type": "Feature", "properties": {"id": 1},
            "geometry": {"type": "LineString", "coordinates": [[0, 0], [100, 0]]}}
    polygon = {"type": "Feature", "properties": {"id": 2}, "geometry": {
        "type": "Polygon", "coordinates": [[[0, 0], [200, 0], [200, 200], [0, 200], [0, 0]]]}}
    returned_line = {"type": "Feature", "properties": {"id": 1},
                     "geometry": {"type": "LineString", "coordinates": [[100, 0], [0, 0]]}}
    returned_polygon = {"type": "Feature", "properties": {"id": 2}, "geometry": {
        "type": "Polygon", "coordinates": [[[200, 200], [200, 0], [0, 0], [0, 200], [200, 200]]]}}
    write_geojson(source, "EPSG:32650", in_utm_zone_50([line, polygon]))
    write_geojson(output, "EPSG:32650", in_utm_zone_50([returned_polygon, returned_line]))

    def validate():
        return asyncio.run(validate_spatial_step(call, input_paths={"source": str(source)},
                                                 output_paths={"result": str(output)}))

    report = validate()
    assert report["status"] == "passed", report
    returned_line["geometry"]["coordinates"][0][0] += 5
    write_geojson(output, "EPSG:32650", in_utm_zone_50([returned_polygon, returned_line]))
    assert validate()["status"] == "failed"


def write_geopackage_features(path, features, geometry_type):
    script = r"""
import json, sys
from osgeo import ogr, osr
ogr.UseExceptions()
osr.UseExceptions()
features = json.load(sys.stdin)
ds = ogr.GetDriverByName("GPKG").CreateDataSource(sys.argv[1])
srs = osr.SpatialReference()
srs.ImportFromEPSG(32650)
layer = ds.CreateLayer("result", srs=srs,
                       geom_type=ogr.wkbPoint if sys.argv[2] == "point" else ogr.wkbMultiPolygon)
for name, kind in [("id", ogr.OFTInteger), ("group", ogr.OFTString),
                   ("value", ogr.OFTInteger), ("driver_added", ogr.OFTInteger)]:
    layer.CreateField(ogr.FieldDefn(name, kind))
for item in features:
    feature = ogr.Feature(layer.GetLayerDefn())
    for name, value in item["properties"].items():
        feature.SetField(name, value)
    feature.SetField("driver_added", 42)
    feature.SetGeometry(ogr.CreateGeometryFromJson(json.dumps(item["geometry"])))
    layer.CreateFeature(feature)
ds = None
"""
    subprocess.run(["/usr/bin/python3", "-c", script, str(path), geometry_type],
                   input=json.dumps(features), check=True, capture_output=True, text=True, timeout=15)


def test_nonempty_geopackage_chain_against_frozen_numerical_oracle(tmp_path):
    data = oracle()
    source = ROOT / data["input"]["path"]
    projected, buffered = tmp_path / "projected.gpkg", tmp_path / "buffered.gpkg"
    plan = chain(source, projected, buffered)
    assert asyncio.run(preflight_spatial_plan(plan))["status"] == "passed"
    rows = data["reprojection"]["features"]
    write_geopackage_features(projected, [point_feature(row["attributes"], row["expected_coordinates_m"])
                                          for row in rows], "point")
    reprojection = asyncio.run(validate_spatial_step(plan.tool_calls[0], input_paths={"source": str(source)},
                                                     output_paths={"projected": str(projected)}))
    assert reprojection["status"] == "passed", reprojection
    buffers = []
    for row in rows:
        x, y = row["expected_coordinates_m"]
        ring = [[x + 100 * math.cos(2 * math.pi * n / 32), y + 100 * math.sin(2 * math.pi * n / 32)]
                for n in range(32)]
        ring.append(ring[0])
        attributes = dict(row["attributes"])
        attributes["driver_added"] = 42
        buffers.append({"type": "Feature", "properties": attributes,
                        "geometry": {"type": "MultiPolygon", "coordinates": [[ring]]}})
    write_geopackage_features(buffered, buffers, "polygon")
    validation = asyncio.run(validate_spatial_step(plan.tool_calls[1], input_paths={"projected": str(projected)},
                                                   output_paths={"buffered": str(buffered)}))
    assert validation["status"] == "passed", validation
    assert validation["facts"]["output"]["feature_count"] == 3
    assert any(check.get("features_checked") == 3 for check in validation["checks"])


def test_centimetre_buffer_rejects_a_wrong_radius_even_with_small_area(tmp_path):
    source, output = tmp_path / "point.geojson", tmp_path / "buffer.geojson"
    call = chain(source, tmp_path / "unused.geojson", output).tool_calls[1]
    call.arguments["parameters"]["DISTANCE"] = 0.01
    point = {"type": "Feature", "properties": {"id": 1},
             "geometry": {"type": "Point", "coordinates": [0, 0]}}
    def buffer(radius):
        ring = [[radius * math.cos(2 * math.pi * n / 32), radius * math.sin(2 * math.pi * n / 32)]
                for n in range(32)]
        ring.append(ring[0])
        return {"type": "Feature", "properties": {"id": 1},
                "geometry": {"type": "Polygon", "coordinates": [ring]}}

    def validate():
        return asyncio.run(validate_spatial_step(call, input_paths={"source": str(source)},
                                                 output_paths={"result": str(output)}))

    write_geojson(source, "EPSG:32650", in_utm_zone_50([point]))
    write_geojson(output, "EPSG:32650", in_utm_zone_50([buffer(0.01)]))
    report = validate()
    assert report["status"] == "passed", report
    write_geojson(output, "EPSG:32650", in_utm_zone_50([buffer(0.0095)]))
    assert validate()["status"] == "failed"
