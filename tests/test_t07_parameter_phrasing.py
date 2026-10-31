"""Explicit finite GIS phrases reach public review with matching protected plans."""

import copy
import os
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from agent.graph import build_graph
from agent.nodes import executor_node as executor_module
from agent.nodes import planner_node as planner_module
from agent.planning.plan_schema import TaskPlan
from conftest import fake_spatial_inspector
import hashlib
import json
import socket
from pathlib import Path

import pytest
from test_t06_workflow import (
    buffer_plan,
    parameter_plan,
    reproject_buffer_load_plan,
    setup_workflow,
    two_buffer_segments_plan,
)
import ui.backend.main as backend

MANIFEST = json.loads(
    (Path(__file__).parent / "fixtures/gis/t07_benchmark.json").read_text()
)
BUFFER_REQUESTS = [
    (scenario, request)
    for scenario in MANIFEST["scenarios"]
    if scenario["family"] == "buffer"
    for request in scenario["requests"]
]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for key, value in {
        "DATABASE_URL": "",
        "PYTHON_DOTENV_DISABLED": "1",
        "OPENAI_API_KEY": "offline-test-key",
        "QGIS_AGENT_LANGSMITH_ENABLED": "false",
        "LANGSMITH_TRACING": "false",
        "LANGSMITH_TRACING_V2": "false",
        "LANGCHAIN_TRACING_V2": "false",
    }.items():
        monkeypatch.setenv(key, value)

    def forbid_network(*args, **kwargs):
        raise AssertionError("Public planning tests must remain offline")

    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    monkeypatch.setattr(socket, "create_connection", forbid_network)
    monkeypatch.setattr(backend, "get_task_execution_record", lambda _: None)


def chain_proposal(scenario):
    expected = scenario["expected"]

    def proposal(source):
        plan = copy.deepcopy(reproject_buffer_load_plan(source))
        projected, buffered = expected["output_filenames"]
        plan["expected_outputs"][0]["path"] = projected
        plan["expected_outputs"][1]["path"] = buffered
        plan["tool_calls"][0]["arguments"]["parameters"].update(
            TARGET_CRS=expected["target_crs"], OUTPUT=projected
        )
        plan["tool_calls"][1]["arguments"]["parameters"].update(
            INPUT=projected,
            DISTANCE=expected["distance_m"],
            SEGMENTS=expected["segments"],
            DISSOLVE=expected["dissolve"],
            OUTPUT=buffered,
        )
        plan["tool_calls"][2]["arguments"].update(
            path=buffered, name=expected["loads"][0]["name"]
        )
        return plan

    return proposal


def submit(
    monkeypatch,
    tmp_path,
    proposal,
    message,
    *,
    enabled="false",
    filename="source.geojson",
):
    monkeypatch.setenv("QGIS_AGENT_SKILLS_ENABLED", enabled)
    client, source, prompts, gis_calls = setup_workflow(
        monkeypatch, tmp_path, proposal, filename=filename
    )
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    response = client.post(
        "/api/chat/stream",
        json={
            "thread_id": "explicit-parameters",
            "message": message,
            "files": ["new-file"],
        },
    )
    assert response.status_code == 200
    snapshot = client.get("/api/state/explicit-parameters")
    assert snapshot.status_code == 200
    assert gis_calls == []
    assert hashlib.sha256(source.read_bytes()).hexdigest() == checksum
    assert not (tmp_path / "shared" / "outputs").exists()
    return response, snapshot.json(), prompts


def assert_review(response, snapshot, prompts):
    assert snapshot["pause"] and snapshot["pause"]["kind"] == "plan_review", snapshot[
        "state"
    ]["planning_error"]
    assert "human_review_required" in response.text and len(prompts) == 1
    assert snapshot["state"]["task_plan"]["review_status"] == "pending_review"
    return snapshot["state"]["task_plan"]


@pytest.mark.parametrize("enabled", ["false", "true"], ids=["A0", "A1"])
@pytest.mark.parametrize(
    "scenario,frozen_request",
    BUFFER_REQUESTS,
    ids=[r["id"] for _, r in BUFFER_REQUESTS],
)
def test_frozen_explicit_buffer_parameters_reach_public_review(
    monkeypatch, tmp_path, enabled, scenario, frozen_request
):
    input_key = scenario["inputs"][0]
    filename = MANIFEST["datasets"][input_key]["filename"]
    message = frozen_request["text"].format(**{input_key: filename})
    response, snapshot, prompts = submit(
        monkeypatch,
        tmp_path,
        chain_proposal(scenario),
        message,
        enabled=enabled,
        filename=filename,
    )
    plan = assert_review(response, snapshot, prompts)
    choices = plan["metadata"]["parameter_requirements"]["choices"]
    assert choices["targets"] == [scenario["expected"]["target_crs"]]
    assert choices["buffer_distances"] == [scenario["expected"]["distance_m"]]
    assert choices["buffer_segments"] == [8] and choices["buffer_dissolve"] == [False]
    assert [call["arguments"].get("algorithm") for call in plan["tool_calls"][:2]] == [
        "native:reprojectlayer",
        "native:buffer",
    ]
    assert [Path(item["path"]).name for item in plan["expected_outputs"]] == scenario[
        "expected"
    ]["output_filenames"]


