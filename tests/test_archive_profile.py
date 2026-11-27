import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import unittest

from repo_temp import workspace


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('archive_profile_test', ROOT / 'scripts/archive_profile.py')
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)
tools_spec = importlib.util.spec_from_file_location('project_tools_profile_test', ROOT / 'scripts/project_tools.py')
tools = importlib.util.module_from_spec(tools_spec)
tools_spec.loader.exec_module(tools)


class JsonEqualityTests(unittest.TestCase):
    """Exercise the public validator without filesystem fixtures or mock results."""

    def test_const_and_enum_distinguish_booleans_recursively(self):
        for actual, expected in [(True, 1), (False, 0), ([True], [1]),
                                 ({'a': [False]}, {'a': [0]})]:
            for schema in ({'const': expected}, {'enum': [expected]}):
                with self.subTest(actual=actual, schema=schema):
                    with self.assertRaises(ValueError):
                        profile.validate_schema(actual, schema)

    def test_const_and_enum_keep_json_numeric_and_structural_equality(self):
        for actual, expected in [(1, 1.0), (-0.0, 0), (True, True),
                                 (None, None), ('1', '1'),
                                 ({'b': [1], 'a': None}, {'a': None, 'b': [1.0]})]:
            for schema in ({'const': expected}, {'enum': [expected]}):
                with self.subTest(actual=actual, schema=schema):
                    profile.validate_schema(actual, schema)

    def test_unique_items_rejects_equivalent_numbers_and_nested_objects(self):
        for values in [[1, 1.0], [0, -0.0], [[1], [1.0]],
                       [{'a': 1, 'b': [False]}, {'b': [False], 'a': 1.0}]]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                profile.validate_schema(values, {'uniqueItems': True})

    def test_unique_items_preserves_boolean_types_and_array_order(self):
        for values in [[True, 1], [False, 0], [[True], [1]],
                       [[1, 2], [2, 1]], [None, False, 0, '0']]:
            with self.subTest(values=values):
                profile.validate_schema(values, {'uniqueItems': True})


class CanonicalRecordTests(unittest.TestCase):
    """Check real archive metadata with no filesystem mutations or fixtures."""

    def setUp(self):
        self.index = json.loads((ROOT / 'solutions/index.json').read_text(encoding='utf-8'))

    def test_noncanonical_paths_are_rejected(self):
        entry = self.index['entries'][0]
        path = entry['code']
        for changed_path in (path.replace('solutions/', 'solutions/./', 1),
                             path.replace('solutions/', 'solutions//', 1),
                             path + '/', path.replace('/', '\\')):
            changed = copy.deepcopy(entry)
            changed['code'] = changed_path
            with self.subTest(path=changed_path), self.assertRaises(ValueError):
                profile.validate_entry(changed)

    def test_digests_reject_trailing_newlines(self):
        for field in ('code_sha256', 'evidence_sha256'):
            changed = copy.deepcopy(self.index['entries'][0])
            changed['evidence_basis'] = 'artifact-sha256'
            changed[field] += '\n'
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'invalid archive digest'):
                profile.validate_entry(changed)

    def test_current_archive_paths_and_digests_remain_valid(self):
        profile.validate_index(self.index)
        for entry in self.index['entries']:
            profile.validate_entry(entry)
            categories, name, revision = profile.archive_file_identity(entry['code'])
            self.assertEqual((categories, name, revision),
                             (tuple(entry['categories']), entry['name'], entry['revision']))


