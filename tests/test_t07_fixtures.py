"""T07 fixture contract and independent oracle through the public CLI."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/build_t07_fixtures.py"
DEFINITION = ROOT / "tests/fixtures/gis/t07_benchmark.json"
ORACLE = ROOT / "tests/fixtures/gis/t07_benchmark_oracle.json"


def cli(*args, python=sys.executable):
    return subprocess.run(
        [python, str(CLI), *map(str, args)],
        text=True,
        capture_output=True,
        check=False,
        timeout=40,
        cwd=ROOT,
    )


def test_public_validate_freezes_independent_oracle_and_request_set():
    result = cli("validate")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "passed"
    definition = json.loads(DEFINITION.read_text())
    oracle = json.loads(ORACLE.read_text())
    assert len(definition["scenarios"]) == 12
    assert sum(len(s["requests"]) for s in definition["scenarios"]) == 24
    assert len(definition["dev_references"]) == 6
    assert len(definition["exceptions"]) >= 8 and definition["browser"]
    assert [
        len(oracle["points"][k]["features"]) for k in ("west_points", "east_points")
    ] == [5, 7]
    assert all(
        len(point["buffer_vertices_m"]) == 32
        for group in oracle["points"].values()
        for point in group["features"]
    )
    assert [
        oracle["points"][k]["buffer"]["radius_m"]
        for k in ("west_points", "east_points")
    ] == [60, 175]
    assert oracle["parcels"]["areas_m2"] == [50.0, 50.0, 24.0, 24.0, 24.0]
    assert oracle["statistics"]["score"] == {
        "count": 5,
        "valid": 4,
        "null": 1,
        "sum": 14.75,
        "min": -4.5,
        "max": 12.25,
    }
    assert [v["area_m2"] for v in oracle["clip"]["features"]] == [37.5, 12.5]
    assert oracle["dissolve"]["group_area_m2"] == {"A": 100.0, "B": 48.0, "C": 24.0}
    assert oracle["dissolve"]["total_area_m2"] == 172.0


def test_public_validate_rejects_definition_or_oracle_tampering(tmp_path):
    definition = json.loads(DEFINITION.read_text())
    definition["datasets"]["west_points"]["features"][0]["geometry"]["coordinates"][
        0
    ] += 0.01
    altered_definition = tmp_path / "changed.json"
    altered_definition.write_text(json.dumps(definition))
    result = cli("validate", "--definition", altered_definition)
    assert result.returncode != 0
    assert "mismatch" in result.stderr.lower()
    oracle = json.loads(ORACLE.read_text())
    oracle["clip"]["features"][0]["area_m2"] = 0
    altered_oracle = tmp_path / "oracle.json"
    altered_oracle.write_text(json.dumps(oracle))
    result = cli("validate", "--oracle", altered_oracle)
    assert result.returncode != 0
    assert "oracle" in result.stderr.lower()


def test_public_verifier_reports_missing_gdal_as_unavailable(tmp_path):
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}")
    output = tmp_path / "report.json"
    # -S deliberately removes distro site-packages; GDAL is unavailable in
    # this clean interpreter even on hosts that have it installed system-wide.
    result = subprocess.run(
        [
            "/usr/bin/python3",
            "-S",
            str(CLI),
            "verify-result",
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--scenario",
            "clip-1",
            "--execution-json",
            str(evidence),
            "--output-json",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
    )
    assert output.exists(), result.stderr
    report = json.loads(output.read_text())
    assert result.returncode == 2, report
    assert report["status"] == "unavailable" and report["empty"] is False
    assert "osgeo" in " ".join(report["errors"])


def test_public_verifier_reports_malformed_evidence_as_unavailable(tmp_path):
    evidence = tmp_path / "evidence.json"
    evidence.write_text("not-json")
    output = tmp_path / "report.json"
    result = cli(
        "verify-result",
        "--manifest",
        tmp_path / "manifest.json",
        "--scenario",
        "clip-1",
        "--execution-json",
        evidence,
        "--output-json",
        output,
    )
    assert output.exists(), result.stderr
    report = json.loads(output.read_text())
    assert result.returncode == 2, report
    assert report["status"] == "unavailable" and report["empty"] is False
    assert "JSONDecodeError" in " ".join(report["errors"])


@pytest.mark.parametrize(
    ("group", "category"),
    [
        ("exceptions", "missing_file"),
        ("exceptions", "unknown_crs"),
        ("exceptions", "ambiguous_units"),
        ("exceptions", "invalid_parameters"),
        ("exceptions", "unsupported_algorithm"),
        ("exceptions", "qgis_interrupt"),
        ("exceptions", "unapproved"),
        ("exceptions", "rejected"),
        ("exceptions", "stale_review"),
        ("exceptions", "duplicate_resume"),
        ("browser", "plan_restart"),
        ("browser", "risk_restart"),
        ("browser", "result_restart"),
        ("browser", "switch_session"),
        ("browser", "cancel_old_stream"),
        ("browser", "text_rendering"),
        ("browser", "deep_link"),
    ],
)
def test_public_validate_rejects_missing_required_special_case(
    tmp_path, group, category
):
    import hashlib

    definition = json.loads(DEFINITION.read_text())
    definition[group] = [
        case for case in definition[group] if case.get("category") != category
    ]
    definition_path = tmp_path / "missing-case.json"
    data = json.dumps(definition, ensure_ascii=False).encode()
    definition_path.write_bytes(data)
    oracle = json.loads(ORACLE.read_text())
    oracle["definition_sha256"] = hashlib.sha256(data).hexdigest()
    oracle_path = tmp_path / "oracle.json"
    oracle_path.write_text(json.dumps(oracle, ensure_ascii=False))
    result = cli("validate", "--definition", definition_path, "--oracle", oracle_path)
    assert result.returncode != 0
    assert category in result.stderr


def test_public_validate_reports_special_cases_as_unrun():
    result = cli("validate")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    definition = json.loads(DEFINITION.read_text())
    for group in ("exceptions", "browser"):
        assert report["special_cases"][group] == {
            "defined": len(definition[group]),
            "not_run": len(definition[group]),
        }
        assert all(case["status"] == "not_run" for case in definition[group])
        assert all(
            case["category"]
            and case["input"]
            and case["actions"]
            and case["expected"]
            and case["execution"]["kind"]
            and case["execution"]["source"]
            and case["execution"]["command"]
            and case["execution"]["scope"]
            for case in definition[group]
        )
    metrics = {case["id"]: case["metrics"] for case in definition["exceptions"]}
    assert metrics["wrong-geometry"] == ["false_success"]
    assert metrics["unapproved-execution"] == ["unapproved_execution"]
    assert metrics["valid-empty-clip"] == []


@pytest.mark.parametrize(
    ("group", "missing"), [("exceptions", "actions"), ("browser", "execution")]
)
def test_public_validate_rejects_special_case_without_a_reproducible_procedure(
    tmp_path, group, missing
):
    import hashlib

    definition = json.loads(DEFINITION.read_text())
    definition[group][0].pop(missing)
    data = json.dumps(definition, ensure_ascii=False).encode()
    source = tmp_path / "incomplete.json"
    source.write_bytes(data)
    oracle = json.loads(ORACLE.read_text())
    oracle["definition_sha256"] = hashlib.sha256(data).hexdigest()
    oracle_path = tmp_path / "oracle.json"
    oracle_path.write_text(json.dumps(oracle, ensure_ascii=False))
    result = cli("validate", "--definition", source, "--oracle", oracle_path)
    assert result.returncode != 0 and "special case" in result.stderr.lower()


@pytest.mark.external
def test_materialize_writes_provenance_without_overwrite(tmp_path):
    output = tmp_path / "fixtures"
    result = cli("materialize", "--output-dir", output, python="/usr/bin/python3")
    assert result.returncode == 0, result.stderr
    manifest = json.loads((output / "manifest.json").read_text())
    assert set(manifest["datasets"]) == {
        "west_points",
        "east_points",
        "parcels",
        "left_mask",
        "far_mask",
    }
    assert all(
        (output / d["filename"]).is_file() and len(d["sha256"]) == 64
        for d in manifest["datasets"].values()
    )
    assert (
        manifest["definition_sha256"]
        and manifest["oracle_sha256"]
        and manifest["generator_sha256"]
        and manifest["snyder_helper_sha256"]
    )
    assert (
        cli("materialize", "--output-dir", output, python="/usr/bin/python3").returncode
        != 0
    )


# The fake files below are written directly from explicit coordinates and values;
# neither QGIS nor a Processing algorithm is used to create expected results.
WRITER = r"""
import json, sys
from pathlib import Path
from osgeo import ogr, osr
ogr.UseExceptions()
types = {'Integer': ogr.OFTInteger, 'Real': ogr.OFTReal, 'String': ogr.OFTString}
for spec in json.load(sys.stdin):
    path = Path(spec['path']); path.parent.mkdir(parents=True, exist_ok=True)
    ds = ogr.GetDriverByName('GPKG').CreateDataSource(str(path))
    srs = None
    if spec['crs']:
        srs = osr.SpatialReference(); srs.SetFromUserInput(spec['crs'])
    geometry = {'Point': ogr.wkbPoint, 'Polygon': ogr.wkbPolygon,
                'MultiPolygon': ogr.wkbMultiPolygon, 'None': ogr.wkbNone}[spec['geometry_type']]
    layer = ds.CreateLayer('result', srs, geometry, options=['SPATIAL_INDEX=NO'])
    for field in spec['fields']:
        layer.CreateField(ogr.FieldDefn(field['name'], types[field['type']]))
    for row in spec['features']:
        feature = ogr.Feature(layer.GetLayerDefn())
        for k, v in row['properties'].items():
            if v is None:
                feature.SetFieldNull(feature.GetFieldIndex(k))
            else:
                feature.SetField(k, v)
        if row['geometry'] is not None:
            feature.SetGeometry(ogr.CreateGeometryFromJson(json.dumps(row['geometry'])))
        layer.CreateFeature(feature)
        feature = None
    layer = ds = None
