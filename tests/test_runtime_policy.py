"""Runtime policy boundaries without installing or retargeting any runtime."""
import importlib.util
import collections
import io
from unittest.mock import patch
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('runtime_booster', ROOT / 'scripts/booster.py')
b = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(b)


class RuntimePolicyTests(unittest.TestCase):
    def test_python_observes_this_process(self):
        if sys.version_info.releaselevel != 'final':
            with self.assertRaisesRegex(ValueError, 'stable release'):
                b.runtime_check('python')
            return
        result = b.runtime_check('python')
        self.assertEqual(result['observed_version'], '.'.join(map(str, sys.version_info[:3])))
        self.assertEqual(result['valid'], sys.version_info >= (3, 12))
        self.assertTrue(result['environment_verified'])
        self.assertFalse(result['runtime_changed'])
        self.assertFalse(result['product_acceptance_verified'])

    def test_current_prerelease_interpreter_is_not_reported_as_stable(self):
        Version = collections.namedtuple('Version', 'major minor micro releaselevel serial')
        for level in ('alpha', 'beta', 'candidate'):
            with patch.object(b.sys, 'version_info', Version(3, 12, 0, level, 1)):
                with self.assertRaisesRegex(ValueError, 'stable release'):
                    b.runtime_check('python')

    def test_cli_policy_failure_and_explicit_match_exit_codes(self):
        argv = ['booster.py', 'runtime-check', '--language', 'python',
                '--observed-version', '3.11.9']
        with patch.object(b.sys, 'argv', argv), patch.object(b.sys, 'stdout', io.StringIO()):
            self.assertEqual(b.main(), 1)
        with patch.object(b.sys, 'argv', argv + ['--requested-version', '3.11']), \
                patch.object(b.sys, 'stdout', io.StringIO()):
            self.assertEqual(b.main(), 0)

    def test_default_boundaries_and_reported_provenance(self):
        for language, version, valid in (
                ('python', '3.11.9', False), ('python', '3.12', True),
                ('python', '3.14.0', True), ('dotnet', '9.0.300', False),
                ('dotnet', '10.0.100', True), ('dotnet', '11.0.100', True)):
            with self.subTest(language=language, version=version):
                result = b.runtime_check(language, version)
                self.assertEqual(result['valid'], valid)
                self.assertFalse(result['environment_verified'])
                self.assertEqual(result['observation_source'], 'caller_reported')

    def test_explicit_requested_version_overrides_default_only_when_matching(self):
        for language, observed, requested, valid in (
                ('python', '3.10.11', '3.10', True),
                ('python', '3.10.11', '3.10.11', True),
                ('python', '3.10.12', '3.10.11', False),
                ('python', '3.14.0', '3.12', False),
                ('dotnet', '8.0.400', '8.0', True),
                ('dotnet', '10.0.100', '8.0', False)):
            with self.subTest(observed=observed, requested=requested):
                result = b.runtime_check(language, observed, requested)
                self.assertEqual(result['valid'], valid)
                self.assertEqual(result['policy'], 'explicit_user_version_request')

    def test_malformed_or_prerelease_versions_and_unknown_sdk_rejected(self):
        for value in ('3', '3.12-preview', '3.12.0rc1', '03.12', '3.12\n', '', True, 312):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    b.runtime_check('python', value)
                with self.assertRaises(ValueError):
                    b.runtime_check('python', '3.12.0', value)
        with self.assertRaises(ValueError):
            b.runtime_check('dotnet')
        with self.assertRaises(ValueError):
            b.runtime_check('unknown', '3.12')


if __name__ == '__main__':
    unittest.main()
