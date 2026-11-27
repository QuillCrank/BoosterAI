import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import repo_temp


class RepositoryTempTests(unittest.TestCase):
    def setUp(self):
        self.directory = repo_temp.workspace(self)

    def test_parent_and_real_child_use_owned_temp(self):
        for key in ('TEMP', 'TMP', 'TMPDIR'):
            self.assertEqual(Path(os.environ[key]), self.directory)
        with tempfile.NamedTemporaryFile() as probe:
            self.assertEqual(Path(probe.name).parent, self.directory)
        result = subprocess.run(
            [sys.executable, '-I', '-S', '-B', '-c',
             'import tempfile; from pathlib import Path; '
             'f=tempfile.NamedTemporaryFile(); print(Path(f.name).parent); f.close()'],
            capture_output=True, text=True, check=True, cwd=repo_temp.ROOT,
        )
        self.assertEqual(Path(result.stdout.strip()), self.directory)

    def test_success_failure_and_setup_failure_leave_no_scratch(self):
        environment = {key: os.environ.get(key) for key in ('TEMP', 'TMP', 'TMPDIR')}
        cached = tempfile.tempdir
        sibling = self.directory / 'keep.txt'
        sibling.write_text('another live operation', encoding='utf-8')
        for failure in ('none', 'body', 'setup'):
            created = []

            class Probe(unittest.TestCase):
                def setUp(self):
                    directory = repo_temp.workspace(self)
                    created.append(directory)
                    (directory / 'disposable.txt').write_text('fixture', encoding='utf-8')
                    if failure == 'setup':
                        raise RuntimeError('expected setup failure')

                def runTest(self):
                    if failure == 'body':
                        self.fail('expected assertion failure')

            result = unittest.TestResult()
            Probe().run(result)
            self.assertEqual(len(result.errors), int(failure == 'setup'))
            self.assertEqual(len(result.failures), int(failure == 'body'))
            self.assertEqual(len(created), 1)
            self.assertFalse(created[0].exists())
            # Independent processes may create or release their own siblings.
            # Only this probe's registered directory and known sibling are ours.
            self.assertEqual(tempfile.tempdir, cached)
            self.assertEqual({key: os.environ.get(key) for key in environment}, environment)
            self.assertEqual(sibling.read_text(encoding='utf-8'), 'another live operation')

    def test_existing_file_is_preserved(self):
        collision = self.directory / '_temp-files'
        collision.write_text('existing owner file', encoding='utf-8')
        case = unittest.TestCase()
        self.addCleanup(case.doCleanups)
        with patch.object(repo_temp, 'ROOT', self.directory):
            with self.assertRaises(FileExistsError):
                repo_temp.workspace(case)
        self.assertEqual(collision.read_text(encoding='utf-8'), 'existing owner file')

    def test_junction_or_symlink_is_rejected(self):
        target = self.directory / 'retained'
        target.mkdir()
        retained = target / 'keep.txt'
        retained.write_text('preserve target', encoding='utf-8')
        link = self.directory / '_temp-files'
        if os.name == 'nt':
            # Both sides are inside this test's owned directory; no elevation is needed.
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.addCleanup(link.rmdir)
        else:
            link.symlink_to(target, target_is_directory=True)
            self.addCleanup(link.unlink)
        case = unittest.TestCase()
        self.addCleanup(case.doCleanups)
        with patch.object(repo_temp, 'ROOT', self.directory):
            with self.assertRaisesRegex(ValueError, 'inside the repository'):
                repo_temp.workspace(case)
        self.assertEqual(retained.read_text(encoding='utf-8'), 'preserve target')
        self.assertEqual(list(target.iterdir()), [retained])


if __name__ == '__main__':
    unittest.main(verbosity=2)
