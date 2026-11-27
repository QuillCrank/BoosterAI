"""Fixture-free coverage of the active archive pointer used by search."""

from contextlib import nullcontext, redirect_stdout
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import archive_sync

WORK = ROOT / '_boosterAI' / 'archive-sync'
ACTIVE_PATH = WORK / 'active.json'


class ArchiveSearchActivePointerTest(unittest.TestCase):
    def search_failure(self, payload):
        stream = Mock(wraps=io.BytesIO(payload))
        output = io.StringIO()
        with (
            patch.object(Path, 'mkdir', side_effect=AssertionError('search created storage')) as mkdir,
            patch.object(Path, 'open', autospec=True, return_value=nullcontext(stream)) as open_file,
            patch.object(archive_sync, 'verify_snapshot') as verify,
            patch.object(sys, 'argv', ['archive_sync.py', 'search', '--root', str(ROOT)]),
            redirect_stdout(output),
        ):
            self.assertEqual(archive_sync.main(), 1)
        open_file.assert_called_once_with(ACTIVE_PATH, 'rb')
        stream.read.assert_called_once_with(archive_sync.MAX_METADATA + 1)
        verify.assert_not_called()
        mkdir.assert_not_called()
        response = json.loads(output.getvalue())
        self.assertEqual(response, {'status': 'FAILED', 'reason': response['reason']})
        return response['reason']

    def test_search_rejects_duplicate_decoded_metadata_without_opening_snapshot(self):
        cases = {
            'root': '"note":1,"note":2',
            'nested': '"details":{"note":1,"note":2}',
            'escaped': r'"note":1,"\u006eote":2',
        }
        for variant, metadata in cases.items():
            with self.subTest(variant=variant):
                payload = ('{"commit":"' + '0' * 40 + '",' + metadata + '}').encode('utf-8')
                self.assertEqual(self.search_failure(payload), 'duplicate archive manifest field')

    def test_search_rejects_over_limit_before_decoding(self):
        limit = 128
        payload = b' ' * limit + b'\xff' + b' ' * 8
        with patch.object(archive_sync, 'MAX_METADATA', limit):
            self.assertEqual(self.search_failure(payload), 'active pointer too large')

    def test_search_preserves_utf8_only_without_bom(self):
        valid = json.dumps({'commit': '0' * 40})
        for payload, diagnostic in (
            (valid.encode('utf-16'), "'utf-8' codec"),
            (('\ufeff' + valid).encode('utf-8'), 'Unexpected UTF-8 BOM'),
        ):
            with self.subTest(diagnostic=diagnostic):
                self.assertIn(diagnostic, self.search_failure(payload))

    def test_search_rejects_invalid_explicit_repository_before_opening_snapshot(self):
        commit = '0' * 40
        repositories = ('urn:example:archive-readback', '', None, True, 1, [], {})
        for repository in repositories:
            with self.subTest(repository=repository):
                payload = json.dumps({'commit': commit, 'repository': repository}).encode('utf-8')
                self.assertEqual(self.search_failure(payload), 'active pointer repository mismatch')

    def test_search_allows_same_metadata_name_in_separate_objects(self):
        commit = '0' * 40
        payload = json.dumps({
            'commit': commit, 'left': {'note': '\u00e9'}, 'right': {'note': 2},
        }, ensure_ascii=False).encode('utf-8')
        stream = Mock(wraps=io.BytesIO(payload))
        with (
            patch.object(archive_sync, 'MAX_METADATA', len(payload)),
            patch.object(Path, 'mkdir', side_effect=AssertionError('search created storage')) as mkdir,
            patch.object(Path, 'open', autospec=True, return_value=nullcontext(stream)) as open_file,
            patch.object(archive_sync, 'verify_snapshot', return_value={'entries': []}) as verify,
        ):
            actual = archive_sync.search(ROOT)
        open_file.assert_called_once_with(ACTIVE_PATH, 'rb')
        stream.read.assert_called_once_with(len(payload) + 1)
        verify.assert_called_once_with(WORK, commit)
        mkdir.assert_not_called()
        self.assertEqual(actual, {
            'commit': commit, 'results': [], 'related_files_by_record': {},
            'execution_authorized': False,
        })


if __name__ == '__main__':
    unittest.main()