"""


@pytest.fixture(scope="module")
def isolated_gdal_cases(tmp_path_factory):
    import hashlib
    import math
    import shutil

    base = tmp_path_factory.mktemp("t07-gdal")
    fixture_dir = base / "fixtures"
    result = cli("materialize", "--output-dir", fixture_dir, python="/usr/bin/python3")
    assert result.returncode == 0, result.stderr
    manifest = json.loads((fixture_dir / "manifest.json").read_text())
    definition = json.loads(DEFINITION.read_text())
    oracle = json.loads(ORACLE.read_text())
    workspace = base / "workspace"
    upload_dir = workspace / "shared/uploads"
    upload_dir.mkdir(parents=True)
    uploads = {}
    for key, data in manifest["datasets"].items():
        path = upload_dir / (key + ".gpkg")
        shutil.copyfile(fixture_dir / data["filename"], path)
        uploads[key] = {
            "file_id": key,
            "filename": data["filename"],
            "sha256": data["sha256"],
            "size": path.stat().st_size,
        }
    parcel_features = definition["datasets"]["parcels"]["features"]
    result_specs = []
    cases = {}
    for scene in definition["scenarios"]:
        case = scene["id"]
        model = scene["expected"]
        source_key = scene["inputs"][0]
        source = definition["datasets"][source_key]
        outputs = [
            workspace / "shared/outputs" / case / "r1" / f"artifact-{i}" / filename
            for i, filename in enumerate(model["output_filenames"])
        ]
        out_specs = []

        def add(path, crs, geom_type, fields, features):
            out_specs.append(
                {
                    "path": str(path),
                    "crs": crs,
                    "geometry_type": geom_type,
                    "fields": fields,
                    "features": features,
                }
            )

        if scene["family"] in ("reproject", "buffer"):
            point_oracle = oracle["points"][source_key]
            points = [
                {
                    "geometry": {
                        "type": "Point",
                        "coordinates": p["projected_coordinates_m"],
                    },
                    "properties": p["properties"],
                }
                for p in point_oracle["features"]
            ]
            add(outputs[0], model["target_crs"], "Point", source["fields"], points)
            if scene["family"] == "buffer":
                radius = point_oracle["buffer"]["radius_m"]
                polygons = []
                for item in point_oracle["features"]:
                    x, y = item["projected_coordinates_m"]
                    ring = [
                        [
                            x + radius * math.cos(2 * math.pi * i / 32),
                            y + radius * math.sin(2 * math.pi * i / 32),
                        ]
                        for i in range(32)
                    ]
                    ring.append(ring[0])
                    polygons.append(
                        {
                            "geometry": {"type": "Polygon", "coordinates": [ring]},
                            "properties": item["properties"],
                        }
                    )
                add(
                    outputs[1],
                    model["target_crs"],
                    "Polygon",
                    source["fields"],
                    polygons,
                )
        elif case == "clip-1":
            add(
                outputs[0],
                "EPSG:32649",
                "Polygon",
                source["fields"],
                [
                    {"geometry": x["geometry"], "properties": x["properties"]}
                    for x in oracle["clip"]["features"]
                ],
            )
        elif case == "clip-2":
            add(outputs[0], "EPSG:32649", "Polygon", source["fields"], [])
        elif case == "dissolve-1":
            rows = [
                {
                    "geometry": geom,
                    "properties": next(
                        f["properties"]
                        for f in parcel_features
                        if f["properties"]["district"] == group
                    ),
                }
                for group, geom in oracle["dissolve"]["geometries"].items()
            ]
            add(outputs[0], "EPSG:32649", "Polygon", source["fields"], rows)
        elif case == "dissolve-2":
            geometry = {
                "type": "MultiPolygon",
                "coordinates": [
                    oracle["dissolve"]["geometries"][g]["coordinates"]
                    for g in ("A", "B", "C")
                ],
            }
            add(
                outputs[0],
                "EPSG:32649",
                "MultiPolygon",
                source["fields"],
                [
                    {
                        "geometry": geometry,
                        "properties": parcel_features[0]["properties"],
                    }
                ],
            )
        elif case == "statistics-2":
            area_field = {"name": "area", "type": "Real", "nullable": True}
            rows = [
                {
                    "geometry": f["geometry"],
                    "properties": {**f["properties"], "area": area},
                }
                for f, area in zip(parcel_features, oracle["parcels"]["areas_m2"])
            ]
            add(
                outputs[0],
                "EPSG:32649",
                "Polygon",
                source["fields"] + [area_field],
                rows,
            )
        if scene["family"] == "statistics":
            which = "score" if case == "statistics-1" else "area"
            stat = oracle["statistics"][which]
            fields = [
                {
                    "name": key,
                    "type": "Integer"
                    if key in ("count", "filled", "empty")
                    else "Real",
                    "nullable": True,
                }
                for key in ("count", "filled", "empty", "sum", "min", "max")
            ]
            props = {
                "count": stat["valid"],
                "filled": stat["valid"],
                "empty": stat["null"],
                "sum": stat["sum"],
                "min": stat["min"],
                "max": stat["max"],
            }
            add(
                outputs[-1],
                None,
                "None",
                fields,
                [{"geometry": None, "properties": props}],
            )
        result_specs.extend(out_specs)
        cases[case] = (scene, outputs)

    written = subprocess.run(
        ["/usr/bin/python3", "-c", WRITER],
        input=json.dumps(result_specs),
        text=True,
        capture_output=True,
        check=False,
        timeout=40,
        cwd=ROOT,
    )
    assert written.returncode == 0, written.stderr

    def evidence(case):
        scene, outputs = cases[case]
        model = scene["expected"]
        source = definition["datasets"][scene["inputs"][0]]
        required = [
            {
                "file_id": uploads[key]["file_id"],
                "sha256": uploads[key]["sha256"],
                "path": str(upload_dir / (key + ".gpkg"))
                + "|layername="
                + definition["datasets"][key]["layer"],
            }
            for key in scene["inputs"]
        ]
        attempts = []
        for i, (algo, output) in enumerate(zip(model["algorithms"], outputs)):
            src = required[0]["path"] if i == 0 else str(outputs[i - 1])
            params = {"OUTPUT": str(output)}
            params[
                "INPUT_LAYER" if algo == "native:basicstatisticsforfields" else "INPUT"
            ] = src
            if algo == "native:clip":
                params["OVERLAY"] = required[1]["path"]
            if algo == "native:reprojectlayer":
                params["TARGET_CRS"] = model["target_crs"]
            if algo == "native:buffer":
                params.update(
                    DISTANCE=model["distance_m"],
                    SEGMENTS=model["segments"],
                    DISSOLVE=False,
                )
            if algo == "native:dissolve":
                params.update(FIELD=model["group_fields"], SEPARATE_DISJOINT=False)
            if algo == "qgis:exportaddgeometrycolumns":
                params["CALC_METHOD"] = model["calculation_method"]
            if algo == "native:basicstatisticsforfields":
                params["FIELD_NAME"] = model["field"]
            artifacts = [
                {
                    "kind": "path",
                    "path": str(output),
                    "size_bytes": output.stat().st_size,
                    "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                }
            ]
            attempts.append(
                {
                    "tool_name": "execute_processing",
                    "tool_arguments": {"algorithm": algo, "parameters": params},
                    "status": "success",
                    "observation": {"status": "success"},
                    "artifacts": artifacts,
                }
            )
        for load in model["loads"]:
            original = load["source"] == "input"
            file = required[0]["path"] if original else str(outputs[-1])
            crs = source["crs"] if original else model["target_crs"]
            geometry = (
                source["geometry_type"]
                if original
                else ("Polygon" if scene["family"] == "buffer" else "Point")
            )
            attempts.append(
                {
                    "tool_name": "add_layer",
                    "tool_arguments": {
                        "name": load["name"],
                        "path": file,
                        "layer_type": "vector",
                    },
                    "status": "success",
                    "observation": {
                        "status": "success",
                        "data": {
                            "id": "layer-1",
                            "name": load["name"],
                            "source": file,
                            "crs": crs,
                            "geometry": geometry,
                            "feature_count": len(source["features"]),
                            "fields": [f["name"] for f in source["fields"]],
                        },
                    },
                    "artifacts": [],
                }
            )
        return {
            "execution": {
                "task_id": case,
                "plan_revision": 1,
                "status": "success",
                "plan": {"task_id": case, "revision": 1, "required_inputs": required},
                "attempts": attempts,
            },
            "uploads": {key: uploads[key] for key in scene["inputs"]},
        }

    return {
        "workspace": workspace,
        "manifest": fixture_dir / "manifest.json",
        "cases": cases,
        "evidence": evidence,
        "output_specs": result_specs,
    }


def verify_case(tmp_path, dataset, case, evidence):
    source = tmp_path / "evidence.json"
    source.write_text(json.dumps(evidence))
    output = tmp_path / "report.json"
    result = cli(
        "verify-result",
        "--manifest",
        dataset["manifest"],
        "--scenario",
        case,
        "--execution-json",
        source,
        "--output-json",
        output,
        "--workspace-root",
        dataset["workspace"],
        python="/usr/bin/python3",
    )
    assert output.exists(), result.stderr
    report = json.loads(output.read_text())
    assert json.loads(result.stdout) == report
    return result, report


@pytest.mark.external
@pytest.mark.parametrize("manifest_case", ["missing", "invalid_json"])
def test_public_verifier_reports_unusable_manifest_as_unavailable(
    tmp_path, manifest_case
):
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}")
    manifest = tmp_path / "manifest.json"
    if manifest_case == "invalid_json":
        manifest.write_text("not-json")
    output = tmp_path / "report.json"
    result = cli(
        "verify-result",
        "--manifest",
        manifest,
        "--scenario",
        "clip-1",
        "--execution-json",
        evidence,
        "--output-json",
        output,
        python="/usr/bin/python3",
    )
    assert output.exists(), result.stderr
    report = json.loads(output.read_text())
    assert result.returncode == 2, report
    assert report["status"] == "unavailable" and report["empty"] is False


@pytest.mark.external
def test_public_verifier_reports_unexpected_inspector_crash_as_unavailable(tmp_path):
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}")
    output = tmp_path / "report.json"
    worker = """
