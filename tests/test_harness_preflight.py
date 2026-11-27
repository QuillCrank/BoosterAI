"""Real local package/plan/state preflight; no acceptance runner or Codex host."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from repo_temp import workspace
SPEC = importlib.util.spec_from_file_location('preflight_harness', ROOT / 'scripts/harness_5b7e9c31.py')
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)


@unittest.skipUnless(sys.version_info.releaselevel == 'final', 'preflight rejects preview interpreters')
class CombinedPreflight(unittest.TestCase):
    def setUp(self):
        self.root = workspace(self)
        (self.root / 'code.py').write_text('value = 1\n', encoding='utf-8')
        (self.root / 'test.py').write_text('# Never executed by this test\n', encoding='utf-8')
        card = {'id': 'slice', 'owner': 'test-owner', 'contract': 'Produce a value',
                'inputs': ['source'], 'outputs': ['value'], 'read_files': ['code.py'],
                'file_owners': {'code.py': 'test-owner'}, 'depends_on': [],
                'acceptance': 'value equals expected',
                'real_test': {'target': 'local fixture', 'action': 'inspect value', 'expected': 'expected value'}}
        (self.root / 'plan.json').write_text(json.dumps({'revision': 'fixture', 'nodes': [
            {'id': 'node', 'depends_on': [], 'slices': [card]}]}), encoding='utf-8')
        self.requested = '.'.join(map(str, sys.version_info[:2]))

    def observe(self):
        return h.preflight(self.root, 'plan.json', self.requested)

    def register(self):
        return h.start(self.root, 'plan.json', 'slice', 'test.py', 'Checks.test_ok', 3, 30)

    def mutate_state(self, change):
        with h._STATE.locked(self.root):
            data, raw = h._STATE.read(self.root)
            change(data)
            h._STATE.write(self.root, raw, data)

    def test_ready_reads_actual_package_and_graph_without_task_state(self):
        result = self.observe()
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['card_count'], 1)
        self.assertTrue(result['package_valid'])
        self.assertTrue(result['runtime']['environment_verified'])
        for name in ('state_present', 'state_modified', 'task_start_authorized',
                     'product_acceptance_verified', 'codex_host_verified', 'automatic_hook_verified'):
            self.assertFalse(result[name])
        self.assertFalse((self.root / h._STATE.STATE).exists())

    def test_active_and_stopped_state_block_new_work_and_are_preserved(self):
        self.register()
        path = self.root / h._STATE.STATE
        before = path.read_bytes()
        result = self.observe()
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['active_tasks'], ['slice'])
        self.assertEqual(path.read_bytes(), before)
        self.mutate_state(lambda data: data.update(stopped=True))
        self.assertIn('project_stopped', self.observe()['blocked_reasons'])

    def test_stale_observation_does_not_write_state(self):
        self.register()
        _, cards = h._source(self.root, 'plan.json')
        def accepted(data):
            data['tasks']['slice'].update(status='VERIFIED',
                verified_files=h._snapshot(self.root, cards['slice'], 'test.py'),
                verified_tools=h._tool_snapshot())
        self.mutate_state(accepted)
        (self.root / 'code.py').write_text('value = 2\n', encoding='utf-8')
        path = self.root / h._STATE.STATE
        before = path.read_bytes()
        result = self.observe()
        self.assertEqual(result['stale_observed'], ['slice'])
        self.assertEqual(path.read_bytes(), before)
        # VERIFIED state here is synthetic metadata, not a passed acceptance test.

    def test_unknown_state_and_changed_contract_require_review(self):
        self.register()
        self.mutate_state(lambda data: data['tasks']['slice'].update(status='UNKNOWN'))
        self.assertIn('unrecognized_task_state', self.observe()['blocked_reasons'])
        self.mutate_state(lambda data: data['tasks']['slice'].update(card_sha256='changed'))
        self.assertIn('task_contract_review_required', self.observe()['blocked_reasons'])

    def test_malformed_task_record_returns_not_run_and_preserves_bytes(self):
        self.register()
        path = self.root / h._STATE.STATE
        data = json.loads(path.read_text(encoding='utf-8'))
        data['tasks']['slice'] = None
        path.write_text(json.dumps(data), encoding='utf-8')
        before = path.read_bytes()
        with patch.object(h.sys, 'stdout', io.StringIO()) as output:
            code = h.main(['--root', str(self.root), '--source', 'plan.json', 'preflight',
                           '--requested-python', self.requested])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())['status'], 'NOT_RUN')
        self.assertEqual(path.read_bytes(), before)

    def test_cli_blocked_is_nonzero_without_starting_verifier(self):
        self.register()
        with patch.object(h.sys, 'stdout', io.StringIO()) as output:
            code = h.main(['--root', str(self.root), '--source', 'plan.json', 'preflight',
                           '--requested-python', self.requested])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())['status'], 'BLOCKED')


if __name__ == '__main__':
    unittest.main()