def simple_buffer(source):
    plan = buffer_plan(source, distance=60, dissolve=False)
    plan["tool_calls"][0]["arguments"]["parameters"]["SEGMENTS"] = 8
    return plan


@pytest.mark.parametrize("enabled", ["false", "true"], ids=["A0", "A1"])
def test_parameter_first_minimum_reaches_public_review(monkeypatch, tmp_path, enabled):
    response, snapshot, prompts = submit(
        monkeypatch,
        tmp_path,
        simple_buffer,
        "60米、每象限8段、不融合缓冲",
        enabled=enabled,
    )
    plan = assert_review(response, snapshot, prompts)
    assert plan["metadata"]["parameter_requirements"]["choices"]["buffer_segments"] == [
        8
    ]


@pytest.mark.parametrize("verb", ["重投影至", "投影至", "转换到"])
def test_explicit_reprojection_alias_has_reviewable_target(monkeypatch, tmp_path, verb):
    response, snapshot, prompts = submit(
        monkeypatch,
        tmp_path,
        lambda source: parameter_plan(
            source, "native:reprojectlayer", {"TARGET_CRS": "EPSG:32649"}
        ),
        verb + " EPSG:32649",
    )
    plan = assert_review(response, snapshot, prompts)
    assert plan["metadata"]["parameter_requirements"]["choices"]["targets"] == [
        "EPSG:32649"
    ]


@pytest.mark.parametrize(
    "message",
    [
        "60度、每象限8段、不融合缓冲",
        "60平方米、每象限8段、不融合缓冲",
        "60米或80米、每象限8段、不融合缓冲",
        "60米、80米、每象限8段、不融合缓冲",
        "60米、每象限8段或12段、不融合缓冲",
        "60米、每象限8段、不融合，不要缓冲",
        "60米、每象限8段、不融合缓冲，然后融合",
    ],
)
def test_parameter_first_form_does_not_authorize_ambiguous_or_negative_requests(
    monkeypatch, tmp_path, message
):
    response, snapshot, prompts = submit(monkeypatch, tmp_path, simple_buffer, message)
    assert (
        "event: error" in response.text and "human_review_required" not in response.text
    )
    assert snapshot["pause"] is None and snapshot["state"]["task_plan"] is None


@pytest.mark.parametrize("segments,allowed", [([8, 12], True), ([12, 8], False)])
def test_parameter_first_repeated_buffers_keep_option_order(
    monkeypatch, tmp_path, segments, allowed
):
    response, snapshot, prompts = submit(
        monkeypatch,
        tmp_path,
        lambda source: two_buffer_segments_plan(source, segments),
        "100米、每象限8段、不融合缓冲，然后200米、每象限12段、不融合缓冲",
    )
    if allowed:
        plan = assert_review(response, snapshot, prompts)
        choices = plan["metadata"]["parameter_requirements"]["choices"]
        assert choices["buffer_distances"] == [100, 200]
        assert choices["buffer_segments"] == [8, 12] and choices["buffer_dissolve"] == [
            False,
            False,
        ]
    else:
        assert "SEGMENTS" in snapshot["state"]["planning_error"]
        assert (
            snapshot["pause"] is None and "human_review_required" not in response.text
        )


@pytest.mark.parametrize("verb", ["重投影至", "投影至", "转换到"])
@pytest.mark.parametrize("suffix", [" 或 EPSG:32651", ""])
def test_reprojection_alias_does_not_bypass_ambiguity_or_operation_negation(
    monkeypatch, tmp_path, verb, suffix
):
    message = ("" if suffix else "不要") + verb + " EPSG:32649" + suffix
    response, snapshot, prompts = submit(
        monkeypatch,
        tmp_path,
        lambda source: parameter_plan(
            source, "native:reprojectlayer", {"TARGET_CRS": "EPSG:32649"}
        ),
        message,
    )
    assert (
        "event: error" in response.text and "human_review_required" not in response.text
    )
    assert snapshot["pause"] is None and snapshot["state"]["task_plan"] is None
    assert (
        snapshot["state"]["planning_failure_kind"] == "request_rejected"
        and prompts == []
    )


SCENARIOS = MANIFEST["scenarios"]
CASES = [(s, r) for s in SCENARIOS for r in s["requests"]]


