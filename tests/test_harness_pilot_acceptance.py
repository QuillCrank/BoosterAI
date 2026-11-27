"""Exercise the real plan CLI's handling of duplicate JSON object keys."""

import json
from pathlib import Path
import subprocess
import sys
import unittest

from repo_temp import workspace


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scripts' / 'booster.py'


class PlanJsonAcceptance(unittest.TestCase):
    """Check actual CLI output for a valid plan and ambiguous input documents."""

    def test_valid_and_duplicate_keys(self):
        """Reject repeated top-level and nested keys without changing inputs.

        Invoke the production plan validator in the current Python environment.
        The valid plan must be accepted, whereas both different duplicate-key
        locations must produce a nonzero result with an explanatory JSON error.
        """
        root = workspace(self)
        plan = {'revision': 'r1', 'goal': 'observable result', 'next': 'a',
                'nodes': [{'id': 'a', 'owner': 'root', 'depends_on': [],
                           'status': 'planned', 'acceptance': 'visible result',
                           'target': 'terminal', 'revision': 'r1'}]}
        valid = json.dumps(plan)
        cases = [('valid', valid, True),
                 ('duplicate_root', '{"revision":"old",' + valid[1:], False),
                 ('duplicate_node', valid.replace('"id": "a"',
                                                  '"id": "old", "id": "a"'), False)]
        for name, source, accepted in cases:
            with self.subTest(name=name):
                path = root / (name + '.json')
                original = source.encode('utf-8')
                path.write_bytes(original)
                result = subprocess.run([sys.executable, '-I', '-S', '-B', str(CLI),
                                         'validate', str(path)], cwd=ROOT,
                                        capture_output=True, text=True, timeout=20)
                response = json.loads(result.stdout)
                if accepted:
                    self.assertEqual(result.returncode, 0)
                    self.assertTrue(response['valid'])
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('duplicate', response['error'].lower())
                self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
