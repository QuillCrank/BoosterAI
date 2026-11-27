"""Check the documented freshness resource requirement without changing disk state."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'booster.py'
RESOURCE = 'scripts/project_index_freshness_a62e9d47.py'
SPEC = importlib.util.spec_from_file_location('booster_freshness_resource', SCRIPT)
BOOSTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOSTER)


class FreshnessResourceTest(unittest.TestCase):
    def test_self_check_rejects_missing_documented_helper(self):
        target = ROOT / RESOURCE
        self.assertTrue(target.is_file())
        original_is_file = Path.is_file
        target_checks = []

        def omit_only_target(path, *args, **kwargs):
            if path == target:
                target_checks.append(path)
                return False
            return original_is_file(path, *args, **kwargs)

        with (
            patch.object(Path, 'is_file', omit_only_target),
            patch.object(sys, 'argv', [str(SCRIPT), 'self-check']),
            redirect_stdout(io.StringIO()) as output,
        ):
            exit_code = BOOSTER.main()

        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(output.getvalue()), {'error': 'missing package file: ' + RESOURCE})
        self.assertEqual(target_checks, [target])


if __name__ == '__main__':
    unittest.main()