def legal_plan(scenario, paths):
    expected = scenario["expected"]
    primary = scenario["inputs"][0]
    refs = {key: "source_" + key for key in paths}
    plan = dict(
        task_id="model-task",
        task_summary="Explicit deterministic GIS plan",
        risk_level="low",
        review_status="pending_review",
        required_inputs=[
            dict(input_id=refs[key], file_id=key, description=key, path=paths[key])
            for key in scenario["inputs"]
        ],
        expected_outputs=[],
        steps=[],
        tool_calls=[],
        metadata={},
    )
    current_ref, current_path = refs[primary], paths[primary]
    for i, algorithm in enumerate(expected["algorithms"]):
        step_id = i + 1
        output_ref, output_path = f"artifact_{i}", expected["output_filenames"][i]
        input_refs = [current_ref]
        params = {"INPUT": current_path, "OUTPUT": output_path}
        if algorithm == "native:reprojectlayer":
            params["TARGET_CRS"] = expected["target_crs"]
        elif algorithm == "native:buffer":
            params.update(
                DISTANCE=expected["distance_m"],
                SEGMENTS=expected["segments"],
                END_CAP_STYLE=0,
                JOIN_STYLE=0,
                MITER_LIMIT=2,
                DISSOLVE=False,
                SEPARATE_DISJOINT=False,
            )
        elif algorithm == "native:clip":
            mask = scenario["inputs"][1]
            params["OVERLAY"] = paths[mask]
            input_refs.append(refs[mask])
        elif algorithm == "native:dissolve":
            params.update(
                FIELD=expected["group_fields"],
                SEPARATE_DISJOINT=expected.get("separate_disjoint", False),
            )
        elif algorithm == "qgis:exportaddgeometrycolumns":
            params["CALC_METHOD"] = expected["calculation_method"]
        elif algorithm == "native:basicstatisticsforfields":
            params.pop("INPUT")
            params.update(INPUT_LAYER=current_path, FIELD_NAME=expected["field"])
        else:
            raise AssertionError(f"Unspecified deterministic algorithm {algorithm}")
        plan["expected_outputs"].append(
            dict(output_id=output_ref, description=output_ref, path=output_path)
        )
        plan["steps"].append(
            dict(
                step_id=step_id,
                description=algorithm,
                allowed_tools=["execute_processing"],
                input_refs=input_refs,
                output_refs=[output_ref],
                risk_level="low",
                requires_review=False,
            )
        )
        call = dict(
            step_id=step_id,
            tool_name="execute_processing",
            arguments=dict(algorithm=algorithm, parameters=params),
        )
        if algorithm == "native:buffer":
            call["distance_unit"] = "m"
        plan["tool_calls"].append(call)
        current_ref, current_path = output_ref, output_path
    for load in expected["loads"]:
        step_id = len(plan["steps"]) + 1
        if load["source"] == "input":
            current_ref, current_path = refs[primary], paths[primary]
        plan["steps"].append(
            dict(
                step_id=step_id,
                description="Load selected result",
                allowed_tools=["add_layer"],
                input_refs=[current_ref],
                output_refs=[],
                risk_level="low",
                requires_review=False,
            )
        )
        plan["tool_calls"].append(
            dict(
                step_id=step_id,
                tool_name="add_layer",
                arguments=dict(
                    path=current_path, layer_type="vector", name=load["name"]
                ),
            )
        )
    TaskPlan.model_validate(plan)
    return plan


def control_message(scenario):
    e = scenario["expected"]
    if scenario["family"] == "load":
        return "加载选中的原图层"
    if scenario["family"] == "reproject":
        return f"重投影到 {e['target_crs']}，最后只加载最终结果"
    if scenario["family"] == "buffer":
        return (
            f"重投影到 {e['target_crs']}，然后缓冲{e['distance_m']}米，"
            f"每象限{e['segments']}段，不融合，最后只加载最终结果"
        )
    if scenario["family"] == "clip":
        return "裁剪选中的输入与裁剪面，不加载图层"
    if scenario["family"] == "dissolve":
        return "按 district 字段融合" if e["group_fields"] else "全部融合"
    if len(e["algorithms"]) == 1:
        return "统计 score 字段"
    return "计算面积，然后统计 area 字段"


