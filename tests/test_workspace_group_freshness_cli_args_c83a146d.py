"""Check freshness CLI arguments and exit codes without opening an index."""

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'project_index_freshness_a62e9d47.py'
SPEC = importlib.util.spec_from_file_location('freshness_cli_args', SCRIPT)
FRESHNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FRESHNESS)


class FreshnessCliArgumentTest(unittest.TestCase):
    def invoke_parser(self, argv):
        with (
            patch.object(FRESHNESS, 'check_freshness',
                         side_effect=AssertionError('unexpected freshness comparison')) as check,
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()) as stderr,
            self.assertRaises(SystemExit) as exit_info,
        ):
            FRESHNESS.main(argv)
        check.assert_not_called()
        return exit_info.exception.code, stdout.getvalue(), stderr.getvalue()

    def test_unknown_arguments_are_not_echoed(self):
        unknown = '--fictional-option'
        value = 'synthetic-argument-value'
        code, stdout, stderr = self.invoke_parser([
            '--root', '.', '--path', '_partials/README.md', unknown, value,
        ])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, '')
        self.assertTrue(stderr.rstrip().endswith('invalid arguments. Use --help for usage.'))
        self.assertFalse(unknown in stderr or value in stderr)

    def test_help_is_printed_to_stdout(self):
        code, stdout, stderr = self.invoke_parser(['--help'])
        self.assertEqual(code, 0)
        self.assertEqual(stderr, '')
        self.assertIn('--root', stdout)
        self.assertIn('--path', stdout)
        self.assertIn('--require-current', stdout)

    def test_repeated_root_is_rejected_before_comparison(self):
        base = ['--root', '.', '--path', '_partials/README.md',
                '--path', '_boosterAI/_partials/README.md']
        for extra in (['--root', '.'], ['--root', 'other-root'], ['--root=other-root']):
            with self.subTest(extra=extra):
                code, stdout, stderr = self.invoke_parser(base + extra)
                self.assertEqual(code, 2)
                self.assertEqual(stdout, '')
                self.assertTrue(stderr.rstrip().endswith('invalid arguments. Use --help for usage.'))
                self.assertNotIn('other-root', stderr)


    def test_empty_root_is_rejected_before_comparison(self):
        for root in (['--root='], ['--root', '']):
            with self.subTest(root=root):
                code, stdout, stderr = self.invoke_parser(root + ['--path', '_partials/README.md'])
                self.assertEqual(code, 2)
                self.assertEqual(stdout, '')
                self.assertTrue(stderr.rstrip().endswith('invalid arguments. Use --help for usage.'))

    def test_whitespace_only_root_is_rejected_before_comparison(self):
        path_arg = ['--path', '_partials/README.md']
        _, _, usage = self.invoke_parser(['--root='] + path_arg)
        for root in (['--root= '], ['--root', '\t'], ['--root= \t '], ['--root', '\u2003']):
            with self.subTest(root=root):
                self.assertEqual(self.invoke_parser(root + path_arg), (2, '', usage))

    def test_require_current_rejects_an_explicit_value_before_comparison(self):
        value = 'synthetic-argument-value'
        code, stdout, stderr = self.invoke_parser([
            '--root', '.', '--path', '_partials/README.md', f'--require-current={value}',
        ])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, '')
        self.assertTrue(stderr.rstrip().endswith('invalid arguments. Use --help for usage.'))
        self.assertNotIn(value, stderr)

    def compare(self, paths, strict, *, root='.', report=None, error=None):
        argv = ['--root', root]
        for path in paths:
            argv.extend(['--path', path])
        if strict:
            argv.append('--require-current')
        with (
            patch.object(FRESHNESS, 'check_freshness', return_value=report, side_effect=error) as check,
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()) as stderr,
        ):
            code = FRESHNESS.main(argv)
        check.assert_called_once_with(root, *paths)
        self.assertEqual(stderr.getvalue(), '')
        return code, stdout.getvalue()

    def test_nonblank_root_is_forwarded_verbatim(self):
        root = ' synthetic-root \t'
        path = '_partials/README.md'
        report = {'status': 'CURRENT', 'scope': 'selected_index_entry', 'path': path}
        self.assertEqual(self.compare([path], False, root=root, report=report),
                         (0, json.dumps(report) + '\n'))

    def test_require_current_single_entry_keeps_the_report(self):
        path = '_partials/README.md'
        for status, strict_exit in (('CURRENT', 0), ('STALE', 3)):
            report = {'status': status, 'scope': 'selected_index_entry', 'path': path}
            for strict, expected in ((False, 0), (True, strict_exit)):
                with self.subTest(status=status, strict=strict):
                    self.assertEqual(self.compare([path], strict, report=report),
                                     (expected, json.dumps(report) + '\n'))

    def test_require_current_uses_aggregate_status_and_keeps_ordered_entries(self):
        current_path = '_partials/README.md'
        other_path = '_boosterAI/_partials/README.md'
        for status, strict_exit in (('CURRENT', 0), ('STALE', 3)):
            report = {'status': status, 'scope': 'selected_index_entries', 'entries': [
                {'status': status, 'scope': 'selected_index_entry', 'path': other_path},
                {'status': 'CURRENT', 'scope': 'selected_index_entry', 'path': current_path},
            ]}
            for strict, expected in ((False, 0), (True, strict_exit)):
                with self.subTest(status=status, strict=strict):
                    self.assertEqual(self.compare([current_path, other_path], strict, report=report),
                                     (expected, json.dumps(report) + '\n'))

    def test_require_current_preserves_incomplete_comparison_errors(self):
        marker = 'synthetic-source-detail'
        for error, code in ((FRESHNESS.FreshnessError('path_not_indexed'), 'path_not_indexed'),
                            (PermissionError(marker), 'access_denied'),
                            (OSError(marker), 'index_or_source_unavailable')):
            expected = json.dumps({'status': 'ERROR', 'error': code}) + '\n'
            for strict in (False, True):
                with self.subTest(code=code, strict=strict):
                    self.assertEqual(self.compare(['_partials/README.md'], strict, error=error),
                                     (1, expected))


if __name__ == '__main__':
    unittest.main()
