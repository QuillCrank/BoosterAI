"""Exercise the actual offline contribution source gate with ASCII and lookalikes."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/contribution_preflight_5c294f8d.py'


class ContributionSourceAsciiAcceptance(unittest.TestCase):
    def test_cli_keeps_github_source_syntax_ascii_only(self):
        """Reject Unicode lookalikes while preserving valid links and local inputs.

        Each candidate goes through the real CLI with synthetic review metadata.
        No network, contributor authentication, rights decision, or PR is made.
        """
        scratch = Path(os.environ['TEMP']).resolve(strict=True)
        self.assertTrue(scratch.is_relative_to(ROOT / '_temp-files'))
        with tempfile.TemporaryDirectory(prefix='source-ascii-', dir=scratch) as name:
            directory = Path(name)
            evidence = directory / 'evidence.txt'
            evidence.write_text('Synthetic evidence only', encoding='utf-8')
            evidence_bytes = evidence.read_bytes()
            review_path = directory / 'review.json'
            review = {
                'contributor': 'fixture-contributor',
                'consent_reference': 'fixture-consent',
                'consent_scope': 'fixture-scope',
                'license': 'MIT',
                'rights_review': 'fixture-rights',
                'privacy_review': 'fixture-privacy',
                'security_review': 'fixture-security',
                'test_evidence': evidence.relative_to(ROOT).as_posix(),
            }
            base = 'https://github.com/QuillCrank/BoosterAI'
            good = (
                base + '/issues/123',
                base.upper() + '/PULL/42',
                base + '/commit/' + 'a' * 40,
                base + '/blob/' + 'b' * 40 + '/dir/example.py',
            )
            bad = (
                'http\u017f://github.com/QuillCrank/BoosterAI/issues/1',
                'https://g\u0131thub.com/QuillCrank/BoosterAI/issues/1',
                'https://github.com/Qu\u0131llCrank/BoosterAI/issues/1',
                'https://github.com/QuillCrank/Boo\u017fterAI/issues/1',
                base + '/blob/' + 'c' * 40 + '/\u212a.py',
            )
            for url, accepted in [(item, True) for item in good] + [(item, False) for item in bad]:
                with self.subTest(accepted=accepted, url=url):
                    review['source_url'] = url
                    review_path.write_text(json.dumps(review), encoding='utf-8')
                    before = review_path.read_bytes()
                    result = subprocess.run(
                        [sys.executable, '-I', '-S', '-B', str(SCRIPT), '--root', str(ROOT),
                         '--review', review_path.relative_to(ROOT).as_posix()],
                        cwd=ROOT, capture_output=True, text=True, timeout=15, check=False)
                    self.assertEqual(result.returncode, 0 if accepted else 1, result.stderr)
                    self.assertEqual(result.stderr, '')
                    data = json.loads(result.stdout)
                    self.assertEqual(data['status'],
                                     'MANUAL_AUTHORIZATION_AND_REVIEW_REQUIRED' if accepted else 'NOT_READY')
                    self.assertEqual('source_url' in data['invalid'], not accepted)
                    for field in ('identity_verified', 'consent_verified', 'rights_verified',
                                  'privacy_verified', 'security_verified', 'test_evidence_authenticated',
                                  'network_accessed', 'source_executed', 'pr_created'):
                        self.assertFalse(data[field], field)
                    self.assertEqual(data['pr_status'], 'NOT_RUN')
                    self.assertNotIn('fixture-consent', result.stdout)
                    self.assertEqual(review_path.read_bytes(), before)
                    self.assertEqual(evidence.read_bytes(), evidence_bytes)


if __name__ == '__main__':
    unittest.main(verbosity=2)
