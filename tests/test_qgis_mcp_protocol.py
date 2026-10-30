import asyncio
import json
import socket

import pytest

from qgis_mcp_server import qgis_mcp_server
from qgis_mcp_server.qgis_mcp_plugin.processing_policy import SUPPORTED_PROCESSING_ALGORITHMS


ALLOWED_TOOLS = {
    "ping",
    "get_qgis_info",
    "get_project_info",
    "get_layers",
    "add_layer",
    "get_layer_features",
    "capture_map_canvas",
    "execute_processing",
}

ALLOWED_ALGORITHMS = {
    "native:reprojectlayer",
    "native:buffer",
    "native:clip",
    "native:dissolve",
    "qgis:statisticsbycategories",
    "native:basicstatisticsforfields",
}


class FakeSocket:
    def __init__(self, response=None, recv_error=None, chunk_size=None):
        self.response = bytearray(response or b"")
        self.recv_error = recv_error
        self.chunk_size = chunk_size
        self.sent = []
        self.closed = False

    def sendall(self, data):
        self.sent.append(bytes(data))

    def recv(self, size):
        if self.recv_error is not None:
            raise self.recv_error
        if self.chunk_size is not None:
            size = min(size, self.chunk_size)
        chunk = bytes(self.response[:size])
        del self.response[:size]
        return chunk

    def close(self):
        self.closed = True


def test_mcp_tool_list_is_the_explicit_safe_subset():
    tools = {
        tool.name: tool for tool in asyncio.run(qgis_mcp_server.mcp.list_tools())
    }

    assert set(tools) == ALLOWED_TOOLS
    assert {"path", "layer_type"} <= set(tools["add_layer"].inputSchema["required"])
    assert {"algorithm", "parameters"} <= set(
        tools["execute_processing"].inputSchema["required"]
    )


def test_client_round_trips_one_raw_json_message(monkeypatch):
    fake_socket = FakeSocket(
        response=b'{"status":"success","result":{"pong":true}}',
        chunk_size=3,
    )
    client = qgis_mcp_server.QgisMCPServer()

    def connect_once():
        client.socket = fake_socket
        return True

    monkeypatch.setattr(client, "connect", connect_once)

    response = client.send_command("ping")

    assert response == {"status": "success", "result": {"pong": True}}
    assert fake_socket.sent == [b'{"type": "ping", "params": {}}']


def test_write_timeout_does_not_retry_with_another_protocol(monkeypatch):
    first_socket = FakeSocket(recv_error=socket.timeout("response deadline"))
    second_socket = FakeSocket(response=b'{"status":"success"}')
    sockets = iter((first_socket, second_socket))
    connect_calls = []
    client = qgis_mcp_server.QgisMCPServer()

    def connect_once():
        connect_calls.append(True)
        client.socket = next(sockets)
        return True

    monkeypatch.setattr(client, "connect", connect_once)

    result = client.send_command(
        "execute_processing",
        {"algorithm": "native:buffer", "parameters": {"INPUT": "layer"}},
    )

    assert result is None
    assert len(connect_calls) == 1
    assert len(first_socket.sent) == 1
    assert json.loads(first_socket.sent[0]) == {
        "type": "execute_processing",
        "params": {"algorithm": "native:buffer", "parameters": {"INPUT": "layer"}},
    }
    assert second_socket.sent == []


def test_processing_tool_rejects_unknown_algorithm_before_socket_write(monkeypatch):
    calls = []

    class FakeQGIS:
        def send_command(self, command_type, params=None):
            calls.append((command_type, params))
            return {"status": "success"}

    monkeypatch.setattr(qgis_mcp_server, "get_qgis_connection", lambda: FakeQGIS())

    with pytest.raises(ValueError, match="not supported"):
        qgis_mcp_server.execute_processing(
            None,
            algorithm="native:centroid",
            parameters={"INPUT": "roads", "OUTPUT": "result.gpkg"},
        )

    assert calls == []


@pytest.mark.parametrize("parameters", [{}, None])
def test_processing_tool_rejects_missing_parameters_before_socket_write(monkeypatch, parameters):
    calls = []

    class FakeQGIS:
        def send_command(self, command_type, params=None):
            calls.append((command_type, params))
            return {"status": "success"}

    monkeypatch.setattr(qgis_mcp_server, "get_qgis_connection", lambda: FakeQGIS())

    with pytest.raises(ValueError, match="parameters"):
        qgis_mcp_server.execute_processing(
            None,
            algorithm="native:buffer",
            parameters=parameters,
        )

    assert calls == []


def test_processing_whitelist_is_exact():
    assert SUPPORTED_PROCESSING_ALGORITHMS == ALLOWED_ALGORITHMS


@pytest.mark.parametrize("algorithm", sorted(ALLOWED_ALGORITHMS))
def test_supported_processing_algorithm_is_forwarded_explicitly(monkeypatch, algorithm):
    calls = []

    class FakeQGIS:
        def send_command(self, command_type, params=None):
            calls.append((command_type, params))
            return {"status": "success"}

    monkeypatch.setattr(qgis_mcp_server, "get_qgis_connection", lambda: FakeQGIS())
    parameters = {"INPUT": "roads", "OUTPUT": "shared/outputs/protocol-test/r1/result/result.gpkg"}

    qgis_mcp_server.execute_processing(None, algorithm=algorithm, parameters=parameters)

    assert calls == [
        (
            "execute_processing",
            {"algorithm": algorithm, "parameters": parameters},
        )
    ]


def test_plugin_policy_requires_algorithm_parameters_from_qgis_schema():
    from qgis_mcp_server.qgis_mcp_plugin.processing_policy import (
        validate_processing_request,
        validate_required_parameters,
    )

    class ParameterDefinition:
        FlagOptional = 8
        FlagHidden = 4

        def __init__(self, name, flags=0):
            self._name = name
            self._flags = flags

        def name(self):
            return self._name

        def flags(self):
            return self._flags

    definitions = [
        ParameterDefinition("INPUT"),
        ParameterDefinition("DISTANCE"),
        ParameterDefinition("SEGMENTS", flags=ParameterDefinition.FlagOptional),
        ParameterDefinition("INTERNAL", flags=ParameterDefinition.FlagHidden),
    ]

    with pytest.raises(ValueError, match="DISTANCE"):
        validate_required_parameters({"INPUT": "roads"}, definitions)
