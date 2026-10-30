from pathlib import Path
import json

import pytest

from qgis_mcp_server import qgis_mcp_server
from qgis_mcp_server.qgis_mcp_server import _resolve_local_path


def test_resolve_local_path_expands_project_relative_paths():
    resolved = Path(_resolve_local_path("shared/uploads/phase11_points.geojson"))

    assert resolved.is_absolute()
    assert resolved.parts[-3:] == ("shared", "uploads", "phase11_points.geojson")


def test_resolve_local_path_leaves_urls_unchanged():
    url = "postgres://localhost:5432/qgis"

    assert _resolve_local_path(url) == url


def test_capture_map_canvas_reports_unsupported_command_without_fallback(monkeypatch, tmp_path):
    calls = []

    class FakeQGIS:
        def send_command(self, command_type, params=None):
            calls.append((command_type, params))
            return {"status": "error", "message": f"Unknown command type: {command_type}"}

    monkeypatch.setattr(qgis_mcp_server, "get_qgis_connection", lambda: FakeQGIS())
    target = Path(__file__).resolve().parents[1] / "shared/outputs/protocol-test/r1/result/result.png"
    response = qgis_mcp_server.capture_map_canvas(None, path=str(target), width=321, height=123)

    assert calls == [("capture_map_canvas", {"path": str(target), "width": 321, "height": 123})]
    assert json.loads(response)["status"] == "error"
    assert not target.exists()


def test_capture_map_canvas_forwards_one_successful_request(monkeypatch, tmp_path):
    calls = []

    class FakeQGIS:
        def send_command(self, command_type, params=None):
            calls.append((command_type, params))
            return {"status": "success", "result": {"path": params["path"], "width": params["width"]}}

    monkeypatch.setattr(qgis_mcp_server, "get_qgis_connection", lambda: FakeQGIS())
    target = Path(__file__).resolve().parents[1] / "shared/outputs/protocol-test/r1/screenshot/screenshot.png"
    response = qgis_mcp_server.capture_map_canvas(None, path=str(target))

    assert calls == [("capture_map_canvas", {"path": str(target), "width": 800, "height": 600})]
    assert json.loads(response)["result"]["path"] == str(target)


def test_save_project_forwards_one_owned_absolute_path(monkeypatch, tmp_path):
    calls = []

    class FakeQGIS:
        def send_command(self, command_type, params=None):
            calls.append((command_type, params))
            return {"status": "success", "result": {
                "success": True, "path": params["path"], "layer_count": 0}}

    monkeypatch.setenv("QWA_ROOT", str(tmp_path))
    monkeypatch.setattr(qgis_mcp_server, "get_qgis_connection", lambda: FakeQGIS())
    relative = "shared/outputs/task-1/r1/project/result.qgz"
    response = qgis_mcp_server.save_project(None, path=relative)

    target = str(tmp_path / relative)
    assert calls == [("save_project", {"path": target})]
    assert json.loads(response)["result"]["path"] == target
    assert not Path(target).parent.exists()


@pytest.mark.parametrize('path', [
    'shared/outputs/task-1/r1/project/result.qgs',
    'shared/outputs/task-1/r1/project/result.QGZ',
    'shared/uploads/original.qgz',
])
def test_save_project_wrapper_rejects_bad_path_before_connecting(monkeypatch, tmp_path, path):
    monkeypatch.setenv("QWA_ROOT", str(tmp_path))

    def unexpected_connection():
        raise AssertionError('must reject destination before QGIS connection')

    monkeypatch.setattr(qgis_mcp_server, "get_qgis_connection", unexpected_connection)
    with pytest.raises(ValueError):
        qgis_mcp_server.save_project(None, path=path)
