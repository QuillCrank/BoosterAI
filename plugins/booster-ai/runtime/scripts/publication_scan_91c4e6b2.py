"""Scan explicitly listed text files for publication risks without writing or networking."""

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys


MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_FILES = 20000
MAX_TERMS = 1000
MAX_COMMITS = 20000
HEX_RUN = re.compile(r'(?<![0-9a-f])[0-9a-f]{7,40}(?![0-9a-f])', re.IGNORECASE)
PERSONAL_PATH = re.compile(
    r'(?i)(?<![A-Za-z0-9])(?:[A-Za-z]:\\(?:Users|Repos|Documents and Settings)\\[^\\\s"\']+'
    r'|/(?:home|Users)/[A-Za-z0-9._-]+(?:/|\b))'
)
CREDENTIAL = re.compile(
    r'(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}'
    r'|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}'
    r'|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)'
)


class ScanError(ValueError):
    """Carry a fixed error code and optional file index without retaining private input."""

    def __init__(self, code, index=None):
        """Save only a fixed diagnostic category and an optional zero-based index."""
        super().__init__(code)
        self.code = code
        self.index = index


class PrivateParser(argparse.ArgumentParser):
    """Avoid echoing private command-line arguments when parsing fails."""

    def error(self, message):
        """Exit with a fixed message, ignoring the parser's potentially private details."""
        self.exit(2, 'invalid arguments\n')


def validated_root(requested):
    """Resolve a directory only after rejecting links or reparse-point ancestors.

    The host must already have authority to inspect the supplied directory. This
    check does not provide an OS sandbox against concurrent external replacement.
    """
    try:
        root = Path(requested).absolute()
        for ancestor in (root, *root.parents):
            info = ancestor.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ScanError('unsafe_root')
        if not root.is_dir():
            raise ScanError('invalid_root')
        return root.resolve()
    except PermissionError as error:
        raise ScanError('access_denied') from error
    except OSError as error:
        raise ScanError('invalid_root') from error


def parse_manifest(raw):
    """Validate the complete stdin manifest without printing its private contents.

    File paths and private terms must be nonempty strings; old commits must be full
    forty-hex Git object identifiers. Empty term or commit lists are permitted when
    explicitly supplied; the human reviewer remains responsible for completeness.

    Return a tuple containing the file list in manifest order, a list of
    case-folded private terms in supplied order, and a set of lowercase
    old-commit prefixes of 7 to 40 hexadecimal digits.
    """
    if not isinstance(raw, dict) or set(raw) != {'files', 'private_terms', 'old_commits'}:
        raise ScanError('invalid_manifest')
    files, terms, commits = raw['files'], raw['private_terms'], raw['old_commits']
    if (not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES
            or any(not isinstance(item, str) or not item.strip() for item in files)
            or len(set(files)) != len(files)):
        raise ScanError('invalid_manifest')
    if (not isinstance(terms, list) or len(terms) > MAX_TERMS
            or any(not isinstance(item, str) or not item.strip() or len(item) > 1000 for item in terms)):
        raise ScanError('invalid_manifest')
    if (not isinstance(commits, list) or len(commits) > MAX_COMMITS
            or any(not isinstance(item, str) or re.fullmatch(r'[0-9a-fA-F]{40}', item) is None for item in commits)):
        raise ScanError('invalid_commit_id')
    prefixes = {commit[:length].lower() for commit in commits for length in range(7, 41)}
    return files, [term.casefold() for term in terms], prefixes


def contained_file(root, relative, index):
    """Resolve one listed regular file without following links, reparse points or escapes.

    Inspect every path component before reading. Metadata and source paths are
    never echoed; all diagnostics refer to the zero-based manifest file index.
    """
    if not relative or '\x00' in relative or ':' in relative:
        raise ScanError('unsafe_path', index)
    raw = Path(relative)
    if raw.is_absolute() or raw.drive or '..' in raw.parts or not raw.parts:
        raise ScanError('unsafe_path', index)
    if (raw.parts[0].casefold() in ('_boosterai', '_temp-files')
            or any(part.casefold() == '.git' for part in raw.parts)):
        raise ScanError('unsafe_path', index)
    path = root
    try:
        for part in raw.parts:
            path /= part
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ScanError('unsafe_path', index)
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise ScanError('unsafe_path', index)
        if not stat.S_ISREG(info.st_mode) or not path.resolve().is_relative_to(root):
            raise ScanError('unsafe_path', index)
        return path, info
    except PermissionError as error:
        raise ScanError('access_denied', index) from error
    except FileNotFoundError as error:
        raise ScanError('file_missing', index) from error
    except OSError as error:
        raise ScanError('read_failed', index) from error


