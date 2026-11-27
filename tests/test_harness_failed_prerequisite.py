"""Durably failed dependents may yield; no subprocesses or real denials run."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('waiting_fixture', ROOT / 'tests/test_harness_waiting_prerequisite.py')
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)
h = fixture.h


class FailedPrerequisiteTests(fixture.WaitingPrerequisiteTests):
    """Reuse safe in-memory API fixture and retain its original seven barriers."""

    def fail_dependent(self):
        item = self.data['tasks']['B']
        item.update(status='FAILED', attempts=1, retry_requires_change=True,
                    failed_files=self.files['B'].copy(), failed_tools=self.tools.copy(),
                    verification={'outcome': 'FAILED'},
                    attempt_history=[{'outcome': 'FAILED'}])

    def test_completed_failure_yields_but_cannot_repeat_unchanged(self):
        self.fail_dependent()
        baseline = copy.deepcopy(self.data['tasks']['B'])
        h.reconcile(ROOT, 'plan.json', 'B', 'B')
        self.assertEqual(self.data['tasks']['B']['status'], 'FAILED')
        self.assertEqual(h.reconcile(ROOT, 'plan.json', 'B', 'B', effects_reviewed=True)['status'],
                         'WAITING_PREREQUISITE')
        with self.assertRaises(fixture.ReachedVerifierGate):
            self.verify('A')
        with self.assertRaisesRegex(ValueError, 'prerequisite lacks'):
            self.verify('B')
        self.data['tasks']['A'].update(status='VERIFIED', verified_files=self.files['A'].copy())
        with self.assertRaisesRegex(ValueError, 'requires a changed'):
            self.verify('B')
        self.files['B']['b.py'] = 'repaired'
        with self.assertRaises(fixture.ReachedVerifierGate):
            self.verify('B')
        for key in ('attempts', 'run_id', 'failed_files', 'failed_tools', 'attempt_history'):
            self.assertEqual(self.data['tasks']['B'][key], baseline[key])

    def test_failed_yield_keeps_review_and_denial_gates(self):
        self.fail_dependent()
        with self.assertRaisesRegex(ValueError, 'observe effects'):
            h.reconcile(ROOT, 'plan.json', 'B', 'B', effects_reviewed=True)
        h.reconcile(ROOT, 'plan.json', 'B', 'B')
        self.files['B']['b.py'] = 'changed after observation'
        with self.assertRaisesRegex(ValueError, 'effects changed'):
            h.reconcile(ROOT, 'plan.json', 'B', 'B', effects_reviewed=True)
        self.data['tasks']['B']['access_denial'] = {'retry_prohibited': True}
        with self.assertRaisesRegex(ValueError, 'access denial'):
            h.reconcile(ROOT, 'plan.json', 'B', 'B')

    def test_failed_waiting_preserves_limits_and_stop(self):
        self.fail_dependent()
        self.assertEqual(self.review()['status'], 'WAITING_PREREQUISITE')
        self.data['tasks']['A'].update(status='VERIFIED', verified_files=self.files['A'].copy())
        self.files['B']['b.py'] = 'repaired'
        baseline = copy.deepcopy(self.data)
        for field, value, message in (
                ('attempts', 5, 'attempt limit'),
                ('started_at', '2000-01-01T00:00:00+00:00', 'elapsed task limit'),
                ('stopped', True, 'harness is stopped')):
            self.data = copy.deepcopy(baseline)
            if field == 'stopped':
                self.data[field] = value
            else:
                self.data['tasks']['B'][field] = value
            before = copy.deepcopy(self.data)
            with self.assertRaisesRegex(ValueError, message):
                self.verify('B')
            self.assertEqual(self.data, before)

    def test_incomplete_failed_metadata_does_not_yield(self):
        for field in ('failed_files', 'failed_tools', 'verification'):
            with self.subTest(field=field):
                self.fail_dependent()
                self.data['tasks']['B'][field] = None
                self.assertEqual(self.review()['status'], 'ACTIVE')

    def test_attempted_ambiguous_failure_report_does_not_yield(self):
        self.fail_dependent()
        self.data['tasks']['B']['status'] = 'AMBIGUOUS'
        self.data['tasks']['B']['scratch'] = None
        self.assertEqual(self.review()['status'], 'AMBIGUOUS')
        with self.assertRaisesRegex(ValueError, 'another task is active'):
            self.verify('A')


if __name__ == '__main__':
    unittest.main()
