"""Independent checks of the weighted, cross-language archive profile."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('archive_relationship_profile', ROOT / 'scripts/archive_profile.py')
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


def make_entry(template, name, revision, extension):
    """Create profile-valid, in-memory metadata for one invented archive record."""
    entry = copy.deepcopy(template)
    digest = hashlib.sha256((name + str(revision) + extension).encode('utf-8')).hexdigest()
    entry.update({
        'name': name,
        'revision': revision,
        'categories': ['algorithms', 'reuse'],
        'programmingLanguage': profile.LANGUAGES[extension],
        'code_sha256': digest,
        'code': '/'.join(['solutions', 'algorithms', 'reuse', name, 'revisions',
                          str(revision).zfill(4), profile.code_filename(name, revision, digest, extension)]),
    })
    return entry


def historical_path(entry, revision, extension):
    """Construct a syntactically valid earlier path, without claiming it exists."""
    digest = hashlib.sha256((entry['name'] + str(revision) + extension).encode('utf-8')).hexdigest()
    return '/'.join(['solutions', *entry['categories'], entry['name'], 'revisions',
                     str(revision).zfill(4), profile.code_filename(entry['name'], revision, digest, extension)])


def relationship(file, related, strength, derived):
    """Create one directional provenance assertion and bidirectional association."""
    return {'file': file, 'relatedFile': related, 'strength': strength, 'derivedFrom': derived}


class WeightedRelationshipTests(unittest.TestCase):
    def setUp(self):
        template = json.loads((ROOT / 'solutions/index.json').read_text(encoding='utf-8'))['entries'][0]
        self.python = make_entry(template, 'python-solution', 2, '.py')
        self.csharp = make_entry(template, 'csharp-solution', 2, '.cs')
        self.javascript = make_entry(template, 'javascript-solution', 1, '.js')
        self.old_python = historical_path(self.python, 1, '.py')
        self.old_csharp = historical_path(self.csharp, 1, '.cs')
        self.relationships = [
            relationship(self.csharp['code'], self.python['code'], .7, True),
            relationship(self.csharp['code'], self.javascript['code'], 0, False),
            relationship(self.old_csharp, self.python['code'], .4, True),
            relationship(self.javascript['code'], self.old_python, 1, True)
        ]
        self.entries = [self.python, self.csharp, self.javascript]

    def test_cross_language_many_to_many_and_revision_search_are_bidirectional(self):
        index = profile.build_index(self.entries, self.relationships)
        self.assertEqual(index['format'], 'boosterai-index-v3')
        self.assertEqual(profile.build_index(list(reversed(self.entries)),
                                             list(reversed(self.relationships))), index)
        links = profile.related_links(index, self.python)
        self.assertEqual(len(links), 3)
        self.assertEqual({link['strength'] for link in links}, {.4, .7, 1})
        self.assertEqual({link['relatedFile'] for link in links},
                         {self.old_csharp, self.csharp['code'], self.javascript['code']})
        self.assertTrue(all(link['relation'] == 'source-for' for link in links))
        forward = profile.related_links(index, self.csharp['code'])
        self.assertEqual({(link['relatedFile'], link['strength'], link['relation']) for link in forward},
                         {(self.python['code'], .7, 'derived-from'),
                          (self.javascript['code'], 0, 'related')})
        reverse = profile.related_links(index, self.javascript['code'])
        self.assertIn({'file': self.javascript['code'], 'relatedFile': self.csharp['code'],
                       'strength': 0, 'relation': 'related'}, reverse)
        self.assertIn({'file': self.javascript['code'], 'relatedFile': self.old_python,
                       'strength': 1, 'relation': 'derived-from'}, reverse)
        self.assertEqual(links, sorted(links, key=lambda link: (link['file'], link['relatedFile'], link['relation'])))

    def test_existing_v1_and_v2_indices_remain_usable(self):
        original = json.loads((ROOT / 'solutions/index.json').read_text(encoding='utf-8'))
        links = original.get('relationships') if original['format'] == 'boosterai-index-v3' else None
        self.assertEqual(profile.build_index(original['entries'], links), original)
        old_entries = copy.deepcopy(original['entries'])
        old_entries[0]['relationships'] = ['custom']
        old_entries[0]['related_files'] = {'custom': ['opaque', 2]}
        unchanged = copy.deepcopy(old_entries)
        old_index = profile.build_index(old_entries)
        self.assertEqual(old_index['format'], 'boosterai-index-v2')
        self.assertNotIn('relationships', old_index)
        self.assertEqual(profile.validate_index(old_index), old_index)
        self.assertEqual(old_entries, unchanged)
        self.assertEqual(old_index['entries'][0]['relationships'], ['custom'])
        self.assertEqual(old_index['entries'][0]['related_files'], {'custom': ['opaque', 2]})
        self.assertEqual(profile.build_index(list(reversed(old_entries))), old_index)
        for entry in old_entries:
            self.assertEqual(profile.related_links(old_index, entry), [])
        self.assertEqual(profile.related_links({'format': 'boosterai-index-v1', 'entries': []}, 'unused'), [])
        new_index = profile.build_index(old_entries, [])
        self.assertEqual(new_index['format'], 'boosterai-index-v3')
        self.assertEqual(new_index['entries'], old_index['entries'])
        self.assertEqual(profile.related_links(new_index, old_entries[0]), [])
        schema = json.loads((ROOT / 'solutions/archive.schema.json').read_text(encoding='utf-8'))
        profile.validate_schema(old_index, schema['$defs']['indexV2'], schema)
        with self.assertRaises(ValueError):
            profile.validate_schema(new_index, schema['$defs']['indexV2'], schema)

    def test_schema_distinguishes_v2_and_v3_graph_location(self):
        legacy = profile.build_index(self.entries)
        current = profile.build_index(self.entries, self.relationships)
        for changed in (
                {**legacy, 'relationships': []},
                {key: value for key, value in current.items() if key != 'relationships'},
                {**current, 'format': 'boosterai-index-v4'}):
            with self.subTest(index=changed.get('format')), self.assertRaises(ValueError):
                profile.validate_index(changed)
        with self.assertRaisesRegex(ValueError, 'unsupported schema keyword'):
            profile.validate_schema({}, {'oneOf': [{'type': 'object'}, {'unknownConstraint': True}]})

    def test_weights_reject_invalid_boolean_nonfinite_and_out_of_range_values(self):
        for bad in (True, False, -0.01, 1.01, '0.5', None, float('nan'),
                    float('inf'), -float('inf'), 10 ** 500):
            changed = copy.deepcopy(self.relationships)
            changed[0]['strength'] = bad
            with self.subTest(weight=str(bad)), self.assertRaises(ValueError):
                profile.build_index(self.entries, changed)
        for valid in (0, 1, 0.25):
            changed = copy.deepcopy(self.relationships)
            changed[0]['strength'] = valid
            with self.subTest(weight=valid):
                profile.build_index(self.entries, changed)

    def test_rejects_missing_target_forged_current_path_and_future_revision(self):
        missing = make_entry(self.python, 'missing-record', 1, '.py')['code']
        forged = self.python['code'].replace('.py', '.cs')
        future = historical_path(self.python, 3, '.py')
        for field in ('file', 'relatedFile'):
            for bad in (missing, forged, future, 'solutions/../../outside.py'):
                changed = copy.deepcopy(self.relationships)
                changed[0][field] = bad
                with self.subTest(field=field, path=bad), self.assertRaises(ValueError):
                    profile.build_index(self.entries, changed)

    def test_rejects_self_links_unsorted_links_and_duplicate_pairs(self):
        changed = copy.deepcopy(self.relationships)
        changed[0]['relatedFile'] = changed[0]['file']
        with self.assertRaises(ValueError):
            profile.build_index(self.entries, changed)
        changed = profile.build_index(self.entries, self.relationships)
        changed['relationships'].reverse()
        with self.assertRaisesRegex(ValueError, 'sorted and unique'):
            profile.validate_index(changed)
        changed = [*self.relationships, relationship(self.python['code'], self.csharp['code'], .2, False)]
        with self.assertRaisesRegex(ValueError, 'duplicate or conflicting'):
            profile.build_index(self.entries, changed)

    def test_profile_does_not_claim_historical_file_existence(self):
        # A syntactically valid historical path is metadata, not proof that the
        # immutable file exists or hashes correctly. The archive writer must check it.
        self.assertFalse((ROOT / self.old_python).exists())
        index = profile.build_index(self.entries, self.relationships)
        self.assertEqual(profile.related_links(index, self.old_python)[0]['relation'], 'source-for')


if __name__ == '__main__':
    unittest.main(verbosity=2)
