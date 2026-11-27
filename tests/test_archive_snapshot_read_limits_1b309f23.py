"""Exercise snapshot byte limits with small in-memory source files."""

from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import archive_sync

WORK = ROOT / 'in-memory-snapshot-read-limits'
COMMIT = '0' * 40
SNAPSHOT = WORK / 'snapshots' / COMMIT
SOURCE = b'# example\n'


def source_path(entry):
    return 'code/' + entry['code_sha256'] + '.txt'


def snapshot_inputs(*sources):
    entries = []
    files = {}
    for number, source in enumerate(sources):
        name = f'example-{number}'
        entry = {
            'name': name,
            'code': f'solutions/{name}/revisions/0001/example.py',
            'code_sha256': sha256(source).hexdigest(),
            'outcome': 'success',
            'evidence_sha256': 'a' * 64,
            'title': 'Example',
            'test_summary': 'Example output checked',
            'categories': ['testing'],
            'tags': ['limits'],
        }
        entries.append(entry)
        files[source_path(entry)] = source
    manifest = {'format': 'boosterai-index-v1', 'entries': entries}
    files['index.json'] = json.dumps(manifest, separators=(',', ':')).encode('ascii')
    return manifest, files


class ObservedBytesIO(BytesIO):
    def __init__(self, data, name, reads):
        super().__init__(data)
        self.name = name
        self.reads = reads

    def read(self, size=-1):
        data = super().read(size)
        self.reads.append((self.name, size, len(data)))
        return data


class ArchiveSnapshotReadLimitsTest(unittest.TestCase):
    @contextmanager
    def snapshot(self, files, *, index_limit, code_limit, total_limit):
        reads = []
        metadata = json.dumps({
            'commit': COMMIT,
            'repository': archive_sync.REPOSITORY,
            'index_sha256': sha256(files['index.json']).hexdigest(),
        }).encode('utf-8')
        files = {**files, 'snapshot.json': metadata}

        def open_binary(path, mode='r', **kwargs):
            self.assertEqual(mode, 'rb')
            name = path.relative_to(SNAPSHOT).as_posix()
            return ObservedBytesIO(files[name], name, reads)

        with (
            patch.multiple(archive_sync, MAX_INDEX=index_limit, MAX_CODE=code_limit, MAX_TOTAL=total_limit),
            patch.object(Path, 'open', autospec=True, side_effect=open_binary),
        ):
            yield reads
        self.assertEqual(reads[:1], [('snapshot.json', archive_sync.MAX_METADATA + 1, len(metadata))])
        del reads[:1]

    def test_index_at_limit_is_accepted(self):
        manifest, files = snapshot_inputs()
        size = len(files['index.json'])
        with self.snapshot(files, index_limit=size, code_limit=len(SOURCE), total_limit=size + 32) as reads:
            self.assertEqual(archive_sync.verify_snapshot(WORK, COMMIT), manifest)
        self.assertEqual(reads, [('index.json', size + 1, size)])

    def test_index_over_limit_reads_only_one_extra_byte(self):
        _, files = snapshot_inputs(SOURCE)
        limit = len(files['index.json'])
        files['index.json'] += b'       \n'
        with self.snapshot(files, index_limit=limit, code_limit=len(SOURCE), total_limit=limit + 32) as reads:
            with self.assertRaises(ValueError) as error:
                archive_sync.verify_snapshot(WORK, COMMIT)
        self.assertEqual(str(error.exception), 'index too large')
        self.assertEqual(reads, [('index.json', limit + 1, limit + 1)])

    def test_code_at_limit_is_accepted(self):
        manifest, files = snapshot_inputs(SOURCE)
        size = len(files['index.json'])
        code = source_path(manifest['entries'][0])
        with self.snapshot(files, index_limit=size, code_limit=len(SOURCE), total_limit=size + 32) as reads:
            self.assertEqual(archive_sync.verify_snapshot(WORK, COMMIT), manifest)
        self.assertEqual(reads, [('index.json', size + 1, size), (code, len(SOURCE) + 1, len(SOURCE))])

    def test_code_over_limit_reads_only_one_extra_byte(self):
        manifest, files = snapshot_inputs(SOURCE + b'# padding\n')
        size = len(files['index.json'])
        code = source_path(manifest['entries'][0])
        limit = len(SOURCE)
        with self.snapshot(files, index_limit=size, code_limit=limit, total_limit=size + 32) as reads:
            with self.assertRaises(ValueError) as error:
                archive_sync.verify_snapshot(WORK, COMMIT)
        self.assertEqual(str(error.exception), 'local downloaded source too large')
        self.assertEqual(reads, [('index.json', size + 1, size), (code, limit + 1, limit + 1)])

    def test_total_at_limit_counts_index_and_each_entry_with_the_same_digest(self):
        manifest, files = snapshot_inputs(SOURCE, SOURCE)
        size = len(files['index.json'])
        code = source_path(manifest['entries'][0])
        total = size + 2 * len(SOURCE)
        code_limit = len(SOURCE) + 4
        with self.snapshot(files, index_limit=total + 8, code_limit=code_limit, total_limit=total) as reads:
            self.assertEqual(archive_sync.verify_snapshot(WORK, COMMIT), manifest)
        self.assertEqual(reads, [
            ('index.json', total + 1, size),
            (code, code_limit + 1, len(SOURCE)),
            (code, len(SOURCE) + 1, len(SOURCE)),
        ])

    def test_total_over_limit_reads_only_one_byte_beyond_remaining_budget(self):
        manifest, files = snapshot_inputs(SOURCE, SOURCE)
        size = len(files['index.json'])
        code = source_path(manifest['entries'][0])
        remaining = 3
        total = size + len(SOURCE) + remaining
        code_limit = len(SOURCE) + 4
        with self.snapshot(files, index_limit=total + 8, code_limit=code_limit, total_limit=total) as reads:
            with self.assertRaises(ValueError) as error:
                archive_sync.verify_snapshot(WORK, COMMIT)
        self.assertEqual(str(error.exception), 'snapshot total size exceeded')
        self.assertEqual(reads, [
            ('index.json', total + 1, size),
            (code, len(SOURCE) + remaining + 1, len(SOURCE)),
            (code, remaining + 1, remaining + 1),
        ])


if __name__ == '__main__':
    unittest.main()
