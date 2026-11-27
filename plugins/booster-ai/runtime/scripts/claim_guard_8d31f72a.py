"""Cooperative, process-lifetime claims for a project slice and its files.

Use ``with claim(project, slice_id, files): ...``. All participants must use the
same project and this protocol. Each identity hashes to one of 256 persistent
stripe files in ``_boosterAI/claim-guards``. Collisions conservatively return
BUSY; the fixed stripe count bounds persistent state and avoids per-run files.
The files are never removed, even after a crash. An unrecognized or incomplete
file is preserved and requires inspection, not automatic recovery.

OS locks protect only cooperating processes while their handles are open. A
process crash closes its handles; this does not verify the interrupted work or
its external effects. The path checks reject observed links, reparse points and
hard links, but are not a sandbox against hostile concurrent path replacement.
"""

from contextlib import ExitStack, contextmanager
import errno
import hashlib
import os
from pathlib import Path
import re
import stat
import unicodedata


STRIPES = 256
_GUARD_DIR = Path('_boosterAI') / 'claim-guards'
_RESERVED = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$', re.I)


class ClaimBusyError(BlockingIOError):
    """A cooperating live owner currently holds a required stripe."""


class UnsafeClaimError(ValueError):
    """An identity or persistent guard could not be safely recognized."""


def _check(condition, message):
    """Enforce a condition using the claim protocol's validation error.

    Args: condition is the truth value to check; message describes a failure.
    Returns: None when the condition is true.
    Raises: UnsafeClaimError with message when the condition is false.
    """
    if not condition:
        raise UnsafeClaimError(message)


def _plain(info, kind):
    """Validate filesystem metadata for an ordinary, unlinked object.

    Args: info is stat-like metadata; kind selects a directory or a regular file.
    Returns: None when the observed type and link attributes are permitted.
    Raises: UnsafeClaimError for links, reparse points, wrong types or hardlinked files.
    """
    _check(not stat.S_ISLNK(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & 0x400),
           'links and reparse points are forbidden')
    if kind == 'directory':
        _check(stat.S_ISDIR(info.st_mode), 'expected an ordinary directory')
    else:
        _check(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
               'expected a single-link regular file')


def _root(project):
    """Resolve an existing project directory after checking its ancestors.

    Args: project is a path to the project root.
    Returns: An absolute, resolved Path for that directory.
    Raises: UnsafeClaimError for observed links or invalid types; filesystem errors
        such as missing paths or access denial propagate. Checks are not atomic.
    """
    base = Path(project).absolute()
    for component in (base, *base.parents):
        _plain(component.lstat(), 'directory')
    resolved = base.resolve(strict=True)
    _check(resolved.is_dir(), 'project root must be a directory')
    return resolved


def _normal(value):
    """Normalize an identity for deterministic cooperative comparisons.

    Args: value is a Unicode string.
    Returns: Its NFC-normalized, case-folded text. Distinct case-sensitive names
        may conservatively compare equal. Invalid input type errors propagate.
    """
    return unicodedata.normalize('NFC', value).casefold()


def _slice_key(slice_id):
    """Validate and normalize a logical slice identity.

    Args: slice_id is a nonempty string of at most 256 characters without controls.
    Returns: The normalized identity prefixed with the slice namespace.
    Raises: UnsafeClaimError when the supplied identity violates those constraints.
    """
    _check(isinstance(slice_id, str) and 0 < len(slice_id) <= 256,
           'slice identity must be a nonempty string')
    _check(not any(ord(char) < 32 for char in slice_id), 'invalid slice identity')
    return 'slice:' + _normal(slice_id)