def read_checked(path, original, index):
    """Read bounded UTF-8 bytes after verifying the opened file's identity.

    Fail closed if the file is replaced or altered while being read. Existing file
    bytes remain untouched; permission denials have a distinct non-sensitive code.
    """
    try:
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or (opened.st_dev, opened.st_ino) != (original.st_dev, original.st_ino)):
                raise ScanError('file_changed', index)
            content = stream.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise ScanError('file_too_large', index)
        current = path.lstat()
        if (stat.S_ISLNK(current.st_mode) or getattr(current, 'st_file_attributes', 0) & 0x400
                or current.st_nlink != 1
                or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
                != (original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns)):
            raise ScanError('file_changed', index)
        try:
            return content.decode('utf-8-sig')
        except UnicodeDecodeError as error:
            raise ScanError('unsupported_encoding', index) from error
    except PermissionError as error:
        raise ScanError('access_denied', index) from error
    except FileNotFoundError as error:
        raise ScanError('file_missing', index) from error
    except OSError as error:
        raise ScanError('read_failed', index) from error


def scan_manifest(root, manifest):
    """Scan only listed filenames and UTF-8 files and return redacted finding categories.

    A clean result is explicitly not proof of complete privacy. Prefix matching
    accepts old Git IDs from seven through forty hex digits only when bounded by
    non-hex characters, avoiding matches inside contiguous SHA-256 values.
    Reject a second selection of the same file before reading it again.
    """
    root = validated_root(root)
    files, terms, prefixes = parse_manifest(manifest)
    findings = []
    selected_files = set()
    for index, relative in enumerate(files):
        path, info = contained_file(root, relative, index)
        identity = (info.st_dev, info.st_ino) if info.st_ino else path.as_posix()
        if identity in selected_files:
            raise ScanError('duplicate_file', index)
        selected_files.add(identity)
        content = read_checked(path, info, index)
        searchable = relative + '\n' + content
        folded = searchable.casefold()
        categories = []
        if any(term in folded for term in terms):
            categories.append('private_term')
        if any(match.group().lower() in prefixes for match in HEX_RUN.finditer(searchable)):
            categories.append('old_commit')
        if CREDENTIAL.search(searchable):
            categories.append('credential')
        if PERSONAL_PATH.search(searchable.replace('\\\\', '\\')):
            categories.append('personal_path')
        findings.extend({'file_index': index, 'category': category} for category in categories)
    return {'status': 'FINDINGS' if findings else 'NO_MATCH_REVIEW_REQUIRED',
            'files_checked': len(files), 'private_terms_supplied': len(terms),
            'old_commits_supplied': len(manifest['old_commits']),
            'findings': findings, 'privacy_proven': False}


def unique_manifest_object(pairs):
    """Reject duplicate JSON object names without retaining input in diagnostics.

    The JSON decoder calls this for every object, including nested objects, after
    decoding escaped names. No manifest field or value is included in the error.
    """
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('duplicate_manifest_field')
        result[name] = value
    return result


def main(argv=None):
    """Require one nonblank project root before reading a bounded, private stdin manifest."""
    parser = PrivateParser(description=__doc__)
    parser.add_argument('--root', action='append', required=True)
    args = parser.parse_args(argv)
    if len(args.root) != 1 or not args.root[0].strip():
        parser.error('exactly one project root is required')
    try:
        root = validated_root(args.root[0])
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ScanError('invalid_manifest')
        try:
            manifest = json.loads(raw.decode('utf-8-sig'),
                                  object_pairs_hook=unique_manifest_object)
        except (UnicodeError, ValueError, RecursionError) as error:
            raise ScanError('invalid_manifest') from error
        result = scan_manifest(root, manifest)
        print(json.dumps(result, sort_keys=True))
        return 1 if result['findings'] else 0
    except ScanError as error:
        result = {'status': 'ERROR', 'error': error.code, 'privacy_proven': False}
        if error.index is not None:
            result['file_index'] = error.index
        print(json.dumps(result, sort_keys=True))
        return 2
    except PermissionError:
        print(json.dumps({'status': 'ERROR', 'error': 'access_denied', 'privacy_proven': False}))
        return 2
    except OSError:
        print(json.dumps({'status': 'ERROR', 'error': 'read_failed', 'privacy_proven': False}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
