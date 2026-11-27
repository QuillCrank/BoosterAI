"""Read-only integrity checks against the actual checked-in archive, not fixtures."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('repository_archive_tools', ROOT / 'scripts/project_tools.py')
tools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tools)


class RepositoryArchiveIntegrityTests(unittest.TestCase):
    """Check bytes and discoverability without executing or authenticating examples."""

    def setUp(self):
        self.index = json.loads((ROOT / 'solutions/index.json').read_text(encoding='utf-8'))
        tools.profile.validate_index(self.index)
        self.assertTrue(self.index['entries'], 'bundled examples must not silently disappear')
        self.assertTrue(self.index.get('relationships'), 'bundled weighted relationship must be exercised')

    def test_index_matches_real_current_records_and_revision_bytes(self):
        records, files = tools.archive_records_and_files(ROOT)
        self.assertEqual(sorted(records, key=lambda entry: entry['name']), self.index['entries'])
        for entry in records:
            self.assertIn(entry['code'], files)

    def test_relationship_registry_matches_index_and_real_revision_endpoints(self):
        _, files = tools.archive_records_and_files(ROOT)
        registry = tools.read_archive_relationships(ROOT)
        relations = [] if registry is None else registry['relationships']
        self.assertEqual(relations, self.index.get('relationships', []))
        for relation in relations:
            self.assertIn(relation['file'], files)
            self.assertIn(relation['relatedFile'], files)

    def test_current_records_are_searchable_with_bidirectional_weights(self):
        result = tools.archive_search(ROOT)
        self.assertEqual([entry['name'] for entry in result['results']],
                         [entry['name'] for entry in self.index['entries']])
        for entry in self.index['entries']:
            selected = tools.archive_search(ROOT, query=entry['name'],
                                           category='/'.join(entry['categories']), tag=entry['tags'][0])
            self.assertIn(entry['name'], [record['name'] for record in selected['results']])
            expected = tools.profile.related_links(self.index, entry)
            self.assertEqual(selected['related_files_by_record'][entry['name']], expected)
        for relation in self.index.get('relationships', []):
            forward = tools.profile.related_links(self.index, relation['file'])
            reverse = tools.profile.related_links(self.index, relation['relatedFile'])
            self.assertIn({'file': relation['file'], 'relatedFile': relation['relatedFile'],
                           'strength': relation['strength'],
                           'relation': 'derived-from' if relation['derivedFrom'] else 'related'}, forward)
            self.assertIn({'file': relation['relatedFile'], 'relatedFile': relation['file'],
                           'strength': relation['strength'],
                           'relation': 'source-for' if relation['derivedFrom'] else 'related'}, reverse)


if __name__ == '__main__':
    unittest.main()

