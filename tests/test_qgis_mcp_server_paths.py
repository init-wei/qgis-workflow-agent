from pathlib import Path

from qgis_mcp_server import qgis_mcp_server
from qgis_mcp_server.qgis_mcp_server import _resolve_local_path


def test_resolve_local_path_expands_project_relative_paths():
    resolved = Path(_resolve_local_path("shared/uploads/phase11_points.geojson"))

    assert resolved.is_absolute()
    assert resolved.parts[-3:] == ("shared", "uploads", "phase11_points.geojson")


def test_resolve_local_path_leaves_urls_unchanged():
    url = "postgres://localhost:5432/qgis"

    assert _resolve_local_path(url) == url


def test_capture_map_canvas_falls_back_to_render_map_base64(monkeypatch, tmp_path):
    calls = []
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
        "/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )

    class FakeQGIS:
        def send_command(self, command_type, params=None):
            calls.append((command_type, params))
            if command_type == "capture_map_canvas":
                return {
                    "status": "error",
                    "message": "Unknown command type: capture_map_canvas",
                }
            if command_type == "render_map_base64":
                return {
                    "status": "success",
                    "result": {
                        "base64_data": png_b64,
                        "mime_type": "image/png",
                        "width": 321,
                        "height": 123,
                    },
                }
            raise AssertionError(command_type)

    monkeypatch.setattr(qgis_mcp_server, "get_qgis_connection", lambda: FakeQGIS())

    response = qgis_mcp_server.capture_map_canvas(
        None,
        path=str(tmp_path / "fallback.png"),
        width=321,
        height=123,
    )

    assert calls[0][0] == "capture_map_canvas"
    assert calls[1] == (
        "render_map_base64",
        {"path": str(tmp_path / "fallback.png"), "width": 321, "height": 123},
    )
    assert (tmp_path / "fallback.png").exists()
    assert "base64_data" not in response
    assert '"fallback_tool": "render_map_base64"' in response


def test_capture_map_canvas_falls_back_to_canvas_screenshot(monkeypatch, tmp_path):
    calls = []
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
        "/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )

    class FakeQGIS:
        def send_command(self, command_type, params=None):
            calls.append((command_type, params))
            if command_type in {"capture_map_canvas", "render_map_base64"}:
                return {"status": "error", "message": f"Unknown command type: {command_type}"}
            if command_type == "get_canvas_screenshot":
                return {
                    "status": "success",
                    "result": {
                        "base64_data": png_b64,
                        "mime_type": "image/png",
                        "width": 640,
                        "height": 480,
                    },
                }
            raise AssertionError(command_type)

    monkeypatch.setattr(qgis_mcp_server, "get_qgis_connection", lambda: FakeQGIS())

    response = qgis_mcp_server.capture_map_canvas(
        None,
        path=str(tmp_path / "screenshot.png"),
    )

    assert [call[0] for call in calls] == [
        "capture_map_canvas",
        "render_map_base64",
        "get_canvas_screenshot",
    ]
    assert (tmp_path / "screenshot.png").exists()
    assert "base64_data" not in response
    assert '"fallback_tool": "get_canvas_screenshot"' in response
