"""Rule-sensitive acceptance freshness without starting a verifier or a process."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('policy_fingerprint', ROOT / 'scripts/harness_5b7e9c31.py')
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


class PolicyFingerprint(unittest.TestCase):
    """Read actual bundled rules and separately exercise synthetic freshness."""

    def test_manifest_covers_bundled_governing_references(self):
        """New references require an explicit decision to include their bytes."""
        expected = {'SKILL.md', 'AGENTS.md'} | {
            p.relative_to(ROOT).as_posix() for p in (ROOT / 'references').glob('*.md')}
        self.assertEqual(expected, set(harness._POLICY_FILES))
        observed = harness._tool_snapshot()
        for name in expected:
            self.assertEqual(observed[name], harness._CORE._read_hash(ROOT, name))
            self.assertIsNotNone(observed[name])

    def test_each_rule_change_invalidates_evidence(self):
        """Actual snapshot generation sees each changed byte hash; no file is edited."""
        baseline = harness._tool_snapshot()
        card = {'depends_on': []}
        for name in harness._POLICY_FILES:
            with self.subTest(rule=name):
                task = {'status': 'VERIFIED', 'card_sha256': harness._digest(card),
                        'test': 'test.py', 'verified_files': {}, 'verified_tools': baseline}
                def changed_hash(root, relative):
                    return 'f' * 64 if relative == name else baseline[relative]
                with patch.object(harness, '_snapshot', return_value={}), \
                        patch.object(harness._CORE, '_read_hash', side_effect=changed_hash):
                    data = {'tasks': {'piece': task}}
                    self.assertEqual(harness._refresh(ROOT, data, {'piece': card}), ['piece'])
                    self.assertEqual(task['status'], 'STALE')

    def test_unchanged_rules_preserve_freshness(self):
        """A matching explicit rule snapshot does not invalidate valid metadata."""
        card = {'depends_on': []}
        task = {'status': 'VERIFIED', 'card_sha256': harness._digest(card),
                'test': 'test.py', 'verified_files': {},
                'verified_tools': harness._tool_snapshot()}
        with patch.object(harness, '_snapshot', return_value={}):
            self.assertTrue(harness._fresh(ROOT, {'piece': task}, {'piece': card}, 'piece'))


if __name__ == '__main__':
    unittest.main()
