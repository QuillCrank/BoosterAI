import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from repo_temp import workspace


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/find_directory.py'
spec = importlib.util.spec_from_file_location('find_directory', SCRIPT)
finder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finder)


class DirectoryFinderTests(unittest.TestCase):
    def setUp(self):
        self.root = workspace(self)

    def cli(self, *args):
        values=list(map(str,args))
        if '--name' not in values:values.extend(['--name','SampleFolder'])
        return subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPT), *values],
                              cwd=ROOT, capture_output=True, text=True)

    def test_cli_finds_exact_names_case_insensitively_and_ignores_files(self):
        first = self.root / 'one/SampleFolder'
        second = self.root / 'two/sAmPlEfOlDeR'
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        (self.root / 'SampleFolder').write_text('file, not folder', encoding='utf-8')
        (self.root / 'SampleFolder-old').mkdir()
        result = self.cli('--root', self.root, '--root', first)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertTrue(data['complete'])
        self.assertEqual({Path(p) for p in data['matches']}, {first, second})
        self.assertEqual(data['error_count'], 0)

    def test_no_match_and_missing_root_are_distinct(self):
        absent = self.cli('--root', self.root, '--name', 'SampleFolder')
        self.assertEqual(absent.returncode, 1, absent.stderr)
        self.assertTrue(json.loads(absent.stdout)['complete'])
        self.assertEqual(json.loads(absent.stdout)['matches'], [])
        missing = self.cli('--root', self.root / 'missing')
        self.assertEqual(missing.returncode, 2, missing.stderr)
        data = json.loads(missing.stdout)
        self.assertFalse(data['complete'])
        self.assertEqual(data['error_count'], 1)

    def test_invalid_directory_name_is_rejected(self):
        result = self.cli('--root', self.root, '--name', '../SampleFolder')
        self.assertEqual(result.returncode, 2)
        self.assertIn('single nonempty directory name', json.loads(result.stderr)['error'])

    def test_name_must_be_explicit(self):
        result = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPT), '--root', str(self.root)],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--name', result.stderr)

    def test_inaccessible_child_is_reported_as_incomplete(self):
        blocked = self.root / 'inaccessible'
        blocked.mkdir()
        original = finder.os.scandir

        def selective(path):
            if Path(path) == blocked:
                raise PermissionError('fixture permission failure')
            return original(path)

        with patch.object(finder.os, 'scandir', side_effect=selective):
            result = finder.find_named_directories([self.root], 'SampleFolder')
        self.assertFalse(result['complete'])
        self.assertEqual(result['error_count'], 1)
        self.assertEqual(Path(result['errors'][0]['path']), blocked)

    def test_junction_is_not_traversed_and_is_reported(self):
        search = self.root / 'search'
        target = self.root / 'target/SampleFolder'
        search.mkdir()
        target.mkdir(parents=True)
        link = search / 'junction'
        if os.name == 'nt':
            command = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target.parent)],
                                     capture_output=True, text=True)
            self.assertEqual(command.returncode, 0, command.stdout + command.stderr)
            self.addCleanup(link.rmdir)
        else:
            link.symlink_to(target.parent, target_is_directory=True)
            self.addCleanup(link.unlink)
        result = self.cli('--root', search)
        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertEqual(data['matches'], [])
        self.assertEqual(data['skipped_links'], 1)
        self.assertFalse(data['complete'])
        rejected = self.cli('--root', link)
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(json.loads(rejected.stdout)['error_count'], 1)

    @unittest.skipUnless(os.name == 'nt', 'Windows drive enumeration only')
    def test_real_windows_drive_enumeration_without_scanning(self):
        drives = finder.windows_local_drives()
        self.assertTrue(drives)
        self.assertIn(ROOT.anchor.upper(), [d.upper() for d in drives])


if __name__ == '__main__':
    unittest.main(verbosity=2)
