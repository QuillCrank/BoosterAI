"""Exercise daily eligibility only; no network, activation, or snapshot moves."""
import datetime as dt
import importlib.util
import json
from contextlib import nullcontext
from pathlib import Path
import unittest
from unittest.mock import patch
from repo_temp import workspace

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('archive_daily_gate', ROOT / 'scripts/archive_sync.py')
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


class DailyGateTests(unittest.TestCase):
    def setUp(self):
        self.root = workspace(self)
        self.work = sync.storage(self.root)
        self.commit = 'a' * 40
        snapshot = self.work / 'snapshots' / self.commit
        snapshot.mkdir(parents=True)
        raw = json.dumps({'format': 'boosterai-index-v1', 'entries': []}).encode()
        (snapshot / 'index.json').write_bytes(raw)
        (snapshot / 'snapshot.json').write_text(json.dumps({
            'commit': self.commit, 'repository': sync.REPOSITORY,
            'index_sha256': sync.digest(raw)}), encoding='utf-8')
        self.active = self.work / 'active.json'
        self.active.write_text(json.dumps({'commit': self.commit, 'repository': sync.REPOSITORY,
            'checked_utc_date': '2026-11-15'}), encoding='utf-8')
        self.before = self.active.read_bytes()

    def gate(self, now, force=False):
        """Use real pointer/snapshot reads but replace locks and remote transport.

        An assertion guard prevents any activation or stage rename from running.
        A failed transport is a synthetic offline condition, not a service denial.
        """
        with patch.object(sync, 'sync_operation', return_value=nullcontext(None)), \
             patch.object(sync.os, 'replace', side_effect=AssertionError('activation forbidden')) as moved:
            calls = []
            def offline(url, limit):
                calls.append(url)
                raise OSError('offline fixture')
            result = sync.sync(self.root, now=now, force=force, fetch=offline)
            moved.assert_not_called()
        self.assertEqual(self.active.read_bytes(), self.before)
        return result, calls

    def test_same_utc_date_skips_remote_even_with_different_local_date(self):
        local = dt.datetime(2026, 11, 16, 1, tzinfo=dt.timezone(dt.timedelta(hours=3)))
        result, calls = self.gate(local)
        self.assertEqual(result['status'], 'ALREADY_CHECKED_TODAY')
        self.assertEqual(calls, [])
        self.assertFalse(result['downloaded_code_executed'])

    def test_new_utc_day_attempts_remote_and_failure_does_not_mark_day_complete(self):
        result, calls = self.gate(dt.datetime(2026, 11, 16, tzinfo=dt.timezone.utc))
        self.assertEqual(result['status'], 'SYNC_FAILED')
        self.assertEqual(calls, [sync.COMMIT_URL])
        self.assertEqual(result['active_pointer_effect'], 'UNCHANGED')

    def test_explicit_force_bypasses_daily_skip_without_activating_on_failure(self):
        result, calls = self.gate(dt.datetime(2026, 11, 15, 12, tzinfo=dt.timezone.utc), force=True)
        self.assertEqual(result['status'], 'SYNC_FAILED')
        self.assertEqual(calls, [sync.COMMIT_URL])

    def test_utc_midnight_controls_eligibility_in_fixed_offset_time_zones(self):
        midnight = dt.datetime(2026, 11, 16, tzinfo=dt.timezone.utc)
        before_midnight = midnight - dt.timedelta(microseconds=1)
        for hours in (-12, 0, 14):
            zone = dt.timezone(dt.timedelta(hours=hours))
            with self.subTest(utc_boundary='before', offset_hours=hours):
                result, calls = self.gate(before_midnight.astimezone(zone))
                self.assertEqual(result['status'], 'ALREADY_CHECKED_TODAY')
                self.assertEqual(result['commit'], self.commit)
                self.assertEqual(calls, [])
                self.assertFalse(result['downloaded_code_executed'])
            with self.subTest(utc_boundary='at', offset_hours=hours):
                result, calls = self.gate(midnight.astimezone(zone))
                self.assertEqual(result['status'], 'SYNC_FAILED')
                self.assertEqual(calls, [sync.COMMIT_URL])
                self.assertEqual(result['active_pointer_effect'], 'UNCHANGED')


if __name__ == '__main__':
    unittest.main()
