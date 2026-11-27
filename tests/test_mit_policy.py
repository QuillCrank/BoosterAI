"""Verify the MIT-only admission policy without relicensing existing material."""
import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

from repo_temp import workspace
import test_archive_review_readiness as admission_tests
import test_archive_profile as profile_tests

ROOT = Path(__file__).resolve().parents[1]
OTHER = ('Apache-2.0', 'BSD-3-Clause', 'CC0-1.0', 'MIT OR Apache-2.0', 'mit')

class MitPolicyTests(unittest.TestCase):
    def test_current_index_valid_and_non_mit_schema_values_rejected(self):
        schema = json.loads((ROOT / 'solutions/archive.schema.json').read_text())
        index = json.loads((ROOT / 'solutions/index.json').read_text())
        profile_tests.profile.validate_schema(index, schema)
        for license_id in OTHER:
            changed = copy.deepcopy(index)
            changed['entries'][0]['license'] = license_id
            with self.subTest(license=license_id), self.assertRaises(ValueError):
                profile_tests.profile.validate_schema(changed, schema)

    def test_archive_producer_rejects_non_mit_before_writer(self):
        helper = admission_tests.ArchiveReviewReadinessTests()
        for license_id in OTHER:
            review = helper.review()
            review['license'] = license_id
            with self.subTest(license=license_id):
                self.assertIn('license_not_supported_or_confirmed', helper.admission(review))
        with self.assertRaisesRegex(AssertionError, '^writer reached$'):
            helper.admission(helper.review())

    def test_real_contribution_cli_accepts_only_mit_for_manual_review(self):
        directory = workspace(self)
        evidence = directory / 'proof.txt'
        evidence.write_text('Synthetic test evidence, not a contributor approval.')
        review_file = directory / 'review.json'
        review = dict(contributor='fixture-user',
                      source_url='https://github.com/QuillCrank/BoosterAI/issues/123',
                      consent_reference='fixture-consent', consent_scope='fixture-scope',
                      rights_review='fixture-rights', privacy_review='fixture-privacy',
                      security_review='fixture-security',
                      test_evidence=evidence.relative_to(ROOT).as_posix())
        for license_id in ('MIT', *OTHER):
            review['license'] = license_id
            review_file.write_text(json.dumps(review))
            before = review_file.read_bytes()
            result = subprocess.run([sys.executable, '-I', '-S', '-B',
                str(ROOT / 'scripts/contribution_preflight_5c294f8d.py'),
                '--root', str(ROOT), '--review', review_file.relative_to(ROOT).as_posix()],
                cwd=ROOT, capture_output=True, text=True, timeout=15)
            with self.subTest(license=license_id):
                data = json.loads(result.stdout)
                self.assertEqual(result.returncode, 0 if license_id == 'MIT' else 1)
                self.assertEqual(data['invalid'], [] if license_id == 'MIT' else ['license'])
                self.assertEqual(data['status'], 'MANUAL_AUTHORIZATION_AND_REVIEW_REQUIRED'
                                 if license_id == 'MIT' else 'NOT_READY')
                self.assertFalse(data['pr_created'])
                self.assertFalse(data['rights_verified'])
                self.assertEqual(review_file.read_bytes(), before)
                self.assertEqual(result.stderr, '')
                self.assertNotIn('fixture-', result.stdout)

if __name__ == '__main__':
    unittest.main()