def _file_key(base, relative):
    """Validate and canonicalize one project-relative file identity.

    Args: base is the resolved project Path; relative is a string or path-like file
        identity using forward slashes. The target may not exist yet.
    Returns: A normalized, case-folded file-namespace key. Case variants can collide.
    Raises: UnsafeClaimError for unsafe paths, observed links, reparse points,
        hardlinks or an escape. Other filesystem errors propagate. Ancestor checks
        and resolution are not a sandbox against concurrent path replacement.
    """
    _check(isinstance(relative, (str, os.PathLike)), 'file identity must be a path')
    value = os.fspath(relative)
    _check(isinstance(value, str) and 0 < len(value) <= 4096, 'invalid file identity')
    _check('\\' not in value and ':' not in value and not value.startswith('/'),
           'file identity must be project-relative with forward slashes')
    parts = value.split('/')
    _check(all(part not in ('', '.', '..') and not part.endswith(('.', ' '))
               and not _RESERVED.fullmatch(part)
               and not any(ord(char) < 32 for char in part) for part in parts),
           'unsafe file identity')
    current = base
    missing = False
    for index, part in enumerate(parts):
        current = current / part
        if missing:
            continue
        try:
            info = current.lstat()
        except FileNotFoundError:
            missing = True
            continue
        _plain(info, 'file' if index == len(parts) - 1 else 'directory')
    candidate = current.resolve(strict=False)
    try:
        normalized = candidate.relative_to(base).as_posix()
    except ValueError as error:
        raise UnsafeClaimError('file identity escaped project root') from error
    return 'file:' + _normal(normalized)


def _stripe(key):
    """Map a canonical claim key to the bounded lock namespace.

    Args: key is a UTF-8 encodable normalized identity string.
    Returns: An integer stripe in the range zero through STRIPES minus one. Different
        keys may share a stripe and then conservatively conflict; encoding errors
        propagate without changing any filesystem state.
    """
    return int.from_bytes(hashlib.sha256(key.encode('utf-8')).digest()[:2], 'big') % STRIPES


def _guard_directory(base):
    """Locate or create the persistent project guard directory.

    Args: base is the resolved project Path and its _boosterAI directory must exist.
    Returns: The Path to its claim-guards directory after containment and type checks.
    Side effects: Creates that directory only when absent; existing contents remain.
    Raises: UnsafeClaimError for unsafe observed paths; filesystem errors propagate.
    """
    workspace = base / '_boosterAI'
    _plain(workspace.lstat(), 'directory')
    directory = base / _GUARD_DIR
    try:
        directory.mkdir()
    except FileExistsError:
        pass
    _plain(directory.lstat(), 'directory')
    _check(directory.resolve(strict=True).parent == workspace.resolve(strict=True),
           'guard directory escaped the project workspace')
    return directory


def _identity(info):
    """Extract an observed filesystem object's identity fields.

    Args: info is stat-like metadata with st_dev and st_ino attributes.
    Returns: A (device, inode) tuple for comparison with another observation.
        This is not a guarantee against all filesystem races; no files are modified.
    """
    return info.st_dev, info.st_ino


def _open_guard(directory, stripe):
    """Open one persistent stripe without modifying existing contents.

    Args: directory is the validated guard Path; stripe is the stripe number.
    Returns: (open descriptor, path, initial handle metadata, created flag); the
        caller must close the descriptor. A new file is created exclusively when
        absent, and an existing unknown file is only opened, not initialized here.
    Raises: UnsafeClaimError for invalid observed identity or link state; OS errors
        propagate. A descriptor is closed if validation fails after it is opened.
    """
    path = directory / ('stripe-%02x.lock' % stripe)
    flags = os.O_RDWR | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOINHERIT', 0) | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
    except FileExistsError:
        _plain(path.lstat(), 'file')
        fd = os.open(path, flags)
        created = False
    try:
        handle_info = os.fstat(fd)
        path_info = path.lstat()
        _plain(handle_info, 'file')
        _plain(path_info, 'file')
        _check(_identity(handle_info) == _identity(path_info), 'guard identity changed')
    except BaseException:
        os.close(fd)
        raise
    return fd, path, handle_info, created