import sys
sys.path.insert(0, sys.argv[1])
import build_t07_fixtures as module
def crash():
    raise RuntimeError('unexpected inspector crash')
module.checked = crash
sys.argv = ['build_t07_fixtures.py', *sys.argv[2:]]
raise SystemExit(module.main())
"""
    result = subprocess.run(
        [
            "/usr/bin/python3",
            "-c",
            worker,
            str(ROOT / "scripts"),
            "verify-result",
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--scenario",
            "clip-1",
            "--execution-json",
            str(evidence),
            "--output-json",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
    )
    assert output.exists(), result.stderr
    report = json.loads(output.read_text())
    assert result.returncode == 2, report
    assert report["status"] == "unavailable" and report["empty"] is False
    assert "unexpected inspector crash" in " ".join(report["errors"])


@pytest.mark.external
@pytest.mark.parametrize(
    "missing", ["artifact_checksum", "load_echo", "attempts", "artifact_record"]
)
def test_public_verifier_reports_incomplete_business_evidence_as_unavailable(
    isolated_gdal_cases, tmp_path, missing
):
    case = "load-1" if missing == "load_echo" else "clip-1"
    evidence = isolated_gdal_cases["evidence"](case)
    attempt = evidence["execution"]["attempts"][0]
    if missing == "artifact_checksum":
        attempt["artifacts"][0].pop("sha256")
    elif missing == "load_echo":
        attempt["observation"]["data"].pop("source")
    elif missing == "attempts":
        evidence["execution"]["attempts"] = []
    else:
        attempt["artifacts"] = []
    result, report = verify_case(tmp_path, isolated_gdal_cases, case, evidence)
    assert result.returncode == 2, report
    assert report["status"] == "unavailable" and report["empty"] is False


@pytest.mark.external
@pytest.mark.parametrize(
    "case",
    [
        "load-1",
        "load-2",
        "reproject-1",
        "reproject-2",
        "buffer-1",
        "buffer-2",
        "clip-1",
        "clip-2",
        "dissolve-1",
        "dissolve-2",
        "statistics-1",
        "statistics-2",
    ],
)
def test_verifier_reads_each_fake_file_and_checks_full_frozen_result(
    isolated_gdal_cases, tmp_path, case
):
    result, report = verify_case(
        tmp_path, isolated_gdal_cases, case, isolated_gdal_cases["evidence"](case)
    )
    assert result.returncode == 0, report
    assert report["status"] == "passed"
    assert report["empty"] is (case == "clip-2")


MUTATE = r"""
import json, math, sys
from osgeo import gdal, ogr
ogr.UseExceptions()
mode, path, source = sys.argv[1:]
ds = gdal.OpenEx(path, gdal.OF_VECTOR | gdal.OF_UPDATE)
layer = ds.GetLayer(0)
feature = layer.GetNextFeature()
if mode == 'wrong-geometry':
    geom = feature.GetGeometryRef().Clone()
    def move(g):
        if g.GetGeometryCount():
            for n in range(g.GetGeometryCount()): move(g.GetGeometryRef(n))
        else:
            for n in range(g.GetPointCount()):
                x, y = g.GetPoint_2D(n); g.SetPoint_2D(n, x + 100, y)
    move(geom); feature.SetGeometry(geom); layer.SetFeature(feature)
