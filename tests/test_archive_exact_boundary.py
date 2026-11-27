"""Exact aggregate-byte boundary regression for the real archive APIs.

The transport is deliberately offline: these checks prove local producer/reader
compatibility, not GitHub publication, Codex behavior, or downloaded-code safety.
"""

import copy
import hashlib
import json
import unittest

import test_archive_compatibility as support


class ExactArchiveBoundaryTests(unittest.TestCase):
    """Keep all fixtures isolated through the existing repository workspace helper."""

    setUp = support.ArchiveCompatibilityTests.setUp

    def fixture(self):
        """Build 32 valid sources totaling exactly 8 MiB including serialized index.

        The first 31 files share a digest deliberately. Accounting must count each
        indexed file even when the consumer stores only one content-addressed blob.
        A final shorter source fills the remaining bytes without changing the
        fixed-width SHA-256 metadata or the serialized index length.
        """
        reader, producer = support.reader, support.producer
        full = support.code_bytes(reader.MAX_CODE)
        contents, entries = {}, []
        for number in range(32):
            name = 'exact-%02d' % number
            path = 'solutions/%s/revisions/0001/example.py' % name
            contents[path] = full
            entries.append({'name': name, 'title': 'Exact aggregate boundary',
                            'code': path, 'code_sha256': hashlib.sha256(full).hexdigest(),
                            'outcome': 'success', 'evidence_sha256': 'b' * 64,
                            'test_summary': 'Boundary fixture only',
                            'categories': ['testing'], 'tags': ['limits']})
        manifest = {'format': 'boosterai-index-v1', 'entries': entries}
        remainder = reader.MAX_TOTAL - len(producer.archive_json_bytes(manifest)) - 31 * len(full)
        self.assertGreater(remainder, 0)
        self.assertLess(remainder, reader.MAX_CODE)
        tail = support.code_bytes(remainder)
        contents[entries[-1]['code']] = tail
        entries[-1]['code_sha256'] = hashlib.sha256(tail).hexdigest()
        for path, content in contents.items():
            destination = self.project / path
            destination.parent.mkdir(parents=True)
            destination.write_bytes(content)
            compile(content, path, 'exec')
        return manifest, contents

    def consume(self, consumer, manifest, contents, commit):
        """Invoke the actual reader with bounded in-memory HTTP response fixtures."""
        reader = support.reader
        raw = support.producer.archive_json_bytes(manifest)
        def fetch(url, limit):
            """Resolve only pinned fixture URLs; fail for an unexpected request."""
            if url == reader.COMMIT_URL:
                data = json.dumps({'sha': commit}).encode('utf-8')
            elif url == reader.RAW + commit + '/solutions/index.json':
                data = raw
            else:
                prefix = reader.RAW + commit + '/'
                self.assertTrue(url.startswith(prefix), url)
                data = contents[url[len(prefix):]]
            self.assertLessEqual(len(data), limit)
            return data
        return reader.sync(consumer, force=True, fetch=fetch)

    def test_exact_total_passes_and_one_more_byte_preserves_active_snapshot(self):
        """Accept exact equality, reject +1, and retain the previously usable state."""
        producer, reader = support.producer, support.reader
        manifest, contents = self.fixture()
        total = len(producer.archive_json_bytes(manifest)) + sum(map(len, contents.values()))
        self.assertEqual(total, 8388608)
        self.assertEqual(total, reader.MAX_TOTAL)
        self.assertEqual(len({entry['code_sha256'] for entry in manifest['entries']}), 2)
        producer.require_archive_content_limits(self.project, manifest)
        consumer = self.project / 'consumer'
        consumer.mkdir()
        accepted = self.consume(consumer, manifest, contents, 'e' * 40)
        self.assertEqual(accepted['status'], 'UPDATED', accepted)
        pointer = consumer / '_boosterAI/archive-sync/active.json'
        before = pointer.read_bytes()
        larger = copy.deepcopy(manifest)
        path = larger['entries'][-1]['code']
        contents[path] += b'\n'
        (self.project / path).write_bytes(contents[path])
        larger['entries'][-1]['code_sha256'] = hashlib.sha256(contents[path]).hexdigest()
        self.assertEqual(len(producer.archive_json_bytes(larger)) + sum(map(len, contents.values())), total + 1)
        with self.assertRaisesRegex(ValueError, 'archive snapshot exceeds sync total size limit'):
            producer.require_archive_content_limits(self.project, larger)
        rejected = self.consume(consumer, larger, contents, 'f' * 40)
        self.assertEqual(rejected['status'], 'SYNC_FAILED', rejected)
        self.assertIn('snapshot total size exceeded', rejected['reason'])
        self.assertTrue(rejected['active_pointer_preserved'], rejected)
        self.assertEqual(pointer.read_bytes(), before)
        self.assertEqual(reader.search(consumer, 'exact')['commit'], 'e' * 40)


if __name__ == '__main__':
    unittest.main()
