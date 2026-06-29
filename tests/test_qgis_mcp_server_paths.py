from pathlib import Path

from qgis_mcp_server.qgis_mcp_server import _resolve_local_path


def test_resolve_local_path_expands_project_relative_paths():
    resolved = Path(_resolve_local_path("shared/uploads/phase11_points.geojson"))

    assert resolved.is_absolute()
    assert resolved.parts[-3:] == ("shared", "uploads", "phase11_points.geojson")


def test_resolve_local_path_leaves_urls_unchanged():
    url = "postgres://localhost:5432/qgis"

    assert _resolve_local_path(url) == url
