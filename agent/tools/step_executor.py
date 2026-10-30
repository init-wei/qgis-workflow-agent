#!/usr/bin/env python3
"""Minimal controlled executor for one planned MCP tool call."""

from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import stat
from time import perf_counter
from typing import Iterable
from uuid import uuid4

from agent.tools.mcp_response_parser import MCPResponseParser
from agent.tools.tool_contracts import (
    ExecutionTrace,
    StepAttempt,
    ToolCallPlan,
    ToolObservation,
)
from agent.tools.tool_policy import ToolPolicy
from agent.tools.tool_registry import ToolRegistry
from qgis_mcp_server.qgis_mcp_plugin.artifact_paths import (
    ActiveSQLiteSidecar,
    artifact_component_paths,
    call_output_paths,
    require_standalone_sqlite,
    resolve_owned_output_path,
    validate_qgz_archive,
)


class StepExecutor:
    """Execute a single ToolCallPlan and append a replay-friendly trace attempt."""

    def __init__(
        self,
        mcp_client,
        registry: ToolRegistry,
        workspace_root: str | Path,
        parser: MCPResponseParser | None = None,
        policy: ToolPolicy | None = None,
    ):
        self.mcp_client = mcp_client
        self.registry = registry
        self.workspace_root = Path(workspace_root).resolve()
        self.parser = parser or MCPResponseParser()
        self.policy = policy or ToolPolicy(
            registry=registry, workspace_root=workspace_root
        )

    async def execute(
        self,
        call: ToolCallPlan,
        trace: ExecutionTrace,
        allowed_tools: Iterable[str] | None = None,
        approved: bool = False,
    ) -> StepAttempt:
        started_at = datetime.now(UTC)
        start_counter = perf_counter()

        policy_decision = self.policy.evaluate(
            call, allowed_tools=allowed_tools, approved=approved
        )
        if not policy_decision.allowed:
            observation = ToolObservation(
                tool_name=call.tool_name,
                status="error",
                error_type="unknown_error",
                message=policy_decision.message or policy_decision.reason,
                raw_response=policy_decision.model_dump(),
                retryable=False,
                outcome="not_started",
            )
            attempt = self._build_attempt(
                call=call,
                observation=observation,
                status="blocked",
                started_at=started_at,
                start_counter=start_counter,
                metadata={"policy_reason": policy_decision.reason},
            )
            trace.append_attempt(attempt)
            return attempt

        try:
            raw_response = await self.mcp_client.call_tool(
                call.tool_name, call.arguments
            )
        except Exception as exc:
            observation = self.parser.parse(call.tool_name, exc)
            observation = observation.model_copy(
                update={
                    "raw_response": {
                        "exception_type": type(exc).__name__,
                        "message": str(exc),
                    }
                }
            )
        else:
            observation = self.parser.parse(call.tool_name, raw_response)
            if observation.status == "success" and call.tool_name in {
                "capture_map_canvas", "execute_processing", "save_project"
            }:
                try:
                    self._verify_output_files(call, observation)
                except ActiveSQLiteSidecar as exc:
                    # The tool completed, but its database is not a verifiable
                    # standalone artifact. Keep tool facts; the executor aborts
                    # on this separate integrity evidence before spatial readback.
                    for artifact in observation.artifacts:
                        artifact.update(deliverable=False, integrity_validation={
                            'status': 'failed', 'errors': [str(exc)],
                        })
                except (ValueError, OSError) as exc:
                    observation = observation.model_copy(update={
                        "status": "error", "error_type": "execution_error",
                        "message": f"Cannot verify the write result: {exc}",
                        "outcome": "unknown", "retryable": False,
                    })

        status = "success" if observation.status == "success" else "failed"
        attempt = self._build_attempt(
            call=call,
            observation=observation,
            status=status,
            started_at=started_at,
            start_counter=start_counter,
        )
        trace.append_attempt(attempt)
        return attempt

    def _verify_output_files(self, call: ToolCallPlan, observation: ToolObservation) -> None:
        if (call.tool_name == "execute_processing"
                and observation.data.get("algorithm") != call.arguments.get("algorithm")):
            raise ValueError("Processing response names a different algorithm")
        expected = {
            resolve_owned_output_path(path, self.workspace_root)
            for path in call_output_paths(call.tool_name, call.arguments)
        }
        actual = {
            resolve_owned_output_path(item["path"], self.workspace_root)
            for item in observation.artifacts if item.get("kind") == "path"
        }
        if actual != expected or (call.tool_name in {"capture_map_canvas", "save_project"} and len(expected) != 1):
            raise ValueError("returned artifact paths differ from the requested outputs")
        for path in expected:
            components = [self._read_file_evidence(
                resolve_owned_output_path(str(component), self.workspace_root),
                project=call.tool_name == "save_project",
            ) for component in artifact_component_paths(path)]
            for item in observation.artifacts:
                if (item.get("kind") == "path"
                        and resolve_owned_output_path(item["path"], self.workspace_root) == path):
                    item.update(size_bytes=components[0]["size_bytes"], sha256=components[0]["sha256"])
                    if len(components) > 1:
                        item["components"] = components[1:]

    def _read_file_evidence(self, path: Path, *, project: bool = False) -> dict:
        require_standalone_sqlite(path)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o444:
                raise ValueError(f"artifact is not a readable file: {path}")
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            if project:
                validate_qgz_archive(stream)
            after = os.fstat(stream.fileno())
        if not info.st_size:
            raise ValueError(f"artifact is empty: {path}")
        if (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f"artifact changed while being read: {path}")
        require_standalone_sqlite(path)
        return {"path": str(path), "size_bytes": info.st_size, "sha256": digest.hexdigest()}

    def _build_attempt(
        self,
        call: ToolCallPlan,
        observation: ToolObservation,
        status: str,
        started_at: datetime,
        start_counter: float,
        metadata: dict | None = None,
    ) -> StepAttempt:
        finished_at = datetime.now(UTC)
        duration_ms = max(0, round((perf_counter() - start_counter) * 1000))
        return StepAttempt(
            attempt_id=str(uuid4()),
            step_id=call.step_id,
            tool_call=call,
            observation=observation,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            artifacts=list(observation.artifacts),
            metadata=metadata or {},
        )
