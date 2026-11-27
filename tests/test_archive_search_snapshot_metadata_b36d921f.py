"""Fixture-free checks for snapshot metadata rejected by archive search."""

from contextlib import nullcontext, redirect_stdout
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import archive_sync

COMMIT = '0' * 40
WORK = ROOT / '_boosterAI' / 'archive-sync'
ACTIVE_PATH = WORK / 'active.json'
METADATA_PATH = WORK / 'snapshots' / COMMIT / 'snapshot.json'


class ArchiveSearchSnapshotMetadataTest(unittest.TestCase):
    def assert_metadata_rejected(self, payload, reason):
        streams = {
            ACTIVE_PATH: Mock(wraps=io.BytesIO(json.dumps({'commit': COMMIT}).encode('utf-8'))),
            METADATA_PATH: Mock(wraps=io.BytesIO(payload)),
        }
        output = io.StringIO()
        with (
            patch.object(Path, 'mkdir', side_effect=AssertionError('search created storage')) as mkdir,
            patch.object(Path, 'open', autospec=True,
                         side_effect=lambda path, mode: nullcontext(streams[path])) as open_file,
            patch.object(sys, 'argv', ['archive_sync.py', 'search', '--root', str(ROOT)]),
            redirect_stdout(output),
        ):
            self.assertEqual(archive_sync.main(), 1)
        self.assertEqual(json.loads(output.getvalue()), {'status': 'FAILED', 'reason': reason})
        self.assertEqual(open_file.call_args_list, [call(ACTIVE_PATH, 'rb'), call(METADATA_PATH, 'rb')])
        for stream in streams.values():
            stream.read.assert_called_once_with(archive_sync.MAX_METADATA + 1)
        mkdir.assert_not_called()

    def test_search_rejects_duplicate_snapshot_metadata_before_reading_index(self):
        metadata_header = json.dumps({
            'commit': COMMIT,
            'repository': archive_sync.REPOSITORY,
            'index_sha256': '0' * 64,
        })[:-1]
        variants = {
            'root': '"note":1,"note":2',
            'nested': '"details":{"note":1,"note":2}',
            'escaped': r'"note":1,"\u006eote":2',
        }
        for name, fields in variants.items():
            with self.subTest(variant=name):
                payload = (metadata_header + ',' + fields + '}').encode('utf-8')
                self.assert_metadata_rejected(payload, 'duplicate archive manifest field')

    def test_search_rejects_over_limit_metadata_before_decoding_or_reading_index(self):
        limit = 128
        payload = b' ' * limit + b'\xff' + b' ' * 8
        with patch.object(archive_sync, 'MAX_METADATA', limit):
            self.assert_metadata_rejected(payload, 'snapshot metadata too large')


if __name__ == '__main__':
    unittest.main()
