"""Real CLI and local-fixture checks for the offline contribution preflight."""

import importlib.util
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from repo_temp import workspace


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/contribution_preflight_5c294f8d.py'
spec = importlib.util.spec_from_file_location('contribution_preflight', SCRIPT)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class ContributionOwnerScopeTests(unittest.TestCase):
    """Exercise the real offline gate against canonical and unrelated URL forms."""

    def test_only_canonical_links_and_non_owner_contributors_are_eligible(self):
        current = 'https://github.com/QuillCrank/BoosterAI'
        other = 'https://github.com/UnrelatedProject/BoosterAI'
        review = dict.fromkeys(preflight.TEXT_FIELDS, 'synthetic test reference')
        review.update(contributor='fixture-contributor', license='MIT',
                      test_evidence='README.md', target_repository=current)
        for repository in (current,):
            with self.subTest(source=repository):
                result = preflight.evaluate(ROOT, dict(review, source_url=repository + '/issues/1'))
                self.assertEqual(result['status'], 'MANUAL_AUTHORIZATION_AND_REVIEW_REQUIRED')
                self.assertEqual(result['target_repository'], current)
                self.assertFalse(result['network_accessed'])
                self.assertFalse(result['pr_created'])
        review['source_url'] = current + '/pull/3'
        result = preflight.evaluate(ROOT, dict(review, target_repository=other,
                                              contributor='qUiLlCrAnK'))
        self.assertEqual(result['invalid'], ['contributor', 'target_repository'])
        result = preflight.evaluate(ROOT, dict(review, contributor='qUiLlCrAnK'))
        self.assertEqual(result['invalid'], ['contributor'])
        self.assertFalse(preflight.specific_source_url(
            'https://github.com/Other/BoosterAI/issues/1'))


