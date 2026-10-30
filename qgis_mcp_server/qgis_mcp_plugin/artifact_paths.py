"""Shared, dependency-free rules for new task-owned output files."""

import os
from pathlib import Path, PureWindowsPath
import re
from zlib import error as ZlibError
from zipfile import BadZipFile, ZipFile


SOURCE_WORKSPACE = Path(__file__).resolve().parents[2]
OUTPUT_SUBDIRECTORY = Path('shared/outputs')
WRITE_TOOLS = frozenset({'add_layer', 'execute_processing', 'capture_map_canvas', 'save_project'})


class ActiveSQLiteSidecar(ValueError):
    """The main file alone cannot establish the SQLite dataset's content."""


def require_standalone_sqlite(path):
    """Reject transaction sidecars without opening or changing the database.

    WAL can contain committed data absent from the main-file checksum; a rollback
    journal may require recovery, and SHM indicates WAL coordination. Even empty
    or stale files are rejected: V1 does not decide whether checkpoint/recovery
    is safe, or modify the user's files to obtain a standalone snapshot.
    """
    path = Path(path)
    if path.suffix.lower() not in {'.gpkg', '.sqlite', '.sqlite3', '.db'}:
        return
    for suffix in ('-wal', '-shm', '-journal'):
        sidecar = Path(str(path) + suffix)
        if os.path.lexists(sidecar):
            raise ActiveSQLiteSidecar(
                f'SQLite transaction sidecar prevents standalone file verification: {sidecar}. '
                'Close the writer and provide a safely finalized standalone dataset.'
            )


def workspace_directory(workspace_root=None):
    if workspace_root is not None:
        return Path(workspace_root).resolve()
    configured = os.environ.get('QWA_ROOT')
    if configured:
        return Path(configured).resolve()
    if (SOURCE_WORKSPACE / 'pyproject.toml').is_file() and (SOURCE_WORKSPACE / 'qgis_mcp_server').is_dir():
        return SOURCE_WORKSPACE
    raise ValueError('QWA_ROOT must identify the project workspace for the installed plugin')


def output_root(workspace_root=None):
    return workspace_directory(workspace_root) / OUTPUT_SUBDIRECTORY


def task_output_directory(task_id, revision, workspace_root=None):
    if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', task_id):
        raise ValueError('Task ID cannot identify an output directory')
    if not isinstance(revision, int) or revision < 1:
        raise ValueError('Invalid output revision')
    return output_root(workspace_root) / task_id / f'r{revision}'


def resolve_owned_output_path(raw_path, workspace_root=None, *, task_id=None, revision=None):
    """Resolve an owned artifact path without requiring it to be new or creating it."""
    if not isinstance(raw_path, str) or not raw_path or '|' in raw_path or '://' in raw_path:
        raise ValueError('Output must be a plain local file path')
    candidate = Path(raw_path)
    if '..' in candidate.parts or '\\' in raw_path:
        raise ValueError('Output path cannot contain traversal or backslashes')
    if PureWindowsPath(raw_path).is_absolute() and not candidate.is_absolute():
        raise ValueError('Output path is not local to this host')
    workspace = workspace_directory(workspace_root)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    root = output_root(workspace_root)
    resolved = Path(os.path.abspath(candidate))
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError('Output must be in the controlled task output directory') from exc
    if (len(relative.parts) != 4
            or not re.fullmatch(r'[A-Za-z0-9_-]+', relative.parts[0])
            or not re.fullmatch(r'r[1-9][0-9]*', relative.parts[1])
            or not re.fullmatch(r'[A-Za-z0-9_-]+', relative.parts[2])
            or relative.parts[3] in {'.', '..'}):
        raise ValueError('Output must include task, revision, artifact and filename')
    current = workspace
    for part in resolved.relative_to(workspace).parts:
        current /= part
        if current.is_symlink():
            raise ValueError('Output paths may not contain symbolic links')
    if task_id is not None:
        owned = task_output_directory(task_id, revision, workspace_root)
        if owned not in resolved.parents:
            raise ValueError('Output belongs to a different task or revision')
    return resolved