class ArchiveProfileTests(unittest.TestCase):
    def setUp(self):
        self.root = workspace(self)
        self.index = json.loads((ROOT / 'solutions/index.json').read_text(encoding='utf-8'))

    def test_real_archive_profile_paths_hashes_and_function_docs(self):
        self.assertEqual(profile.validate_index(self.index), self.index)
        filenames = []
        metadata = tools.archive_metadata_files(ROOT)
        relationships = self.index.get('relationships') if self.index['format'] == 'boosterai-index-v3' else None
        self.assertEqual(profile.build_index(tools.current_archive_records(ROOT), relationships), self.index)
        expected_revisions = {('find-named-directory', 1), ('find-named-directory', 2),
                              ('owned-unittest-temp', 1)}
        observed_revisions = set()
        for record in metadata:
            entry = json.loads(record.read_text(encoding='utf-8'))
            profile.validate_entry(entry)
            code = ROOT / entry['code']
            self.assertEqual(hashlib.sha256(code.read_bytes()).hexdigest(), entry['code_sha256'])
            content = code.read_text(encoding='utf-8')
            self.assertIn(code.suffix, profile.LANGUAGES)
            if code.suffix == '.py':
                self.assertTrue(tools.python_docs_complete(content), str(code))
            elif code.suffix == '.js':
                self.assert_js_declarations_documented(content, code)
            self.assertFalse(any(key.startswith('legacy_') for key in entry))
            self.assertIn(entry['evidence_basis'], ('sanitized-summary-sha256', 'artifact-sha256'))
            if entry['evidence_basis'] == 'sanitized-summary-sha256':
                self.assertEqual(entry['evidence_sha256'], hashlib.sha256(entry['test_summary'].encode('utf-8')).hexdigest())
            if 'revisions' in record.parts:
                filenames.append(code.name)
                observed_revisions.add((entry['name'], entry['revision']))
                self.assertRegex(code.name, r'^[a-z0-9-]+-[0-9a-f]{8}' + re.escape(code.suffix) + r'$')
        self.assertTrue(expected_revisions <= observed_revisions)
        self.assertEqual(len(filenames), len(set(filenames)))
        schema = json.loads((ROOT / 'solutions/archive.schema.json').read_text(encoding='utf-8'))
        known_fields = schema['$defs']['entry']['properties']
        for entry in self.index['entries']:
            revision_metadata = (ROOT / entry['code']).parent / 'metadata.json'
            historical = json.loads(revision_metadata.read_text(encoding='utf-8'))
            self.assertEqual({key: historical.get(key) for key in known_fields},
                             {key: entry.get(key) for key in known_fields})
        previous = list((ROOT / 'solutions').rglob('previous.json'))
        self.assertGreaterEqual(len(previous), 1)
        for record in previous:
            entry = json.loads(record.read_text(encoding='utf-8'))
            profile.validate_entry(entry)
            self.assertFalse(any(key.startswith('legacy_') for key in entry))
            self.assertIn(entry['evidence_basis'], ('sanitized-summary-sha256', 'artifact-sha256'))
            if entry['evidence_basis'] == 'sanitized-summary-sha256':
                self.assertEqual(entry['evidence_sha256'], hashlib.sha256(entry['test_summary'].encode('utf-8')).hexdigest())

    def assert_js_declarations_documented(self, content, code):
        """Check the archive's named JS declarations, not the entire JS grammar."""
        declarations = list(re.finditer(r'(?m)^[ \t]*(?:async[ \t]+)?function[ \t]+[A-Za-z_$][\w$]*[ \t]*\(', content))
        self.assertTrue(declarations, str(code))
        for declaration in declarations:
            preceding = content[:declaration.start()].rstrip()
            start = preceding.rfind('/**')
            self.assertGreaterEqual(start, 0, str(code))
            comment = preceding[start:]
            self.assertRegex(comment, r'(?s)^/\*\*(?:(?!\*/).)*\*/$')
            self.assertGreaterEqual(len(re.findall(r'\w+', comment)), 12, str(code))

    def test_schema_and_semantics_reject_invalid_records(self):
        for field, value in [('code', 'solutions/../../outside.py'), ('revision', True),
                             ('keywords', []), ('code_sha256', 'bad'), ('name', 'bad/name')]:
            changed = copy.deepcopy(self.index)
            changed['entries'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                profile.validate_index(changed)
        with self.assertRaisesRegex(ValueError, 'unsupported schema keyword'):
            profile.validate_schema({}, {'unknownConstraint': True})

    def test_sanitized_summary_hash_mismatch_is_rejected(self):
        sanitized = next(i for i, entry in enumerate(self.index['entries'])
                         if entry.get('evidence_basis') == 'sanitized-summary-sha256')
        for field, value in [('test_summary', 'A changed summary that does not match the recorded digest.'),
                             ('evidence_sha256', '0' * 64)]:
            changed = copy.deepcopy(self.index)
            changed['entries'][sanitized][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'summary hash mismatch'):
                profile.validate_index(changed)

    def test_deterministic_index_and_nested_category(self):
        entries = list(reversed(self.index['entries']))
        relationships = self.index.get('relationships') if self.index['format'] == 'boosterai-index-v3' else None
        rebuilt = profile.build_index(entries, relationships)
        self.assertEqual(rebuilt, self.index)
        self.assertIn(['filesystem', 'search'], rebuilt['categories'])
        self.assertEqual(json.dumps(rebuilt, sort_keys=True), json.dumps(profile.build_index(entries, relationships), sort_keys=True))


if __name__ == '__main__':
    unittest.main(verbosity=2)
