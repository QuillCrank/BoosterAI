"""Exercise the redacted, read-only publication scanner in real Python processes."""

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from repo_temp import workspace


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/publication_scan_91c4e6b2.py'
spec = importlib.util.spec_from_file_location('publication_scan', SCRIPT)
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


class PublicationScanTests(unittest.TestCase):
    def setUp(self):
        """Create a uniquely owned repository-local fixture and a synthetic old SHA."""
        self.directory = workspace(self)
        self.commit = 'a' * 40
        self.term = 'Private' + 'LanternExample'
        self.file = self.directory / 'sample.txt'
        self.file.write_text('general reusable text\n', encoding='utf-8')
        self.manifest = {'files': [self.file.name], 'private_terms': [self.term],
                         'old_commits': [self.commit]}

    def cli(self, manifest, *extra):
        """Invoke the real isolated CLI while sending private inputs only on stdin."""
        return subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPT),
                               '--root', str(self.directory), *extra],
                              input=json.dumps(manifest), text=True, capture_output=True,
                              cwd=ROOT)

    def test_deep_json_returns_redacted_manifest_error(self):
        payload = '[' * 30000 + '0' + ']' * 30000
        self.assertLess(len(payload.encode('utf-8')), scanner.MAX_INPUT_BYTES)
        before = self.file.read_bytes()
        result = subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(SCRIPT), '--root', str(self.directory)],
            input=payload, text=True, capture_output=True, cwd=ROOT)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, '')
        self.assertEqual(json.loads(result.stdout), {
            'status': 'ERROR', 'error': 'invalid_manifest', 'privacy_proven': False})
        self.assertNotIn(str(self.directory), result.stdout)
        self.assertEqual(self.file.read_bytes(), before)

    def test_real_cli_clean_requires_manual_privacy_review(self):
        before = self.file.read_bytes()
        result = self.cli(self.manifest)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        outcome = json.loads(result.stdout)
        self.assertEqual(outcome['status'], 'NO_MATCH_REVIEW_REQUIRED')
        self.assertEqual(outcome['files_checked'], 1)
        self.assertEqual(outcome['private_terms_supplied'], 1)
        self.assertEqual(outcome['old_commits_supplied'], 1)
        self.assertFalse(outcome['privacy_proven'])
        self.assertEqual(self.file.read_bytes(), before)
        for private in (self.term, self.commit, self.file.name, str(self.directory)):
            self.assertNotIn(private, result.stdout + result.stderr)

    def test_detects_terms_commit_prefixes_credentials_and_personal_paths(self):
        token = 'gh' + 'p_' + 'R' * 40
        personal_path = 'C:' + '\\' + 'Users' + '\\' + 'FixtureOwner' + '\\' + 'data'
        contents = '\n'.join((self.term.swapcase(), self.commit[:9], token, personal_path))
        self.file.write_text(contents, encoding='utf-8')
        result = self.cli(self.manifest)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['status'], 'FINDINGS')
        self.assertEqual({x['category'] for x in data['findings']},
                         {'private_term', 'old_commit', 'credential', 'personal_path'})
        self.assertTrue(all(x['file_index'] == 0 for x in data['findings']))
        for private in (self.term, self.commit, self.commit[:9], token, personal_path, self.file.name):
            self.assertNotIn(private, result.stdout + result.stderr)

    def test_filename_is_checked_and_never_echoed(self):
        private_name = self.term + '.txt'
        (self.directory / private_name).write_text('generic text', encoding='utf-8')
        result = self.cli(dict(self.manifest, files=[private_name]))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['findings'],
                         [{'file_index': 0, 'category': 'private_term'}])
        self.assertNotIn(private_name, result.stdout + result.stderr)

    def test_sha256_containing_old_commit_prefix_is_not_misclassified(self):
        self.file.write_text('sha256: ' + self.commit + 'b' * 24, encoding='utf-8')
        result = self.cli(self.manifest)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'NO_MATCH_REVIEW_REQUIRED')

    def test_old_commits_only_match_real_seven_or_more_hex_prefixes(self):
        for value, should_match in ((self.commit[:6], False), (self.commit[:7], True),
                                    (self.commit[:39], True), (self.commit, True),
                                    (self.commit[:7] + 'b', False)):
            with self.subTest(length=len(value), match=should_match):
                self.file.write_text('value ' + value + '\n', encoding='utf-8')
                result = self.cli(self.manifest)
                data = json.loads(result.stdout)
                self.assertEqual(any(x['category'] == 'old_commit' for x in data['findings']), should_match)

    def test_invalid_manifests_and_commit_ids_fail_closed(self):
        cases = ({}, {'files': [], 'private_terms': [], 'old_commits': []},
                 dict(self.manifest, old_commits=['a' * 39]),
                 dict(self.manifest, private_terms=['  ']),
                 dict(self.manifest, files=[self.file.name, self.file.name]),
                 dict(self.manifest, unexpected=True))
        for manifest in cases:
            with self.subTest(manifest_type=type(manifest).__name__):
                result = self.cli(manifest)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)['status'], 'ERROR')
                self.assertNotIn(self.file.name, result.stdout + result.stderr)

    def test_missing_and_unsafe_files_fail_closed_without_echoing_paths(self):
        for name, code in (('../outside-fixture.txt', 'unsafe_path'),
                           ('_boosterAI/private-fixture.txt', 'unsafe_path'),
                           ('nested/.git/config', 'unsafe_path'),
                           ('missing-fixture.txt', 'file_missing'),
                           (str(self.file), 'unsafe_path')):
            with self.subTest(code=code):
                result = self.cli(dict(self.manifest, files=[name]))
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)['error'], code)
                self.assertNotIn(name, result.stdout + result.stderr)

    def test_non_utf8_and_hardlinks_fail_closed(self):
        self.file.write_bytes(b'\xff\xfe\xff')
        result = self.cli(self.manifest)
        self.assertEqual(json.loads(result.stdout)['error'], 'unsupported_encoding')
        self.file.write_text('generic', encoding='utf-8')
        alias = self.directory / 'hardlink.txt'
        alias.hardlink_to(self.file)
        result = self.cli(self.manifest)
        self.assertEqual(json.loads(result.stdout)['error'], 'unsafe_path')
        self.assertEqual(alias.read_text(encoding='utf-8'), 'generic')

    def test_access_denial_has_distinct_redacted_error(self):
        info = self.file.lstat()
        with patch.object(Path, 'open', side_effect=PermissionError('private-fixture-path')):
            with self.assertRaises(scanner.ScanError) as caught:
                scanner.read_checked(self.file, info, 0)
        self.assertEqual(caught.exception.code, 'access_denied')
        self.assertEqual(caught.exception.index, 0)
        self.assertNotIn('private-fixture-path', str(caught.exception))

    @unittest.skipUnless(sys.platform == 'win32', 'Windows junction test')
    def test_junction_inside_manifest_is_rejected_without_touching_target(self):
        target = self.directory / 'target'
        target.mkdir()
        marker = target / 'keep.txt'
        marker.write_text('preserve', encoding='utf-8')
        junction = self.directory / 'linked'
        made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(target)],
                              capture_output=True, text=True)
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        self.addCleanup(junction.rmdir)
        result = self.cli(dict(self.manifest, files=['linked/keep.txt']))
        self.assertEqual(json.loads(result.stdout)['error'], 'unsafe_path')
        self.assertEqual(marker.read_text(encoding='utf-8'), 'preserve')

    @unittest.skipUnless(sys.platform == 'win32', 'Windows junction test')
    def test_imported_scanner_rejects_junction_root_before_reading(self):
        target = self.directory / 'target-root'
        target.mkdir()
        marker = target / 'keep.txt'
        marker.write_text('preserve', encoding='utf-8')
        junction = self.directory / 'linked-root'
        made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(target)],
                              capture_output=True, text=True)
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        self.addCleanup(junction.rmdir)
        manifest = dict(self.manifest, files=['keep.txt'])
        with patch.object(scanner, 'read_checked', side_effect=AssertionError('unexpected read')) as read:
            with self.assertRaises(scanner.ScanError) as caught:
                scanner.scan_manifest(junction, manifest)
        self.assertEqual(caught.exception.code, 'unsafe_root')
        read.assert_not_called()
        self.assertEqual(marker.read_text(encoding='utf-8'), 'preserve')


