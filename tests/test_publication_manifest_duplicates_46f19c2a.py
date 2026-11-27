"""Check publication-manifest uniqueness through the actual scanner CLI."""

import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / 'scripts/publication_scan_91c4e6b2.py'
LICENSE = ROOT / 'LICENSE'
TERM = 'Permission is hereby granted'


class PublicationManifestAcceptance(unittest.TestCase):
    def test_duplicate_fields_fail_closed_and_unique_manifest_scans(self):
        """Reject ambiguous stdin before a review term can be hidden or leaked.

        Each case invokes the real scanner against the existing license file.
        Invalid object names remain private; the license bytes must not change.
        """
        before = LICENSE.read_bytes()
        self.assertIn(TERM.encode('utf-8'), before)
        duplicate_manifests = (
            '{"files":["LICENSE"],"private_terms":["' + TERM
            + '"],"private_terms":[],"old_commits":[]}',
            '{"files":["LICENSE"],"files":["missing-fixture"],'
            '"private_terms":[],"old_commits":[]}',
            '{"files":["LICENSE"],"private_terms":[],"old_commits":[],"old_commits":[]}',
            '{"files":["LICENSE"],"private_terms":[],"private_\\u0074erms":[],"old_commits":[]}',
            '{"files":["LICENSE"],"private_terms":[],"old_commits":[],'
            '"private-extra":{"private-field":1,"private-field":2}}',
        )
        for raw in duplicate_manifests:
            with self.subTest(payload_index=duplicate_manifests.index(raw)):
                result = subprocess.run(
                    [sys.executable, '-I', '-S', '-B', str(SCANNER), '--root', str(ROOT)],
                    input=raw, text=True, capture_output=True, cwd=ROOT, timeout=15, check=False)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stderr, '')
                self.assertEqual(json.loads(result.stdout), {
                    'status': 'ERROR', 'error': 'invalid_manifest', 'privacy_proven': False})
                for private in (TERM, 'missing-fixture', 'private-field', str(ROOT)):
                    self.assertNotIn(private, result.stdout + result.stderr)
                self.assertEqual(LICENSE.read_bytes(), before)

        valid = json.dumps({'files': ['LICENSE'], 'private_terms': [TERM], 'old_commits': []})
        result = subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(SCANNER), '--root', str(ROOT)],
            input=valid, text=True, capture_output=True, cwd=ROOT, timeout=15, check=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, '')
        data = json.loads(result.stdout)
        self.assertEqual(data['status'], 'FINDINGS')
        self.assertEqual(data['findings'], [{'file_index': 0, 'category': 'private_term'}])
        self.assertEqual(data['private_terms_supplied'], 1)
        self.assertFalse(data['privacy_proven'])
        self.assertNotIn(TERM, result.stdout)
        self.assertEqual(LICENSE.read_bytes(), before)

    def test_repeated_file_alias_is_rejected_without_echoing_input(self):
        """Reject a manifest that selects the existing license through two aliases."""
        before = LICENSE.read_bytes()
        manifest = json.dumps({'files': ['LICENSE', './LICENSE'], 'private_terms': [TERM],
                               'old_commits': []})
        result = subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(SCANNER), '--root', str(ROOT)],
            input=manifest, text=True, capture_output=True, cwd=ROOT, timeout=15, check=False)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertEqual(json.loads(result.stdout), {
            'status': 'ERROR', 'error': 'duplicate_file', 'file_index': 1, 'privacy_proven': False})
        for private in (TERM, 'LICENSE', str(ROOT)):
            self.assertNotIn(private, result.stdout)
        self.assertEqual(LICENSE.read_bytes(), before)

    def test_repeated_root_is_rejected_without_echoing_input(self):
        """Reject two root arguments even when both select the existing checkout."""
        manifest = json.dumps({'files': ['LICENSE'], 'private_terms': [TERM], 'old_commits': []})
        result = subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(SCANNER), '--root', '.', '--root=' + str(ROOT)],
            input=manifest, text=True, capture_output=True, cwd=ROOT, timeout=15, check=False)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, 'invalid arguments\n')


if __name__ == '__main__':
    unittest.main(verbosity=2)