class ContributionPreflightTests(unittest.TestCase):
    def setUp(self):
        self.directory = workspace(self)
        self.evidence = self.directory / 'test-proof.txt'
        self.evidence.write_text('fixture evidence; not a real contributor', encoding='utf-8')
        self.review = {
            'contributor': 'fixture-contributor',
            'source_url': preflight.TARGET + '/issues/123',
            'consent_reference': 'private-fixture-consent-reference',
            'consent_scope': 'private-fixture-consent-scope',
            'license': 'MIT',
            'rights_review': 'private-fixture-rights-reference',
            'privacy_review': 'private-fixture-privacy-reference',
            'security_review': 'private-fixture-security-reference',
            'test_evidence': self.evidence.relative_to(ROOT).as_posix(),
        }

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPT),
                               '--root', str(ROOT), *args], cwd=ROOT,
                              capture_output=True, text=True)

    def test_real_inputless_cli_reports_missing_without_a_pr(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['status'], 'NOT_READY')
        self.assertEqual(set(data['missing']), set(preflight.REQUIRED))
        self.assertEqual(data['invalid'], [])
        self.assertEqual(data['pr_status'], 'NOT_RUN')
        self.assertFalse(data['pr_created'])
        self.assertFalse(data['consent_verified'])
        self.assertFalse(data['network_accessed'])

    def test_complete_fixture_requires_manual_review_and_hides_private_values(self):
        path = self.directory / 'private-review.json'
        path.write_text(json.dumps(self.review), encoding='utf-8')
        before_review, before_evidence = path.read_bytes(), self.evidence.read_bytes()
        result = self.run_cli('--review', path.relative_to(ROOT).as_posix())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['status'], 'MANUAL_AUTHORIZATION_AND_REVIEW_REQUIRED')
        self.assertEqual(data['missing'], [])
        self.assertEqual(data['invalid'], [])
        self.assertEqual(data['pr_status'], 'NOT_RUN')
        for key in ('identity_verified', 'consent_verified', 'rights_verified',
                    'privacy_verified', 'security_verified', 'test_evidence_authenticated',
                    'network_accessed', 'source_executed', 'pr_created'):
            self.assertFalse(data[key], key)
        for private in self.review.values():
            self.assertNotIn(private, result.stdout + result.stderr)
        self.assertEqual(path.read_bytes(), before_review)
        self.assertEqual(self.evidence.read_bytes(), before_evidence)

    def test_external_or_ambiguous_links_and_owner_identity_are_rejected(self):
        bad_urls = [
            'https://example.com/qUiLlCrAnK/BoosterAI/issues/1',
            'https://github.com/Other/BoosterAI/issues/1',
            preflight.TARGET + '/issues/1?token=private-fixture-token',
            preflight.TARGET + '/blob/' + 'a' * 40 + '/../private',
            preflight.TARGET,
        ]
        for url in bad_urls:
            with self.subTest(url=url):
                changed = dict(self.review, source_url=url)
                result = preflight.evaluate(ROOT, changed)
                self.assertEqual(result['status'], 'NOT_READY')
                self.assertIn('source_url', result['invalid'])
        self.assertTrue(preflight.specific_source_url(preflight.TARGET + '/blob/' + 'a' * 40 + '/dir/example.py'))
        result = preflight.evaluate(ROOT, dict(self.review, contributor='qUiLlCrAnK'))
        self.assertIn('contributor', result['invalid'])
        self.assertFalse(result['identity_verified'])

    def test_invalid_contributor_login_syntax_is_rejected_without_claiming_identity(self):
        for name in ('alice-', 'alice--x', '-alice', '-', 'a' * 40):
            with self.subTest(name=name):
                result = preflight.evaluate(ROOT, dict(self.review, contributor=name))
                self.assertEqual(result['status'], 'NOT_READY')
                self.assertIn('contributor', result['invalid'])
                self.assertFalse(result['identity_verified'])
        for name in ('alice', 'alice-x', 'a' * 39):
            with self.subTest(name=name):
                result = preflight.evaluate(ROOT, dict(self.review, contributor=name))
                self.assertEqual(result['status'], 'MANUAL_AUTHORIZATION_AND_REVIEW_REQUIRED')
                self.assertFalse(result['identity_verified'])

    def test_imported_evaluate_rejects_other_root_before_any_stat(self):
        with patch.object(preflight.Path, 'lstat', side_effect=AssertionError('outside root was inspected')) as inspected:
            with self.assertRaisesRegex(ValueError, 'unsupported_project_root'):
                preflight.evaluate(ROOT.parent, self.review)
        inspected.assert_not_called()

    def test_external_target_and_unsafe_evidence_paths_fail_closed(self):
        for key, value in [('target_repository', 'https://github.com/other/repo'),
                           ('target_branch', 'other'), ('test_evidence', '../outside.txt'),
                           ('test_evidence', str(self.evidence))]:
            with self.subTest(key=key, value=value):
                result = preflight.evaluate(ROOT, dict(self.review, **{key: value}))
                self.assertEqual(result['status'], 'NOT_READY')
                self.assertIn(key, result['invalid'])
        missing = preflight.evaluate(ROOT, dict(self.review, test_evidence='_temp-files/no-such-evidence.txt'))
        self.assertIn('test_evidence', missing['missing'])

    def test_bad_review_and_arguments_never_echo_private_inputs(self):
        path = self.directory / 'review.json'
        path.write_text('{private-fixture-bad-json', encoding='utf-8')
        result = self.run_cli('--review', path.relative_to(ROOT).as_posix())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['status'], 'NOT_READY')
        self.assertNotIn('private-fixture', result.stdout + result.stderr)
        args = self.run_cli('--private-fixture-unknown', 'private-fixture-secret')
        self.assertNotEqual(args.returncode, 0)
        self.assertNotIn('private-fixture', args.stdout + args.stderr)
        outside = self.run_cli('--review', '../private-fixture-outside.json')
        self.assertNotEqual(outside.returncode, 0)
        self.assertNotIn('private-fixture', outside.stdout + outside.stderr)

    def test_deep_json_fails_privately_without_traceback_or_mutation(self):
        path = self.directory / 'private-deep-review.json'
        content = '[' * 30000 + '0' + ']' * 30000
        self.assertLess(len(content.encode('utf-8')), 65536)
        path.write_text(content, encoding='utf-8')
        result = self.run_cli('--review', path.relative_to(ROOT).as_posix())
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, '', 'private CLI must not emit a traceback')
        self.assertEqual(json.loads(result.stdout), {
            'status': 'NOT_READY', 'error': 'invalid_or_unavailable_input',
            'pr_status': 'NOT_RUN', 'pr_created': False})
        self.assertNotIn(str(ROOT), result.stdout)
        self.assertNotIn('private-deep-review', result.stdout)
        self.assertEqual(path.read_text(encoding='utf-8'), content)

    def test_hardlinked_evidence_is_rejected(self):
        alias = self.directory / 'alias.txt'
        alias.hardlink_to(self.evidence)
        result = preflight.evaluate(ROOT, dict(self.review, test_evidence=alias.relative_to(ROOT).as_posix()))
        self.assertEqual(result['status'], 'NOT_READY')
        self.assertIn('test_evidence', result['invalid'])

    def test_access_denials_are_reported_without_retry_or_private_values(self):
        failure = PermissionError('private-fixture-denied-path')
        with patch.object(preflight, 'ordinary_file', side_effect=failure) as read:
            result = preflight.evaluate(ROOT, self.review)
        read.assert_called_once()
        self.assertEqual(result['status'], 'NOT_READY')
        self.assertEqual(result['access_denied'], ['test_evidence'])
        self.assertNotIn('test_evidence', result['invalid'])
        self.assertEqual(result['pr_status'], 'NOT_RUN')
        self.assertNotIn('private-fixture', json.dumps(result))

        captured = io.StringIO()
        with patch.object(preflight, 'ordinary_file', side_effect=failure) as read:
            with contextlib.redirect_stdout(captured):
                exit_code = preflight.main(['--root', str(ROOT), '--review', '_temp-files/private-fixture.json'])
        read.assert_called_once()
        self.assertEqual(exit_code, 1)
        output = captured.getvalue()
        self.assertEqual(json.loads(output)['error'], 'access_denied')
        self.assertEqual(json.loads(output)['pr_status'], 'NOT_RUN')
        self.assertNotIn('private-fixture', output)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows junction test')
    def test_evidence_inside_junction_is_rejected_without_following_it(self):
        target = self.directory / 'target'
        target.mkdir()
        (target / 'proof.txt').write_text('fixture evidence', encoding='utf-8')
        junction = self.directory / 'linked'
        made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(target)],
                              capture_output=True, text=True)
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        self.addCleanup(junction.rmdir)
        path = (junction / 'proof.txt').relative_to(ROOT).as_posix()
        result = preflight.evaluate(ROOT, dict(self.review, test_evidence=path))
        self.assertEqual(result['status'], 'NOT_READY')
        self.assertIn('test_evidence', result['invalid'])
        self.assertTrue((target / 'proof.txt').is_file())


if __name__ == '__main__':
    unittest.main(verbosity=2)
