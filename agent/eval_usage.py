"""Opt-in local evidence of raw provider usage; never a business authority."""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import stat
import time
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)
_PROJECT = Path(__file__).resolve().parents[1]
_IDENTIFIER = re.compile(r'[A-Za-z0-9_.-]{1,128}')
_CASE_IDENTIFIER = re.compile(r'[A-Za-z0-9_.:-]{1,128}')
_USAGE = ('prompt_tokens', 'completion_tokens', 'total_tokens',
          'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens', 'reasoning_tokens')
_WARNING = 'Evaluation usage journal unavailable; usage evidence may be incomplete.'


def _integer(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _identifier(value, *, pattern=_IDENTIFIER):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        return None
    for name in ('OPENAI_API_KEY', 'LANGSMITH_API_KEY', 'LANGCHAIN_API_KEY'):
        secret = os.getenv(name, '').strip().strip('"').strip("'")
        if secret and secret in value:
            return None
    return value


def _run_id(value):
    try:
        if not isinstance(value, (str, UUID)) or not _identifier(str(value)):
            return None
        return _identifier(str(UUID(str(value))))
    except (ValueError, AttributeError):
        return None


def _append(path, row):
    """Walk from the project with no symlink following, including the leaf."""
    path = Path(path)
    if not path.is_absolute():
        path = _PROJECT / path
    relative = path.relative_to(_PROJECT)
    parts = relative.parts
    if len(parts) < 3 or parts[:2] != ('.target', 't07') or '..' in parts:
        raise ValueError('Invalid journal destination')
    directory = os.open(_PROJECT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            try:
                os.mkdir(part, mode=0o700, dir_fd=directory)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(parts[-1], os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o600, dir_fd=directory)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Invalid journal file')
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, 'a', encoding='utf-8', closefd=False) as stream:
                stream.write(json.dumps(row, ensure_ascii=True, allow_nan=False) + '\n')
                stream.flush()
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


class UsageJournal:
    """One graph invocation, with an independently auditable model inventory."""

    def __init__(self, session_id, values):
        self.path = os.getenv('QGIS_AGENT_EVAL_USAGE_PATH')
        self.enabled = (os.getenv('QGIS_AGENT_EVAL_NONSTREAMING', '').strip().lower() == 'true'
                        and bool(self.path))
        self.identity = {'invocation_id': str(uuid4()), 'session_id': _identifier(session_id),
                         'task_id': _identifier(values.get('task_id')),
                         'case_id': _identifier(values.get('case_id'), pattern=_CASE_IDENTIFIER),
                         'revision': _integer(values.get('planning_revision'))}
        self.model_ids = set()
        self.active = {}
        self.unidentified_active = []
        self.start_count = self.unidentified = self.write_errors = 0
        if self.enabled:
            self._write('graph_start')

    def _write(self, event, **fields):
        try:
            _append(self.path, {'schema_version': 1, 'event': event, **self.identity,
                               'timestamp_utc': datetime.now(timezone.utc).isoformat(),
                               'monotonic_ns': time.monotonic_ns(), **fields})
        except Exception:
            self.write_errors += 1
            logger.warning(_WARNING)

    def observe(self, event):
        if not self.enabled:
            return
        try:
            self._observe(event)
        except Exception:
            # Even malformed SDK output cannot change the graph's result.
            self.unidentified += 1
            self.write_errors += 1
            logger.warning(_WARNING)

    def _observe(self, event):
        kind = {'on_chat_model_start': 'start', 'on_chat_model_end': 'end',
                'on_chat_model_error': 'error'}.get(event.get('event'))
        if kind is None:
            return
        run_id = _run_id(event.get('run_id'))
        if run_id is None:
            self.unidentified += 1
        else:
            self.model_ids.add(run_id)
        metadata = event.get('metadata')
        metadata = metadata if isinstance(metadata, dict) else {}
        fields = {'run_id': run_id, 'node': _identifier(metadata.get('langgraph_node')),
                  'requested_model': _identifier(metadata.get('ls_model_name')),
                  'provider_response_model': None, 'provider_response_id': None, 'finish_reason': None}
        if kind == 'start':
            self.start_count += 1
            if run_id is not None:
                self.active[run_id] = fields
            else:
                self.unidentified_active.append(fields)
        else:
            previous = self.active.pop(run_id, {})
            fields['requested_model'] = previous.get('requested_model') or fields['requested_model']
        if kind == 'end':
            data = event.get('data')
            output = data.get('output') if isinstance(data, dict) else None
            response = (output.get('response_metadata') if isinstance(output, dict)
                        else getattr(output, 'response_metadata', None))
            response = response if isinstance(response, dict) else {}
            fields.update(provider_response_model=_identifier(response.get('model_name')),
                          provider_response_id=_identifier(response.get('id')),
                          finish_reason=_identifier(response.get('finish_reason')))
            raw = response.get('token_usage')
            raw = raw if isinstance(raw, dict) else None
            details = raw.get('completion_tokens_details') if raw is not None else None
            details = details if isinstance(details, dict) else {}
            usage = {name: _integer(details.get('reasoning_tokens') if name == 'reasoning_tokens'
                                   else raw.get(name) if raw is not None else None) for name in _USAGE}
            fields.update(usage=usage, usage_missing_fields=[name for name in _USAGE if usage[name] is None],
                          usage_source='provider_response_metadata.token_usage' if raw is not None else None)
        self._write(kind, **fields)

    def finish(self, *, failed=False):
        if not self.enabled:
            return
        for fields in [*self.active.values(), *self.unidentified_active]:
            self._write('error', **fields)
        self.active.clear()
        self.unidentified_active.clear()
        self._write('graph_error' if failed else 'graph_end', model_run_ids=sorted(self.model_ids),
                    model_start_count=self.start_count, unidentified_model_events=self.unidentified,
                    write_error_count=self.write_errors)
