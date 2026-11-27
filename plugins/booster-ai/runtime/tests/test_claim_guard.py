"""Real cross-process acceptance checks for cooperative slice/file claims."""
import importlib.util
import json
import os
from pathlib import Path
from queue import Queue, Empty
import stat
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from repo_temp import workspace

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'scripts/claim_guard_8d31f72a.py'
spec = importlib.util.spec_from_file_location('claim_guard', SOURCE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

CHILD = r'''
import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location('claim_guard_child', sys.argv[1])
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
try:
    with guard.claim(sys.argv[2], sys.argv[3], json.loads(sys.argv[4])):
        print('ACQUIRED', flush=True)
        if sys.argv[5] == 'hold':
            if sys.stdin.readline().strip() == 'crash':
                os._exit(17)
except guard.ClaimBusyError:
    print('BUSY', flush=True)
    raise SystemExit(75)
'''


class ClaimGuardTests(unittest.TestCase):
    def setUp(self):
        self.project = workspace(self)
        (self.project / '_boosterAI').mkdir()

    def args(self, slice_id, files=(), hold=False):
        return [sys.executable, '-I', '-S', '-B', '-c', CHILD, str(SOURCE),
                str(self.project), slice_id, json.dumps(list(files)), 'hold' if hold else 'once']

    def stop_owned_child(self, process):
        """Reap only this test's child before removing its owned workspace."""
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    def holder(self, slice_id, files=()):
        process = subprocess.Popen(self.args(slice_id, files, hold=True), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop_owned_child, process)
        output = Queue()
        def read_first():
            output.put(process.stdout.readline())
        threading.Thread(target=read_first, daemon=True).start()
        try:
            first = output.get(timeout=20)
        except Empty:
            self.fail('child did not report claim state within 20 seconds')
        self.assertEqual(first.strip(), 'ACQUIRED', 'child failed before acquiring claim')
        self.assertIsNone(process.poll())
        return process

    def probe(self, slice_id, files=()):
        return subprocess.run(self.args(slice_id, files), input='', capture_output=True,
                              text=True, timeout=20)

    def finish(self, process, command='release', expected=0):
        output, error = process.communicate(command + '\n', timeout=20)
        self.assertEqual(process.returncode, expected, output + error)

    def test_two_processes_conflict_then_normal_exit_releases_same_guard(self):
        first = self.holder('feature-one', ['source/file.py'])
        second = self.probe('feature-one', ['source/file.py'])
        self.assertEqual((second.returncode, second.stdout.strip()), (75, 'BUSY'), second.stderr)
        directory = self.project / '_boosterAI/claim-guards'
        self.finish(first)
        before = {item.name: (item.stat().st_ino, item.read_bytes()) for item in directory.iterdir()}
        third = self.probe('feature-one', ['source/file.py'])
        self.assertEqual((third.returncode, third.stdout.strip()), (0, 'ACQUIRED'), third.stderr)
        after = {item.name: (item.stat().st_ino, item.read_bytes()) for item in directory.iterdir()}
        self.assertEqual(before, after, 'guard files must persist across ownership transfer')

    def test_different_slices_claiming_the_same_file_conflict(self):
        first = self.holder('first', ['shared/file.py'])
        second = self.probe('second', ['shared/file.py'])
        self.assertEqual((second.returncode, second.stdout.strip()), (75, 'BUSY'), second.stderr)
        self.finish(first)

    def test_independent_processes_run_concurrently(self):
        first_id = 'independent-0'
        first_stripe = guard._stripe(guard._slice_key(first_id))
        second_id = next('independent-' + str(i) for i in range(1, 1000)
                         if guard._stripe(guard._slice_key('independent-' + str(i))) != first_stripe)
        first = self.holder(first_id)
        second = self.holder(second_id)
        self.assertIsNone(first.poll())
        self.assertIsNone(second.poll())
        self.finish(second)
        self.finish(first)

    def test_crashed_process_releases_os_lock_without_deleting_guard(self):
        first = self.holder('crash-test', ['after/crash.py'])
        directory = self.project / '_boosterAI/claim-guards'
        self.finish(first, command='crash', expected=17)
        before = {item.name: item.read_bytes() for item in directory.iterdir()}
        second = self.probe('crash-test', ['after/crash.py'])
        self.assertEqual((second.returncode, second.stdout.strip()), (0, 'ACQUIRED'), second.stderr)
        self.assertEqual(before, {item.name: item.read_bytes() for item in directory.iterdir()})

    def test_failed_multi_claim_releases_earlier_stripes(self):
        shared = next('shared/item-' + str(i) + '.py' for i in range(1000)
                      if guard._stripe('file:shared/item-' + str(i) + '.py') > 64)
        busy_stripe = guard._stripe('file:' + shared)
        owner_stripe = guard._stripe(guard._slice_key('primary-owner'))
        free_id = next('partial-' + str(i) for i in range(1000)
                       if guard._stripe(guard._slice_key('partial-' + str(i))) < busy_stripe
                       and guard._stripe(guard._slice_key('partial-' + str(i))) != owner_stripe)
        first = self.holder('primary-owner', [shared])
        failed = self.probe(free_id, [shared])
        self.assertEqual((failed.returncode, failed.stdout.strip()), (75, 'BUSY'), failed.stderr)
        independent = self.probe(free_id)
        self.assertEqual((independent.returncode, independent.stdout.strip()), (0, 'ACQUIRED'), independent.stderr)
        self.finish(first)

    def test_unknown_guard_is_preserved(self):
        directory = self.project / '_boosterAI/claim-guards'
        directory.mkdir()
        stripe = guard._stripe(guard._slice_key('unknown'))
        existing = directory / ('stripe-%02x.lock' % stripe)
        existing.write_bytes(b'unrecognized owner data')
        with self.assertRaisesRegex(guard.UnsafeClaimError, 'unknown'):
            with guard.claim(self.project, 'unknown'):
                self.fail('unknown guard must not be accepted')
        self.assertEqual(existing.read_bytes(), b'unrecognized owner data')

    def test_paths_and_hardlinks_are_rejected(self):
        for invalid in ('../other', '/absolute', 'a//b', 'name:stream', 'a\\b', 'folder/..'):
            with self.subTest(path=invalid), self.assertRaises(guard.UnsafeClaimError):
                with guard.claim(self.project, 'invalid-path', [invalid]):
                    self.fail('invalid path was accepted')
        source = self.project / 'source.py'
        source.write_text('preserve', encoding='utf-8')
        alias = self.project / 'alias.py'
        os.link(source, alias)
        with self.assertRaisesRegex(guard.UnsafeClaimError, 'single-link'):
            with guard.claim(self.project, 'hardlink', ['source.py']):
                self.fail('hardlinked source was accepted')
        self.assertEqual(source.read_text(encoding='utf-8'), 'preserve')

    def test_guard_hardlink_and_reparse_metadata_are_rejected(self):
        with guard.claim(self.project, 'guard-hardlink'):
            pass
        stripe = guard._stripe(guard._slice_key('guard-hardlink'))
        path = self.project / '_boosterAI/claim-guards' / ('stripe-%02x.lock' % stripe)
        alias = self.project / 'guard-alias'
        original = path.read_bytes()
        os.link(path, alias)
        with self.assertRaisesRegex(guard.UnsafeClaimError, 'single-link'):
            with guard.claim(self.project, 'guard-hardlink'):
                self.fail('hardlinked guard was accepted')
        self.assertEqual(path.read_bytes(), original)
        from types import SimpleNamespace
        for mode, attributes in ((stat.S_IFLNK, 0), (stat.S_IFDIR, 0x400)):
            with self.subTest(mode=mode, attributes=attributes), self.assertRaises(guard.UnsafeClaimError):
                guard._plain(SimpleNamespace(st_mode=mode, st_file_attributes=attributes), 'directory')

    def test_access_denial_is_not_relabelled_busy(self):
        with patch.object(guard, '_try_lock', side_effect=PermissionError('access denied')):
            with self.assertRaises(PermissionError):
                with guard.claim(self.project, 'denied'):
                    self.fail('claim should fail')


if __name__ == '__main__':
    unittest.main(verbosity=2)
