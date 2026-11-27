"""Reject ambiguous review JSON through the real offline preflight CLI."""

import json
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

from repo_temp import workspace


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/contribution_preflight_5c294f8d.py'


class DuplicateReviewFieldsTests(unittest.TestCase):
    def test_pure_parser_rejects_duplicates_at_every_object_depth(self):
        spec = importlib.util.spec_from_file_location('review_duplicates', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for raw in (
            '{"consent_scope":"first","consent_scope":"second"}',
            '{"license":"MIT","license":"MIT"}',
            '{"notes":{"key":1,"key":2}}',
            '{"consent_scope":1,"consent_\\u0073cope":2}',
        ):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, '^duplicate_review_field$'):
                    json.loads(raw, object_pairs_hook=module.unique_review_object)
        self.assertEqual(
            json.loads('{"scope":"one","notes":{"key":1}}',
                       object_pairs_hook=module.unique_review_object),
            {'scope': 'one', 'notes': {'key': 1}})

    def test_duplicate_fields_fail_privately_without_mutating_input(self):
        directory = workspace(self)
        review = directory / 'review.json'
        cases = (
            '{"consent_scope":"private-first","consent_scope":"private-second"}',
            '{"license":"MIT","license":"MIT"}',
            '{"notes":{"private-key":1,"private-key":2}}',
            '{"consent_scope":1,"consent_\\u0073cope":2}',
        )
        for raw in cases:
            with self.subTest(raw=raw):
                review.write_text(raw, encoding='utf-8')
                before = review.read_bytes()
                result = subprocess.run(
                    [sys.executable, '-I', '-S', '-B', str(SCRIPT),
                     '--root', str(ROOT), '--review', review.relative_to(ROOT).as_posix()],
                    cwd=ROOT, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, '')
                self.assertEqual(json.loads(result.stdout), {
                    'status': 'NOT_READY', 'error': 'invalid_or_unavailable_input',
                    'pr_status': 'NOT_RUN', 'pr_created': False})
                self.assertNotIn('private-', result.stdout)
                self.assertEqual(review.read_bytes(), before)

    def test_unique_object_keeps_existing_missing_input_contract(self):
        directory = workspace(self)
        review = directory / 'review.json'
        review.write_text('{}', encoding='utf-8')
        result = subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(SCRIPT),
             '--root', str(ROOT), '--review', review.relative_to(ROOT).as_posix()],
            cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        data = json.loads(result.stdout)
        self.assertEqual(data['status'], 'NOT_READY')
        self.assertIn('consent_scope', data['missing'])
        self.assertFalse(data['pr_created'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
