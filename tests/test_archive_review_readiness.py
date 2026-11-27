"""Exercise archive admission without files, publication, or permission claims.

Review values below are synthetic fixtures, never approval for a real example.
The writer boundary deliberately raises if a rejected review reaches it.
"""

import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import project_tools as p


class ArchiveReviewReadinessTests(unittest.TestCase):
    """Keep review rejection read-only even when candidate evidence exists."""

    def review(self):
        """Return a complete synthetic review solely for admission regression tests."""
        return dict(rights_confirmed=True, privacy_reviewed=True,
                    generalized=True, security_reviewed=True,
                    real_test_verified=True, outcome='success', license='MIT',
                    title='Read a value', name='read-value',
                    description='An independent synthetic source fixture',
                    test_summary='Synthetic admission fixture, not product acceptance',
                    categories=['data'], tags=['example'],
                    function_docs_reviewed=True, evidence='proof.json',
                    evidence_sha256='a' * 64, code_sha256='b' * 64)

    def admission(self, review, proof_hash='a' * 64, content='// Independent fixture\n'):
        """Run the real admission function with read-only in-memory path doubles."""
        code = Mock(suffix='.cs')
        code.is_file.return_value = True
        code.open.return_value = io.StringIO(content)
        record = Mock()
        record.is_file.return_value = True
        record.read_text.return_value = json.dumps(review)
        proof = Mock()
        proof.is_file.return_value = True
        paths = {'source.cs': code, 'review.json': record, 'proof.json': proof}
        with patch.object(p, 'repository_root', return_value=Path('fixture')), \
             patch.object(p, 'safe', side_effect=lambda base, name: paths[name]), \
             patch.object(p, 'sha', side_effect=lambda path: 'b' * 64 if path is code else proof_hash), \
             patch.object(p, 'workspace', side_effect=AssertionError('writer reached')) as writer:
            result = p.archive_example('fixture', 'source.cs', 'review.json')
            writer.assert_not_called()
        self.assertEqual(result['status'], 'LOCAL_ONLY')
        self.assertIs(result['published'], False)
        return result['reasons']

    def test_each_unconfirmed_review_gate_prevents_writes(self):
        for gate in ('rights_confirmed', 'privacy_reviewed', 'generalized',
                     'security_reviewed', 'real_test_verified'):
            for value in (None, False, 1, 'true'):
                with self.subTest(gate=gate, value=value):
                    review = self.review()
                    review[gate] = value
                    self.assertIn(gate, self.admission(review))

    def test_missing_license_does_not_turn_into_publication(self):
        review = self.review()
        review['license'] = None
        self.assertIn('license_not_supported_or_confirmed', self.admission(review))

    def test_csharp_function_review_is_required(self):
        review = self.review()
        review.pop('function_docs_reviewed')
        self.assertIn('function_documentation_review_required', self.admission(review))

    def test_mismatched_evidence_never_reaches_writer(self):
        self.assertIn('unverified_evidence_reference',
                      self.admission(self.review(), proof_hash='c' * 64))

    def test_private_content_never_reaches_writer(self):
        review = self.review()
        review['private_terms'] = ['private-fixture-marker']
        self.assertIn('possible_private_data',
                      self.admission(review, content='// private-fixture-marker\n'))

    def test_success_claim_without_successful_review_is_rejected(self):
        review = self.review()
        review['outcome'] = 'not-run'
        self.assertIn('successful_real_verification_required', self.admission(review))

    def test_complete_fixture_reaches_guarded_writer_boundary(self):
        # Positive control: an implementation that rejects every review must fail.
        # The guard stops before any actual workspace or archive write occurs.
        with self.assertRaisesRegex(AssertionError, '^writer reached$'):
            self.admission(self.review())


if __name__ == '__main__':
    unittest.main()
