"""Exercise archived-file relationship writes and searches in isolated projects."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from repo_temp import workspace


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('archive_link_tools', ROOT / 'scripts/project_tools.py')
tools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tools)


class ArchiveLinkTests(unittest.TestCase):
    def setUp(self):
        self.project = workspace(self)
        self.candidates = self.project / 'candidates'
        self.candidates.mkdir()

    def stage(self, name, extension='.py', relationships=None):
        """Archive a documented, deliberately local fixture and return its record."""
        if extension == '.py':
            text = 'def fixture(value):\n    """Return a fixture value with a unique label and no side effects."""\n    return value, ' + repr(name) + '\n'
        else:
            text = '/** Return a supplied fixture value without changing it or any files. */\nfunction fixture(value) { return [value, ' + json.dumps(name) + ']; }\n'
        source = self.candidates / (name + extension)
        source.write_text(text, encoding='utf-8')
        proof = self.candidates / (name + '.proof')
        proof.write_text('fixture assertion for ' + name, encoding='utf-8')
        review = {
            'name': name, 'title': 'Fixture ' + name, 'license': 'MIT',
            'code_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'evidence': proof.relative_to(self.project).as_posix(),
            'evidence_sha256': hashlib.sha256(proof.read_bytes()).hexdigest(),
            'outcome': 'success', 'test_summary': 'Fixture assertion completed',
            'categories': ['examples', 'links'], 'tags': ['fixture'],
            'function_docs_reviewed': True,
        }
        for key in ('rights_confirmed', 'privacy_reviewed', 'generalized',
                    'security_reviewed', 'real_test_verified'):
            review[key] = True
        if relationships is not None:
            review['relationships'] = relationships
        manifest = self.candidates / (name + '.json')
        manifest.write_text(json.dumps(review), encoding='utf-8')
        result = tools.archive_example(self.project, source.relative_to(self.project).as_posix(),
                                       manifest.relative_to(self.project).as_posix())
        self.assertIn(result['status'], ('READY_FOR_AUTHORIZED_PUBLICATION', 'UPDATED_EXISTING'))
        return next(e for e in tools.archive_search(self.project, include_related=False)['results']
                    if e['name'] == name)

    def cli(self, *args):
        """Run the real project-tools entry point against this owned test project."""
        return subprocess.run([sys.executable, '-I', '-S', '-B', str(ROOT / 'scripts/project_tools.py'),
                               *args, '--root', str(self.project)], capture_output=True, text=True)

    def test_new_language_archives_multiple_links_and_searches_in_both_directions(self):
        python = self.stage('python-source')
        other = self.stage('other-source')
        javascript = self.stage('javascript-target', '.js', [
            {'relatedFile': python['code'], 'strength': .7, 'derivedFrom': True},
            {'relatedFile': other['code'], 'strength': .4, 'derivedFrom': False},
        ])
        self.assertNotEqual(python['code'], javascript['code'])
        self.assertEqual((self.project / javascript['code']).suffix, '.js')
        result = tools.archive_search(self.project, 'javascript')['results'][0]['related_files']
        self.assertEqual({(x['relatedFile'], x['strength'], x['relation']) for x in result},
                         {(python['code'], .7, 'derived-from'), (other['code'], .4, 'related')})
        reverse = tools.archive_search(self.project, 'python')['results'][0]['related_files']
        self.assertEqual(reverse[0]['relatedFile'], javascript['code'])
        self.assertEqual(reverse[0]['relation'], 'source-for')
        before = (self.project / 'solutions/index.json').read_bytes()
        tools.rebuild_index(self.project)
        self.assertEqual((self.project / 'solutions/index.json').read_bytes(), before)

    def test_cli_links_existing_files_without_changing_revision_metadata(self):
        first = self.stage('first-source')
        second = self.stage('second-source')
        third = self.stage('third-source')
        immutable = (self.project / second['code']).parent / 'metadata.json'
        before = immutable.read_bytes()
        args = ('link-archive-files', '--file', second['code'], '--related-file', first['code'],
                '--strength', '.6', '--derived-from')
        created = self.cli(*args)
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        self.assertEqual(json.loads(created.stdout)['status'], 'LINKED')
        again = self.cli(*args)
        self.assertEqual(json.loads(again.stdout)['status'], 'UNCHANGED')
        self.assertEqual(immutable.read_bytes(), before)
        self.assertEqual(tools.link_archive_files(self.project, third['code'], first['code'], 1)['status'], 'LINKED')
        self.assertEqual(tools.link_archive_files(self.project, third['code'], second['code'], 0)['status'], 'LINKED')
        links = tools.archive_search(self.project, 'first')['results'][0]['related_files']
        self.assertEqual({link['strength'] for link in links}, {.6, 1})
        with self.assertRaisesRegex(ValueError, 'different metadata'):
            tools.link_archive_files(self.project, second['code'], first['code'], .5, True)
        for weight in (True, -0.1, 1.1, float('nan'), float('inf')):
            with self.subTest(weight=weight), self.assertRaises(ValueError):
                tools.link_archive_files(self.project, first['code'], second['code'], weight)
        with self.assertRaises(ValueError):
            tools.link_archive_files(self.project, first['code'], first['code'], .5)

    def test_unverified_relationship_targets_are_rejected_before_archive_changes(self):
        first = self.stage('existing-source')
        index = self.project / 'solutions/index.json'
        before = index.read_bytes()
        missing = first['code'].replace('0001/', '0002/')
        with self.assertRaisesRegex(ValueError, 'endpoint missing'):
            self.stage('new-target', '.js', [
                {'relatedFile': missing, 'strength': .5, 'derivedFrom': True}
            ])
        self.assertEqual(index.read_bytes(), before)
        self.assertFalse((self.project / 'solutions/examples/links/new-target').exists())
        before_code = (self.project / first['code']).read_bytes()
        (self.project / first['code']).write_bytes(b'changed fixture')
        with self.assertRaisesRegex(ValueError, 'code missing or changed'):
            tools.link_archive_files(self.project, first['code'], missing, .5)
        (self.project / first['code']).write_bytes(before_code)
        self.assertEqual(index.read_bytes(), before)

    def test_legacy_custom_fields_are_preserved_through_link_and_search(self):
        first = self.stage('first-source')
        second = self.stage('second-source')
        current = self.project / 'solutions/examples/links/first-source/metadata.json'
        record = json.loads(current.read_text(encoding='utf-8'))
        record['relationships'] = ['existing custom value']
        record['related_files'] = {'owner': 'existing custom value'}
        current.write_text(json.dumps(record), encoding='utf-8')
        rebuilt = tools.rebuild_index(self.project)
        self.assertEqual(rebuilt['format'], 'boosterai-index-v2')
        before = current.read_bytes()
        tools.link_archive_files(self.project, second['code'], first['code'], .3, True)
        result = tools.archive_search(self.project, 'first')
        self.assertEqual(result['results'][0]['relationships'], ['existing custom value'])
        self.assertEqual(result['results'][0]['related_files'], {'owner': 'existing custom value'})
        self.assertEqual(result['related_files_by_record']['first-source'][0]['strength'], .3)
        self.assertEqual(current.read_bytes(), before)

    def test_rebuild_refuses_to_drop_previously_indexed_relationship(self):
        first = self.stage('first-source')
        second = self.stage('second-source')
        tools.link_archive_files(self.project, second['code'], first['code'], .6, True)
        index = self.project / 'solutions/index.json'
        registry = self.project / 'solutions/relationships.json'
        before = index.read_bytes()
        graph = json.loads(registry.read_text(encoding='utf-8'))
        graph['relationships'] = []
        registry.write_text(json.dumps(graph), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'refusing to drop or change'):
            tools.rebuild_index(self.project)
        self.assertEqual(index.read_bytes(), before)

    def test_partial_archive_write_reports_reconciliation_required(self):
        self.stage('existing-source')
        index = self.project / 'solutions/index.json'
        before = index.read_bytes()
        with patch.object(tools, 'rebuild_index', side_effect=OSError('fixture index failure')):
            with self.assertRaisesRegex(ValueError, 'may have changed; reconcile before retrying'):
                self.stage('new-target')
        self.assertTrue((self.project / 'solutions/examples/links/new-target/metadata.json').exists())
        self.assertEqual(index.read_bytes(), before)

    def test_rebuild_rejects_revision_regression_and_custom_field_loss(self):
        first = self.stage('versioned-source')
        self.stage('versioned-source')
        index = self.project / 'solutions/index.json'
        current = self.project / 'solutions/examples/links/versioned-source/metadata.json'
        original = current.read_bytes()
        prior = (self.project / first['code']).parent / 'metadata.json'
        before = index.read_bytes()
        current.write_bytes(prior.read_bytes())
        with self.assertRaisesRegex(ValueError, 'regress an archive revision'):
            tools.rebuild_index(self.project)
        self.assertEqual(index.read_bytes(), before)
        current.write_bytes(original)
        metadata = json.loads(original)
        metadata['owner_custom'] = {'keep': True}
        current.write_text(json.dumps(metadata), encoding='utf-8')
        tools.rebuild_index(self.project)
        with_custom = index.read_bytes()
        current.write_bytes(original)
        with self.assertRaisesRegex(ValueError, 'drop or change existing archive metadata'):
            tools.rebuild_index(self.project)
        self.assertEqual(index.read_bytes(), with_custom)

    def test_oversized_index_is_rejected_before_link_registry_changes(self):
        first = self.stage('first-source')
        second = self.stage('second-source')
        index = self.project / 'solutions/index.json'
        before = index.read_bytes()
        with patch.object(tools.profile, 'MAX_INDEX_BYTES', 1):
            with self.assertRaisesRegex(ValueError, 'sync size limit'):
                tools.link_archive_files(self.project, second['code'], first['code'], .5)
            with self.assertRaisesRegex(ValueError, 'sync size limit'):
                self.stage('third-source')
            with self.assertRaisesRegex(ValueError, 'sync size limit'):
                tools.rebuild_index(self.project)
        self.assertFalse((self.project / 'solutions/relationships.json').exists())
        self.assertFalse((self.project / 'solutions/examples/links/third-source').exists())
        self.assertEqual(index.read_bytes(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
