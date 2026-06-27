import pytest


@pytest.fixture
def mock_mcp_responses():
    return {
        "success": {"status": "success", "result": {"path": "shared/output/map.png"}},
        "parameter_error": {
            "status": "error",
            "message": "Missing required parameter: path",
        },
        "missing_file": {
            "status": "error",
            "message": "Layer is not valid: data/missing.gpkg",
        },
        "disconnect": ConnectionError(
            "Could not connect to Qgis. Make sure the Qgis plugin is running."
        ),
    }