def submit_workflow(monkeypatch, tmp_path, scenario, message, enabled, *, tag):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("QGIS_AGENT_SKILLS_ENABLED", enabled)
    records = {}
    paths = {}
    checksums = {}
    for key in scenario["inputs"]:
        dataset = MANIFEST["datasets"][key]
        path = tmp_path / dataset["filename"]
        # Deliberate fake bytes, accepted only by fake GIS inspectors. Not an actual GPKG.
        path.write_text('{"type":"FeatureCollection","features":[]}')
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        checksums[path] = checksum
        paths[key] = str(path) + "|layername=" + dataset["layer"]
        records[key] = dict(
            file_id=key,
            filename=path.name,
            storage_path=path.name,
            sha256=checksum,
            layers=[
                dict(
                    layer_name=dataset["layer"],
                    crs=dataset["crs"],
                    geometry=dataset["geometry_type"],
                    is_2d=True,
                    feature_count=len(dataset["features"]),
                    fields=dataset["fields"],
                )
            ],
        )
    proposal = legal_plan(scenario, paths)
    prompts = []
    gis_calls = []

    class Model:
        async def ainvoke(self, messages):
            prompts.append([str(m.content) for m in messages])
            return SimpleNamespace(content=json.dumps(proposal, ensure_ascii=False))

    def processing(request):
        if request == {"capabilities": True}:
            return dict(status="success", result=dict(processing_capabilities=[]))
        return dict(
            status="success",
            result=dict(
                processing_validation=[
                    dict(
                        step_id=c["step_id"],
                        valid=True,
                        source_paths=[
                            value
                            for key, value in c["parameters"].items()
                            if key in {"INPUT", "INPUT_LAYER", "OVERLAY"}
                        ],
                        output_paths=[c["parameters"]["OUTPUT"]],
                    )
                    for c in request
                ]
            ),
        )

    def spatial(request):
        result = fake_spatial_inspector(request)
        if request.get("mode") == "preflight":
            crs = {
                f"source_{key}": MANIFEST["datasets"][key]["crs"]
                for key in scenario["inputs"]
            }
            geom = {
                f"source_{key}": MANIFEST["datasets"][key]["geometry_type"]
                for key in scenario["inputs"]
            }
            steps = {s["step_id"]: s for s in request["plan"]["steps"]}
            for item, call in zip(
                result["facts"]["review_steps"], request["plan"]["tool_calls"]
            ):
                step = steps[call["step_id"]]
                algorithm = call["arguments"].get("algorithm")
                item["input_crs"] = [
                    dict(ref=ref, crs=crs[ref]) for ref in step["input_refs"]
                ]
                output_crs = (
                    call["arguments"]["parameters"]["TARGET_CRS"]
                    if algorithm == "native:reprojectlayer"
                    else crs[step["input_refs"][0]]
                )
                if item["output_kind"] != "table":
                    item["output_crs"] = output_crs
                item["geometry"] = (
                    None
                    if item["output_kind"] == "table"
                    else "Polygon"
                    if algorithm
                    in {
                        "native:buffer",
                        "native:clip",
                        "native:dissolve",
                        "qgis:exportaddgeometrycolumns",
                    }
                    else geom[step["input_refs"][0]]
                )
                for ref in step["output_refs"]:
                    crs[ref] = output_crs
                    geom[ref] = item["geometry"]
        return result

    graph = build_graph(
        MemorySaver(), processing_inspector=processing, spatial_inspector=spatial
    )

    async def get_graph():
        return graph

    async def forbidden_gis():
        gis_calls.append("connected")
        raise AssertionError("Unapproved planning cannot connect to MCP")

    monkeypatch.setattr(backend, "get_app_with_checkpointer", get_graph)
    monkeypatch.setattr(backend, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(backend, "get_uploaded_file_record", records.get)
    monkeypatch.setattr(backend, "save_task_execution_record", lambda **_: None)
    monkeypatch.setattr(backend, "get_task_execution_record", lambda _: None)
    monkeypatch.setattr(planner_module, "_get_llm", lambda _: Model())
    monkeypatch.setattr(executor_module, "get_mcp_client", forbidden_gis)
    client = TestClient(backend.app)
    response = client.post(
        "/api/chat/stream",
        json=dict(thread_id="phase6", message=message, files=scenario["inputs"]),
    )
    assert response.status_code == 200
    snapshot_response = client.get("/api/state/phase6")
    assert snapshot_response.status_code == 200
    snapshot = snapshot_response.json()
    state = snapshot["state"]
    assert gis_calls == [] and not (tmp_path / "shared" / "outputs").exists()
    assert all(
        hashlib.sha256(p.read_bytes()).hexdigest() == sha
        for p, sha in checksums.items()
    )
    summary = dict(
        scenario=scenario["id"],
        tag=tag,
        arm="A1" if enabled == "true" else "A0",
        message=message,
        pause=snapshot["pause"],
        next=snapshot["next"],
        error=state.get("planning_error"),
        failure_kind=state.get("planning_failure_kind"),
        diagnostics=state.get("planning_attempt_diagnostics"),
        model_calls=len(prompts),
        gis_calls=gis_calls,
        requirements=state.get("parameter_requirements"),
        plan_validation=state.get("plan_validation"),
    )
    folder = Path(os.getenv("PHASE6_EVIDENCE_DIR", str(tmp_path / "evidence")))
    folder.mkdir(exist_ok=True)
    (folder / (tag + "-" + summary["arm"] + ".json")).write_text(
        json.dumps(
            dict(
                summary=summary, proposal=proposal, snapshot=snapshot, sse=response.text
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print("PHASE6_RESULT " + json.dumps(summary, ensure_ascii=False))
    return response, snapshot, prompts


@pytest.mark.parametrize("enabled", ["false", "true"], ids=["A0", "A1"])
@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_legal_plan_control(monkeypatch, tmp_path, scenario, enabled):
    response, snapshot, prompts = submit_workflow(
        monkeypatch,
        tmp_path,
        scenario,
        control_message(scenario),
        enabled,
        tag="control-" + scenario["id"],
    )
    assert snapshot["pause"] and snapshot["pause"]["kind"] == "plan_review", snapshot[
        "state"
    ]["planning_error"]
    assert len(prompts) == 1 and "human_review_required" in response.text


@pytest.mark.parametrize("enabled", ["false", "true"], ids=["A0", "A1"])
@pytest.mark.parametrize(
    "scenario,frozen_request", CASES, ids=[r["id"] for _, r in CASES]
)
def test_frozen_request_with_legal_plan(
    monkeypatch, tmp_path, scenario, frozen_request, enabled
):
    message = frozen_request["text"].format(
        **{key: MANIFEST["datasets"][key]["filename"] for key in scenario["inputs"]}
    )
    response, snapshot, prompts = submit_workflow(
        monkeypatch, tmp_path, scenario, message, enabled, tag=frozen_request["id"]
    )
    assert snapshot["pause"] and snapshot["pause"]["kind"] == "plan_review", snapshot[
        "state"
    ]["planning_error"]
    assert len(prompts) == 1 and "human_review_required" in response.text


def development_case(kind):
    """Unexposed field/value variants use the same algorithm mapping, not case IDs."""
    scenario = copy.deepcopy(
        next(
            s
            for s in SCENARIOS
            if s["id"]
            == {
                "crs-verb": "reproject-1",
                "crs-order": "reproject-1",
                "buffer": "buffer-1",
                "group": "dissolve-1",
                "whole": "dissolve-2",
                "score-prefix": "statistics-1",
                "score-postfix": "statistics-1",
                "area-metric": "statistics-2",
                "area-sum": "statistics-2",
            }[kind]
        )
    )
    expected = scenario["expected"]
    if kind.startswith("crs") or kind == "buffer":
        expected["target_crs"] = "EPSG:32648"
    if kind == "buffer":
        expected.update(distance_m=92.5, segments=16)
    if kind == "group":
        expected["group_fields"] = ["bucket_key"]
    if kind.startswith("score"):
        expected["field"] = "measured_value"
    messages = {
        "crs-verb": "转成 EPSG:32648 坐标系，只加载最终结果",
        "crs-order": "做 EPSG:32648 重投影，只加载最终结果",
        "buffer": "重投影至 EPSG:32648，再以92.5米、每象限16段、不融合制作缓冲区，只加载最终结果",
        "group": "以 bucket_key 分组融合",
        "whole": "整体融合，不指定分组字段且保持不相连部分在同一要素中",
        "score-prefix": "统计 t07_parcels.gpkg 的 measured_value：报告和、最小值及最大值",
        "score-postfix": "measured_value 数值字段做统计",
        "area-metric": "计算面积，再统计面积总和",
        "area-sum": "平面计算面积，再对新增 area 字段求 min",
    }
    return scenario, messages[kind]


@pytest.mark.parametrize("enabled", ["false", "true"], ids=["A0", "A1"])
@pytest.mark.parametrize(
    "kind",
    [
        "crs-verb",
        "crs-order",
        "buffer",
        "group",
        "whole",
        "score-prefix",
        "score-postfix",
        "area-metric",
        "area-sum",
    ],
)
def test_finite_grammar_generalizes_to_development_fields_and_values(
    monkeypatch, tmp_path, kind, enabled
):
    # Explicitly register the new development fields in fake input metadata too.
    fields = copy.deepcopy(MANIFEST["datasets"]["parcels"]["fields"]) + [
        dict(name="bucket_key", type="String", nullable=False),
        dict(name="measured_value", type="Real", nullable=True),
    ]
    monkeypatch.setitem(MANIFEST["datasets"]["parcels"], "fields", fields)
    scenario, message = development_case(kind)
    response, snapshot, prompts = submit_workflow(
        monkeypatch, tmp_path, scenario, message, enabled, tag="development-" + kind
    )
    assert snapshot["pause"] and snapshot["pause"]["kind"] == "plan_review", snapshot[
        "state"
    ]["planning_error"]
    assert len(prompts) == 1 and "human_review_required" in response.text


@pytest.mark.parametrize(
    "scenario_id,message,expected_overrides",
    [
        ("reproject-1", "添加到地图；不做转换", {}),
        ("reproject-1", "不要转成 EPSG:32649", {}),
        ("reproject-1", "不要做 EPSG:32649 重投影", {}),
        ("reproject-1", "转成 EPSG:32649 或 EPSG:32651", {}),
        ("reproject-1", "EPSG:32649 或 EPSG:32651 重投影", {}),
        ("reproject-1", "EPSG:32649重投影或EPSG:32651重投影", {}),
        ("reproject-1", "EPSG:32649重投影 / EPSG:32651重投影", {}),
        ("dissolve-1", "以 district 分组融合", dict(group_fields=["parcel_id"])),
        ("dissolve-2", "整体融合，不指定分组字段", dict(group_fields=["district"])),
        (
            "dissolve-2",
            "全部融合（不按字段分组，不拆分不相连部分）",
            dict(separate_disjoint=True),
        ),
        (
            "dissolve-2",
            "整体融合，不指定分组字段且保持不相连部分在同一要素中",
            dict(separate_disjoint=True),
        ),
        ("dissolve-2", "全部融合（不按字段分组）；不要融合", {}),
        ("dissolve-2", "整体融合，不指定分组字段且不要统计 score", {}),
        ("dissolve-1", "不以 district 分组融合", {}),
        ("statistics-1", "统计 t07_parcels.gpkg 的 score", dict(field="parcel_id")),
        ("statistics-1", "score 字段做统计或 parcel_id 字段做统计", {}),
        ("statistics-1", "不统计 t07_parcels.gpkg 的 score", {}),
        ("statistics-1", "不要对 score 字段求 sum", {}),
    ],
)
def test_finite_phrases_keep_plan_mismatch_conflict_and_negation_protection(
    monkeypatch, tmp_path, scenario_id, message, expected_overrides
):
    scenario = copy.deepcopy(next(s for s in SCENARIOS if s["id"] == scenario_id))
    scenario["expected"].update(expected_overrides)
    response, snapshot, prompts = submit_workflow(
        monkeypatch,
        tmp_path,
        scenario,
        message,
        "false",
        tag="protected-" + hashlib.sha256(message.encode()).hexdigest()[:12],
    )
    assert snapshot["pause"] is None and snapshot["state"]["task_plan"] is None
    assert (
        "event: error" in response.text and "human_review_required" not in response.text
    )


@pytest.mark.parametrize("enabled", ["false", "true"], ids=["A0", "A1"])
@pytest.mark.parametrize(
    "scenario_id,message",
    [
        ("load-1", "添加到地图；不做转换"),
        ("reproject-1", "转成EPSG:32649"),
        ("reproject-2", "EPSG:32651重投影"),
        ("dissolve-1", "以district分组融合"),
        ("dissolve-2", "全部融合（不按字段分组）"),
        ("dissolve-2", "整体融合，不指定分组字段"),
        ("statistics-1", "统计 t07_parcels.gpkg 的 score"),
        ("statistics-1", "score 字段做统计"),
        ("statistics-2", "计算面积，再统计面积总和"),
        ("statistics-2", "平面计算面积；再对area 字段求 sum"),
    ],
)
def test_minimized_business_requests_reach_review(
    monkeypatch, tmp_path, scenario_id, message, enabled
):
    scenario = copy.deepcopy(next(s for s in SCENARIOS if s["id"] == scenario_id))
    response, snapshot, prompts = submit_workflow(
        monkeypatch,
        tmp_path,
        scenario,
        message,
        enabled,
        tag="minimum-" + hashlib.sha256(message.encode()).hexdigest()[:12],
    )
    assert snapshot["pause"] and snapshot["pause"]["kind"] == "plan_review", snapshot[
        "state"
    ]["planning_error"]
    assert len(prompts) == 1 and "human_review_required" in response.text


@pytest.mark.parametrize(
    "message",
    [
        "不要转换坐标系，转成 EPSG:32649",
        "不做转换。转成 EPSG:32649",
        "缓冲60米，不要融合和缓冲",
        "缓冲60米，不能融合和缓冲",
        "缓冲60米，不要融合和再次缓冲",
        "重投影到EPSG:32649，然后EPSG:32651重投影",
        "EPSG:32649重投影，然后EPSG:32651重投影",
        "EPSG:32649重投影和EPSG:32651重投影",
    ],
)
def test_review_shared_negation_and_targets_cannot_drop_constraints(
    monkeypatch, tmp_path, message
):
    def proposal(source):
        if "缓冲" in message:
            return buffer_plan(source, distance=60, dissolve=False)
        return parameter_plan(
            source, "native:reprojectlayer", {"TARGET_CRS": "EPSG:32649"}
        )

    response, snapshot, prompts = submit(monkeypatch, tmp_path, proposal, message)
    print(
        "REVIEW_FIX_RESULT "
        + json.dumps(
            dict(
                message=message,
                pause=snapshot["pause"],
                error=snapshot["state"]["planning_error"],
                model_calls=len(prompts),
            ),
            ensure_ascii=False,
        )
    )
    assert snapshot["pause"] is None and "human_review_required" not in response.text


@pytest.mark.parametrize("connector", ["到", "至", "为"])
@pytest.mark.parametrize("spaces", [" ", "  "])
@pytest.mark.parametrize(
    "model_target,allowed", [("EPSG:32650", False), ("EPSG:32651", True)]
)
def test_review_source_crs_before_verb_does_not_replace_real_target(
    monkeypatch, tmp_path, connector, spaces, model_target, allowed
):
    message = "做 EPSG:32650 重投影" + spaces + connector + " EPSG:32651"
    response, snapshot, prompts = submit(
        monkeypatch,
        tmp_path,
        lambda source: parameter_plan(
            source, "native:reprojectlayer", {"TARGET_CRS": model_target}
        ),
        message,
    )
    print(
        "REVIEW_FIX_RESULT "
        + json.dumps(
            dict(
                message=message,
                model_target=model_target,
                pause=snapshot["pause"],
                error=snapshot["state"]["planning_error"],
            ),
            ensure_ascii=False,
        )
    )
    if allowed:
        plan = assert_review(response, snapshot, prompts)
        assert plan["metadata"]["parameter_requirements"]["choices"]["targets"] == [
            "EPSG:32651"
        ]
    else:
        assert (
            snapshot["pause"] is None and "human_review_required" not in response.text
        )


@pytest.mark.parametrize(
    "message",
    [
        "全部融合，SEPARATE_DISJOINT=true",
        "全部融合，拆分不相连部分",
        "全部融合，SEPARATE_DISJOINT=false 或 true",
    ],
)
@pytest.mark.parametrize("model_split", [False, True])
def test_review_explicit_split_is_never_silently_replaced(
    monkeypatch, tmp_path, message, model_split
):
    response, snapshot, prompts = submit(
        monkeypatch,
        tmp_path,
        lambda source: parameter_plan(
            source, "native:dissolve", {"FIELD": [], "SEPARATE_DISJOINT": model_split}
        ),
        message,
    )
    print(
        "REVIEW_FIX_RESULT "
        + json.dumps(
            dict(
                message=message,
                model_split=model_split,
                pause=snapshot["pause"],
                error=snapshot["state"]["planning_error"],
            ),
            ensure_ascii=False,
        )
    )
    assert snapshot["pause"] is None and "human_review_required" not in response.text


@pytest.mark.parametrize("word", ["不能", "不要", "不"])
def test_review_equivalent_no_dissolve_options_still_reach_review(
    monkeypatch, tmp_path, word
):
    response, snapshot, prompts = submit(
        monkeypatch, tmp_path, simple_buffer, "60米缓冲，每象限8段，" + word + "融合"
    )
    plan = assert_review(response, snapshot, prompts)
    assert plan["metadata"]["parameter_requirements"]["choices"]["buffer_dissolve"] == [
        False
    ]


@pytest.mark.parametrize("model_split,allowed", [(False, True), (True, False)])
def test_review_grouped_no_split_is_consumed(
    monkeypatch, tmp_path, model_split, allowed
):
    response, snapshot, prompts = submit(
        monkeypatch,
        tmp_path,
        lambda source: parameter_plan(
            source,
            "native:dissolve",
            {"FIELD": ["district"], "SEPARATE_DISJOINT": model_split},
        ),
        "以district分组融合，不拆分不相连部分",
    )
    if allowed:
        plan = assert_review(response, snapshot, prompts)
        assert (
            plan["tool_calls"][0]["arguments"]["parameters"]["SEPARATE_DISJOINT"]
            is False
        )
    else:
        assert (
            snapshot["pause"] is None and "human_review_required" not in response.text
        )


@pytest.mark.parametrize(
    "constrained,model_splits,allowed",
    [
        (0, [False, True], True),
        (0, [True, False], False),
        (1, [True, False], True),
        (1, [False, True], False),
    ],
)
def test_review_no_split_constraints_keep_dissolve_call_order(
    monkeypatch, tmp_path, constrained, model_splits, allowed
):
    from test_t06_workflow import same_family_plan

    first = "按district字段融合" + ("，不拆分不相连部分" if constrained == 0 else "")
    second = "按parcel_id字段融合" + ("，不拆分不相连部分" if constrained == 1 else "")
    response, snapshot, prompts = submit(
        monkeypatch,
        tmp_path,
        lambda source: same_family_plan(
            source,
            "native:dissolve",
            [
                {"FIELD": ["district"], "SEPARATE_DISJOINT": model_splits[0]},
                {"FIELD": ["parcel_id"], "SEPARATE_DISJOINT": model_splits[1]},
            ],
        ),
        first + "，然后" + second,
    )
    if allowed:
        plan = assert_review(response, snapshot, prompts)
        assert [
            call["arguments"]["parameters"]["SEPARATE_DISJOINT"]
            for call in plan["tool_calls"]
        ] == model_splits
    else:
        assert (
            snapshot["pause"] is None
            and "SEPARATE_DISJOINT" in snapshot["state"]["planning_error"]
        )


def test_review_no_split_survives_a_name_only_revision(monkeypatch, tmp_path):
    current = {"split": False}

    def proposal(source):
        return parameter_plan(
            source,
            "native:dissolve",
            {"FIELD": ["district"], "SEPARATE_DISJOINT": current["split"]},
        )

    response, snapshot, prompts = submit(
        monkeypatch, tmp_path, proposal, "按district字段融合，不拆分不相连部分"
    )
    plan = assert_review(response, snapshot, prompts)
    source = tmp_path / "new.geojson"
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    current["split"] = True
    client = TestClient(backend.app)
    assert (
        client.post(
            "/api/review",
            json=dict(
                thread_id="explicit-parameters",
                task_id=plan["task_id"],
                revision=plan["revision"],
                approved=False,
                advise="仅修改结果名称",
            ),
        ).status_code
        == 200
    )
    rejected = client.post("/api/resume/explicit-parameters")
    revised = client.get("/api/state/explicit-parameters").json()
    assert "event: error" in rejected.text and revised["pause"] is None
    assert "SEPARATE_DISJOINT" in revised["state"]["planning_error"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == checksum
    assert not (tmp_path / "shared" / "outputs").exists()


def test_review_unqualified_all_dissolve_does_not_invent_a_split_constraint(
    monkeypatch, tmp_path
):
    response, snapshot, prompts = submit(
        monkeypatch,
        tmp_path,
        lambda source: parameter_plan(
            source, "native:dissolve", {"FIELD": [], "SEPARATE_DISJOINT": True}
        ),
        "全部融合",
    )
    plan = assert_review(response, snapshot, prompts)
    assert plan["tool_calls"][0]["arguments"]["parameters"]["SEPARATE_DISJOINT"] is True


@pytest.mark.parametrize(
    "operation,fields", [("全部融合", []), ("以district分组融合", ["district"])]
)
@pytest.mark.parametrize(
    "option",
    [
        "保持不相连部分在同一要素中",
        "不拆分不相连部分",
        "SEPARATE_DISJOINT=false",
    ],
)
@pytest.mark.parametrize("negation", ["不要", "禁止"])
@pytest.mark.parametrize("model_split", [False, True])
def test_review_v2_negated_no_split_options_require_clarification(
    monkeypatch, tmp_path, operation, fields, option, negation, model_split
):
    response, snapshot, prompts = submit(
        monkeypatch,
        tmp_path,
        lambda source: parameter_plan(
            source,
            "native:dissolve",
            {"FIELD": fields, "SEPARATE_DISJOINT": model_split},
        ),
        operation + "，" + negation + " " + option,
    )
    assert snapshot["pause"] is None and "human_review_required" not in response.text
    assert (
        snapshot["state"]["planning_failure_kind"] == "request_rejected"
        and prompts == []
    )


@pytest.mark.parametrize(
    "operation,fields", [("全部融合", []), ("以district分组融合", ["district"])]
)
@pytest.mark.parametrize("joiner", ["且", "并"])
@pytest.mark.parametrize("requested_split", ["true", "false"])
@pytest.mark.parametrize("model_split", [False, True])
def test_review_v2_chinese_adjacent_split_keys_are_consumed(
    monkeypatch, tmp_path, operation, fields, joiner, requested_split, model_split
):
    response, snapshot, prompts = submit(
        monkeypatch,
        tmp_path,
        lambda source: parameter_plan(
            source,
            "native:dissolve",
            {"FIELD": fields, "SEPARATE_DISJOINT": model_split},
        ),
        operation + joiner + "SEPARATE_DISJOINT=" + requested_split,
    )
    if requested_split == "false" and model_split is False:
        plan = assert_review(response, snapshot, prompts)
        assert (
            plan["tool_calls"][0]["arguments"]["parameters"]["SEPARATE_DISJOINT"]
            is False
        )
    else:
        assert (
            snapshot["pause"] is None and "human_review_required" not in response.text
        )


@pytest.mark.parametrize(
    "advice,reordered,model_splits,allowed",
    [
        ("仅修改结果名称", False, [False, True], True),
        ("先按district字段融合，然后按parcel_id字段融合", False, [False, True], True),
        ("先按parcel_id字段融合，然后按district字段融合", True, [False, True], False),
        ("先按parcel_id字段融合，然后按district字段融合", True, [False, False], False),
        (
            "先按parcel_id字段融合，不拆分不相连部分，然后按district字段融合，不拆分不相连部分",
            True,
            [False, False],
            True,
        ),
    ],
)
def test_review_v2_reordered_dissolves_do_not_carry_options_by_old_index(
    monkeypatch, tmp_path, advice, reordered, model_splits, allowed
):
    from test_t06_workflow import same_family_plan

    current = {"revision": 1}

    def proposal(source):
        fields = (
            ["district", "parcel_id"]
            if current["revision"] == 1 or not reordered
            else ["parcel_id", "district"]
        )
        splits = [False, True] if current["revision"] == 1 else model_splits
        return same_family_plan(
            source,
            "native:dissolve",
            [
                {"FIELD": [field], "SEPARATE_DISJOINT": split}
                for field, split in zip(fields, splits)
            ],
        )

    response, snapshot, prompts = submit(
        monkeypatch,
        tmp_path,
        proposal,
        "按district字段融合，不拆分不相连部分，然后按parcel_id字段融合",
    )
    plan = assert_review(response, snapshot, prompts)
    source = tmp_path / "new.geojson"
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    current["revision"] = 2
    client = TestClient(backend.app)
    assert (
        client.post(
            "/api/review",
            json=dict(
                thread_id="explicit-parameters",
                task_id=plan["task_id"],
                revision=plan["revision"],
                approved=False,
                advise=advice,
            ),
        ).status_code
        == 200
    )
    resumed = client.post("/api/resume/explicit-parameters")
    revised = client.get("/api/state/explicit-parameters").json()
    if allowed:
        assert revised["pause"] and revised["pause"]["kind"] == "plan_review", revised[
            "state"
        ]["planning_error"]
        assert [
            call["arguments"]["parameters"]["SEPARATE_DISJOINT"]
            for call in revised["state"]["task_plan"]["tool_calls"]
        ] == model_splits
    else:
        assert revised["pause"] is None and "event: error" in resumed.text
        assert revised["state"]["planning_failure_kind"] == "request_rejected"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == checksum
    assert not (tmp_path / "shared" / "outputs").exists()
