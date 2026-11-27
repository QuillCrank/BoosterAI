"""In-memory regression of a reviewed, never-verified dependent yielding its slot.

These tests exercise public state transitions, not real-target acceptance or
process-liveness detection. No verifier, subprocess or filesystem fixture runs.
"""
import contextlib
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('waiting_harness', ROOT / 'scripts/harness_5b7e9c31.py')
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)


class ReachedVerifierGate(Exception):
    """Stop before creating any scratch directory or launching a verifier."""


class WaitingPrerequisiteTests(unittest.TestCase):
    def setUp(self):
        """Keep state and observed bytes in memory while using public APIs."""
        self.cards = {'A': {'depends_on': [], 'file_owners': ['a.py']},
                      'B': {'depends_on': ['A'], 'file_owners': ['b.py']}}
        self.files = {'A': {'a.py': 'new', 'test.py': 'test'},
                      'B': {'b.py': 'b', 'test.py': 'test'}}
        self.tools = {'tool': 'same'}
        self.data = {'format': h._STATE.FORMAT, 'source': 'plan.json',
                     'tasks': {}, 'stopped': False}
        for name, state in (('A', 'VERIFIED'), ('B', 'ACTIVE')):
            self.data['tasks'][name] = {
                'status': state, 'run_id': name, 'card_sha256': h._digest(self.cards[name]),
                'test': 'test.py', 'case': 'Checks.test_ok', 'attempts': 1 if name == 'A' else 0,
                'max_attempts': 5, 'started_at': h._now(), 'started_files': self.files[name].copy(),
                'verified_files': {'a.py': 'old', 'test.py': 'test'} if name == 'A' else None,
                'verified_tools': self.tools.copy(), 'scratch': None,
                'verification': None, 'retry_requires_change': False}
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for obj, name, value in (
                (h._STATE, 'locked', lambda *args: contextlib.nullcontext(ROOT)),
                (h._STATE, 'read', lambda root: (copy.deepcopy(self.data), b'old')),
                (h._STATE, 'write', self.write),
                (h, '_source', lambda *args: ({}, self.cards)),
                (h, '_snapshot', self.snapshot),
                (h, '_tool_snapshot', lambda: self.tools.copy())):
            self.stack.enter_context(patch.object(obj, name, value))
        self.stack.enter_context(patch.object(h._TOOLS, 'scratch_directory',
                                             side_effect=ReachedVerifierGate))

    def write(self, root, raw, value):
        self.data.clear()
        self.data.update(copy.deepcopy(value))

    def snapshot(self, root, card, test):
        return self.files['B' if card is self.cards['B'] else 'A'].copy()

    def verify(self, name):
        return h.verify(ROOT, 'plan.json', name, name, 'test.py', 'Checks.test_ok')

    def review(self):
        h.reconcile(ROOT, 'plan.json', 'B', 'B')
        return h.reconcile(ROOT, 'plan.json', 'B', 'B', effects_reviewed=True)

    def test_reviewed_unattempted_dependent_releases_slot(self):
        """Unchanged two-step review permits prerequisite repair, not stale acceptance."""
        self.assertEqual(h.status(ROOT, 'plan.json')['tasks']['A']['status'], 'STALE')
        with self.assertRaisesRegex(ValueError, 'another task is active'):
            self.verify('A')
        with self.assertRaisesRegex(ValueError, 'prerequisite lacks'):
            self.verify('B')
        self.assertEqual(self.review()['status'], 'WAITING_PREREQUISITE')
        with self.assertRaises(ReachedVerifierGate):
            self.verify('A')
        with self.assertRaisesRegex(ValueError, 'prerequisite lacks'):
            self.verify('B')
        # Model successful prerequisite verification without pretending to run it.
        self.data['tasks']['A'].update(status='VERIFIED', verified_files=self.files['A'].copy())
        with self.assertRaises(ReachedVerifierGate):
            self.verify('B')
        self.assertEqual(self.data['tasks']['B']['run_id'], 'B')
        self.assertEqual(self.data['tasks']['B']['attempts'], 0)

    def test_observation_alone_keeps_slot_and_changed_effects_block_review(self):
        h.reconcile(ROOT, 'plan.json', 'B', 'B')
        self.assertEqual(self.data['tasks']['B']['status'], 'AMBIGUOUS')
        with self.assertRaisesRegex(ValueError, 'another task is active'):
            self.verify('A')
        self.files['B']['b.py'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'effects changed'):
            h.reconcile(ROOT, 'plan.json', 'B', 'B', effects_reviewed=True)

    def test_attempted_or_uncertain_verifier_never_yields_slot(self):
        for field, value in (('attempts', 1), ('scratch', 'some-operation'),
                             ('verification', {'uncertain': True}), ('retry_requires_change', True)):
            with self.subTest(field=field):
                original = copy.deepcopy(self.data)
                self.data['tasks']['B'][field] = value
                # No scratch report read should be necessary for this test.
                with patch.object(h._CORE, '_path', return_value=(ROOT / 'unused', False)):
                    expected = 'AMBIGUOUS' if field == 'attempts' else 'ACTIVE'
                    self.assertEqual(self.review()['status'], expected)
                self.data = original

    def test_stop_denial_and_stale_contract_are_not_resumed(self):
        for state in ('DENIED', 'STOPPED', 'STALE'):
            with self.subTest(state=state):
                self.data['tasks']['B']['status'] = state
                with self.assertRaisesRegex(ValueError, 'not recoverable'):
                    h.reconcile(ROOT, 'plan.json', 'B', 'B')
        self.data['tasks']['B']['status'] = 'ACTIVE'
        self.data['stopped'] = True
        with self.assertRaisesRegex(ValueError, 'harness is stopped'):
            h.reconcile(ROOT, 'plan.json', 'B', 'B')
        self.data['stopped'] = False
        self.cards['B']['file_owners'] = ['different.py']
        with self.assertRaisesRegex(ValueError, 'contract changed'):
            h.reconcile(ROOT, 'plan.json', 'B', 'B')

    def test_waiting_keeps_run_and_test_identity_gates(self):
        self.assertEqual(self.review()['status'], 'WAITING_PREREQUISITE')
        self.data['tasks']['A']['verified_files'] = self.files['A'].copy()
        for run_id, test, case, message in (
                ('wrong', 'test.py', 'Checks.test_ok', 'run'),
                ('B', 'other.py', 'Checks.test_ok', 'test differs'),
                ('B', 'test.py', 'Checks.test_other', 'test differs')):
            with self.subTest(run_id=run_id, test=test, case=case):
                with self.assertRaisesRegex(ValueError, message):
                    h.verify(ROOT, 'plan.json', 'B', run_id, test, case)
                self.assertEqual(self.data['tasks']['B']['status'], 'WAITING_PREREQUISITE')
                self.assertEqual(self.data['tasks']['B']['attempts'], 0)

    def test_waiting_preserves_attempt_elapsed_and_stop_barriers(self):
        self.assertEqual(self.review()['status'], 'WAITING_PREREQUISITE')
        self.data['tasks']['A']['verified_files'] = self.files['A'].copy()
        baseline = copy.deepcopy(self.data)
        for limit, value, message in (
                ('attempts', 5, 'attempt limit'),
                ('started_at', '2000-01-01T00:00:00+00:00', 'elapsed task limit'),
                ('stopped', True, 'harness is stopped')):
            with self.subTest(limit=limit):
                self.data = copy.deepcopy(baseline)
                if limit == 'stopped':
                    self.data[limit] = value
                else:
                    self.data['tasks']['B'][limit] = value
                before = copy.deepcopy(self.data)
                with self.assertRaisesRegex(ValueError, message):
                    self.verify('B')
                self.assertEqual(self.data, before)

    def test_fresh_prerequisite_keeps_reviewed_dependent_active(self):
        self.data['tasks']['A']['verified_files'] = self.files['A'].copy()
        self.assertEqual(self.review()['status'], 'ACTIVE')


if __name__ == '__main__':
    unittest.main()
