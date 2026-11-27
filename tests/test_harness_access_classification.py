"""Check runner exception classification with synthetic owned fixtures only."""

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
RUNNER = ROOT / 'scripts/harness_unittest_b913ed42.py'
SPEC = importlib.util.spec_from_file_location('access_test_runner', RUNNER)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class HarnessAccessClassificationTests(unittest.TestCase):
    """Exercise the actual runner CLI without invoking a denied operation."""

    def setUp(self):
        """Own an isolated project and register its cleanup before fixture writes."""
        self.project = workspace(self)
        self.counter = 0

    def invoke(self, source):
        """Run one explicit test in a fresh fixture; return its sanitized report."""
        self.counter += 1
        test = self.project / ('fixture_' + str(self.counter) + '.py')
        test.write_text(source, encoding='utf-8')
        temporary = self.project / ('child_' + str(self.counter))
        temporary.mkdir()
        report = temporary / 'result.json'
        env = os.environ.copy()
        for key in ('TEMP', 'TMP', 'TMPDIR'):
            env[key] = str(temporary)
        process = subprocess.run([sys.executable, '-I', '-S', '-B', str(RUNNER),
                                  '--root', str(self.project), '--test', test.name,
                                  '--case', 'Example.test_case', '--report', str(report)],
                                 cwd=self.project, env=env, capture_output=True,
                                 text=True, timeout=30)
        encoded = report.read_text(encoding='utf-8')
        self.assertNotIn('fixture-private-message', encoded)
        self.assertNotIn('Traceback', encoded)
        return process.returncode, json.loads(encoded)

    def test_direct_test_and_subtest_permission_errors_are_classified(self):
        """Test and subtest exceptions are counted without copying their details."""
        for body in (
            "raise PermissionError('fixture-private-message')",
            "with self.subTest(part=1):\n            raise PermissionError('fixture-private-message')",
        ):
            with self.subTest(body=body.split('(')[0]):
                source = ('import unittest\nclass Example(unittest.TestCase):\n'
                          '    def test_case(self):\n        ' + body + '\n')
                code, result = self.invoke(source)
                self.assertEqual(code, 1)
                self.assertEqual(result['tests_run'], 1)
                self.assertTrue(result['access_denied'])
                self.assertFalse(result['success'])

    def test_import_and_setup_permission_errors_are_classified(self):
        """Module import and class or module setup errors are not hidden."""
        fixtures = (
            "raise PermissionError('fixture-private-message')\n",
            "import unittest\ndef setUpModule():\n    raise PermissionError('fixture-private-message')\n"
            "class Example(unittest.TestCase):\n    def test_case(self): pass\n",
            "import unittest\nclass Example(unittest.TestCase):\n"
            "    @classmethod\n    def setUpClass(cls):\n"
            "        raise PermissionError('fixture-private-message')\n"
            "    def test_case(self): pass\n",
        )
        for source in fixtures:
            with self.subTest(fixture=fixtures.index(source)):
                code, result = self.invoke(source)
                self.assertEqual(code, 1)
                self.assertEqual(result['tests_run'], 0)
                self.assertTrue(result['access_denied'])
                self.assertFalse(result['success'])

    def test_expected_failure_does_not_hide_permission_error(self):
        """Unittest's expected-failure path retains the observed denial type."""
        code, result = self.invoke(
            'import unittest\nclass Example(unittest.TestCase):\n'
            '    @unittest.expectedFailure\n    def test_case(self):\n'
            "        raise PermissionError('fixture-private-message')\n")
        self.assertEqual(code, 1)
        self.assertEqual(result['expected_failures'], 1)
        self.assertTrue(result['access_denied'])

    def test_other_failure_and_success_do_not_assert_access_denial(self):
        """Ordinary assertion failures and passing tests remain distinguishable."""
        for statement, expected_code, success in (
            ('self.fail()', 1, False), ('self.assertEqual(2 + 2, 4)', 0, True),
        ):
            with self.subTest(statement=statement):
                code, result = self.invoke(
                    'import unittest\nclass Example(unittest.TestCase):\n'
                    '    def test_case(self):\n        ' + statement + '\n')
                self.assertEqual(code, expected_code)
                self.assertEqual(result['tests_run'], 1)
                self.assertFalse(result['access_denied'])
                self.assertEqual(result['success'], success)

    def test_path_permission_error_returns_fixed_code_without_details(self):
        """A synthetic preflight denial propagates exit 77 from a real child."""
        probe = (
            'import importlib.util, sys\n'
            'from unittest.mock import patch\n'
            'spec = importlib.util.spec_from_file_location("runner_probe", sys.argv[1])\n'
            'runner = importlib.util.module_from_spec(spec)\n'
            'spec.loader.exec_module(runner)\n'
            'args = ["--root", ".", "--test", "x.py", "--case", "Example.test_case", '
            '"--report", "result.json"]\n'
            'with patch.object(runner, "_paths", side_effect=PermissionError("fixture-private-message")):\n'
            '    code = runner.main(args)\n'
            'sys.exit(code)\n'
        )
        child = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', probe, str(RUNNER)],
                               cwd=self.project, capture_output=True, text=True, timeout=30)
        self.assertEqual(child.returncode, 77)
        self.assertNotIn('fixture-private-message', child.stdout + child.stderr)

    def test_report_and_second_path_check_denials_return_fixed_code(self):
        """Both post-test denial sites return 77 without creating an output file."""
        test = self.project / 'selected.py'
        output = self.project / 'result.json'
        paths = (self.project, test, output)
        args = ['--root', str(self.project), '--test', test.name,
                '--case', 'Example.test_case', '--report', str(output)]
        with patch.object(runner, '_paths', side_effect=[paths, PermissionError('fixture')]), \
             patch.object(runner, 'run_case', return_value={'success': True}):
            self.assertEqual(runner.main(args), 77)
        with patch.object(runner, '_paths', return_value=paths), \
             patch.object(runner, 'run_case', return_value={'success': True}), \
             patch.object(runner, '_write_report', side_effect=PermissionError('fixture')):
            self.assertEqual(runner.main(args), 77)
        self.assertFalse(output.exists())

    def test_other_path_errors_keep_code_two(self):
        """Non-permission path errors do not claim that access was denied."""
        args = ['--root', str(self.project), '--test', 'x.py',
                '--case', 'Example.test_case', '--report', str(self.project / 'result.json')]
        for error in (OSError('fixture'), ValueError('fixture')):
            with self.subTest(error=type(error).__name__), \
                 patch.object(runner, '_paths', side_effect=error):
                self.assertEqual(runner.main(args), 2)


if __name__ == '__main__':
    unittest.main()
