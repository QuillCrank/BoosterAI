"""Check legacy and derived archive relationship fields without disk fixtures."""

from contextlib import nullcontext, redirect_stdout
from hashlib import sha256
from io import BytesIO, StringIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import archive_profile
import archive_sync
import project_tools

WORK = Path('virtual-archive-work')
PROJECT = Path('virtual-archive-project')
COMMIT = '0' * 40
CODE = {'example': b'# ordinary example\n', 'reference': b'# ordinary reference\n'}
LEGACY_RELATED = {'custom': ['legacy annotation', 2]}
EXTRA = {'note': 'ordinary extension'}


def ordinary_entry(name, *, legacy):
    entry = {
        'name': name, 'title': name.title(), 'code': f'solutions/{name}/revisions/0001/example.py',
        'code_sha256': sha256(CODE[name]).hexdigest(), 'evidence_sha256': '0' * 64,
        'outcome': 'success', 'test_summary': 'Checked', 'categories': ['testing'],
        'tags': [name], 'additional_metadata': EXTRA,
    }
    if legacy:
        entry['related_files'] = LEGACY_RELATED
    return entry


def v3_entry(name, *, legacy):
    entry = ordinary_entry(name, legacy=legacy)
    filename = archive_profile.code_filename(name, 1, entry['code_sha256'], '.py')
    entry.update({
        '@context': 'https://w3id.org/codemeta/4.0', '@type': 'SoftwareSourceCode',
        'format': 'boosterai-source-v1', 'description': 'An ordinary archive record',
        'programmingLanguage': archive_profile.LANGUAGES['.py'], 'runtimePlatform': 'Python',
        'codeRepository': archive_sync.REPOSITORY, 'codeSampleType': 'full',
        'license': 'MIT', 'revision': 1, 'categories': ['algorithms', 'reuse'],
        'categoryAliases': [], 'keywords': entry['tags'],
        'code': f'solutions/algorithms/reuse/{name}/revisions/0001/{filename}',
        'provenance': 'Controlled example', 'publication': 'Controlled metadata',
        'savings_measured': False,
    })
    return entry


def archive_with_graph(*, legacy):
    example = v3_entry('example', legacy=legacy)
    reference = v3_entry('reference', legacy=False)
    relationship = {'file': example['code'], 'relatedFile': reference['code'],
                    'strength': 0.65, 'derivedFrom': True}
    manifest = archive_profile.build_index([example, reference], relationships=[relationship])
    return json.dumps(manifest).encode('utf-8'), example, reference


def run_handler(module, action):
    stdout = StringIO()
    with (
        patch.object(sys, 'argv', [module.__name__, action, '--root', str(PROJECT), '--query', 'example']),
        redirect_stdout(stdout),
    ):
        exit_code = module.main()
    if exit_code != 0:
        raise AssertionError('search handler failed')
    return json.loads(stdout.getvalue())


def root_results(raw_index, *, include_related=True):
    index = Mock(spec_set=Path)
    index.exists.return_value = True
    with BytesIO(raw_index) as stream:
        index.open.return_value = nullcontext(stream)
        with (
            patch.object(project_tools, 'repository_root', return_value=PROJECT),
            patch.object(project_tools, 'safe', return_value=index),
        ):
            if include_related:
                return run_handler(project_tools, 'search-archive')
            return project_tools.archive_search(PROJECT, query='example', include_related=False)


def snapshot_results(raw_index):
    snapshot = WORK / 'snapshots' / COMMIT
    files = {
        WORK / 'active.json': json.dumps({'commit': COMMIT}).encode('utf-8'),
        snapshot / 'snapshot.json': json.dumps({
            'commit': COMMIT, 'repository': archive_sync.REPOSITORY,
            'index_sha256': sha256(raw_index).hexdigest(),
        }).encode('utf-8'),
        snapshot / 'index.json': raw_index,
    }
    for payload in CODE.values():
        files[snapshot / 'code' / (sha256(payload).hexdigest() + '.txt')] = payload
    open_schema = Path.open

    def open_file(path, mode='r', *args, **kwargs):
        if path == archive_profile.SCHEMA_PATH and mode == 'r':
            return open_schema(path, mode, *args, **kwargs)
        if mode != 'rb':
            raise AssertionError('unexpected snapshot open mode')
        return BytesIO(files[path])

    with (
        patch.object(archive_sync, 'storage', return_value=WORK),
        patch.object(archive_sync, 'safe', side_effect=lambda base, name: base / name),
        patch.object(Path, 'open', autospec=True, side_effect=open_file),
    ):
        return run_handler(archive_sync, 'search')


class ArchiveSearchRelatedProjectionTest(unittest.TestCase):
    def test_v1_handlers_preserve_legacy_value_with_empty_derived_links(self):
        manifest = {'format': 'boosterai-index-v1', 'entries': [ordinary_entry('example', legacy=True)]}
        raw_index = json.dumps(manifest).encode('utf-8')
        for read in (root_results, snapshot_results):
            with self.subTest(reader=read.__name__):
                result = read(raw_index)
                self.assertEqual(result['results'][0]['related_files'], LEGACY_RELATED)
                self.assertEqual(result['results'][0]['additional_metadata'], EXTRA)
                self.assertEqual(result['related_files_by_record'], {'example': []})

    def test_v3_handlers_validate_the_index_once(self):
        raw_index, _, _ = archive_with_graph(legacy=False)
        manifest = json.loads(raw_index)
        for read in (root_results, snapshot_results):
            with (
                self.subTest(reader=read.__name__),
                patch.object(archive_profile, 'validate_index',
                             wraps=archive_profile.validate_index) as validate,
            ):
                result = read(raw_index)
                self.assertEqual([entry['name'] for entry in result['results']], ['example'])
                validate.assert_called_once_with(manifest)

    def test_v3_graph_remains_separate_from_legacy_value(self):
        for legacy in (True, False):
            raw_index, example, reference = archive_with_graph(legacy=legacy)
            link = {'file': example['code'], 'relatedFile': reference['code'],
                    'strength': 0.65, 'relation': 'derived-from'}
            snapshot_link = {
                **link, 'local_inert_source': f"snapshots/{COMMIT}/code/{reference['code_sha256']}.txt",
                'untrusted': True,
            }
            for read, expected in ((root_results, link), (snapshot_results, snapshot_link)):
                with self.subTest(reader=read.__name__, legacy=legacy):
                    result = read(raw_index)
                    self.assertEqual(result['related_files_by_record'], {'example': [expected]})
                    entry = result['results'][0]
                    self.assertEqual(entry['related_files'], LEGACY_RELATED if legacy else [expected])
                    self.assertEqual(entry['additional_metadata'], EXTRA)

    def test_root_opt_out_preserves_legacy_field_and_skips_graph_lookup(self):
        for legacy in (True, False):
            raw_index, _, _ = archive_with_graph(legacy=legacy)
            with (
                self.subTest(legacy=legacy),
                patch.object(archive_profile, '_related_links_from_validated_index') as lookup,
            ):
                result = root_results(raw_index, include_related=False)
                lookup.assert_not_called()
                self.assertEqual(result['related_files_by_record'], {})
                entry = result['results'][0]
                self.assertEqual(entry['additional_metadata'], EXTRA)
                if legacy:
                    self.assertEqual(entry['related_files'], LEGACY_RELATED)
                else:
                    self.assertNotIn('related_files', entry)


if __name__ == '__main__':
    unittest.main()