class PublicationScanRootArgumentTests(unittest.TestCase):
    def assert_root_rejected_before_access(self, argv):
        with (
            patch.object(scanner, 'validated_root',
                         side_effect=AssertionError('unexpected root validation')) as validate,
            patch.object(sys, 'stdin') as stdin,
            redirect_stdout(StringIO()) as stdout,
            redirect_stderr(StringIO()) as stderr,
            self.assertRaises(SystemExit) as caught,
        ):
            stdin.buffer.read.side_effect = AssertionError('unexpected manifest read')
            scanner.main(argv)
        self.assertEqual(caught.exception.code, 2)
        validate.assert_not_called()
        stdin.buffer.read.assert_not_called()
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(stderr.getvalue(), 'invalid arguments\n')

    def test_blank_root_is_rejected_before_access(self):
        for root in ('', ' \t ', '\u2003'):
            for argv in (['--root', root], ['--root=' + root]):
                with self.subTest(argv=argv):
                    self.assert_root_rejected_before_access(argv)

    def test_root_must_be_supplied_exactly_once(self):
        for argv in ([], ['--root', '.', '--root=private-second-root']):
            with self.subTest(argv=argv):
                self.assert_root_rejected_before_access(argv)

    def test_nonblank_root_keeps_its_original_spelling(self):
        root = ' project folder'
        manifest = {'files': ['LICENSE'], 'private_terms': [], 'old_commits': []}
        report = {'findings': []}
        with (
            patch.object(scanner, 'validated_root', return_value=ROOT) as validate,
            patch.object(scanner, 'scan_manifest', return_value=report) as scan,
            patch.object(sys, 'stdin') as stdin,
            redirect_stdout(StringIO()) as stdout,
            redirect_stderr(StringIO()) as stderr,
        ):
            stdin.buffer.read.return_value = json.dumps(manifest).encode('utf-8')
            self.assertEqual(scanner.main(['--root', root]), 0)
        validate.assert_called_once_with(root)
        stdin.buffer.read.assert_called_once_with(scanner.MAX_INPUT_BYTES + 1)
        scan.assert_called_once_with(ROOT, manifest)
        self.assertEqual(json.loads(stdout.getvalue()), report)
        self.assertEqual(stderr.getvalue(), '')


if __name__ == '__main__':
    unittest.main(verbosity=2)
