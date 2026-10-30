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
                raise ExecutionBusy('此任务正在运行，请等待后重试。 Task busy; this graph run is already active.')
            _active_threads.add(thread_id)
        self.thread_id = thread_id

    def release(self):
        with _owners_lock:
            _active_threads.discard(self.thread_id)


def is_task_active(task_id: str) -> bool:
    with _owners_lock:
        return task_id in _active_tasks


def is_thread_active(thread_id: str) -> bool:
    with _owners_lock:
        return thread_id in _active_threads


def block_unknown_task(task_id: str) -> None:
    """T02 must reconcile actual remote work before allowing a new GIS task."""
    with _owners_lock:
        _unknown_tasks.add(task_id)


@contextmanager
def reconciliation_slot(task_id: str):
    """Serialize an operator's read-only probe with all GIS work."""
    with _owners_lock:
        if task_id not in _unknown_tasks:
            raise ExecutionBusy('This task is not awaiting unknown-outcome reconciliation.')
        if not _execution_lock.acquire(blocking=False):
            raise ExecutionBusy('GIS 仍在执行，请等待并检查实际状态。 GIS execution is still active.')
    try:
        yield
    finally:
        _execution_lock.release()


def clear_reconciled_task(task_id: str) -> None:
    """Release only the task whose operator evidence was durably recorded."""
    with _owners_lock:
        _unknown_tasks.discard(task_id)


class ExecutionSlot:
    def __init__(self, task_id: str | None = None):
        with _owners_lock:
            if _unknown_tasks:
                raise ExecutionBusy('此前 GIS 操作结果未知，请先核查。 GIS execution needs attention.')
            if not _execution_lock.acquire(blocking=False):
                raise ExecutionBusy('GIS 正在执行另一项任务，请等待后重试；不会排队。 GIS execution busy.')
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
