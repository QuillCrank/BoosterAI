"""Verify omitted and explicit-empty archive filters through both CLI handlers."""

from contextlib import nullcontext, redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import archive_sync
import project_tools

PROJECT = Path('virtual-archive-filter-root')
COMMIT = '0' * 40


def entry(name, categories, tags):
    return {
        'name': name, 'title': name, 'code': f'solutions/{name}/revisions/0001/example.py',
        'code_sha256': '0' * 64, 'evidence_sha256': '1' * 64, 'outcome': 'success',
        'test_summary': 'Checked', 'categories': categories, 'tags': tags,
    }


class ArchiveEmptyFilterParityTest(unittest.TestCase):
    def setUp(self):
        manifest = {
            'format': 'boosterai-index-v1',
            'entries': [
                entry('empty-path', [], ['python']),
                entry('typed-path', ['filesystem', 'search'], ['python', 'unittest']),
            ],
        }
        self.raw = json.dumps(manifest).encode('utf-8')
        self.manifest = archive_sync.validate_manifest(self.raw)

    def run_handler(self, module, action, flags):
        output = StringIO()
        with (
            patch.object(sys, 'argv', [module.__name__, action, '--root', str(PROJECT), *flags]),
            redirect_stdout(output),
        ):
            self.assertEqual(module.main(), 0)
        return json.loads(output.getvalue())

    def root_result(self, flags):
        index = Mock(spec_set=Path)
        index.exists.return_value = True
        with BytesIO(self.raw) as stream:
            index.open.return_value = nullcontext(stream)
            with (
                patch.object(project_tools, 'repository_root', return_value=PROJECT),
                patch.object(project_tools, 'safe', return_value=index),
            ):
                return self.run_handler(project_tools, 'search-archive', flags)

    def snapshot_result(self, flags):
        with (
            patch.object(archive_sync, 'storage', return_value=PROJECT),
            patch.object(archive_sync, 'safe', side_effect=lambda root, name: root / name),
            patch.object(archive_sync, '_read_metadata_json', return_value={'commit': COMMIT}),
            patch.object(archive_sync, 'verify_snapshot', return_value=self.manifest),
        ):
            return self.run_handler(archive_sync, 'search', flags)

    def test_legacy_v1_filter_parity(self):
        cases = (
            ([], ['empty-path', 'typed-path']),
            (['--tag', 'unittest'], ['typed-path']),
            (['--category', 'filesystem/search'], ['typed-path']),
            (['--category', 'filesystem/search', '--tag', 'python'], ['typed-path']),
            (['--category=', '--tag', 'python'], ['empty-path']),
            (['--category=', '--tag', 'unittest'], []),
            (['--category', 'filesystem/search', '--tag='], []),
            (['--category=', '--tag='], []),
        )
        for flags, names in cases:
            for read in (self.root_result, self.snapshot_result):
                with self.subTest(reader=read.__name__, flags=flags):
                    result = read(flags)
                    self.assertEqual([item['name'] for item in result['results']], names)
                    self.assertEqual(result['related_files_by_record'], {name: [] for name in names})


class ArchiveRepeatedFilterParityTest(unittest.TestCase):
    readers = (
        (project_tools, 'search-archive', 'archive_search'),
        (archive_sync, 'search', 'search'),
    )

    def test_conflicts_exit_before_search(self):
        pairs = (('first', 'second'), ('second', 'first'), ('', 'first'), ('first', ''))
        for module, command, method in self.readers:
            for flag in ('--query', '--category', '--tag'):
                for first, second in pairs:
                    args = [module.__name__, command, '--root', str(PROJECT), flag, first, flag, second]
                    stdout, stderr = StringIO(), StringIO()
                    with self.subTest(reader=module.__name__, flag=flag, values=(first, second)):
                        with (
                            patch.object(module, method, return_value={}) as search,
                            patch.object(sys, 'argv', args),
                            redirect_stdout(stdout),
                            redirect_stderr(stderr),
                            self.assertRaises(SystemExit) as status,
                        ):
                            module.main()
                        self.assertEqual(status.exception.code, 2)
                        self.assertEqual(stdout.getvalue(), '')
                        self.assertEqual(stderr.getvalue(), 'conflicting archive filter values\n')
                        search.assert_not_called()

    def test_invalid_roots_exit_before_resolution(self):
        repeated_roots = ['--root', str(PROJECT), f'--root={PROJECT}']
        cases = (
            repeated_roots,
            [*repeated_roots, '--root', '.'],
            ['--root', ''],
            ['--root='],
            ['--root', ' \t '],
        )
        for module, command, method in self.readers:
            resolver = 'repository_root' if module is project_tools else 'storage'
            for flags in cases:
                args = [module.__name__, command, *flags]
                stdout, stderr = StringIO(), StringIO()
                with self.subTest(reader=module.__name__, flags=flags):
                    with (
                        patch.object(module, resolver) as resolve,
                        patch.object(module, method, return_value={}) as search,
                        patch.object(sys, 'argv', args),
                        redirect_stdout(stdout),
                        redirect_stderr(stderr),
                        self.assertRaises(SystemExit) as status,
                    ):
                        module.main()
                    self.assertEqual(status.exception.code, 2)
                    self.assertEqual(stdout.getvalue(), '')
                    self.assertEqual(stderr.getvalue(), 'archive search requires exactly one --root\n')
                    resolve.assert_not_called()
                    search.assert_not_called()

    def test_omitted_and_repeated_identical_values_reach_search(self):
        filters = ['--query=one', '--category=two', '--tag=three']
        empty = ['--query=', '--category=', '--tag=']
        cases = (
            ([], ('', None, None)),
            (filters * 2, ('one', 'two', 'three')),
            (empty * 2, ('', '', '')),
        )
        for module, command, method in self.readers:
            for flags, expected in cases:
                args = [module.__name__, command, '--root', str(PROJECT), *flags]
                with (
                    self.subTest(reader=module.__name__, flags=flags),
                    patch.object(module, method, return_value={}) as search,
                    patch.object(sys, 'argv', args),
                    redirect_stdout(StringIO()),
                ):
                    self.assertEqual(module.main(), 0)
                    search.assert_called_once_with(str(PROJECT), *expected)


if __name__ == '__main__':
    unittest.main()
