"""Validate harness API budgets before any ownership or state-file effects."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('harness_input_limits', ROOT / 'scripts/harness_5b7e9c31.py')
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


class InputLimits(unittest.TestCase):
    """Booleans must not silently become one-attempt or one-second budgets."""

    def test_boolean_limits_fail_before_workspace_effects(self):
        """Call the actual public API with each boolean budget and forbid locking."""
        for field in ('attempts', 'timeout', 'max_elapsed'):
            for value in (True, False):
                with self.subTest(field=field, value=value):
                    values = dict(attempts=2, timeout=60, max_elapsed=3600)
                    values[field] = value
                    with patch.object(harness._STATE, 'locked', side_effect=AssertionError('unexpected workspace effect')) as lock:
                        with self.assertRaises(ValueError):
                            harness.start(ROOT, 'unused.json', 'unused', 'unused.py',
                                          'Example.test_case', **values)
                        lock.assert_not_called()




class IntegerBoundaries(unittest.TestCase):
    """Check allowed bounds without running any workspace or test operation."""

    def test_integer_boundaries_reach_workspace_gate(self):
        """Valid minimum and maximum integers pass validation up to the claim gate."""
        class ReachedGate(Exception):
            pass
        for field, maximum in (('attempts', 10), ('timeout', 600), ('max_elapsed', 604800)):
            for value in (1, maximum):
                with self.subTest(field=field, value=value):
                    values = dict(attempts=2, timeout=60, max_elapsed=3600)
                    values[field] = value
                    with patch.object(harness._STATE, 'locked', side_effect=ReachedGate) as lock:
                        with self.assertRaises(ReachedGate):
                            harness.start(ROOT, 'unused.json', 'unused', 'unused.py',
                                          'Example.test_case', **values)
                        lock.assert_called_once_with(ROOT)

    def test_invalid_limits_fail_before_workspace_effects(self):
        """Out-of-range and non-integer limits must not acquire claims or write state."""
        for field, maximum in (('attempts', 10), ('timeout', 600), ('max_elapsed', 604800)):
            for value in (0, -1, maximum + 1, 1.0, '1', None):
                with self.subTest(field=field, value=value):
                    values = dict(attempts=2, timeout=60, max_elapsed=3600)
                    values[field] = value
                    with patch.object(harness._STATE, 'locked', side_effect=AssertionError('unexpected workspace effect')) as lock:
                        with self.assertRaises(ValueError):
                            harness.start(ROOT, 'unused.json', 'unused', 'unused.py',
                                          'Example.test_case', **values)
                        lock.assert_not_called()


class TestPathContract(unittest.TestCase):
    def test_requires_exact_python_suffix(self):
        for path in ('tests/example.py', 'tests.v1/example.case.py'):
            with self.subTest(path=path):
                harness._require_test_path(path)

        for path in ('tests/example', 'tests/example.md', 'tests/example.PY'):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, 'test must be a project Python file'):
                    harness._require_test_path(path)

        with self.assertRaisesRegex(ValueError, 'noncanonical path'):
            harness._require_test_path('tests/../example.md')
        with self.assertRaisesRegex(ValueError, 'outside protected partials directories'):
            harness._require_test_path('_boosterAI/_partials/example.md')


if __name__ == '__main__':
    unittest.main()
