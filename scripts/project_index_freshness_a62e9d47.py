"""Compare explicitly selected project index entries with their source bytes."""

import argparse
import json
from pathlib import Path
import sys

# Isolated Python does not add the entry point's directory to sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from project_tools import project_index_read_file, project_index_state, repository_root


class FreshnessError(ValueError):
    """Identify an incomplete comparison with a fixed, path-free error code."""


def check_freshness(project, path, *additional_paths):
    """Compare the size and SHA-256 of authorized files against one index snapshot.

    Every selector must exactly match a distinct, readable project-relative path
    in the index. Reject all invalid selections before reading any source.
    Return the single-entry report for one path or reports in index path order
    for several paths. A failed read or observed index change aborts the batch.
    """
    requested = (path, *additional_paths)
    if any(not isinstance(selector, str) for selector in requested):
        raise FreshnessError('path_not_indexed')
    selected = set(requested)
    if len(selected) != len(requested):
        raise FreshnessError('duplicate_path')

    base = repository_root(project)
    state = project_index_state(base)
    if state is None:
        raise FreshnessError('index_missing')
    entries = [row for row in state[0]['entries'] if row['path'] in selected]
    if len(entries) != len(selected):
        raise FreshnessError('path_not_indexed')

    reports = []
    for entry in entries:
        try:
            observed, _, _ = project_index_read_file(base, entry['path'])
        except FileNotFoundError as error:
            raise FreshnessError('source_missing') from error
        indexed = {'size': entry['size'], 'sha256': entry['sha256']}
        reports.append({
            'status': 'CURRENT' if observed == indexed else 'STALE',
            'scope': 'selected_index_entry',
            'path': entry['path'],
            'indexed': indexed,
            'observed': observed,
        })

    if project_index_state(base) != state:
        raise FreshnessError('index_changed')
    if not additional_paths:
        return reports[0]
    return {
        'status': 'STALE' if any(report['status'] == 'STALE' for report in reports) else 'CURRENT',
        'scope': 'selected_index_entries',
        'entries': reports,
    }


class _FreshnessArgumentParser(argparse.ArgumentParser):
    """Keep caller-provided argument values out of CLI errors."""

    def error(self, _message):
        super().error('invalid arguments. Use --help for usage.')


def main(argv=None):
    """Report the comparison with an optional exit gate for stale entries."""
    parser = _FreshnessArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--root', action='append', required=True, help='authorized project root')
    parser.add_argument('--path', action='append', required=True,
                        help='exact indexed path authorized for reading (repeat to compare several)')
    parser.add_argument('--require-current', action='store_true',
                        help='exit 3 if any selected indexed file is stale')
    args = parser.parse_args(argv)
    if len(args.root) != 1 or not args.root[0].strip():
        parser.error('exactly one nonempty project root is required')
    try:
        result = check_freshness(args.root[0], *args.path)
    except FreshnessError as error:
        result = {'status': 'ERROR', 'error': str(error)}
    except PermissionError:
        result = {'status': 'ERROR', 'error': 'access_denied'}
    except (OSError, ValueError, RecursionError):
        result = {'status': 'ERROR', 'error': 'index_or_source_unavailable'}
    print(json.dumps(result))
    if args.require_current and result['status'] == 'STALE':
        return 3
    return int(result['status'] == 'ERROR')


if __name__ == '__main__':
    sys.exit(main())
