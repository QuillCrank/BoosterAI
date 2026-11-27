"""Injected denial regressions; no verifier, filesystem fixture or denial is run."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'denial_memory_fixture', ROOT / 'tests/test_harness_waiting_prerequisite.py')
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)
h = fixture.h


class DenialPersistenceTests(unittest.TestCase):
    def setUp(self):
        """Reuse the in-memory state adapter without executing its test methods."""
        self.memory = fixture.WaitingPrerequisiteTests()
        self.memory.setUp()
        self.addCleanup(self.memory.doCleanups)
        self.memory.data['tasks']['A']['verified_files'] = self.memory.files['A'].copy()
        self.memory.data['tasks']['B']['timeout_seconds'] = 10
        stack = self.memory.stack
        stack.enter_context(patch.object(h._TOOLS, 'scratch_directory', return_value=ROOT / '_temp-files'))
        stack.enter_context(patch.object(h.tempfile, 'mkdtemp', return_value=str(ROOT / '_temp-files/unused-denial')))
        stack.enter_context(patch.object(Path, 'lstat', return_value=ROOT.stat()))
        self.denial = PermissionError('synthetic denial only')
        self.run = stack.enter_context(patch.object(h.subprocess, 'run', side_effect=self.denial))

    def test_direct_denial_is_durable_and_not_replayable(self):
        """A known denial survives status/reconciliation without retry eligibility."""
        with patch.object(h.os.environ, 'copy', side_effect=self.denial):
            with self.assertRaisesRegex(h.VerificationAccessDenied, 'denial recorded') as caught:
                self.memory.verify('B')
        self.assertIs(caught.exception.__cause__, self.denial)
        self.run.assert_not_called()
        self.assertEqual(h.status(ROOT, 'plan.json')['tasks']['B']['status'], 'DENIED')
        with self.assertRaisesRegex(ValueError, 'not recoverable'):
            h.reconcile(ROOT, 'plan.json', 'B', 'B')
        with self.assertRaisesRegex(ValueError, 'denied'):
            self.memory.verify('B')
        self.run.assert_not_called()

    def test_potential_live_child_retains_slot_and_cannot_be_reconciled(self):
        """A launch/wait/termination denial leaves child lifetime unknown."""
        with self.assertRaises(h.VerificationAccessDenied):
            self.memory.verify('B')
        item = self.memory.data['tasks']['B']
        self.assertEqual(item['status'], 'AMBIGUOUS')
        self.assertTrue(item['access_denial']['child_outcome_unknown'])
        with self.assertRaisesRegex(ValueError, 'access denial'):
            h.reconcile(ROOT, 'plan.json', 'B', 'B', effects_reviewed=True)
        with self.assertRaisesRegex(ValueError, 'another task'):
            self.memory.verify('A')
        self.assertEqual(self.run.call_count, 1)

    def test_reaped_child_report_denial_can_be_denied(self):
        """A returned/reaped child is distinct from a failed launch or wait."""
        from types import SimpleNamespace
        with patch.object(h.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
             patch.object(h, '_report', side_effect=self.denial):
            with self.assertRaises(h.VerificationAccessDenied):
                self.memory.verify('B')
        item = self.memory.data['tasks']['B']
        self.assertEqual(item['status'], 'DENIED')
        self.assertFalse(item['access_denial']['child_outcome_unknown'])

    def test_pending_stop_retains_unknown_outcome_and_denial(self):
        """A denied termination preserves STOP and unknown effects without replay."""
        def interrupted(command, root, env, timeout, stream, persist_stop):
            persist_stop()
            raise self.denial
        with patch.object(h, '_interruptible', side_effect=interrupted) as stop:
            with self.assertRaises(h.VerificationAccessDenied) as caught:
                h.verify(ROOT, 'plan.json', 'B', 'B', 'test.py',
                         'Checks.test_ok', stop_stream=object())
        self.assertIs(caught.exception.__cause__, self.denial)
        self.assertEqual(stop.call_count, 1)
        item = self.memory.data['tasks']['B']
        self.assertEqual(item['status'], 'STOP_REQUESTED')
        self.assertIsNone(item['ended_at'])
        self.assertTrue(item['access_denial']['retry_prohibited'])
        self.assertTrue(item['access_denial']['stop_outcome_unknown'])
        self.assertTrue(item['scratch'])
        self.assertEqual(h.status(ROOT, 'plan.json')['tasks']['B']['status'], 'AMBIGUOUS')
        with self.assertRaisesRegex(ValueError, 'stopped'):
            self.memory.verify('B')
        with self.assertRaisesRegex(ValueError, 'stopped'):
            h.reconcile(ROOT, 'plan.json', 'B', 'B', effects_reviewed=True)
        self.run.assert_not_called()

    def test_initial_state_write_denial_is_not_retried(self):
        """A failed initial write never launches a verifier or repeats persistence."""
        with patch.object(h._STATE, 'write', side_effect=self.denial) as write:
            with self.assertRaisesRegex(h.VerificationAccessDenied, 'not confirmed') as caught:
                self.memory.verify('B')
        self.assertIs(caught.exception.__cause__, self.denial)
        self.assertEqual(write.call_count, 1)
        self.run.assert_not_called()
        self.assertEqual(self.memory.data['tasks']['B']['status'], 'ACTIVE')

    def test_failed_denial_persistence_preserves_original_exception_and_uncertainty(self):
        """Recording failure does not misreport persistence or mask the first denial."""
        calls = []
        def write(root, raw, value):
            calls.append(value['tasks']['B']['status'])
            if len(calls) == 2:
                raise OSError('synthetic persistence failure')
            return self.memory.write(root, raw, value)
        with patch.object(h._STATE, 'write', side_effect=write):
            with self.assertRaisesRegex(h.VerificationAccessDenied, 'not confirmed') as caught:
                self.memory.verify('B')
        self.assertIs(caught.exception.__cause__, self.denial)
        self.assertEqual(calls, ['VERIFYING', 'AMBIGUOUS'])
        self.assertEqual(self.memory.data['tasks']['B']['status'], 'VERIFYING')
        # Reload the last durable state: its lost denial marker must not make
        # an attempted, unexplained verifier eligible for replay.
        self.assertNotIn('access_denial', self.memory.data['tasks']['B'])
        self.assertEqual(h.status(ROOT, 'plan.json')['tasks']['B']['status'], 'AMBIGUOUS')
        with patch.object(h._CORE, '_path', return_value=(ROOT / 'missing.json', False)):
            self.assertEqual(self.memory.review()['status'], 'AMBIGUOUS')
        # A report may already exist when a later host refusal cannot be saved.
        # Even this late success is not evidence that replay is authorized.
        late = {'success': True, 'tests_run': 1, 'access_denied': False}
        with patch.object(h._CORE, '_path', return_value=(ROOT / 'late.json', True)), \
             patch.object(h, '_report', return_value=(late, 'hash', 'identity')):
            self.assertEqual(self.memory.review()['status'], 'AMBIGUOUS')
        with self.assertRaisesRegex(ValueError, 'requires effect review'):
            self.memory.verify('B')
        with self.assertRaisesRegex(ValueError, 'another task'):
            self.memory.verify('A')
        self.assertEqual(self.run.call_count, 1)

    def test_attempt_without_scratch_or_report_keeps_unknown_outcome(self):
        """Missing metadata is not evidence that an attempted verifier is safe."""
        item = self.memory.data['tasks']['B']
        item.update(status='AMBIGUOUS', attempts=1, scratch=None)
        self.assertEqual(self.memory.review()['status'], 'AMBIGUOUS')
        self.run.assert_not_called()

    def test_unrecognized_report_does_not_reopen_attempt(self):
        """An unreadable or invalid report cannot explain an interrupted run."""
        item = self.memory.data['tasks']['B']
        item.update(status='AMBIGUOUS', attempts=1, scratch='_temp-files/unused-denial')
        with patch.object(h._CORE, '_path', return_value=(ROOT / 'invalid.json', True)), \
             patch.object(h, '_report', return_value=None):
            self.assertEqual(self.memory.review()['status'], 'AMBIGUOUS')
        self.run.assert_not_called()

    def test_late_non_denial_reports_never_authorize_replay(self):
        """Success, zero tests and failure cannot prove the missing host outcome."""
        for success, count in ((True, 1), (False, 0), (False, 1)):
            with self.subTest(success=success, tests_run=count):
                report = {'success': success, 'tests_run': count, 'access_denied': False}
                self.memory.data['tasks']['B'].update(
                    status='AMBIGUOUS', attempts=1, scratch='_temp-files/unused-denial')
                with patch.object(h._CORE, '_path', return_value=(ROOT / 'late.json', True)), \
                     patch.object(h, '_report', return_value=(report, 'hash', 'identity')):
                    self.assertEqual(self.memory.review()['status'], 'AMBIGUOUS')
                item = self.memory.data['tasks']['B']
                self.assertEqual(item['verification']['report'], report)
                self.assertFalse(item['verification']['replay_authorized'])
                with self.assertRaisesRegex(ValueError, 'requires effect review'):
                    self.memory.verify('B')
        self.run.assert_not_called()

    def test_late_denial_report_retains_slot_and_prohibits_replay(self):
        """A late denial cannot prove child reaping or release the executor."""
        report = {'success': False, 'tests_run': 1, 'access_denied': True}
        self.memory.data['tasks']['B'].update(
            status='AMBIGUOUS', attempts=1, scratch='_temp-files/unused-denial')
        with patch.object(h._CORE, '_path', return_value=(ROOT / 'late.json', True)), \
             patch.object(h, '_report', return_value=(report, 'hash', 'identity')):
            self.assertEqual(self.memory.review()['status'], 'AMBIGUOUS')
        item = self.memory.data['tasks']['B']
        self.assertTrue(item['access_denial']['retry_prohibited'])
        self.assertTrue(item['access_denial']['child_outcome_unknown'])
        with self.assertRaisesRegex(ValueError, 'access denial'):
            h.reconcile(ROOT, 'plan.json', 'B', 'B')
        with self.assertRaisesRegex(ValueError, 'another task'):
            self.memory.verify('A')
        with self.assertRaisesRegex(ValueError, 'requires effect review'):
            self.memory.verify('B')
        self.run.assert_not_called()

    def test_stop_state_write_denial_is_not_retried(self):
        """A failed STOP write is never retried under a different status."""
        calls = []
        def write(root, raw, value):
            calls.append(value['tasks']['B']['status'])
            if len(calls) == 2:
                raise self.denial
            return self.memory.write(root, raw, value)
        def interruptible(command, root, env, timeout, stream, persist_stop):
            persist_stop()
        with patch.object(h._STATE, 'write', side_effect=write), patch.object(h, '_interruptible', side_effect=interruptible):
            with self.assertRaisesRegex(h.VerificationAccessDenied, 'not confirmed'):
                h.verify(ROOT, 'plan.json', 'B', 'B', 'test.py', 'Checks.test_ok', stop_stream=object())
        self.assertEqual(calls, ['VERIFYING', 'STOP_REQUESTED'])
        self.run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
