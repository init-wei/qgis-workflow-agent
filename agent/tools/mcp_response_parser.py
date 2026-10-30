#!/usr/bin/env python3
"""Normalize MCP tool responses into ToolObservation objects."""

import json
from pathlib import Path
from typing import Any

from langchain_core.messages import ToolMessage

from agent.tools.tool_contracts import ToolObservation
from qgis_mcp_server.qgis_mcp_plugin.artifact_paths import WRITE_TOOLS, call_output_paths


class MCPResponseParser:
    """Parser for current QGIS MCP socket and HTTP wrapper response shapes."""

    def parse(self, tool_name: str, raw_response: Any) -> ToolObservation:
        observation = self._parse(tool_name, raw_response)
        if tool_name in WRITE_TOOLS:
            payload = self._coerce_payload(raw_response)
            outcome = "completed" if observation.status == "success" else "unknown"
            if isinstance(payload, dict) and observation.status == "error" and payload.get("outcome") == "not_started":
                outcome = "not_started"
            observation = observation.model_copy(update={"outcome": outcome, "retryable": False})
        return observation

    def _parse(self, tool_name: str, raw_response: Any) -> ToolObservation:
        if isinstance(raw_response, BaseException):
            return self._parse_exception(tool_name, raw_response)

        parsed = self._coerce_payload(raw_response)
        if parsed is None:
            return ToolObservation(
                tool_name=tool_name,
                status="disconnected",
                error_type="qgis_disconnected",
                message="No QGIS response was received; the operation outcome is unknown.",
                raw_response=raw_response,
                retryable=False,
            )
        if parsed is _MALFORMED:
            return ToolObservation(
                tool_name=tool_name,
                status="malformed",
                error_type="malformed_response",
                message="MCP response is not valid JSON or structured content.",
                raw_response=raw_response,
                retryable=False,
            )

        if isinstance(parsed, dict):
            return self._parse_dict(tool_name, parsed, raw_response)

        return ToolObservation(
            tool_name=tool_name,
            status="malformed",
            error_type="malformed_response",
            message="QGIS response must be a structured result.",
            raw_response=raw_response,
            retryable=False,
        )

    def _parse_exception(self, tool_name: str, exc: BaseException) -> ToolObservation:
        message = str(exc)
        return ToolObservation(
            tool_name=tool_name,
            status="disconnected" if self._is_disconnect(message) else "error",
            error_type=(
                "qgis_disconnected" if self._is_disconnect(message) else "unknown_error"
            ),
            message=message,
            raw_response=exc,
            retryable=self._is_disconnect(message),
        )

    def _parse_dict(
        self, tool_name: str, payload: dict[str, Any], raw_response: Any
    ) -> ToolObservation:
        if self._is_error_wrapper(payload):
            message = self._message_from_payload(payload)
            return ToolObservation(
                tool_name=tool_name,
                status="error",
                error_type=self._classify_error(message, payload),
                message=message,
                data=payload.get("result"),
                artifacts=self._extract_artifacts(payload.get("result")),
                raw_response=raw_response,
                retryable=False,
            )

        data = payload.get("result") if payload.get("status") == "success" else payload
        nested_error = self._nested_error(data)
        if nested_error:
            return ToolObservation(
                tool_name=tool_name,
                status="error",
                error_type=self._classify_error(nested_error, data),
                message=nested_error,
                data=data,
                artifacts=self._extract_artifacts(data),
                raw_response=raw_response,
                retryable=False,
            )

        valid_write_result = True
        if tool_name == "add_layer":
            valid_write_result = (
                payload.get("status") == "success"
                and isinstance(data, dict)
                and isinstance(data.get("id"), str)
                and bool(data["id"].strip())
            )
        elif tool_name == "capture_map_canvas":
            valid_write_result = (
                payload.get("status") == "success"
                and isinstance(data, dict)
                and data.get("success") is True
                and isinstance(data.get("path"), str)
                and bool(data["path"].strip())
            )
        elif tool_name == "execute_processing":
            valid_write_result = (
                payload.get("status") == "success"
                and isinstance(data, dict)
                and isinstance(data.get("algorithm"), str)
                and bool(data["algorithm"].strip())
                and isinstance(data.get("result"), dict)
                and bool(data["result"])
                and all(isinstance(key, str) and isinstance(value, str)
                        for key, value in data["result"].items())
            )
        elif tool_name == "save_project":
            valid_write_result = (
                payload.get("status") == "success"
                and isinstance(data, dict)
                and data.get("success") is True
                and isinstance(data.get("path"), str)
                and Path(data["path"]).is_absolute()
                and Path(data["path"]).suffix == ".qgz"
                and type(data.get("layer_count")) is int
                and data["layer_count"] >= 0
            )
        if not valid_write_result:
            return ToolObservation(
                tool_name=tool_name,
                status="malformed",
                error_type="malformed_response",
                message="QGIS did not return the required write result; verify the operation outcome.",
                data=data,
                raw_response=raw_response,
                retryable=False,
            )

        if tool_name in {"capture_map_canvas", "save_project"}:
            artifacts = [{"kind": "path", "path": data["path"]}]
        elif tool_name == "execute_processing":
            artifacts = [
                {"kind": "path", "path": path}
                for path in call_output_paths(tool_name, {"parameters": data["result"]})
            ]
        else:
            artifacts = self._extract_artifacts(data)

        return ToolObservation(
            tool_name=tool_name,
            status="success",
            data=data,
            artifacts=artifacts,
            raw_response=raw_response,
            retryable=False,
        )

    def _coerce_payload(self, raw_response: Any) -> Any:
        if isinstance(raw_response, ToolMessage):
            artifact = getattr(raw_response, "artifact", None)
            if isinstance(artifact, dict):
                structured = artifact.get("structured_content") or artifact.get(
                    "structuredContent"
                )
                if structured is not None:
                    return structured
            return self._coerce_payload(getattr(raw_response, "content", None))

        if isinstance(raw_response, dict):
            if (
                raw_response.get("type") == "text"
                and raw_response.get("text") is not None
            ):
                return self._coerce_payload(raw_response.get("text"))
            structured = raw_response.get("structured_content") or raw_response.get(
                "structuredContent"
            )
            if structured is not None:
                return structured
            content = raw_response.get("content")
            if content is not None:
                parsed_content = self._coerce_payload(content)
                return (
                    parsed_content if parsed_content is not _MALFORMED else raw_response
                )
            return raw_response

        if isinstance(raw_response, list):
            for item in raw_response:
                parsed = self._coerce_payload(item)
                if parsed is not _MALFORMED:
                    return parsed
            return _MALFORMED

        if isinstance(raw_response, str):
            try:
                return json.loads(raw_response)
            except json.JSONDecodeError:
                return _MALFORMED

        return raw_response

    def _is_error_wrapper(self, payload: dict[str, Any]) -> bool:
        return payload.get("status") == "error" or (
            "error" in payload and payload.get("success") is not True
        )

    def _message_from_payload(self, payload: dict[str, Any]) -> str:
        message = (
            payload.get("message") or payload.get("error") or payload.get("traceback")
        )
        return str(message or "Unknown MCP error")

    def _nested_error(self, data: Any) -> str | None:
        if not isinstance(data, dict):
            return None
        if data.get("success") is False:
            return str(
                data.get("error") or data.get("message") or "MCP tool reported failure"
            )
        if data.get("executed") is False:
            return str(
                data.get("error")
                or data.get("stderr")
                or data.get("traceback")
                or "Code execution failed"
            )
        if data.get("error"):
            return str(data["error"])
        return None

    def _classify_error(self, message: str, payload: Any) -> str:
        text = f"{message} {payload}".lower()
        if self._is_disconnect(text):
            return "qgis_disconnected"
        if any(
            token in text
            for token in (
                "missing required parameter",
                "required field",
                "invalid parameter",
            )
        ):
            return "parameter_error"
        if any(
            token in text
            for token in ("not found", "not valid", "no such file", "missing file")
        ):
            return "missing_file"
        if any(
            token in text
            for token in (
                "traceback",
                "exception",
                "nameerror",
                "processing error",
                "failed to",
            )
        ):
            return "execution_error"
        return "unknown_error"

    def _is_disconnect(self, message: str) -> bool:
        text = message.lower()
        return any(
            token in text
            for token in (
                "could not connect",
                "connection refused",
                "disconnected",
                "socket",
                "qgis plugin",
                "timeout",
                "timed out",
            )
        )

    def _extract_artifacts(self, data: Any) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        self._collect_paths(data, artifacts)
        return artifacts

    def _collect_paths(self, value: Any, artifacts: list[dict[str, Any]]) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if key in {
                    "path",
                    "saved",
                    "loaded",
                    "rendered",
                    "created",
                } and isinstance(nested, str):
                    artifacts.append({"kind": "path", "path": nested})
                elif self._looks_like_artifact_path(nested):
                    artifacts.append({"kind": "path", "path": nested})
                elif key in {"result", "outputs"}:
                    self._collect_paths(nested, artifacts)
                elif isinstance(nested, (dict, list)):
                    self._collect_paths(nested, artifacts)
        elif isinstance(value, list):
            for item in value:
                self._collect_paths(item, artifacts)

    def _looks_like_artifact_path(self, value: Any) -> bool:
        if not isinstance(value, str):
            return False
        lowered = value.lower()
        return any(
            lowered.endswith(ext)
            for ext in (".gpkg", ".shp", ".tif", ".tiff", ".png", ".qgz", ".qgs")
        )


class _Malformed:
    pass


_MALFORMED = _Malformed()
