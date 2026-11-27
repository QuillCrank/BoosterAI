"""Exercise the production Harness start gate with project state held in memory."""

from contextlib import ExitStack, nullcontext
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch, sentinel


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'harness_5b7e9c31.py'
SPEC = importlib.util.spec_from_file_location('harness_pending_stop_admission', SCRIPT)
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)

SOURCE = 'plan.json'
TEST_PATH = 'tests/acceptance.py'


class CandidateSnapshotReached(Exception):
    """The candidate passed admission and reached its first file observation."""


class PendingStopAdmissionTests(unittest.TestCase):
    def setUp(self):
        card = {
            'owner': 'reviewer', 'contract': 'Inspect the declared input',
            'inputs': [], 'outputs': ['observation'], 'read_files': [TEST_PATH],
            'file_owners': {}, 'depends_on': [], 'acceptance': 'Observe the input',
            'real_test': {'target': TEST_PATH, 'action': 'inspect', 'expected': 'one observation'},
        }
        source = {'revision': 'admission-1', 'nodes': [{
            'id': 'review', 'depends_on': [],
            'slices': [dict(card, id=name) for name in ('peer', 'candidate')],
        }]}
        bundle = HARNESS._CARDS.generate_cards(source)
        self.cards = {item['id']: item for item in bundle['cards']}

        patches = ExitStack()
        self.addCleanup(patches.close)
        patches.enter_context(patch.object(
            HARNESS._STATE, 'locked', return_value=nullcontext(sentinel.project)))
        patches.enter_context(patch.object(HARNESS, '_source', return_value=(bundle, self.cards)))
        self.read_state = patches.enter_context(patch.object(HARNESS._STATE, 'read'))
        self.snapshot = patches.enter_context(patch.object(
            HARNESS, '_snapshot', side_effect=CandidateSnapshotReached))
        for module, name in ((HARNESS, '_tool_snapshot'), (HARNESS._STATE, 'write'), (HARNESS, 'verify'),
                             (HARNESS.subprocess, 'run'), (HARNESS.subprocess, 'Popen')):
            blocked = patches.enter_context(patch.object(module, name, side_effect=AssertionError(name)))
            self.addCleanup(blocked.assert_not_called)

    def start_candidate(self, peer_status):
        peer = {
            'run_id': '1' * 32, 'status': peer_status,
            'card_sha256': HARNESS._digest(self.cards['peer']),
        }
        state = {
            'format': HARNESS._STATE.FORMAT, 'source': SOURCE,
            'stopped': False, 'tasks': {'peer': peer},
        }
        raw = json.dumps(state).encode('utf-8')
        self.read_state.return_value = (state, raw)
        self.addCleanup(self.assertEqual, state, json.loads(raw))

        HARNESS.start(sentinel.project, SOURCE, 'candidate', TEST_PATH, 'Acceptance.test_input',
                      attempts=1, timeout=10)

    def test_pending_stop_rejects_candidate_before_snapshot(self):
        with self.assertRaisesRegex(ValueError, r'\Aanother task is active or ambiguous\Z'):
            self.start_candidate('STOP_REQUESTED')
        self.snapshot.assert_not_called()

    def test_stale_peer_allows_candidate_to_reach_snapshot(self):
        with self.assertRaises(CandidateSnapshotReached):
            self.start_candidate('STALE')
        self.snapshot.assert_called_once_with(sentinel.project, self.cards['candidate'], TEST_PATH)


if __name__ == '__main__':
    unittest.main()