elif mode == 'rotate-buffer':
    geom = feature.GetGeometryRef().Clone(); ring = geom.GetGeometryRef(0)
    points = [ring.GetPoint_2D(n) for n in range(32)]
    cx = sum(p[0] for p in points) / 32; cy = sum(p[1] for p in points) / 32
    angle = math.pi / 32
    for n, (x, y) in enumerate(points + [points[0]]):
        dx, dy = x-cx, y-cy
        ring.SetPoint_2D(n, cx + dx*math.cos(angle) - dy*math.sin(angle),
                        cy + dx*math.sin(angle) + dy*math.cos(angle))
    feature.SetGeometry(geom); layer.SetFeature(feature)
elif mode == 'null-as-zero':
    feature.SetField('count', 5); feature.SetField('filled', 5); feature.SetField('empty', 0)
    layer.SetFeature(feature)
elif mode == 'wrong-sum':
    feature.SetField('sum', 0.0); layer.SetFeature(feature)
elif mode == 'not-empty':
    src = gdal.OpenEx(source, gdal.OF_VECTOR | gdal.OF_READONLY); original = src.GetLayer(0).GetNextFeature()
    feature = ogr.Feature(layer.GetLayerDefn())
    for key, value in original.items().items():
        if value is None: feature.SetFieldNull(feature.GetFieldIndex(key))
        else: feature.SetField(key, value)
    feature.SetGeometry(original.GetGeometryRef()); layer.CreateFeature(feature)
    original = src = None
