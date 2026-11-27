"""Exercise the Harness CLI path guard without opening a project or running a test case."""

from contextlib import ExitStack, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'harness_5b7e9c31.py'
SPEC = importlib.util.spec_from_file_location('harness_partial_path_guard', SCRIPT)
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


class HarnessPartialPathGuardTest(unittest.TestCase):
    def invoke(self, command, test_path):
        args = ['--root', 'unopened-project', '--source', 'unused-plan.json', command,
                '--task', 'task', '--test', test_path, '--case', 'Example.test_acceptance']
        if command == 'verify':
            args.extend(['--run-id', '0' * 32])
        with redirect_stdout(io.StringIO()) as output:
            code = HARNESS.main(args)
        return code, json.loads(output.getvalue())

    def test_protected_paths_are_rejected_before_any_work(self):
        protected_paths = (
            '_partials',
            '_boosterAI/_partials',
            '_partials/test_example.py',
            '_PARTIALS/nested/test_example.py',
            '_boosterAI/_partials/test_example.py',
            '_BOOSTERAI/_PARTIALS/nested/test_example.py',
        )
        boundaries = (
            (HARNESS._CORE, '_root'),
            (HARNESS, '_source'),
            (HARNESS._STATE, 'locked'),
            (HARNESS._TOOLS, 'scratch_directory'),
            (HARNESS.tempfile, 'mkdtemp'),
            (HARNESS.subprocess, 'run'),
            (HARNESS.subprocess, 'Popen'),
        )
        with ExitStack() as stack:
            blocked_calls = [stack.enter_context(patch.object(
                module, name, side_effect=AssertionError(f'unexpected call to {name}')))
                for module, name in boundaries]
            for command in ('start', 'verify'):
                for path in protected_paths:
                    with self.subTest(command=command, path=path):
                        code, result = self.invoke(command, path)
                        self.assertEqual(code, 2)
                        self.assertEqual(result, {
                            'error': 'acceptance test must be outside protected partials directories',
                            'status': 'NOT_RUN',
                            'reconciliation_required': False,
                        })
        for call in blocked_calls:
            call.assert_not_called()

    def test_normal_paths_reach_the_existing_entry_boundary(self):
        normal_paths = (
            'tests/test_example.py',
            '_partials_notes/test_example.py',
            '_boosterAI/_partials_notes/test_example.py',
            'nested/_partials/test_example.py',
        )
        for command, module, name in (
            ('start', HARNESS._STATE, 'locked'),
            ('verify', HARNESS._CORE, '_root'),
        ):
            for path in normal_paths:
                with self.subTest(command=command, path=path):
                    with patch.object(module, name, side_effect=ValueError('entry boundary')) as boundary:
                        code, result = self.invoke(command, path)
                    boundary.assert_called_once_with('unopened-project')
                    self.assertEqual(code, 2)
                    self.assertEqual(result['error'], 'entry boundary')


if __name__ == '__main__':
    unittest.main()
