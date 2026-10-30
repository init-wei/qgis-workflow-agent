"""Processing preflight through the distribution Python, without MCP or writes."""

import hashlib
import json
from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
COMMAND = ["/usr/bin/python3", str(ROOT / "scripts/inspect_processing.py")]


def inspect(requests):
    return subprocess.run(
        COMMAND, input=json.dumps(requests), capture_output=True, text=True, timeout=30,
    )


def test_valid_processing_inspection_returns_reference_paths_without_writing(tmp_path):
    source = ROOT / "tests/fixtures/gis/phase11_points.geojson"
    output = tmp_path / "projected.gpkg"
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()

    result = inspect([{
        "step_id": 1, "algorithm": "native:reprojectlayer",
        "parameters": {"INPUT": str(source), "TARGET_CRS": "EPSG:32650", "OUTPUT": str(output)},
    }])

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["result"]["processing_validation"] == [{
        "step_id": 1, "valid": True,
        "source_paths": [str(source)], "output_paths": [str(output)],
    }]
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []
    assert hashlib.sha256(source.read_bytes()).hexdigest() == checksum


def test_later_invalid_processing_does_not_execute_earlier_request(tmp_path):
    source = ROOT / "tests/fixtures/gis/phase11_points.geojson"
    projected = tmp_path / "projected.gpkg"
    buffered = tmp_path / "buffered.gpkg"

    result = inspect([
        {"step_id": 1, "algorithm": "native:reprojectlayer", "parameters": {
            "INPUT": str(source), "TARGET_CRS": "EPSG:32650", "OUTPUT": str(projected),
        }},
        {"step_id": 2, "algorithm": "native:buffer", "parameters": {
            "INPUT": str(projected), "DISTANCE": "not-a-number", "SEGMENTS": 8,
            "END_CAP_STYLE": 0, "JOIN_STYLE": 0, "MITER_LIMIT": 2,
            "DISSOLVE": False, "SEPARATE_DISJOINT": False, "OUTPUT": str(buffered),
        }},
    ])

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["outcome"] == "not_started"
    assert "DISTANCE" in payload["message"]
    assert list(tmp_path.iterdir()) == []
