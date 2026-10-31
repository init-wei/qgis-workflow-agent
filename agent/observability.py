"""Optional LangSmith tracing with a small, allowlisted telemetry payload.

The graph/checkpointer and execution records remain the business sources of truth.
"""

import asyncio
from contextlib import contextmanager
import logging
import math
import os
import re
import threading
from urllib.parse import urlsplit

from langsmith import Client, get_tracing_context, trace, tracing_context
from urllib3.util import Retry

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_client = None
_client_settings = None
_legacy_conflict = False
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_METADATA_TEXT = {
    "session_id", "task_id", "case_id", "langgraph_node", "tool_name",
    "algorithm", "status", "outcome", "ls_provider", "ls_model_name",
    "attempt_id", "sha256",
}
_METADATA_INTEGER = {"revision", "step_id", "size_bytes", "check_count", "error_count"}
_USAGE_COUNTS = {"input_tokens", "output_tokens", "total_tokens"}
_USAGE_DETAILS = {"input_token_details", "output_token_details"}
_USAGE_DETAIL_NAMES = {"cache_read", "cache_creation", "cache_read_over_200k", "ephemeral_5m_input_tokens",
                       "ephemeral_1h_input_tokens", "audio", "text", "image", "reasoning"}
_CHECK_NAMES = {"target_crs", "feature_count", "attributes_and_transformed_vertices", "buffer_crs_and_units",
                "empty_input_empty_output", "analytic_dissolved_area", "buffer_geometry_and_attributes",
                "independently_verified_empty_geometry", "analytic_distance_and_area", "buffer_metre_crs",
                "load_spatial_layer", "spatial_schema", "statistics_fields", "loaded_layer_matches_input",
                "clip_intersection_and_attributes", "analytic_rectangle_intersection",
                "dissolve_groups_union_and_member_attributes", "analytic_rectangle_union",
                "geometry_attributes_planar_measurements", "statistics_recomputed_from_input"}
_ERROR_CATEGORIES = {"coordinates", "crs", "feature_count", "attributes", "geometry", "distance", "area",
                     "integrity", "inspector_unavailable", "other"}
_REASON_CODES = {"parameter_error", "missing_file", "qgis_disconnected", "execution_error",
                 "malformed_response", "unknown_error", "spatial_validation_failed"}
_EPSG = re.compile(r"^EPSG:[0-9]{1,8}$")


def _contains_configured_key(value):
    for name in ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY", "OPENAI_API_KEY"):
        secret = os.getenv(name, "")
        # SDK 0.6.4 strips surrounding quotes and whitespace from API keys.
        normalized = secret.strip().strip('"').strip("'")
        if (secret and secret in value) or (normalized and normalized in value):
            return True
    return False


def _safe_usage(value):
    if not isinstance(value, dict):
        return None
    result = {key: item for key, item in value.items()
              if key in _USAGE_COUNTS and isinstance(item, int) and not isinstance(item, bool) and item >= 0}
    for key in _USAGE_DETAILS:
        details = value.get(key)
        if isinstance(details, dict):
            filtered = {name: number for name, number in details.items()
                        if name in _USAGE_DETAIL_NAMES
                        and isinstance(number, int) and not isinstance(number, bool) and number >= 0}
            if filtered:
                result[key] = filtered
    return result or None


def _safe_metadata(metadata):
    if not isinstance(metadata, dict):
        return {}
    safe = {}
    for key, value in metadata.items():
        if isinstance(value, str) and _contains_configured_key(value):
            continue
        if key in _METADATA_TEXT and isinstance(value, str) and _IDENTIFIER.fullmatch(value):
            safe[key] = value
        elif key in _METADATA_INTEGER and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            safe[key] = value
        elif key == "usage_metadata":
            usage = _safe_usage(value)
            if usage is not None:
                safe[key] = usage
        elif key == "target_crs" and isinstance(value, str) and _EPSG.fullmatch(value):
            safe[key] = value
        elif key == "distance_unit" and value == "m":
            safe[key] = value
        elif key == "distance" and isinstance(value, (int, float)) and not isinstance(value, bool):
            if abs(value) <= 1_000_000_000 and math.isfinite(value):
                safe[key] = value
        elif key == "reason_code" and isinstance(value, str) and value in _REASON_CODES:
            safe[key] = value
        elif key in {"check_names", "error_categories"} and isinstance(value, list):
            allowed = _CHECK_NAMES if key == "check_names" else _ERROR_CATEGORIES
            safe[key] = [name for name in value if isinstance(name, str) and name in allowed
                         and not _contains_configured_key(name)][:20]
    return safe


