"""Cooperative storage for the optional, project-local BoosterAI harness.

Every harness participant must hold ``locked(project)`` while using ``read`` or
``write``. The OS claim coordinates participants, not arbitrary external writers;
the identity and byte checks below are not a filesystem sandbox or an atomic
compare-and-swap against programs which ignore the claim. An interrupted or failed
write may have changed the file: inspect it before deciding whether to retry.
"""

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claim_guard_8d31f72a as claim_guard
import project_tools


FORMAT = 'boosterai-harness-v1'
STATE = '_boosterAI/harness-state.json'
MAX_BYTES = 2 * 1024 * 1024


@contextmanager
def locked(project, files=()):
    """Hold the common harness slice and state-file claims for one project.

    Create the project's existing BoosterAI workspace when absent and acquire
    nonblocking global, state and optional declared-file claims before yielding.
    Busy, unsafe or denied claims are surfaced unchanged. Guard files persist;
    only cooperating writers are held, and only while this context remains open.
    Callers must retain this context throughout each read/check/write transaction.
    """
    base = project_tools.repository_root(project)
    project_tools.workspace(base)
    if not isinstance(files, (list, tuple)):
        raise ValueError('additional file claims must be a list or tuple')
    with claim_guard.claim(base, 'booster-harness-global', [STATE, *files]):
        yield base


def _plain(info):
    """Accept only a single-link regular file without Windows reparse flags."""
    return (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and not (getattr(info, 'st_file_attributes', 0) & 0x400))


def _identity(info):
    """Compare identity and modification metadata before and after an open read."""
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink)


def _object(pairs):
    """Reject duplicate keys at every JSON object level, including nested values."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate harness state key')
        result[key] = value
    return result


def _invalid_constant(_value):
    """Reject JSON extensions such as NaN and Infinity."""
    raise ValueError('non-finite harness state value')


def _decode(raw):
    """Parse strict bounded UTF-8 JSON and recognize only this state format."""
    if len(raw) > MAX_BYTES:
        raise ValueError('harness state exceeds size limit')
    try:
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=_object,
                          parse_constant=_invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('invalid harness state JSON') from error
    if not isinstance(data, dict) or data.get('format') != FORMAT:
        raise ValueError('unrecognized harness state format')
    try:
        _json_types(data)
    except RecursionError as error:
        raise ValueError('harness state nesting exceeds parser limit') from error
    return data


def _json_types(value):
    """Require JSON-native containers and string object keys before serializing."""
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError('harness state keys must be strings')
        for nested in value.values():
            _json_types(nested)
    elif isinstance(value, list):
        for nested in value:
            _json_types(nested)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError('non-finite harness state value')
    elif value is not None and type(value) not in (str, int, float, bool):
        raise ValueError('harness state must contain JSON values')


def _encoded(data):
    """Validate the proposed JSON and return the exact deterministic output bytes."""
    try:
        _json_types(data)
        raw = project_tools.archive_json_bytes(data)
        _decode(raw)
    except (TypeError, RecursionError, OverflowError) as error:
        raise ValueError('invalid harness state') from error
    return raw


def _read_snapshot(project):
    """Read one bounded, identity-checked state snapshot for a locked caller."""
    base = project_tools.repository_root(project)
    path = project_tools.safe(base, STATE)
    try:
        initial = path.lstat()
    except FileNotFoundError:
        return None, None, None
    if not _plain(initial):
        raise ValueError('unsafe harness state file')
    if initial.st_size > MAX_BYTES:
        raise ValueError('harness state exceeds size limit')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if not _plain(opened) or _identity(opened) != _identity(initial):
            raise ValueError('harness state identity changed')
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            raw = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
        current = project_tools.safe(base, STATE).lstat()
        if (not _plain(after) or not _plain(current)
                or _identity(after) != _identity(initial)
                or _identity(current) != _identity(initial)):
            raise ValueError('harness state identity changed')
    finally:
        if fd is not None:
            os.close(fd)
    return _decode(raw), raw, _identity(initial)


def read(project):
    """Return ``(object, exact_bytes)`` or ``(None, None)`` for an absent state.

    Caller must hold ``locked(project)``. Reject unknown, malformed, oversized,
    linked, or changing files without replacing or deleting them. Only the state
    file is read, bounded to 2 MiB; filesystem permission errors propagate.
    """
    data, raw, _identity_before = _read_snapshot(project)
    return data, raw


def _clean_stage(operation, directory_info, file_info, content, complete, replaced):
    """Remove only this operation's verified staging file and empty directory."""
    if directory_info is None:
        return
    current = operation.lstat()
    if (not stat.S_ISDIR(current.st_mode)
            or getattr(current, 'st_file_attributes', 0) & 0x400
            or (current.st_dev, current.st_ino) != (directory_info.st_dev, directory_info.st_ino)):
        raise ValueError('harness scratch directory changed; preserved')
    staging = operation / 'data.json'
    try:
        staging.lstat()
    except FileNotFoundError:
        if file_info is not None and not replaced:
            raise ValueError('harness scratch file disappeared; directory preserved')
    else:
        if file_info is None:
            raise ValueError('unrecognized harness scratch file; preserved')
        project_tools.verify_archive_stage_file(staging, file_info, content, complete)
        staging.unlink()
    operation.rmdir()