def _try_lock(fd):
    """Acquire one exclusive, nonblocking operating-system lock.

    Args: fd is an open guard descriptor that the caller must keep open.
    Returns: None after acquiring the lock; closing fd releases its OS ownership.
    Raises: ClaimBusyError for the recognized lock-conflict status only. Access
        denial and other OS errors propagate instead of being reported as BUSY.
    """
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        import msvcrt

        class Overlapped(ctypes.Structure):
            _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                        ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD),
                        ('hEvent', wintypes.HANDLE)]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        lock = kernel.LockFileEx
        lock.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                         wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
        lock.restype = wintypes.BOOL
        overlap = Overlapped()
        if not lock(msvcrt.get_osfhandle(fd), 3, 0, 1, 0, ctypes.byref(overlap)):
            code = ctypes.get_last_error()
            if code == 33:  # ERROR_LOCK_VIOLATION, not ERROR_ACCESS_DENIED.
                raise ClaimBusyError(errno.EWOULDBLOCK, 'claim is BUSY')
            raise ctypes.WinError(code)
    else:
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise ClaimBusyError(errno.EWOULDBLOCK, 'claim is BUSY') from error
            raise


def _verify_guard(fd, path, initial, created, stripe):
    """Initialize an owned new guard or recognize an existing guard.

    Args: fd is the already locked descriptor; path and initial describe its opened
        identity; created indicates exclusive creation; stripe selects its marker.
    Returns: None after checking the identity and expected persistent marker.
    Side effects: Writes and flushes the marker only for a newly created empty guard.
    Raises: UnsafeClaimError for changed identity or unknown contents; I/O failures
        propagate. Incomplete files remain in place for explicit reconciliation.
    """
    expected = ('boosterai-claim-guard-v1:%02x\n' % stripe).encode('ascii')
    current = path.lstat()
    _plain(current, 'file')
    _check(_identity(current) == _identity(initial) == _identity(os.fstat(fd)),
           'guard identity changed')
    if created:
        _check(os.fstat(fd).st_size == 0, 'new guard unexpectedly changed')
        offset = 0
        while offset < len(expected):
            written = os.write(fd, expected[offset:])
            if written <= 0:
                raise OSError('incomplete guard initialization; preserve for reconciliation')
            offset += written
        os.fsync(fd)
    else:
        _check(os.fstat(fd).st_size == len(expected), 'unknown or incomplete guard; preserved')
        os.lseek(fd, 0, os.SEEK_SET)
        _check(os.read(fd, len(expected) + 1) == expected, 'unknown guard; preserved')
    current = path.lstat()
    _plain(current, 'file')
    _check(_identity(current) == _identity(initial) == _identity(os.fstat(fd)),
           'guard identity changed')


@contextmanager
def claim(project, slice_id, files=()):
    """Hold cooperative, nonblocking claims for a slice and its files.

    Args: project is the existing project root; slice_id identifies the logical slice;
        files is a list or tuple of project-relative file identities using slashes.
    Yields: None after acquiring the required stripes in deterministic order.
    Side effects: May create persistent guard state and holds OS locks until context
        exit. Partial acquisitions close on failure; guards are never deleted.
    Raises: ClaimBusyError for a held or conservatively colliding stripe;
        UnsafeClaimError for invalid identities or unknown guards; other OS errors
        propagate. Only participating processes are coordinated. A crash releases
        handles but does not reconcile partially completed product effects.
    """
    _check(isinstance(files, (list, tuple)), 'files must be a list or tuple')
    base = _root(project)
    keys = [_slice_key(slice_id), *(_file_key(base, path) for path in files)]
    stripes = sorted({_stripe(key) for key in keys})
    directory = _guard_directory(base)
    directory_identity = _identity(directory.lstat())
    with ExitStack() as handles:
        for stripe in stripes:
            _plain(directory.lstat(), 'directory')
            _check(_identity(directory.lstat()) == directory_identity, 'guard directory changed')
            fd, path, initial, created = _open_guard(directory, stripe)
            handles.callback(os.close, fd)
            _try_lock(fd)
            _verify_guard(fd, path, initial, created, stripe)
        _check(keys == [_slice_key(slice_id), *(_file_key(base, path) for path in files)],
               'file identity changed while acquiring claim')
        yield