def call_metadata(call):
    """Only explicitly reviewed, bounded scalar Processing arguments are observable."""
    try:
        parameters = call.arguments.get("parameters")
        if not isinstance(parameters, dict):
            return {}
        return _safe_metadata({"target_crs": parameters.get("TARGET_CRS"),
                               "distance": parameters.get("DISTANCE") if call.distance_unit == "m" else None,
                               "distance_unit": call.distance_unit})
    except Exception as exc:
        logger.warning("LangSmith call metadata failed (%s).", type(exc).__name__)
        return {}


def validation_metadata(report):
    """Classify validation evidence without transmitting diagnostic text or geometry."""
    try:
        return _validation_metadata(report)
    except Exception as exc:
        logger.warning("LangSmith validation metadata failed (%s).", type(exc).__name__)
        return {}


def _validation_metadata(report):
    checks = [item.get("check") if isinstance(item, dict) else item for item in report.get("checks", [])]
    categories = set()
    for error in report.get("errors", []):
        text = error.lower()
        matched = False
        for category, keywords in (
            ("coordinates", ("coordinate", "transformed")),
            ("crs", ("crs", "projection", "spatial reference")),
            ("feature_count", ("feature count", "missing features", "too many output features")),
            ("attributes", ("attribute", "field")),
            ("geometry", ("geometry", "polygon", "shape")),
            ("distance", ("distance", "radius")),
            ("area", ("area",)),
            ("integrity", ("changed", "unreadable", "checksum", "sqlite")),
            ("inspector_unavailable", ("inspector unavailable", "inspector is unavailable", "invalid report",
                                       "inspection could not complete")),
        ):
            if any(keyword in text for keyword in keywords):
                categories.add(category)
                matched = True
        if not matched:
            categories.add("other")
    return _safe_metadata({"status": report.get("status"), "check_count": len(checks),
                           "error_count": len(report.get("errors", [])), "check_names": checks,
                           "error_categories": sorted(categories),
                           "reason_code": "spatial_validation_failed" if report.get("status") == "failed" else None})


def _safe_run_fields(kwargs):
    """Drop fields the SDK's input/output masking does not cover in version 0.6.4."""
    fields = dict(kwargs)
    extra = fields.get("extra") or {}
    fields["extra"] = {"metadata": _safe_metadata(extra.get("metadata", {}))} if isinstance(extra, dict) else {}
    # LangChain's normalized AIMessage counts may include guessed totals and
    # zeros for fields the provider omitted. Only add verified raw counts below.
    fields.get("extra", {}).get("metadata", {}).pop("usage_metadata", None)
    fields.pop("serialized", None)
    fields.pop("events", None)
    fields.pop("attachments", None)
    fields["tags"] = []
    if fields.get("error"):
        fields["error"] = "Run failed; details remain in the local execution record."
    return fields


def _raw_provider_usage(outputs):
    """Read only fields present in the original OpenAI-compatible usage object."""
    if not isinstance(outputs, dict):
        return None
    llm_output = outputs.get("llm_output")
    usage = llm_output.get("token_usage") if isinstance(llm_output, dict) else None
    if not isinstance(usage, dict):
        generations = outputs.get("generations")
        if isinstance(generations, list) and len(generations) == 1:
            batch = generations[0]
            if isinstance(batch, list) and len(batch) == 1 and isinstance(batch[0], dict):
                message = batch[0].get("message")
                if isinstance(message, dict):
                    message = message.get("kwargs") if isinstance(message.get("kwargs"), dict) else message
                    response = message.get("response_metadata")
                    usage = response.get("token_usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return None
    result = {}
    for source, target in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens"),
                           ("total_tokens", "total_tokens")):
        value = usage.get(source)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[target] = value
    return result or None


class _SafeClient(Client):
    """Keep SDK callbacks and batching while excluding unbounded run fields."""

    def create_run(self, name, inputs, run_type, **kwargs):
        # The SDK falls back to synchronous HTTP without these batching fields.
        if not kwargs.get("trace_id") or not kwargs.get("dotted_order"):
            return None
        return super().create_run(name, inputs, run_type, **_safe_run_fields(kwargs))

    def update_run(self, run_id, **kwargs):
        if not kwargs.get("trace_id") or not kwargs.get("dotted_order"):
            return None
        fields = _safe_run_fields(kwargs)
        # Streaming ChatOpenAI 1.1.7 drops the raw provider usage object; its
        # normalized AIMessage alone cannot prove which counts were supplied.
        if kwargs.get("run_type") == "llm":
            usage = _raw_provider_usage(kwargs.get("outputs"))
            if usage is not None:
                fields.setdefault("extra", {}).setdefault("metadata", {})["usage_metadata"] = usage
        return super().update_run(run_id, **fields)

    def request_with_retries(self, method, pathname, **kwargs):
        # Network retries, including server-provided Retry-After, must not hold
        # shutdown indefinitely. Traces can be lost when the service is down.
        kwargs["stop_after_attempt"] = 1
        return super().request_with_retries(method, pathname, **kwargs)


