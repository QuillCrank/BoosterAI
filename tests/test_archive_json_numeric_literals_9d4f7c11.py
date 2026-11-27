"""Fixture-free checks for archive JSON number-literal parsing."""

from contextlib import nullcontext
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import archive_profile
import archive_sync
import project_tools

VIRTUAL_ROOT = Path('virtual-root')
V1 = b'{"format":"boosterai-index-v1","entries":[]}'

def root_search(raw):
    index = Mock(spec_set=Path)
    index.exists.return_value = True
    with BytesIO(raw) as stream:
        index.open.return_value = nullcontext(stream)
        with (
            patch.object(project_tools, 'repository_root', return_value=VIRTUAL_ROOT),
            patch.object(project_tools, 'safe', return_value=index),
        ):
            return project_tools.archive_search(VIRTUAL_ROOT)


class ArchiveJSONNumericLiteralsTest(unittest.TestCase):
    def test_both_readers_reject_non_finite_literals_in_v1_metadata(self):
        for literal in (b'NaN', b'Infinity', b'-Infinity'):
            raw = V1[:-1] + b',"extension":{"weight":' + literal + b'}}'
            for read in (archive_sync.validate_manifest, root_search):
                with self.subTest(literal=literal, reader=read.__name__):
                    with self.assertRaisesRegex(ValueError, '^non-finite JSON number$'):
                        read(raw)

    def test_finite_numbers_and_quoted_names_survive_v1(self):
        numbers = {'integer': 17, 'fraction': -2.25, 'exponent': 1e-12,
                   'quoted': ['NaN', 'Infinity', '-Infinity']}
        entry = {
            'name': 'example', 'code': 'solutions/example/revisions/0001/example.py',
            'code_sha256': 'a' * 64, 'evidence_sha256': 'b' * 64,
            'outcome': 'success', 'title': 'Example', 'test_summary': 'Checked',
            'categories': ['testing'], 'tags': ['example'], 'numeric_metadata': numbers,
        }
        raw = json.dumps({'format': 'boosterai-index-v1', 'entries': [entry]}).encode('utf-8')
        remote = archive_sync.validate_manifest(raw)
        local = root_search(raw)
        self.assertEqual(remote['entries'][0]['numeric_metadata'], numbers)
        self.assertEqual(local['results'][0]['numeric_metadata'], numbers)

    def test_v2_and_v3_remain_valid(self):
        for version in (2, 3):
            manifest = {'format': f'boosterai-index-v{version}', 'entries': [],
                        'categories': [], 'tags': []}
            if version == 3:
                manifest['relationships'] = []
            raw = json.dumps(manifest).encode('utf-8')
            with self.subTest(version=version):
                self.assertEqual(archive_sync.validate_manifest(raw), manifest)
                self.assertEqual(root_search(raw)['results'], [])

    def test_each_reader_preserves_its_input_encoding_policy(self):
        text = V1.decode('utf-8')
        for encoding, root_error in (('utf-8-sig', json.JSONDecodeError),
                                     ('utf-16', UnicodeDecodeError),
                                     ('utf-32', UnicodeDecodeError)):
            raw = text.encode(encoding)
            with self.subTest(encoding=encoding):
                self.assertEqual(archive_sync.validate_manifest(raw), json.loads(V1))
                with self.assertRaises(root_error):
                    root_search(raw)

    def test_decoded_duplicate_names_remain_rejected(self):
        raw = V1[:-1] + br',"sample":1,"s\u0061mple":2}'
        with self.assertRaisesRegex(ValueError, '^duplicate archive manifest field$'):
            archive_sync.validate_manifest(raw)
        with self.assertRaisesRegex(ValueError, '^duplicate archive index field$'):
            root_search(raw)

    def test_existing_size_checks_precede_literal_parsing(self):
        raw = V1[:-1] + b',"extension":NaN}'
        with (
            patch.object(archive_sync, 'MAX_INDEX', len(raw) - 1),
            patch.object(archive_profile, 'MAX_INDEX_BYTES', len(raw) - 1),
        ):
            with self.assertRaisesRegex(ValueError, '^index too large$'):
                archive_sync.validate_manifest(raw)
            with self.assertRaisesRegex(ValueError, '^archive index too large$'):
                root_search(raw)


if __name__ == '__main__':
    unittest.main()
