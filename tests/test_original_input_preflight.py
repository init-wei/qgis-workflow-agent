"""Original inputs are checked before GIS execution; future outputs are not inputs."""

import asyncio
import hashlib
from pathlib import Path
import sqlite3

import pytest

from agent.planning.plan_schema import InputSpec, OutputSpec, TaskPlan, ToolCallPlan
from agent.planning.processing_preflight import validate_original_inputs, validate_processing_calls


def plan_for(paths, *, outputs=()):
    return TaskPlan(
        task_id="input-preflight", task_summary="Validate originals", steps=[],
        risk_level="low", review_status="approved",
        required_inputs=[InputSpec(input_id=f"in-{i}", description="Original", path=str(path))
                         for i, path in enumerate(paths)],
        expected_outputs=[OutputSpec(output_id=f"out-{i}", description="Future", path=str(path))
                          for i, path in enumerate(outputs)],
    )


def test_missing_later_original_rejects_whole_plan(tmp_path):
    first = tmp_path / "present.geojson"
    first.write_text('{"type":"FeatureCollection","features":[]}')
    plan = plan_for([first, tmp_path / "missing.geojson"])

    with pytest.raises(ValueError, match="in-1.*unavailable"):
        validate_original_inputs(plan)

    assert list(tmp_path.iterdir()) == [first]


def test_future_output_can_be_absent_while_original_container_is_readable(tmp_path):
    source = tmp_path / "source.gpkg"
    source.write_bytes(b"registered input bytes")
    output = tmp_path / "not-created-yet.gpkg"

    validate_original_inputs(plan_for([f"{source}|layername=points"], outputs=[output]))

    assert not output.exists()
    assert source.read_bytes() == b"registered input bytes"


def test_changed_registered_input_is_rejected(tmp_path):
    source = tmp_path / "changed.geojson"
    source.write_bytes(b"unexpected replacement")
    plan = plan_for([])
    plan.required_inputs = [InputSpec(
        input_id="registered", description="Original", path=str(source),
        sha256="bee3424d61d00f88e25ce8a123ec0062abc65a7d8dc9189823f4e975327c8b5d",
    )]

    with pytest.raises(ValueError, match="registered.*changed"):
        validate_original_inputs(plan)


def test_registered_input_matches_known_fixture_checksum():
    source = Path(__file__).parent / "fixtures/gis/phase11_points.geojson"
    plan = plan_for([])
    plan.required_inputs = [InputSpec(
        input_id="registered", description="Original", path=str(source),
        sha256="bee3424d61d00f88e25ce8a123ec0062abc65a7d8dc9189823f4e975327c8b5d",
    )]

    validate_original_inputs(plan)


def test_missing_original_stops_before_processing_metadata_request(tmp_path):
    plan = plan_for([tmp_path / "missing.geojson"])
    plan.tool_calls = [ToolCallPlan(step_id=1, tool_name="execute_processing", arguments={
        "algorithm": "native:buffer", "parameters": {"INPUT": str(tmp_path / "missing.geojson")},
    })]

    class GIS:
        calls = []

        async def call_tool(self, name, arguments):
            self.calls.append(name)
            return {"status": "success", "result": {"processing_validation": []}}

    gis = GIS()
    with pytest.raises(ValueError, match="in-0.*unavailable"):
        asyncio.run(validate_processing_calls(plan, gis))
    assert gis.calls == []


def test_registered_sqlite_input_rejects_committed_wal_despite_unchanged_main_checksum(tmp_path):
    source = tmp_path / 'source.gpkg'
    with sqlite3.connect(source) as setup:
        setup.execute('CREATE TABLE points (value INTEGER)')
        setup.execute('INSERT INTO points VALUES (1)')
    with sqlite3.connect(source) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        plan = plan_for([source])
        plan.required_inputs[0].sha256 = before
        writer.execute('UPDATE points SET value=2')
        writer.commit()
        assert writer.execute('SELECT value FROM points').fetchone() == (2,)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
        with pytest.raises(ValueError, match='SQLite transaction sidecar'):
            validate_original_inputs(plan)
        assert Path(str(source) + '-wal').is_file()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before


@pytest.mark.parametrize('suffix', ['-wal', '-shm', '-journal'])
def test_original_sqlite_sidecars_are_rejected_without_cleanup_even_when_empty(tmp_path, suffix):
    source = tmp_path / 'source.GPKG'
    source.write_bytes(b'input')
    sidecar = Path(str(source) + suffix)
    sidecar.touch()
    with pytest.raises(ValueError, match='SQLite transaction sidecar'):
        validate_original_inputs(plan_for([f'{source}|layername=points']))
    assert sidecar.exists() and source.read_bytes() == b'input'