def _start_client(key, endpoint):
    """Start the SDK on a daemon so its batching threads inherit that property."""
    ready = threading.Event()
    result_lock = threading.Lock()
    result = {"abandoned": False}

    def create():
        try:
            client = _SafeClient(
                api_key=key, api_url=endpoint, timeout_ms=(1000, 2000), retry_config=Retry(total=0),
                hide_inputs=True, hide_outputs=True, hide_metadata=_safe_metadata,
                omit_traced_runtime_info=True,
                # Compression uses the SDK's process-wide executor, whose exit
                # hook can wait indefinitely for a stuck network worker.
                info={"instance_flags": {"zstd_compression_enabled": False}},
            )
            with result_lock:
                if result["abandoned"]:
                    client.cleanup()
                else:
                    result["client"] = client
        except Exception as exc:
            logger.warning("LangSmith setup failed (%s); continuing without tracing.", type(exc).__name__)
        finally:
            ready.set()

    threading.Thread(target=create, daemon=True, name="langsmith-client-setup").start()
    if not ready.wait(1.0):
        with result_lock:
            result["abandoned"] = True
            client = result.pop("client", None)
            if client is not None:
                client.cleanup()
        logger.warning("LangSmith setup exceeded its time limit; continuing without tracing.")
        return None
    return result.get("client")


def guard_global_tracing():
    """Neutralize unsupported V1 flags that otherwise make LangChain fail the task."""
    global _legacy_conflict
    with _lock:
        for name in ("LANGCHAIN_TRACING", "LANGCHAIN_HANDLER"):
            value = os.getenv(name, "")
            # Match langchain_core.utils.env.env_var_is_set exactly.
            if value not in {"", "0", "false", "False"}:
                os.environ[name] = "false"
                _legacy_conflict = True
                logger.warning("Unsupported global SDK tracing flag %s was disabled in this backend process; "
                               "remove it and restart before enabling observability.", name)


def configure_langsmith():
    """Create an official SDK client only for explicit, credentialed tracing."""
    global _client, _client_settings
    guard_global_tracing()
    conflicts = [name for name in ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2")
                 if os.getenv(name, "").strip().lower() not in {"", "0", "false"}]
    if conflicts or _legacy_conflict:
        if conflicts:
            logger.warning("Unsupported global SDK tracing flags %s; continuing without tracing.", ", ".join(conflicts))
        return None
    if os.getenv("QGIS_AGENT_LANGSMITH_ENABLED", "").strip().lower() != "true":
        return None
    key = os.getenv("LANGSMITH_API_KEY", "").strip()
    if not key:
        return None
    # These optional SDK modes can bypass the single configured Cloud destination
    # or its Python masking. They are outside this application's tracing contract.
    unsupported = [f"{prefix}_{suffix}"
                   for prefix in ("LANGSMITH", "LANGCHAIN")
                   for suffix in ("RUNS_ENDPOINTS", "AGENT_ID", "AGENT_ENVIRONMENT")
                   if os.getenv(f"{prefix}_{suffix}")]
    unsupported += [name for name in ("LANGSMITH_USE_PYO3_CLIENT", "LANGCHAIN_USE_PYO3_CLIENT")
                    if os.getenv(name) is not None]
    unsupported += [name for name in ("LANGSMITH_OTEL_ENABLED", "LANGCHAIN_OTEL_ENABLED", "OTEL_ENABLED")
                    if os.getenv(name, "").strip().lower() not in {"", "0", "false"}]
    if unsupported:
        logger.warning("Unsupported LangSmith SDK configuration %s; continuing without tracing.", ", ".join(unsupported))
        return None
    endpoint = os.getenv("LANGSMITH_ENDPOINT") or "https://api.smith.langchain.com"
    project = os.getenv("LANGSMITH_PROJECT") or "default"
    try:
        parsed_endpoint = urlsplit(endpoint)
    except ValueError:
        logger.warning("Invalid LangSmith endpoint configuration; continuing without tracing.")
        return None
    if (parsed_endpoint.scheme != "https" or not parsed_endpoint.hostname or parsed_endpoint.username
            or parsed_endpoint.password or parsed_endpoint.query or parsed_endpoint.fragment
            or _contains_configured_key(endpoint) or _contains_configured_key(project)):
        logger.warning("Invalid LangSmith endpoint or project configuration; continuing without tracing.")
        return None
    settings = (key, endpoint, project)
    with _lock:
        if _client is not None and _client_settings == settings:
            return _client
        try:
            client = _start_client(key, endpoint)
        except Exception as exc:
            logger.warning("LangSmith setup failed (%s); continuing without tracing.", type(exc).__name__)
            return None
        if client is None:
            return None
        old_client = _client
        _client, _client_settings = client, settings
        if old_client is not None:
            try:
                old_client.cleanup()
            except Exception as exc:
                logger.warning("LangSmith cleanup failed (%s).", type(exc).__name__)
        return client


