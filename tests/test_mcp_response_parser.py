import json

from langchain_core.messages import ToolMessage
import pytest

from agent.tools.mcp_response_parser import MCPResponseParser


@pytest.mark.parametrize('source', ['/workspace/buffer.gpkg', '/workspace/points.geojson'])
def test_loaded_layer_source_is_reference_not_generated_artifact(source):
    observation = MCPResponseParser().parse('add_layer', {
        'status': 'success', 'result': {'id': 'layer-1', 'source': source},
    })
    assert observation.status == 'success' and observation.outcome == 'completed'
    assert observation.data == {'id': 'layer-1', 'source': source}
    assert observation.artifacts == []


@pytest.mark.parametrize('tool,reply', [
    ('capture_map_canvas', {}),
    ('capture_map_canvas', {'status': 'success', 'result': {}}),
    ('capture_map_canvas', {'status': 'success', 'result': {'success': True}}),
    ('execute_processing', {}),
    ('execute_processing', {'status': 'success', 'result': {}}),
    ('execute_processing', {'status': 'success', 'result': {'algorithm': 'native:buffer', 'result': {}}}),
])
def test_write_reply_without_required_result_is_unknown(tool, reply):
    observation = MCPResponseParser().parse(tool, reply)
    assert observation.status == 'malformed'
    assert observation.outcome == 'unknown'
    assert observation.retryable is False


def test_processing_statistic_ending_in_file_extension_is_not_an_artifact():
    observation = MCPResponseParser().parse('execute_processing', {
        'status': 'success', 'result': {'algorithm': 'native:basicstatisticsforfields',
                                        'result': {'COUNT': '1', 'MINORITY': 'sample.gpkg'}},
    })
    assert observation.status == 'success'
    assert observation.artifacts == []


def test_parser_normalizes_socket_success_and_extracts_artifact():
    observation = MCPResponseParser().parse(
        "capture_map_canvas",
        {
            "status": "success",
            "result": {"success": True, "path": "shared/screenshots/map.png",
                       "message": "Screenshot saved to shared/screenshots/map.png"},
        },
    )

    assert observation.status == "success"
    assert observation.error_type is None
    assert observation.data == {"success": True, "path": "shared/screenshots/map.png",
                                "message": "Screenshot saved to shared/screenshots/map.png"}
    assert observation.artifacts == [
        {"kind": "path", "path": "shared/screenshots/map.png"}
    ]
    assert observation.retryable is False


def test_parser_normalizes_tool_message_json_content():
    message = ToolMessage(
        content=json.dumps(
            {"status": "success", "result": {
                "success": True, "path": "/workspace/shared/outputs/task/r1/project/demo.qgz",
                "layer_count": 1}}
        ),
        tool_call_id="call-1",
        name="save_project",
    )

    observation = MCPResponseParser().parse("save_project", message)

    assert observation.status == "success"
    assert observation.artifacts == [
        {"kind": "path", "path": "/workspace/shared/outputs/task/r1/project/demo.qgz"}
    ]


@pytest.mark.parametrize('result', [
    {},
    {'success': True, 'path': '/workspace/project.qgz'},
    {'success': True, 'path': '/workspace/project.qgz', 'layer_count': True},
    {'success': True, 'path': '/workspace/project.qgz', 'layer_count': -1},
    {'success': True, 'path': 'relative/project.qgz', 'layer_count': 0},
    {'success': True, 'path': '/workspace/project.qgs', 'layer_count': 0},
])
def test_project_save_rejects_incomplete_or_invalid_success_result(result):
    observation = MCPResponseParser().parse('save_project', {'status': 'success', 'result': result})
    assert observation.status == 'malformed'
    assert observation.outcome == 'unknown'
    assert observation.retryable is False


def test_project_save_disconnect_is_never_automatically_retryable():
    observation = MCPResponseParser().parse('save_project', TimeoutError('socket timed out'))
    assert observation.status == 'disconnected'
    assert observation.outcome == 'unknown'
    assert observation.retryable is False


def test_parser_normalizes_content_block_json():
    observation = MCPResponseParser().parse(
        "execute_processing",
        [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "status": "success",
                        "result": {
                            "algorithm": "native:buffer",
                            "result": {"OUTPUT": "shared/output/buffer.gpkg"},
                        },
                    }
                ),
            }
        ],
    )

    assert observation.status == "success"
    assert {
        "kind": "path",
        "path": "shared/output/buffer.gpkg",
    } in observation.artifacts


def test_parser_classifies_top_level_error():
    observation = MCPResponseParser().parse(
        "add_layer", {"status": "error", "message": "Missing required parameter: path"}
    )

    assert observation.status == "error"
    assert observation.error_type == "parameter_error"
    assert observation.retryable is False


def test_parser_classifies_nested_success_false():
    observation = MCPResponseParser().parse(
        "capture_map_canvas",
        {
            "status": "success",
            "result": {
                "success": False,
                "error": "Failed to save rendered image to C:/bad/out.png",
            },
        },
    )

    assert observation.status == "error"
    assert observation.error_type == "execution_error"
    assert "Failed to save" in observation.message


def test_parser_classifies_execute_code_failure():
    observation = MCPResponseParser().parse(
        "execute_code",
        {
            "status": "success",
            "result": {
                "executed": False,
                "error": "NameError: name x is not defined",
                "traceback": "Traceback...",
            },
        },
    )

    assert observation.status == "error"
    assert observation.error_type == "execution_error"
    assert observation.retryable is False


def test_parser_classifies_malformed_plain_text():
    observation = MCPResponseParser().parse("ping", "not json")

    assert observation.status == "malformed"
    assert observation.error_type == "malformed_response"
    assert observation.retryable is False


def test_parser_classifies_disconnect_exception(mock_mcp_responses):
    observation = MCPResponseParser().parse("ping", mock_mcp_responses["disconnect"])

    assert observation.status == "disconnected"
    assert observation.error_type == "qgis_disconnected"
    assert observation.retryable is True


def test_mock_mcp_fixture_covers_minimum_response_classes(mock_mcp_responses):
    parser = MCPResponseParser()

    assert parser.parse("ping", mock_mcp_responses["success"]).status == "success"
    assert (
        parser.parse("add_layer", mock_mcp_responses["parameter_error"]).error_type
        == "parameter_error"
    )
    assert (
        parser.parse("add_layer", mock_mcp_responses["missing_file"]).error_type
        == "missing_file"
    )
    assert (
        parser.parse("ping", mock_mcp_responses["disconnect"]).status == "disconnected"
    )
