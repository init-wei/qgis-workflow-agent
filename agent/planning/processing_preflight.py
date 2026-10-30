"""Read-only whole-plan checks against the current QGIS parameter definitions."""

import asyncio
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import subprocess
from agent.tools.mcp_response_parser import MCPResponseParser
from qgis_mcp_server.qgis_mcp_plugin.artifact_paths import require_standalone_sqlite


def validate_original_inputs(task_plan):
    """Read every original before any call; generated output references may be absent."""
    for source in task_plan.required_inputs:
        try:
            if not source.path or '://' in source.path:
                raise OSError('Expected a local input file')
            path = Path(source.path.split('|', 1)[0])
            require_standalone_sqlite(path)
            if not path.is_file():
                raise OSError('Expected a regular input file')
            with path.open('rb') as stream:
                if source.sha256:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != source.sha256:
                        raise ValueError(f"Original input '{source.input_id}' changed since upload.")
                else:
                    stream.read(1)
            require_standalone_sqlite(path)
        except OSError as exc:
            raise ValueError(f"Original input '{source.input_id}' is unavailable or unreadable.") from exc


async def validate_processing_calls(task_plan, client=None, *, inspector=None):
    await asyncio.to_thread(validate_original_inputs, task_plan)
    calls = [call for call in task_plan.tool_calls if call.tool_name == 'execute_processing']
    if not calls:
        return
    requests = [{'step_id': call.step_id, **call.arguments} for call in calls]
    if client is None:
        raw = await asyncio.to_thread(inspector or inspect_processing_parameters, requests)
    else:
        raw = await client.call_tool('get_qgis_info', {'processing_requests': requests})
    observation = MCPResponseParser().parse('get_qgis_info', raw)
    if observation.status != 'success' or not isinstance(observation.data, dict):
        raise ValueError(f'QGIS Processing preflight failed: {observation.message}')
    results = observation.data.get('processing_validation')
    if not isinstance(results, list) or len(results) != len(calls):
        raise ValueError('QGIS did not validate every planned Processing call')
    references = {item.input_id: item.path for item in task_plan.required_inputs}
    references.update({item.output_id: item.path for item in task_plan.expected_outputs})
    steps = {step.step_id: step for step in task_plan.steps}
    for call, result in zip(calls, results):
        if result.get('step_id') != call.step_id or result.get('valid') is not True:
            raise ValueError('QGIS Processing validation does not match the planned call')
        step = steps[call.step_id]
        expected_inputs = [references[ref] for ref in step.input_refs]
        expected_outputs = [references[ref] for ref in step.output_refs]
        if (sorted(result.get('source_paths', [])) != sorted(expected_inputs)
                or sorted(result.get('output_paths', [])) != sorted(expected_outputs)):
            raise ValueError('QGIS Processing parameters do not consume/produce exactly the declared references')


async def processing_capabilities(inspector=None):
    """Provide locally installed definitions to planning, without contacting MCP."""
    try:
        raw = (await asyncio.to_thread(inspector, {"capabilities": True})
               if inspector is not None
               else await asyncio.to_thread(_cached_processing_capabilities))
        observation = MCPResponseParser().parse('get_qgis_info', raw)
        if observation.status != 'success' or not isinstance(observation.data, dict):
            raise ValueError(observation.message or 'QGIS capabilities are unavailable')
        capabilities = observation.data.get('processing_capabilities')
        if not isinstance(capabilities, list):
            raise ValueError('QGIS capability definitions are unavailable')
        return json.dumps(observation.data, ensure_ascii=False)
    except (ValueError, OSError) as exc:
        return f'不可用：{exc}。需要安装并检查本机 QGIS 才能规划 Processing。'


@lru_cache(maxsize=1)
def _cached_processing_capabilities():
    return inspect_processing_parameters({"capabilities": True})


def inspect_processing_parameters(requests):
    """Use the isolated distribution Python CLI; an unapproved plan never contacts MCP."""
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'inspect_processing.py'
    try:
        result = subprocess.run(['/usr/bin/python3', str(script)], input=json.dumps(requests),
                                check=False, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('System QGIS Processing inspector is unavailable') from exc
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError('System QGIS Processing inspector returned invalid JSON') from exc
    if result.returncode != 0:
        raise ValueError(payload.get('message') or 'System QGIS Processing inspection failed')
    return payload