feature = layer = ds = None
"""


def isolated_copy(dataset, tmp_path, case):
    import shutil

    old_workspace = dataset["workspace"]
    new_workspace = tmp_path / "workspace"
    shutil.copytree(old_workspace / "shared/uploads", new_workspace / "shared/uploads")
    original_output = old_workspace / "shared/outputs" / case
    if original_output.exists():
        shutil.copytree(original_output, new_workspace / "shared/outputs" / case)
    raw = json.dumps(dataset["evidence"](case))
    evidence = json.loads(raw.replace(str(old_workspace), str(new_workspace)))
    return {**dataset, "workspace": new_workspace}, evidence


@pytest.mark.external
@pytest.mark.parametrize(
    ("case", "mutation", "error"),
    [
        ("clip-1", "wrong-geometry", "polygon coverage"),
        ("clip-2", "not-empty", "zero-row"),
        ("statistics-1", "null-as-zero", "statistics differ"),
        ("statistics-1", "wrong-sum", "statistics differ"),
        ("buffer-1", "wrong-geometry", "center or radius"),
        ("buffer-1", "rotate-buffer", "vertex orientation"),
        ("clip-1", "sidecar", "sidecar"),
        ("clip-1", "missing", "missing or empty"),
        ("load-1", "wrong-load", "loaded layer crs"),
    ],
)
def test_verifier_rejects_fake_success_and_spatial_counterexamples(
    isolated_gdal_cases, tmp_path, case, mutation, error
):
    import hashlib

    dataset, evidence = isolated_copy(isolated_gdal_cases, tmp_path, case)
    attempt = evidence["execution"]["attempts"][-1]
    if mutation == "wrong-load":
        attempt["observation"]["data"]["crs"] = "EPSG:32650"
    else:
        attempt = next(
            a
            for a in evidence["execution"]["attempts"]
            if a["tool_name"] == "execute_processing"
            and (
                mutation not in {"wrong-geometry", "rotate-buffer"}
                or case != "buffer-1"
                or a["tool_arguments"]["algorithm"] == "native:buffer"
            )
        )
        artifact = attempt["artifacts"][0]
        path = Path(artifact["path"])
        if mutation == "sidecar":
            Path(str(path) + "-wal").touch()
        elif mutation == "missing":
            path.unlink()
        else:
            source = dataset["workspace"] / "shared/uploads/parcels.gpkg"
            changed = subprocess.run(
                ["/usr/bin/python3", "-c", MUTATE, mutation, str(path), str(source)],
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
                cwd=ROOT,
            )
            assert changed.returncode == 0, changed.stderr
            # A fresh business-record checksum makes this a content counterexample,
            # not just detection of a stale hash.
            artifact["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            artifact["size_bytes"] = path.stat().st_size
    result, report = verify_case(tmp_path, dataset, case, evidence)
    assert result.returncode == 1, report
    assert report["status"] == "failed" and report["empty"] is False
    assert error in " ".join(report["errors"]).lower()
    if mutation == "sidecar":
        assert Path(str(path) + "-wal").exists()


@pytest.mark.external
@pytest.mark.parametrize(
    "corruption",
    ["wrong_magic", "not_geopackage", "corrupt_schema", "corrupt_database"],
)
def test_public_verifier_rejects_proven_invalid_geopackage(
    isolated_gdal_cases, tmp_path, corruption
):
    import hashlib
    import sqlite3

    dataset, evidence = isolated_copy(isolated_gdal_cases, tmp_path, "clip-1")
    artifact = evidence["execution"]["attempts"][0]["artifacts"][0]
    path = Path(artifact["path"])
    if corruption == "wrong_magic":
        path.write_bytes(b"NOT A SQLITE OR GEOPACKAGE FILE")
    elif corruption == "corrupt_database":
        path.write_bytes(b"SQLite format 3\x00" + bytes(512))
    else:
        path.unlink()
        with sqlite3.connect(path) as database:
            database.execute("PRAGMA application_id = 1196444487")
            if corruption == "not_geopackage":
                database.execute("CREATE TABLE unrelated (id INTEGER)")
            else:
                database.execute(
                    "CREATE TABLE gpkg_contents (table_name TEXT, data_type TEXT)"
                )
                database.execute(
                    "CREATE TABLE gpkg_spatial_ref_sys (srs_name TEXT, srs_id INTEGER, "
                    "organization TEXT, organization_coordsys_id INTEGER, definition TEXT)"
                )
                database.execute(
                    "CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT, "
                    "geometry_type_name TEXT, srs_id INTEGER, z INTEGER, m INTEGER)"
                )
    artifact["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    artifact["size_bytes"] = path.stat().st_size
    result, report = verify_case(tmp_path, dataset, "clip-1", evidence)
    assert (
        "fixed inputs, hashes, fields, attributes, CRS and geometry" in report["checks"]
    )
    assert result.returncode == 1, report
    assert report["status"] == "failed" and report["empty"] is False


@pytest.mark.external
def test_public_verifier_keeps_sqlite_io_failure_unavailable(
    isolated_gdal_cases, tmp_path
):
    dataset = isolated_gdal_cases
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(dataset["evidence"]("clip-1")))
    output = tmp_path / "report.json"
    worker = """
import sqlite3, sys
sys.path.insert(0, sys.argv[1])
import build_t07_fixtures as module
connect = sqlite3.connect
def fail_output(database, *args, **kwargs):
    if 'left_clip.gpkg' in str(database):
        raise sqlite3.OperationalError('injected storage IO failure')
    return connect(database, *args, **kwargs)
sqlite3.connect = fail_output
sys.argv = ['build_t07_fixtures.py', *sys.argv[2:]]
raise SystemExit(module.main())
"""
    result = subprocess.run(
        [
            "/usr/bin/python3",
            "-c",
            worker,
            str(ROOT / "scripts"),
            "verify-result",
            "--manifest",
            str(dataset["manifest"]),
            "--scenario",
            "clip-1",
            "--execution-json",
            str(evidence_path),
            "--output-json",
            str(output),
            "--workspace-root",
            str(dataset["workspace"]),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
    )
    assert output.exists(), result.stderr
    report = json.loads(output.read_text())
    assert (
        "fixed inputs, hashes, fields, attributes, CRS and geometry" in report["checks"]
    )
    assert result.returncode == 2, report
    assert report["status"] == "unavailable" and report["empty"] is False
    assert "injected storage IO failure" in " ".join(report["errors"])
