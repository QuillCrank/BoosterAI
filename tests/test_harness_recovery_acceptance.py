"""Real package self-check with an opt-in abrupt child-interruption scenario."""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RecoveryAcceptance(unittest.TestCase):
    """Check an actual CLI after an optional, explicitly requested interruption."""

    def test_package_self_check(self):
        """Exit the test child on the exact opt-in, otherwise assert self-check.

        The forced exit models an interruption before a result can be recorded;
        it performs no product action. Normal execution invokes the real package
        CLI and checks its structured result rather than only its exit status.
        """
        if os.environ.get('BOOSTERAI_TEST_INTERRUPT_CHILD') == '1':
            os._exit(23)
        result = subprocess.run([sys.executable, '-I', '-S', '-B',
                                 str(ROOT / 'scripts/booster.py'), 'self-check'],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0)
        response = json.loads(result.stdout)
        self.assertTrue(response['valid'])
        self.assertEqual(response['validator'], 'bundled-stdlib')
        self.assertFalse(response['agent_discovery_verified'])


if __name__ == '__main__':
    unittest.main()