def new_output_path(raw_path, workspace_root=None, *, task_id=None, revision=None):
    """Check an artifact destination without creating it."""
    resolved = resolve_owned_output_path(
        raw_path, workspace_root, task_id=task_id, revision=revision
    )
    if os.path.lexists(resolved.parent):
        raise ValueError('Artifact directory already exists; overwriting is forbidden')
    return resolved


def new_project_output_path(raw_path, workspace_root=None, *, task_id=None, revision=None):
    """Require a new owned QGZ destination for a project copy."""
    resolved = new_output_path(raw_path, workspace_root, task_id=task_id, revision=revision)
    if resolved.suffix != '.qgz':
        raise ValueError('Project output must have a .qgz suffix')
    return resolved


def reserve_output_path(raw_path, workspace_root=None):
    """Create exactly one new artifact directory at the plugin write boundary.

    Open each directory without following links and atomically create the final
    directory, so a competing request cannot claim or replace an artifact.
    """
    target = new_output_path(raw_path, workspace_root)
    workspace = workspace_directory(workspace_root)
    if not hasattr(os, 'O_NOFOLLOW') or os.mkdir not in os.supports_dir_fd:
        raise RuntimeError('Safe artifact directory reservation is unavailable')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(workspace, flags)
    try:
        for part in target.parent.relative_to(workspace).parts[:-1]:
            try:
                os.mkdir(part, dir_fd=fd)
            except FileExistsError:
                pass
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        try:
            os.mkdir(target.parent.name, dir_fd=fd)
        except FileExistsError as exc:
            raise ValueError('Artifact directory already exists; overwriting is forbidden') from exc
    finally:
        os.close(fd)
    return target


def artifact_component_paths(path):
    """List required files for a single output without claiming spatial validity."""
    path = Path(path)
    if path.suffix.lower() == '.shp':
        return (path, path.with_suffix('.dbf'), path.with_suffix('.shx'))
    return (path,)


def validate_qgz_archive(stream):
    """Check the complete ZIP and its project member, without claiming spatial validity."""
    try:
        with ZipFile(stream) as archive:
            projects = [item for item in archive.infolist()
                        if item.filename.endswith('.qgs') and item.file_size > 0]
            if len(projects) != 1 or archive.testzip() is not None:
                raise ValueError('Invalid QGZ project archive')
    except (BadZipFile, EOFError, RuntimeError, NotImplementedError, ZlibError) as exc:
        raise ValueError('Unreadable QGZ project archive') from exc


def processing_output_paths(value, key=''):
    """Walk nested QGIS output parameters without losing their dictionary keys."""
    if isinstance(value, dict):
        for nested_key, nested in value.items():
            yield from processing_output_paths(nested, str(nested_key))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from processing_output_paths(nested, key)
    elif isinstance(value, str) and 'OUTPUT' in key.upper().split('_'):
        yield value


def replace_processing_output_paths(value, replacements, key=''):
    """Use absolute destinations for QGIS without changing any input parameter."""
    if isinstance(value, dict):
        return {nested_key: replace_processing_output_paths(nested, replacements, str(nested_key))
                for nested_key, nested in value.items()}
    if isinstance(value, list):
        return [replace_processing_output_paths(nested, replacements, key) for nested in value]
    if isinstance(value, str) and 'OUTPUT' in key.upper().split('_'):
        return replacements[value]
    return value


def call_output_paths(tool_name, arguments):
    if tool_name in {'capture_map_canvas', 'save_project'}:
        path = arguments.get('path')
        if isinstance(path, str):
            yield path
    elif tool_name == 'execute_processing':
        yield from processing_output_paths(arguments.get('parameters', {}))
