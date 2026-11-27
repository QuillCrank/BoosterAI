"""Fixture-free checks for archive index parsing and bounded reads."""

from contextlib import contextmanager, nullcontext
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).absolute().parents[1] / 'scripts'))
import project_tools

VIRTUAL_ROOT = Path('virtual-root')
EMPTY_RESULTS = {'results': [], 'related_files_by_record': {}, 'savings_measured': False}


class ArchiveSearchDuplicateKeysTest(unittest.TestCase):
    @contextmanager
    def archive_index(self, payload):
        index = Mock(spec_set=Path)
        index.exists.return_value = payload is not None
        with BytesIO(payload or b'') as buffer:
            stream = Mock(wraps=buffer)
            index.open.return_value = nullcontext(stream)
            with (
                patch.object(project_tools, 'repository_root', return_value=VIRTUAL_ROOT),
                patch.object(project_tools, 'safe', return_value=index),
            ):
                yield index, stream

    def test_rejects_duplicate_decoded_keys_before_validation(self):
        cases = {
            'root': '{"format":"boosterai-index-v1","entries":[],"sample":"first","sample":"second"}',
            'nested': (
                '{"format":"boosterai-index-v3",'
                '"entries":[{"details":{"sample":"first","sample":"second"}}]}'
            ),
            'escaped': (
                r'{"format":"boosterai-index-v2","entries":[],'
                r'"sample":"first","s\u0061mple":"second"}'
            ),
        }
        for name, payload in cases.items():
            with (
                self.subTest(name=name),
                self.archive_index(payload.encode('utf-8')),
                patch.object(project_tools.profile, 'validate_index') as validate_index,
            ):
                with self.assertRaisesRegex(ValueError, '^duplicate archive index field$'):
                    project_tools.archive_search(VIRTUAL_ROOT, query='unmatched')
                validate_index.assert_not_called()

    def test_rejects_excessive_json_nesting_below_byte_limit(self):
        payload = b'{"format":"boosterai-index-v1","entries":[]}'
        limit = project_tools.profile.MAX_INDEX_BYTES
        self.assertLess(len(payload), limit)
        with (
            self.archive_index(payload) as (_, stream),
            patch.object(project_tools.json, 'loads', side_effect=RecursionError('parser detail')) as parse,
        ):
            with self.assertRaisesRegex(ValueError, r'\Aarchive index JSON nesting is too deep\Z'):
                project_tools.archive_search(VIRTUAL_ROOT)
            stream.read.assert_called_once_with(limit + 1)
            parse.assert_called_once()
            self.assertEqual(parse.call_args.args, (payload.decode('utf-8'),))

    def test_accepts_exact_byte_limit_with_multibyte_utf8(self):
        index = {'format': 'boosterai-index-v1', 'entries': [{'name': 'coffee', 'title': 'Caf\u00e9'}]}
        payload = json.dumps(index, ensure_ascii=False).encode('utf-8')
        limit = len(payload)
        with (
            patch.object(project_tools.profile, 'MAX_INDEX_BYTES', limit),
            self.archive_index(payload) as (path, stream),
        ):
            result = project_tools.archive_search(VIRTUAL_ROOT, query='CAF\u00c9')
            self.assertEqual([entry['title'] for entry in result['results']], ['Caf\u00e9'])
            path.open.assert_called_once_with('rb')
            stream.read.assert_called_once_with(limit + 1)

    def test_rejects_first_excess_byte_before_decoding_or_parsing(self):
        limit = 64
        payload = b' ' * limit + b'\xff' + b' ' * 8
        with (
            patch.object(project_tools.profile, 'MAX_INDEX_BYTES', limit),
            patch.object(project_tools.json, 'loads') as parse,
            self.archive_index(payload) as (path, stream),
        ):
            with self.assertRaisesRegex(ValueError, '^archive index too large$'):
                project_tools.archive_search(VIRTUAL_ROOT)
            path.open.assert_called_once_with('rb')
            stream.read.assert_called_once_with(limit + 1)
            self.assertEqual(stream.tell(), limit + 1)
            parse.assert_not_called()

    def test_preserves_strict_utf8_and_bom_rejection(self):
        valid_index = '{"format":"boosterai-index-v1","entries":[]}'
        cases = (
            ('invalid utf8', b'{"note":"\xff"}', UnicodeDecodeError),
            ('utf16', valid_index.encode('utf-16'), UnicodeDecodeError),
            ('bom', ('\ufeff' + valid_index).encode('utf-8'), json.JSONDecodeError),
        )
        for label, payload, error in cases:
            with self.subTest(label=label), self.archive_index(payload):
                with self.assertRaises(error):
                    project_tools.archive_search(VIRTUAL_ROOT)

    def test_accepts_all_supported_index_versions(self):
        for version in (1, 2, 3):
            manifest = {'format': f'boosterai-index-v{version}', 'entries': []}
            if version >= 2:
                manifest.update(categories=[], tags=[])
            if version == 3:
                manifest['relationships'] = []
            with self.subTest(version=version), self.archive_index(json.dumps(manifest).encode('utf-8')) as (path, _):
                self.assertEqual(project_tools.archive_search(VIRTUAL_ROOT), EMPTY_RESULTS)
                path.open.assert_called_once_with('rb')

    def test_missing_index_is_empty_without_an_open(self):
        with self.archive_index(None) as (path, _):
            self.assertEqual(project_tools.archive_search(VIRTUAL_ROOT), EMPTY_RESULTS)
            path.open.assert_not_called()


if __name__ == '__main__':
    unittest.main()
