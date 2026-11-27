"""Exercise STOP and reviewed retry through actual harness child processes."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

from repo_temp import workspace


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / 'scripts/harness_5b7e9c31.py'


class RealHarnessBoundaryTests(unittest.TestCase):
    """Use owned local fixtures; these tests do not model external services."""

    def setUp(self):
        """Prepare a project-local task and register child cleanup first."""
        self.root = workspace(self)
        self.children = []
        self.addCleanup(self.finish_children)
        (self.root / '_temp-files').mkdir()
        (self.root / 'product.py').write_text('def value():\n    return 1\n', encoding='utf-8')
        (self.root / 'acceptance.py').write_text(
            'import os, time, unittest\n'
            'from pathlib import Path\n'
            'import product\n'
            'scratch = Path(__file__).parent / "_temp-files"\n'
            'class Acceptance(unittest.TestCase):\n'
            '    def test_retry(self):\n'
            '        count = scratch / "effect-count.txt"\n'
            '        count.write_text(str(int(count.read_text()) + 1) if count.exists() else "1")\n'
            '        self.assertEqual(product.value(), 2)\n'
            '    def test_stop(self):\n'
            '        (scratch / "started.txt").write_text("started")\n'
            '        time.sleep(30)\n'
            '        (scratch / "finished.txt").write_text("finished")\n'
            '    def test_denied(self):\n'
            '        count = scratch / "denied-count.txt"\n'
            '        count.write_text(str(int(count.read_text()) + 1) if count.exists() else "1")\n'
            '        raise PermissionError("synthetic fixture denial")\n',
            encoding='utf-8')
        source = {'revision': 'real-boundaries-1', 'nodes': [{
            'id': 'one-node', 'depends_on': [], 'slices': [{
                'id': 'one-task', 'owner': 'owner', 'contract': 'Run the selected acceptance case',
                'inputs': ['Declared fixture code'], 'outputs': ['Observed acceptance result'],
                'read_files': ['product.py', 'acceptance.py'],
                'file_owners': {'product.py': 'owner'}, 'depends_on': [],
                'acceptance': 'The selected case passes in the actual Python runtime',
                'real_test': {'target': 'Owned local project', 'action': 'Run the selected case',
                              'expected': 'Expected result or a safely stopped attempt'},
            }]}]}
        (self.root / 'plan.json').write_text(json.dumps(source), encoding='utf-8')
        self.environment = os.environ.copy()
        self.environment['BOOSTERAI_SCRATCH'] = '_temp-files'
        for name in ('TEMP', 'TMP', 'TMPDIR'):
            self.environment[name] = str(self.root / '_temp-files')

    def finish_children(self):
        """Request STOP and reap only this test's own verifier processes."""
        for child in self.children:
            if child.poll() is None:
                child.communicate(input='STOP\n', timeout=15)

    def command(self, *arguments):
        """Build an explicit harness CLI invocation without shell interpretation."""
        return [sys.executable, '-I', '-S', '-B', str(HARNESS), '--root', str(self.root),
                '--source', 'plan.json', *arguments]

    def invoke(self, *arguments):
        """Run one bounded actual CLI request and decode its structured result."""
        child = subprocess.run(self.command(*arguments), cwd=self.root, env=self.environment,
                               capture_output=True, text=True, timeout=30)
        return child.returncode, json.loads(child.stdout)

    def start(self, case):
        """Register the exact fixture case and return its issued run identifier."""
        code, result = self.invoke('start', '--task', 'one-task', '--test', 'acceptance.py',
                                   '--case', case, '--max-attempts', '3', '--timeout', '45')
        self.assertEqual(code, 0, result)
        return result['run_id']

    def verify_args(self, run_id, case):
        """Return explicit verify arguments for the already registered fixture."""
        return ('verify', '--task', 'one-task', '--run-id', run_id,
                '--test', 'acceptance.py', '--case', case)

    @unittest.skipUnless(sys.version_info.releaselevel == 'final',
                         'combined startup rejects preview interpreters')
    def test_combined_startup_real_acceptance_and_stale_code_failure(self):
        """Exercise real CLI/child results, not a Codex host or external target."""
        preflight = ['preflight']
        if sys.version_info < (3, 12):
            # Explicit compatibility-test override; not an inferred user grant.
            preflight += ['--requested-python', '.'.join(map(str, sys.version_info[:2]))]
        code, ready = self.invoke(*preflight)
        self.assertEqual(code, 0, ready)
        self.assertEqual(ready['status'], 'READY')
        self.assertFalse(ready['automatic_hook_verified'])
        self.assertFalse(ready['codex_host_verified'])
        product = self.root / 'product.py'
        product.write_text('def value():\n    return 2\n', encoding='utf-8')
        case = 'Acceptance.test_retry'
        run_id = self.start(case)
        code, blocked = self.invoke(*preflight)
        self.assertEqual(code, 1, blocked)
        self.assertEqual(blocked['active_tasks'], ['one-task'])
        code, accepted = self.invoke(*self.verify_args(run_id, case))
        self.assertEqual(code, 0, accepted)
        self.assertEqual(accepted['status'], 'VERIFIED')
        self.assertEqual(accepted['verification']['report']['tests_run'], 1)
        self.assertTrue(accepted['verification']['report']['success'])
        self.assertEqual((self.root / '_temp-files/effect-count.txt').read_text(), '1')
        product.write_text('def value():\n    return 3\n', encoding='utf-8')
        state = self.root / '_boosterAI/harness-state.json'
        before = state.read_bytes()
        code, changed = self.invoke(*preflight)
        self.assertEqual(code, 0, changed)
        self.assertEqual(changed['stale_observed'], ['one-task'])
        self.assertEqual(state.read_bytes(), before)
        code, rejected = self.invoke(*self.verify_args(run_id, case))
        self.assertEqual(code, 1, rejected)
        self.assertEqual(rejected['status'], 'FAILED')
        self.assertFalse(rejected['verification']['report']['success'])
        self.assertEqual(rejected['verification']['report']['tests_run'], 1)
        self.assertEqual(rejected['verification']['report']['failures'], 1)
        self.assertEqual(rejected['verification']['report']['errors'], 0)
        self.assertEqual((self.root / '_temp-files/effect-count.txt').read_text(), '2')

    def test_failed_effect_is_reviewed_and_unchanged_retry_is_blocked(self):
        """A failed real test cannot repeat its side effect until review and code change."""
        case = 'Acceptance.test_retry'
        run_id = self.start(case)
        code, failed = self.invoke(*self.verify_args(run_id, case))
        self.assertEqual((code, failed['status']), (1, 'FAILED'))
        effect = self.root / '_temp-files/effect-count.txt'
        self.assertEqual(effect.read_text(), '1')
        code, blocked = self.invoke(*self.verify_args(run_id, case))
        self.assertEqual((code, blocked['status']), (2, 'NOT_RUN'))
        self.assertEqual(effect.read_text(), '1')
        args = ('reconcile', '--task', 'one-task', '--run-id', run_id)
        _, observed = self.invoke(*args)
        self.assertFalse(observed['host_review_declared'])
        _, reviewed = self.invoke(*args, '--effects-reviewed')
        self.assertTrue(reviewed['host_review_declared'])
        code, unchanged = self.invoke(*self.verify_args(run_id, case))
        self.assertEqual((code, unchanged['status']), (2, 'NOT_RUN'))
        self.assertEqual(effect.read_text(), '1')
        (self.root / 'product.py').write_text('def value():\n    return 2\n', encoding='utf-8')
        code, passed = self.invoke(*self.verify_args(run_id, case))
        self.assertEqual((code, passed['status']), (0, 'VERIFIED'))
        self.assertEqual(effect.read_text(), '2')
        self.assertEqual([entry['outcome'] for entry in passed['attempt_history']],
                         ['FAILED', 'VERIFIED'])

    def test_stop_line_interrupts_owned_running_child_and_blocks_restart(self):
        """An explicit host STOP line stops the direct child and persists the barrier."""
        case = 'Acceptance.test_stop'
        run_id = self.start(case)
        child = subprocess.Popen(self.command(*self.verify_args(run_id, case), '--listen-stop'),
                                 cwd=self.root, env=self.environment, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(child)
        marker = self.root / '_temp-files/started.txt'
        deadline = time.monotonic() + 15
        while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not marker.exists():
            output, errors = child.communicate(input='STOP\n', timeout=15)
            self.fail('selected child did not start; verifier output: ' + output + errors)
        output, errors = child.communicate(input='STOP\n', timeout=15)
        result = json.loads(output)
        self.assertEqual((child.returncode, result['status']), (130, 'STOPPED'), errors)
        self.assertFalse((self.root / '_temp-files/finished.txt').exists())
        code, state = self.invoke('status')
        self.assertEqual(code, 0)
        self.assertTrue(state['stopped'])
        code, blocked = self.invoke(*self.verify_args(run_id, case))
        self.assertEqual((code, blocked['status']), (2, 'NOT_RUN'))

    def test_synthetic_permission_error_blocks_retry_in_actual_harness(self):
        """A fixture-raised denial is classified without retrying real refused access."""
        case = 'Acceptance.test_denied'
        run_id = self.start(case)
        code, denied = self.invoke(*self.verify_args(run_id, case))
        self.assertEqual((code, denied['status']), (1, 'DENIED'))
        self.assertTrue(denied['verification']['report']['access_denied'])
        code, review = self.invoke('reconcile', '--task', 'one-task', '--run-id', run_id)
        self.assertEqual((code, review['status']), (2, 'NOT_RUN'))
        code, retry = self.invoke(*self.verify_args(run_id, case))
        self.assertEqual((code, retry['status']), (2, 'NOT_RUN'))
        self.assertEqual((self.root / '_temp-files/denied-count.txt').read_text(), '1')

    def test_stop_listener_does_not_hold_open_a_completed_verifier(self):
        """A successful verifier exits even while its control input remains open."""
        (self.root / 'product.py').write_text('def value():\n    return 2\n', encoding='utf-8')
        case = 'Acceptance.test_retry'
        run_id = self.start(case)
        output_path = self.root / '_temp-files/completed-output.json'
        with output_path.open('w+', encoding='utf-8') as output:
            child = subprocess.Popen(self.command(*self.verify_args(run_id, case), '--listen-stop'),
                                     cwd=self.root, env=self.environment, stdin=subprocess.PIPE,
                                     stdout=output, stderr=subprocess.DEVNULL, text=True)
            self.children.append(child)
            child.wait(timeout=15)
            output.seek(0)
            result = json.load(output)
            child.stdin.close()
        self.assertEqual((child.returncode, result['status']), (0, 'VERIFIED'))


if __name__ == '__main__':
    unittest.main()
