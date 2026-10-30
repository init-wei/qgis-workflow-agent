"""Process ownership for graph runs and the single, non-queued GIS operation."""

from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock


_execution_lock = Lock()
_owners_lock = Lock()
_active_threads: set[str] = set()
_active_tasks: set[str] = set()
_unknown_tasks: set[str] = set()
_current_slot = ContextVar('execution_slot', default=None)


class ExecutionBusy(RuntimeError):
    pass


class ThreadRun:
    """Prevent competing graph producers from mutating the same checkpoint."""

    def __init__(self, thread_id: str):
        with _owners_lock:
            if thread_id in _active_threads:
                raise ExecutionBusy('Task busy; this graph run is already active.')
            _active_threads.add(thread_id)
        self.thread_id = thread_id

    def release(self):
        with _owners_lock:
            _active_threads.discard(self.thread_id)


def is_task_active(task_id: str) -> bool:
    with _owners_lock:
        return task_id in _active_tasks


def block_unknown_task(task_id: str) -> None:
    """T02 must reconcile actual remote work before allowing a new GIS task."""
    with _owners_lock:
        _unknown_tasks.add(task_id)


class ExecutionSlot:
    def __init__(self, task_id: str | None = None):
        with _owners_lock:
            if _unknown_tasks:
                raise ExecutionBusy('GIS execution needs attention: an earlier operation outcome is unknown.')
            if not _execution_lock.acquire(blocking=False):
                raise ExecutionBusy('GIS execution busy; retry after the active task finishes.')
            if task_id:
                _active_tasks.add(task_id)
        self.task_id = task_id
        self._released = False

    def bind_task(self, task_id: str):
        with _owners_lock:
            if self.task_id and self.task_id != task_id:
                raise RuntimeError('GIS reservation belongs to a different task.')
            self.task_id = task_id
            _active_tasks.add(task_id)

    def release(self):
        with _owners_lock:
            if not self._released:
                self._released = True
                if self.task_id:
                    _active_tasks.discard(self.task_id)
                _execution_lock.release()

    @contextmanager
    def activate(self):
        token = _current_slot.set(self)
        try:
            yield
        finally:
            _current_slot.reset(token)
            self.release()


@contextmanager
def execution_slot(task_id: str | None = None):
    slot = _current_slot.get()
    if slot is not None and not slot._released:
        if task_id:
            slot.bind_task(task_id)
        try:
            yield slot
        finally:
            # The reservation ends with the GIS node, even when reflection follows.
            slot.release()
    else:
        with ExecutionSlot(task_id).activate():
            yield
