"""Fixture-free tests of contribution review read bounds and JSON decoding."""

from contextlib import nullcontext, redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import contribution_preflight_5c294f8d as preflight

LIMIT = 65536
VIRTUAL_ROOT = Path('virtual-root')
INVALID = {
    'status': 'NOT_READY',
    'error': 'invalid_or_unavailable_input',
    'pr_status': 'NOT_RUN',
    'pr_created': False,
}
DENIED = {**INVALID, 'error': 'access_denied'}


class UndecodableBytes(bytes):
    def decode(self, *args, **kwargs):
        raise AssertionError('oversized review bytes were decoded')


class ContributionReviewReadLimitTest(unittest.TestCase):
    def run_review(self, path):
        output = StringIO()
        errors = StringIO()
        with (
            patch.object(preflight, 'project_root', return_value=VIRTUAL_ROOT),
            patch.object(preflight, 'ordinary_file', return_value=path),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            status = preflight.main(['--root', '.', '--review', 'private-review.json'])
        self.assertEqual(errors.getvalue(), '')
        return status, json.JSONDecoder().decode(output.getvalue())

    def test_exact_byte_limit_accepts_utf8_with_bom(self):
        prefix = '{"note":"Caf\u00e9"}'.encode('utf-8-sig')
        payload = prefix + b' ' * (LIMIT - len(prefix))
        with BytesIO(payload) as buffer:
            stream = Mock(wraps=buffer)
            path = Mock(spec_set=Path)
            path.stat.return_value = SimpleNamespace(st_size=LIMIT)
            path.open.return_value = nullcontext(stream)

            status, result = self.run_review(path)

            path.open.assert_called_once_with('rb')
            stream.read.assert_called_once_with(LIMIT + 1)
            self.assertEqual(buffer.tell(), LIMIT)
        self.assertEqual(status, 0)
        self.assertEqual(result['status'], 'NOT_READY')
        self.assertEqual(result['missing'], list(preflight.REQUIRED))
        self.assertIs(result['consent_verified'], False)
        self.assertIs(result['network_accessed'], False)
        self.assertIs(result['source_executed'], False)
        self.assertEqual(result['pr_status'], 'NOT_RUN')
        self.assertIs(result['pr_created'], False)

    def test_first_excess_after_stat_stops_before_decode_or_parse(self):
        payload = b'{}' + b' ' * (LIMIT + 30)
        with BytesIO(payload) as buffer:
            stream = Mock()
            stream.read.side_effect = lambda size: UndecodableBytes(buffer.read(size))
            path = Mock(spec_set=Path)
            path.stat.return_value = SimpleNamespace(st_size=LIMIT)
            path.open.return_value = nullcontext(stream)
            with (
                patch.object(preflight.json, 'loads') as parse,
                patch.object(preflight, 'evaluate') as evaluate,
            ):
                self.assertEqual(self.run_review(path), (1, INVALID))
            parse.assert_not_called()
            evaluate.assert_not_called()
            path.open.assert_called_once_with('rb')
            stream.read.assert_called_once_with(LIMIT + 1)
            self.assertEqual(buffer.tell(), LIMIT + 1)

    def test_early_stat_guard_does_not_open_large_file(self):
        path = Mock(spec_set=Path)
        path.stat.return_value = SimpleNamespace(st_size=LIMIT + 1)
        self.assertEqual(self.run_review(path), (1, INVALID))
        path.open.assert_not_called()

    def test_permission_errors_preserve_path_free_result(self):
        for failure in ('open', 'read'):
            with self.subTest(failure=failure):
                path = Mock(spec_set=Path)
                path.stat.return_value = SimpleNamespace(st_size=4)
                denied = PermissionError('private-review.json')
                stream = Mock()
                if failure == 'open':
                    path.open.side_effect = denied
                else:
                    stream.read.side_effect = denied
                    path.open.return_value = nullcontext(stream)
                self.assertEqual(self.run_review(path), (1, DENIED))
                path.open.assert_called_once_with('rb')
                if failure == 'read':
                    stream.read.assert_called_once_with(LIMIT + 1)
                else:
                    stream.read.assert_not_called()

    def test_rejects_duplicate_fields_and_non_utf8_without_input_echo(self):
        cases = (
            ('duplicate', b'{"details":{"note":1,"n\\u006fte":2}}'),
            ('utf16', '{}'.encode('utf-16')),
        )
        for name, payload in cases:
            with self.subTest(name=name), BytesIO(payload) as buffer:
                path = Mock(spec_set=Path)
                path.stat.return_value = SimpleNamespace(st_size=len(payload))
                path.open.return_value = nullcontext(buffer)
                self.assertEqual(self.run_review(path), (1, INVALID))
                path.open.assert_called_once_with('rb')

    def test_rejects_non_json_numbers_before_review_evaluation(self):
        for token in (b'NaN', b'Infinity', b'-Infinity'):
            payload = b'{"notes":[{"measurement":' + token + b'}]}'
            with self.subTest(token=token), BytesIO(payload) as buffer:
                path = Mock(spec_set=Path)
                path.stat.return_value = SimpleNamespace(st_size=len(payload))
                path.open.return_value = nullcontext(buffer)
                with patch.object(preflight, 'evaluate') as evaluate:
                    self.assertEqual(self.run_review(path), (1, INVALID))
                evaluate.assert_not_called()

    def test_accepts_quoted_literals_and_standard_numbers(self):
        payload = b'{"notes":["NaN","Infinity","-Infinity",-12.5,4e2]}'
        with BytesIO(payload) as buffer:
            path = Mock(spec_set=Path)
            path.stat.return_value = SimpleNamespace(st_size=len(payload))
            path.open.return_value = nullcontext(buffer)
            status, result = self.run_review(path)
        self.assertEqual(status, 0)
        self.assertEqual(result['missing'], list(preflight.REQUIRED))
        self.assertEqual(result['invalid'], [])
        self.assertEqual(result['pr_status'], 'NOT_RUN')

    def test_repeated_path_selectors_stop_before_resolving_paths(self):
        """Keep ambiguous roots and review paths out of the preflight reader."""
        for flag in ('--root', '--review'):
            output, errors = StringIO(), StringIO()
            with (
                self.subTest(flag=flag),
                patch.object(preflight, 'project_root') as root,
                redirect_stdout(output),
                redirect_stderr(errors),
                self.assertRaises(SystemExit) as exit_status,
            ):
                preflight.main([flag, 'first-selector', flag + '=second-selector'])
            root.assert_not_called()
            self.assertEqual(exit_status.exception.code, 2)
            self.assertEqual(output.getvalue(), '')
            self.assertEqual(errors.getvalue(), 'invalid arguments; no contribution was evaluated\n')


if __name__ == '__main__':
    unittest.main()
