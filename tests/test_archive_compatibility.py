"""Producer and snapshot reader agree on normalized source and total byte limits."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from repo_temp import workspace


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))


def load(name, path):
    """Load one bundled helper for in-process integration with no network access."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


producer = load('archive_limit_producer', SCRIPTS / 'project_tools.py')
reader = load('archive_limit_reader', SCRIPTS / 'archive_sync.py')


def code_bytes(size):
    """Build valid, documented fixture code with an exact UTF-8 byte count."""
    start = (b'def value():\n'
             b'    """Return a fixed integer without changing the caller state."""\n'
             b'    return 17\n#')
    if size < len(start) + 1:
        raise ValueError('fixture size too small')
    return start + b'x' * (size - len(start) - 1) + b'\n'


class ArchiveCompatibilityTests(unittest.TestCase):
    """Exercise actual producer and reader code in an isolated owned project."""

    def setUp(self):
        self.project = workspace(self)
        self.scratch = self.project / 'scratch'
        self.scratch.mkdir()
        env = patch.dict(os.environ, {'BOOSTERAI_SCRATCH': str(self.scratch)})
        env.start()
        self.addCleanup(env.stop)

    def candidate(self, name, raw):
        """Write and really exercise one candidate before preparing its review."""
        source = self.project / 'candidate.py'
        source.write_bytes(raw)
        probe = 'import runpy,sys; print(runpy.run_path(sys.argv[1])["value"]())'
        observed = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', probe, str(source)],
                                  capture_output=True, text=True)
        self.assertEqual(observed.returncode, 0, observed.stderr)
        self.assertEqual(observed.stdout.strip(), '17')
        proof = self.project / 'proof.txt'
        proof.write_text(observed.stdout, encoding='utf-8')
        review = {
            'name': name, 'title': 'Portable byte limit fixture', 'license': 'MIT',
            'code_sha256': hashlib.sha256(raw).hexdigest(), 'evidence': 'proof.txt',
            'evidence_sha256': hashlib.sha256(proof.read_bytes()).hexdigest(),
            'outcome': 'success', 'test_summary': 'Python returned the expected integer',
            'categories': ['testing'], 'tags': ['limits'],
        }
        for gate in ('rights_confirmed', 'privacy_reviewed', 'generalized',
                     'security_reviewed', 'real_test_verified'):
            review[gate] = True
        (self.project / 'review.json').write_text(json.dumps(review), encoding='utf-8')
        return source

    def archive_cli(self):
        """Call the real producer CLI and decode its reported non-public result."""
        result = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPTS / 'project_tools.py'),
                                 'archive-example', '--root', str(self.project), '--source',
                                 'candidate.py', '--review', 'review.json'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def snapshot(self):
        """Capture existing fixture archive bytes and identity for preservation checks."""
        directory = self.project / 'solutions'
        return {path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
                for path in directory.rglob('*') if path.is_file()}

    def consume(self, consumer, manifest, content, commit):
        """Feed a produced manifest and bytes to the real reader with an offline transport."""
        raw = producer.archive_json_bytes(manifest)
        def fetch(url, limit):
            """Return only the requested pinned fixture data, preserving fetch bounds."""
            if url == reader.COMMIT_URL:
                result = json.dumps({'sha': commit}).encode('utf-8')
            elif url.endswith('/solutions/index.json'):
                result = raw
            else:
                self.assertTrue(url.startswith(reader.RAW + commit + '/solutions/'))
                result = content
            if len(result) > limit:
                raise ValueError('fixture remote file exceeds size limit')
            return result
        return reader.sync(consumer, force=True, fetch=fetch)

    def test_exact_normalized_limit_archives_and_syncs(self):
        """A BOM/CRLF candidate whose normalized output is exactly the limit syncs."""
        normalized = code_bytes(producer.profile.MAX_CODE_BYTES)
        raw = b'\xef\xbb\xbf' + normalized.replace(b'\n', b'\r\n')
        self.assertGreater(len(raw), producer.profile.MAX_CODE_BYTES)
        self.candidate('boundary-example', raw)
        ready = self.archive_cli()
        self.assertEqual(ready['status'], 'READY_FOR_AUTHORIZED_PUBLICATION')
        index = json.loads((self.project / 'solutions/index.json').read_bytes())
        entry = index['entries'][0]
        archived = (self.project / entry['code']).read_bytes()
        self.assertEqual(archived, normalized)
        self.assertEqual(len(archived), reader.MAX_CODE)
        consumer = self.project / 'consumer'
        consumer.mkdir()
        result = self.consume(consumer, index, archived, 'a' * 40)
        self.assertEqual(result['status'], 'UPDATED', result)
        self.assertFalse(result['downloaded_code_executed'])
        self.assertEqual(reader.search(consumer, 'boundary')['commit'], 'a' * 40)

    def test_one_byte_over_limit_is_local_only_and_preserves_existing_archive(self):
        """An oversized valid candidate cannot alter or replace any current record."""
        self.candidate('baseline-example', code_bytes(180))
        self.assertEqual(self.archive_cli()['status'], 'READY_FOR_AUTHORIZED_PUBLICATION')
        before = self.snapshot()
        self.candidate('oversized-example', code_bytes(producer.profile.MAX_CODE_BYTES + 1))
        result = self.archive_cli()
        self.assertEqual(result['status'], 'LOCAL_ONLY')
        self.assertIn('code_exceeds_sync_size_limit', result['reasons'])
        self.assertFalse(result['published'])
        self.assertEqual(self.snapshot(), before)

    def test_limit_counts_utf8_bytes_not_unicode_characters(self):
        """Multi-byte characters cannot bypass the producer's byte limit."""
        text = '# ' + ('\u00e9' * (producer.profile.MAX_CODE_BYTES // 2))
        code = text.encode('utf-8')
        self.assertGreater(len(code), producer.profile.MAX_CODE_BYTES)
        self.assertLess(len(text), producer.profile.MAX_CODE_BYTES)
        (self.project / 'candidate.py').write_bytes(code)
        (self.project / 'review.json').write_text('{}', encoding='utf-8')
        result = self.archive_cli()
        self.assertEqual(result['status'], 'LOCAL_ONLY')
        self.assertIn('code_exceeds_sync_size_limit', result['reasons'])
        self.assertFalse((self.project / 'solutions').exists())

    def test_total_limit_blocks_producer_before_any_archive_change(self):
        """A projected combined snapshot too large for sync leaves records untouched."""
        self.candidate('baseline-example', code_bytes(180))
        self.assertEqual(self.archive_cli()['status'], 'READY_FOR_AUTHORIZED_PUBLICATION')
        old = json.loads((self.project / 'solutions/index.json').read_bytes())
        old_size = len((self.project / old['entries'][0]['code']).read_bytes())
        before = self.snapshot()
        self.candidate('second-example', code_bytes(1000))
        threshold = len(producer.archive_json_bytes(old)) + old_size + 500
        with patch.object(producer.profile, 'MAX_TOTAL_BYTES', threshold):
            with self.assertRaisesRegex(ValueError, 'archive snapshot exceeds sync total size limit'):
                producer.archive_example(self.project, 'candidate.py', 'review.json')
        self.assertEqual(self.snapshot(), before)

    def test_actual_total_boundary_agrees_with_reader_and_preserves_snapshot(self):
        """The real 8 MiB snapshot limit counts the index and every current file."""
        source = code_bytes(reader.MAX_CODE)
        digest = hashlib.sha256(source).hexdigest()
        entries = []
        for number in range(32):
            name = 'example-%02d' % number
            path = 'solutions/%s/revisions/0001/example.py' % name
            file = self.project / path
            file.parent.mkdir(parents=True)
            file.write_bytes(source)
            entries.append({'name': name, 'title': 'Byte-bound source', 'code': path,
                            'code_sha256': digest, 'outcome': 'success', 'evidence_sha256': 'b' * 64,
                            'test_summary': 'Fixture output was checked',
                            'categories': ['testing'], 'tags': ['limits']})
        first = {'format': 'boosterai-index-v1', 'entries': entries[:31]}
        second = {'format': 'boosterai-index-v1', 'entries': entries}
        producer.require_archive_content_limits(self.project, first)
        consumer = self.project / 'consumer'
        consumer.mkdir()
        self.assertEqual(self.consume(consumer, first, source, 'c' * 40)['status'], 'UPDATED')
        active = consumer / '_boosterAI/archive-sync/active.json'
        before = active.read_bytes()
        with self.assertRaisesRegex(ValueError, 'archive snapshot exceeds sync total size limit'):
            producer.require_archive_content_limits(self.project, second)
        result = self.consume(consumer, second, source, 'd' * 40)
        self.assertEqual(result['status'], 'SYNC_FAILED')
        self.assertIn('snapshot total size exceeded', result['reason'])
        self.assertTrue(result['active_pointer_preserved'])
        self.assertEqual(active.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