def _replace(base, target, expected_raw, expected_identity, content):
    """Stage verified bytes, recheck the current state, then atomically replace it.

    The established guarded scratch and staging validators are reused. A
    nonparticipating writer can still race the final comparison and replacement;
    the cooperative claim is not a system-wide compare-and-swap mechanism.
    """
    scratch = project_tools.scratch_directory(base)
    if scratch.stat().st_dev != target.parent.stat().st_dev:
        raise ValueError('scratch and harness state must share a filesystem')
    owner = hashlib.sha256(os.path.normcase(str(base)).encode('utf-8')).hexdigest()[:16]
    prefix = '.harness-state-op-' + owner + '-'
    if any(item.name.startswith(prefix) for item in scratch.iterdir()):
        raise ValueError('unreconciled harness scratch preserved')
    operation = project_tools.safe(scratch, prefix + uuid.uuid4().hex)
    staging = operation / 'data.json'
    directory_info = file_info = None
    complete = replaced = False
    try:
        operation.mkdir()
        try:
            directory_info = operation.lstat()
        except (OSError, ValueError) as error:
            raise OSError('harness scratch directory retained at ' + str(operation)) from error
        if (not stat.S_ISDIR(directory_info.st_mode)
                or getattr(directory_info, 'st_file_attributes', 0) & 0x400):
            raise ValueError('unsafe harness scratch directory; preserved')
        project_tools.safe(scratch, operation.name)
        flags = (os.O_CREAT | os.O_EXCL | os.O_WRONLY
                 | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
        fd = os.open(staging, flags, 0o600)
        try:
            file_info = os.fstat(fd)
            with os.fdopen(fd, 'wb') as stream:
                fd = None
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            complete = True
        finally:
            if fd is not None:
                os.close(fd)
        current = operation.lstat()
        if (not stat.S_ISDIR(current.st_mode)
                or getattr(current, 'st_file_attributes', 0) & 0x400
                or (current.st_dev, current.st_ino) != (directory_info.st_dev, directory_info.st_ino)):
            raise ValueError('harness scratch directory changed; preserved')
        project_tools.verify_archive_stage_file(staging, file_info, content, True)
        staged = staging.lstat()
        if (not _plain(staged)
                or (staged.st_dev, staged.st_ino) != (file_info.st_dev, file_info.st_ino)):
            raise ValueError('harness scratch file changed; preserved')
        staged_identity = _identity(staged)
        _data, actual, identity = _read_snapshot(base)
        if actual != expected_raw or identity != expected_identity:
            raise ValueError('harness state changed; file preserved')
        target = project_tools.safe(base, STATE)
        os.replace(staging, target)
        replaced = True
    except BaseException as error:
        try:
            _clean_stage(operation, directory_info, file_info, content, complete, replaced)
        except BaseException as cleanup_error:
            raise error from cleanup_error
        raise
    _clean_stage(operation, directory_info, file_info, content, complete, replaced)
    return staged_identity


def write(project, expected_raw, data):
    """Write validated state only if the current exact bytes equal the expectation.

    Caller must hold ``locked(project)`` throughout the transaction. Pass None
    only when no file was observed; creation is exclusive and flushed/fsynced.
    Existing recognized state is replaced through guarded same-volume staging after
    rechecking bytes and identity. Unknown files and mismatches are preserved. An error, including
    after fsync or replace, requires readback/reconciliation before any retry.
    Return the exact bytes verified after writing, not a claim of task acceptance.
    """
    if expected_raw is not None and not isinstance(expected_raw, bytes):
        raise ValueError('expected harness state must be bytes or None')
    content = _encoded(data)
    base = project_tools.repository_root(project)
    _current, actual, identity = _read_snapshot(base)
    if actual != expected_raw:
        raise ValueError('harness state changed; file preserved')
    path = project_tools.safe(base, STATE)
    if actual is None:
        flags = (os.O_CREAT | os.O_EXCL | os.O_WRONLY
                 | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
        fd = os.open(path, flags, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                fd = None
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
                saved = os.fstat(stream.fileno())
                if not _plain(saved):
                    raise ValueError('created harness state identity changed')
                written_identity = _identity(saved)
        finally:
            if fd is not None:
                os.close(fd)
    else:
        written_identity = _replace(base, path, actual, identity, content)
    _result, verified, verified_identity = _read_snapshot(base)
    if verified != content or verified_identity != written_identity:
        raise ValueError('harness state write could not be verified')
    return verified
