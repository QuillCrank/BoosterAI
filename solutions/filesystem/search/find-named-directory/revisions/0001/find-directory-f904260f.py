"""Find a literal directory name below caller-selected roots without following links."""

import argparse
import json
import os
from pathlib import Path
import stat
import sys


def find_named_directories(roots, name):
    """Find an exact folder name below the selected roots without modifying files.

    Comparison is case-insensitive. Return matching paths, up to 50 error details,
    the total error count and skipped-link count. Inaccessible entries or links
    make the result incomplete; symbolic links and junctions are not traversed.
    """
    if not isinstance(name, str) or not name or name in ('.', '..') or any(c in name for c in ('/', '\\', '\0')):
        raise ValueError('name must be a single nonempty directory name')
    if not roots:
        raise ValueError('at least one search root is required')

    matches = {}
    errors = []
    error_count = 0
    skipped_links = 0

    def report(path, error):
        """Count an access error and retain a bounded diagnostic for the caller."""
        nonlocal error_count
        error_count += 1
        if len(errors) < 50:
            errors.append({'path': str(path), 'error': str(error)})

    for requested in roots:
        root = Path(os.path.abspath(os.fspath(requested)))
        try:
            # Reject links in the whole path, not just the final root component.
            for component in reversed((root, *root.parents)):
                info = component.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise ValueError('search root or an ancestor is a link or reparse point')
            if not stat.S_ISDIR(info.st_mode):
                raise NotADirectoryError(str(root))
        except (OSError, ValueError) as error:
            report(root, error)
            continue

        if root.name.casefold() == name.casefold():
            matches[os.path.normcase(str(root))] = str(root)
        pending = [str(root)]
        while pending:
            current = pending.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            info = entry.stat(follow_symlinks=False)
                            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                                skipped_links += 1
                                continue
                            if not stat.S_ISDIR(info.st_mode):
                                continue
                            if entry.name.casefold() == name.casefold():
                                matches[os.path.normcase(entry.path)] = entry.path
                            pending.append(entry.path)
                        except OSError as error:
                            report(entry.path, error)
            except OSError as error:
                report(current, error)

    return {
        'matches': sorted(matches.values(), key=str.casefold),
        'errors': errors,
        'error_count': error_count,
        'skipped_links': skipped_links,
        'complete': error_count == 0 and skipped_links == 0,
    }


def windows_local_drives():
    """Return lettered local Windows drives without enumerating their contents.

    Removable, fixed, optical and RAM drives are eligible. Network drives and
    unrecognized types are excluded. Raise an error if Windows reports no drives.
    """
    if os.name != 'nt':
        raise ValueError('--all-local-drives requires Windows')
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetLogicalDrives.argtypes = []
    kernel.GetLogicalDrives.restype = wintypes.DWORD
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetDriveTypeW.restype = wintypes.UINT
    mask = kernel.GetLogicalDrives()
    if not mask:
        raise ctypes.WinError(ctypes.get_last_error())
    drives = []
    for offset in range(26):
        if mask & (1 << offset):
            drive = f'{chr(ord("A") + offset)}:\\'
            if kernel.GetDriveTypeW(drive) in (2, 3, 5, 6):
                drives.append(drive)
    if not drives:
        raise ValueError('no mounted local drives were found')
    return drives


def main(argv=None):
    """Parse the requested scope, execute a read-only search and emit JSON results.

    Exit 0 for a complete match, 1 for a complete search without a match, 2 for
    invalid or incomplete results, and 130 when interrupted. Roots must be explicit.
    """
    parser = argparse.ArgumentParser(description='Find a folder by its exact name without following links.')
    parser.add_argument('--name', required=True, help='exact folder name')
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument('--root', action='append', metavar='PATH', help='authorized root; repeat for multiple roots')
    scope.add_argument('--all-local-drives', action='store_true', help='scan mounted local Windows drives')
    args = parser.parse_args(argv)
    try:
        roots = windows_local_drives() if args.all_local_drives else args.root
        result = find_named_directories(roots, args.name)
        result = {'name': args.name, 'roots': roots, **result}
        print(json.dumps(result, indent=2, ensure_ascii=True))
        if not result['complete']:
            return 2
        return 0 if result['matches'] else 1
    except (OSError, ValueError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=True), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(json.dumps({'error': 'search interrupted; results are incomplete'}), file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
