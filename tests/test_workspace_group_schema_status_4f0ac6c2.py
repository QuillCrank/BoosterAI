"""Exercise schema-status with only in-memory provenance and raw source bytes."""

from contextlib import redirect_stdout
import errno
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'booster.py'
PROVENANCE = '_boosterAI/algorithm.provenance.json'
SOURCE = 'references/project-workspace.md'
WORKING = '_boosterAI/algorithm.md'
SPEC = importlib.util.spec_from_file_location('booster_schema_status', SCRIPT)
BOOSTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOSTER)


class SchemaStatusTest(unittest.TestCase):
    def invoke(self, contents):
        files = {ROOT / path: (path, data) for path, data in contents.items()}
        reads = []

        def open_memory(path, mode='r', *args, **kwargs):
            if mode not in ('r', 'rb'):
                raise AssertionError('schema-status attempted to write a file')
            if path == ROOT / PROVENANCE and path not in files:
                reads.append((PROVENANCE, mode))
                raise FileNotFoundError(errno.ENOENT, 'No such file', '<private provenance path>')
            if path not in files:
                raise AssertionError('schema-status attempted an unexpected file read')
            name, data = files[path]
            reads.append((name, mode))
            if mode == 'rb':
                return io.BytesIO(data)
            return io.StringIO(data.decode(kwargs.get('encoding') or 'utf-8'))

        with (
            patch.object(Path, 'open', open_memory),
            patch.object(sys, 'argv', [str(SCRIPT), 'schema-status', '--root', str(ROOT)]),
            redirect_stdout(io.StringIO()) as output,
        ):
            code = BOOSTER.main()
        return code, json.loads(output.getvalue()), reads

    def test_missing_provenance_reports_a_fixed_error(self):
        code, result, reads = self.invoke({})
        self.assertEqual(code, 1)
        self.assertEqual(result, {'error': 'schema provenance record is missing'})
        self.assertEqual(reads, [(PROVENANCE, 'r')])

    def test_invalid_captured_hash_stops_before_reading_either_schema(self):
        invalid = (
            {},
            *({'source_sha256': value} for value in
              (None, 32, '', 'a' * 63, 'a' * 65, 'g' * 64, ' ' + 'a' * 63)),
        )
        for fields in invalid:
            with self.subTest(fields=fields):
                provenance = json.dumps({'source': SOURCE, **fields}).encode('utf-8')
                code, result, reads = self.invoke({PROVENANCE: provenance})
                self.assertEqual(code, 1)
                self.assertEqual(result, {'error': 'invalid provenance'})
                self.assertEqual(reads, [(PROVENANCE, 'r')])

    def test_uppercase_captured_hash_compares_the_same_bytes(self):
        original = b'# Schema\r\n'
        provenance = json.dumps({
            'source': SOURCE,
            'source_sha256': hashlib.sha256(original).hexdigest().upper(),
        }).encode('utf-8')
        code, result, reads = self.invoke({PROVENANCE: provenance, SOURCE: original, WORKING: original})
        self.assertEqual(code, 0)
        self.assertEqual(result, {
            'source': SOURCE,
            'source_changed': False,
            'working_copy_changed': False,
            'acceptance_verified': False,
        })
        self.assertEqual(reads, [(PROVENANCE, 'r'), (SOURCE, 'rb'), (WORKING, 'rb')])

    def test_source_and_working_copy_hashes_compare_raw_bytes_independently(self):
        original = b'# Schema\r\n'
        changed = b'# Schema\n'
        provenance = json.dumps({
            'source': SOURCE,
            'source_sha256': hashlib.sha256(original).hexdigest(),
        }).encode('utf-8')
        for source_changed in (False, True):
            for working_changed in (False, True):
                with self.subTest(source_changed=source_changed, working_changed=working_changed):
                    code, result, reads = self.invoke({
                        PROVENANCE: provenance,
                        SOURCE: changed if source_changed else original,
                        WORKING: changed if working_changed else original,
                    })
                    self.assertEqual(code, 0)
                    self.assertEqual(result, {
                        'source': SOURCE,
                        'source_changed': source_changed,
                        'working_copy_changed': working_changed,
                        'acceptance_verified': False,
                    })
                    self.assertEqual(reads, [(PROVENANCE, 'r'), (SOURCE, 'rb'), (WORKING, 'rb')])


if __name__ == '__main__':
    unittest.main()