@contextmanager
def _trace_context(client, metadata):
    context = tracing_context(enabled=client is not None, client=client,
                              project_name=os.getenv("LANGSMITH_PROJECT") or "default",
                              metadata=_safe_metadata(metadata), parent=False, replicas=[])
    try:
        context.__enter__()
    except Exception as exc:
        logger.warning("LangSmith context failed (%s); continuing without tracing.", type(exc).__name__)
        context = tracing_context(enabled=False)
        context.__enter__()
    try:
        yield
    finally:
        try:
            context.__exit__(None, None, None)
        except Exception as exc:
            logger.warning("LangSmith context cleanup failed (%s).", type(exc).__name__)


async def graph_events(graph, initial_state, config, session_id, values):
    """Trace each graph invocation independently and correlate across pauses."""
    metadata = {"session_id": session_id, "task_id": values.get("task_id"),
                "case_id": values.get("case_id"), "revision": values.get("planning_revision")}
    from agent.eval_usage import UsageJournal

    journal = UsageJournal(session_id, values)
    try:
        with _trace_context(configure_langsmith(), metadata):
            async for event in graph.astream_events(initial_state, config=config, version="v2"):
                journal.observe(event)
                yield event
    except BaseException:
        journal.finish(failed=True)
        raise
    else:
        journal.finish()


@contextmanager
def span(name, *, run_type="tool", **metadata):
    """Create a nested SDK span without allowing tracing to affect the operation."""
    context = run = outer_context = None
    outer_entered = False
    try:
        previous_context = get_tracing_context()
        if previous_context.get("enabled") is True:
            # SDK 0.6.4 patches a span before restoring its context; if patching
            # fails, this enclosing official context still restores the parent.
            outer_context = tracing_context(**previous_context)
            outer_context.__enter__()
            outer_entered = True
            context = trace(name, run_type=run_type, metadata=_safe_metadata(metadata))
            run = context.__enter__()
    except Exception as exc:
        context = None
        logger.warning("LangSmith span could not start (%s).", type(exc).__name__)
    try:
        yield run
    except BaseException:
        if run is not None:
            try:
                run.end(error="Operation failed; details remain locally.")
            except Exception as exc:
                logger.warning("LangSmith span could not record failure (%s).", type(exc).__name__)
        raise
    finally:
        try:
            if context is not None:
                try:
                    context.__exit__(None, None, None)
                except Exception as exc:
                    logger.warning("LangSmith span could not finish (%s).", type(exc).__name__)
        finally:
            if outer_entered:
                try:
                    outer_context.__exit__(None, None, None)
                except Exception as exc:
                    logger.warning("LangSmith span context could not be restored (%s).", type(exc).__name__)


def span_result(run, **metadata):
    if run is not None:
        try:
            run.metadata.update(_safe_metadata(metadata))
            failed = metadata.get("status") in {"error", "failed", "disconnected", "malformed"} or metadata.get("outcome") == "unknown"
            run.end(outputs={}, error="Operation failed; details remain locally." if failed else None)
        except Exception as exc:
            logger.warning("LangSmith span result could not be recorded (%s).", type(exc).__name__)


async def close_langsmith():
    """Attempt the SDK's flush on shutdown without indefinitely blocking the server."""
    global _client, _client_settings
    with _lock:
        client, _client, _client_settings = _client, None, None
    if client is None:
        return
    done = threading.Event()

    def flush():
        try:
            client.flush()
        except Exception as exc:
            logger.warning("LangSmith flush failed (%s).", type(exc).__name__)
        finally:
            done.set()

    try:
        threading.Thread(target=flush, daemon=True, name="langsmith-shutdown-flush").start()
        if not await asyncio.to_thread(done.wait, 2.0):
            logger.warning("LangSmith flush exceeded the shutdown time limit; traces may be missing.")
    except Exception as exc:
        logger.warning("LangSmith shutdown failed (%s).", type(exc).__name__)
    finally:
        try:
            client.cleanup()
        except Exception as exc:
            logger.warning("LangSmith cleanup failed (%s).", type(exc).__name__)
