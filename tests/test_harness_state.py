"""Acceptance checks for bounded, cooperative project-local harness state."""

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from repo_temp import workspace


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'scripts/harness_state_2fc47a91.py'
spec = importlib.util.spec_from_file_location('harness_state', SOURCE)
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)

CHILD = r'''
import importlib.util, sys
spec = importlib.util.spec_from_file_location('harness_state_child', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
try:
    with module.locked(sys.argv[2]):
        print('ACQUIRED')
except module.claim_guard.ClaimBusyError:
    print('BUSY')
    raise SystemExit(75)
'''


class HarnessStateTests(unittest.TestCase):
    def setUp(self):
        self.project = workspace(self)
        self.scratch = self.project / 'scratch'
        self.scratch.mkdir()
        environment = patch.dict(os.environ, {'BOOSTERAI_SCRATCH': str(self.scratch)})
        environment.start()
        self.addCleanup(environment.stop)

    def test_create_and_guarded_update_preserve_other_project_records(self):
        directory = self.project / '_boosterAI'
        directory.mkdir()
        other = directory / 'algorithm.md'
        other.write_bytes(b'Existing project algorithm\n')
        first = {'format': state.FORMAT, 'tasks': {'alpha': {'status': 'NOT_RUN'}}}
        second = {'format': state.FORMAT, 'tasks': {'alpha': {'status': 'VERIFIED'}}}
        with state.locked(self.project):
            self.assertEqual(state.read(self.project), (None, None))
            first_raw = state.write(self.project, None, first)
            self.assertEqual(state.read(self.project), (first, first_raw))
            second_raw = state.write(self.project, first_raw, second)
            self.assertEqual(state.read(self.project), (second, second_raw))
        self.assertNotEqual(first_raw, second_raw)
        self.assertEqual(other.read_bytes(), b'Existing project algorithm\n')

    def test_lock_creates_missing_workspace_and_read_does_not(self):
        self.assertEqual(state.read(self.project), (None, None))
        self.assertFalse((self.project / '_boosterAI').exists())
        with state.locked(self.project):
            self.assertTrue((self.project / '_boosterAI').is_dir())
            self.assertEqual(state.read(self.project), (None, None))

    def test_other_process_is_excluded_until_claim_is_released(self):
        command = [sys.executable, '-I', '-S', '-B', '-c', CHILD,
                   str(SOURCE), str(self.project)]
        with state.locked(self.project):
            busy = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertEqual((busy.returncode, busy.stdout.strip()), (75, 'BUSY'), busy.stderr)
        released = subprocess.run(command, capture_output=True, text=True, timeout=20)
        self.assertEqual((released.returncode, released.stdout.strip()), (0, 'ACQUIRED'),
                         released.stderr)

    def test_unrecognized_malformed_and_oversized_files_are_preserved(self):
        invalid = (
            b'{',
            b'{"format":"boosterai-harness-v1","nested":{"x":1,"x":2}}',
            b'{"format":"boosterai-harness-v1","value":NaN}',
            b'{"format":"boosterai-harness-v1","value":1e999}',
            b'{"format":"other"}',
            b'{"format":"boosterai-harness-v1","value":"\xff"}',
            b'x' * (state.MAX_BYTES + 1),
        )
        for index, content in enumerate(invalid):
            with self.subTest(index=index):
                project = self.project / str(index)
                project.mkdir()
                directory = project / '_boosterAI'
                directory.mkdir()
                path = directory / 'harness-state.json'
                path.write_bytes(content)
                before = path.stat()
                digest = hashlib.sha256(content).digest()
                with state.locked(project):
                    with self.assertRaises(ValueError):
                        state.read(project)
                    with self.assertRaises(ValueError):
                        state.write(project, None, {'format': state.FORMAT})
                after = path.stat()
                self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), digest)
                self.assertEqual((before.st_ino, before.st_size, before.st_mtime_ns),
                                 (after.st_ino, after.st_size, after.st_mtime_ns))

    def test_exact_size_limit_is_readable(self):
        prefix = b'{"format":"boosterai-harness-v1","value":"'
        suffix = b'"}'
        content = prefix + b'a' * (state.MAX_BYTES - len(prefix) - len(suffix)) + suffix
        with state.locked(self.project):
            path = self.project / state.STATE
            path.write_bytes(content)
            data, raw = state.read(self.project)
            self.assertEqual(len(raw), state.MAX_BYTES)
            self.assertEqual(data['format'], state.FORMAT)
            self.assertEqual(raw, content)

    def test_changed_expected_state_is_not_overwritten(self):
        first = {'format': state.FORMAT, 'status': 'first'}
        external = b'{"format":"boosterai-harness-v1","status":"external"}\n'
        with state.locked(self.project):
            original = state.write(self.project, None, first)
            path = self.project / state.STATE
            path.write_bytes(external)
            before = path.stat()
            with self.assertRaisesRegex(ValueError, 'changed'):
                state.write(self.project, original, {'format': state.FORMAT, 'status': 'new'})
            self.assertEqual(path.read_bytes(), external)
            self.assertEqual((path.stat().st_ino, path.stat().st_mtime_ns),
                             (before.st_ino, before.st_mtime_ns))

    def test_missing_or_unexpected_existing_state_cannot_be_overwritten(self):
        value = {'format': state.FORMAT}
        with state.locked(self.project):
            with self.assertRaisesRegex(ValueError, 'changed'):
                state.write(self.project, b'previous-but-now-missing', value)
            self.assertFalse((self.project / state.STATE).exists())
            raw = state.write(self.project, None, value)
            with self.assertRaisesRegex(ValueError, 'changed'):
                state.write(self.project, None, {'format': state.FORMAT, 'other': True})
            self.assertEqual(state.read(self.project), (value, raw))

    def test_reject_invalid_proposed_values_without_creating_a_state_file(self):
        values = ({'format': 'other'}, {'format': state.FORMAT, 1: 'bad key'},
                  {'format': state.FORMAT, 'value': float('nan')},
                  {'format': state.FORMAT, 'value': ('tuple',)},
                  {'format': state.FORMAT, 'large': 'a' * state.MAX_BYTES})
        with state.locked(self.project):
            for value in values:
                with self.subTest(value_type=type(value.get('value')).__name__):
                    with self.assertRaises(ValueError):
                        state.write(self.project, None, value)
                    self.assertFalse((self.project / state.STATE).exists())

    def test_first_write_fsync_error_leaves_file_for_reconciliation(self):
        value = {'format': state.FORMAT, 'run': 'observed'}
        with state.locked(self.project):
            with patch.object(state.os, 'fsync', side_effect=OSError('simulated sync failure')):
                with self.assertRaisesRegex(OSError, 'simulated sync failure'):
                    state.write(self.project, None, value)
            observed, raw = state.read(self.project)
            self.assertEqual(observed, value)
            with self.assertRaisesRegex(ValueError, 'changed'):
                state.write(self.project, None, value)
            self.assertEqual(state.read(self.project), (value, raw))

    def test_failed_update_preserves_prior_file_for_reconciliation(self):
        value = {'format': state.FORMAT, 'run': 'original'}
        with state.locked(self.project):
            raw = state.write(self.project, None, value)
            state.project_tools.scratch_directory(self.project)
            with patch.object(state.os, 'fsync', side_effect=OSError('simulated stage failure')):
                with self.assertRaisesRegex(OSError, 'simulated stage failure'):
                    state.write(self.project, raw, {'format': state.FORMAT, 'run': 'new'})
            self.assertEqual(state.read(self.project), (value, raw))
            self.assertFalse(any(item.name.startswith('.harness-state-op-')
                                 for item in self.scratch.iterdir()))

    def test_change_during_staging_is_detected_before_replacement(self):
        value = {'format': state.FORMAT, 'run': 'original'}
        changed = b'{"format":"boosterai-harness-v1","run":"external"}\n'
        with state.locked(self.project):
            raw = state.write(self.project, None, value)
            path = self.project / state.STATE
            verify_stage = state.project_tools.verify_archive_stage_file
            changed_once = False

            def inject_external_change(*args):
                nonlocal changed_once
                verify_stage(*args)
                if not changed_once:
                    changed_once = True
                    path.write_bytes(changed)

            with patch.object(state.project_tools, 'verify_archive_stage_file',
                              side_effect=inject_external_change):
                with self.assertRaisesRegex(ValueError, 'changed'):
                    state.write(self.project, raw, {'format': state.FORMAT, 'run': 'new'})
            self.assertEqual(path.read_bytes(), changed)
            self.assertFalse(any(item.name.startswith('.harness-state-op-')
                                 for item in self.scratch.iterdir()))

    def test_same_bytes_with_a_replaced_identity_are_not_overwritten(self):
        value = {'format': state.FORMAT, 'run': 'original'}
        with state.locked(self.project):
            raw = state.write(self.project, None, value)
            path = state.project_tools.safe(self.project, state.STATE)
            replacement = state.project_tools.safe(self.project, 'replacement.json')
            replacement.write_bytes(raw)
            verify_stage = state.project_tools.verify_archive_stage_file
            changed_once = False

            def inject_external_replacement(*args):
                nonlocal changed_once
                verify_stage(*args)
                if not changed_once:
                    changed_once = True
                    os.replace(replacement, path)

            with patch.object(state.project_tools, 'verify_archive_stage_file',
                              side_effect=inject_external_replacement):
                with self.assertRaisesRegex(ValueError, 'changed'):
                    state.write(self.project, raw, {'format': state.FORMAT, 'run': 'new'})
            self.assertEqual(path.read_bytes(), raw)
            self.assertFalse(any(item.name.startswith('.harness-state-op-')
                                 for item in self.scratch.iterdir()))

    def test_disappearance_during_staging_does_not_recreate_state(self):
        value = {'format': state.FORMAT, 'run': 'original'}
        with state.locked(self.project):
            raw = state.write(self.project, None, value)
            path = state.project_tools.safe(self.project, state.STATE)
            saved = state.project_tools.safe(self.project, '_boosterAI/owned-fixture-saved.json')
            verify_stage = state.project_tools.verify_archive_stage_file
            moved_once = False

            def inject_external_move(*args):
                nonlocal moved_once
                verify_stage(*args)
                if not moved_once:
                    moved_once = True
                    path.rename(saved)

            with patch.object(state.project_tools, 'verify_archive_stage_file',
                              side_effect=inject_external_move):
                with self.assertRaisesRegex(ValueError, 'changed'):
                    state.write(self.project, raw, {'format': state.FORMAT, 'run': 'new'})
            self.assertFalse(path.exists())
            self.assertEqual(saved.read_bytes(), raw)
            self.assertFalse(any(item.name.startswith('.harness-state-op-')
                                 for item in self.scratch.iterdir()))

    def test_hardlinked_state_is_rejected_and_preserved(self):
        directory = self.project / '_boosterAI'
        directory.mkdir()
        original = self.project / 'other-owned-file'
        original.write_bytes(b'{"format":"boosterai-harness-v1"}\n')
        path = directory / 'harness-state.json'
        os.link(original, path)
        before = original.read_bytes()
        with self.assertRaises(ValueError):
            with state.locked(self.project):
                pass
        self.assertEqual(original.read_bytes(), before)
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
