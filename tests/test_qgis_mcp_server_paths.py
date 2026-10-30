from pathlib import Path
import json

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
