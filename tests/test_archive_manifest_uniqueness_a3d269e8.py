"""Exercise unambiguous archive-index parsing through the actual search CLI."""

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/archive_sync.py'
COMMIT = 'd' * 40
REPOSITORY = 'https://github.com/QuillCrank/BoosterAI'


def remove_owned_fixture(root, scratch):
    """Remove only the known fixture tree after checking containment and identity.

    Unknown entries, links, reparse points, hardlinks, and changed identities are
    preserved. No archive activation, source execution or external path is used.
    """
    def ordinary(path, directory):
        info = path.lstat()
        if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
                or (directory and not stat.S_ISDIR(info.st_mode))
                or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))):
            raise RuntimeError('unsafe fixture object; preserved')
        return info.st_dev, info.st_ino

    ordinary(scratch, True)
    root_identity = ordinary(root, True)
    resolved_root = root.resolve(strict=True)
    if resolved_root.parent != scratch.resolve(strict=True):
        raise RuntimeError('fixture escaped owned scratch; preserved')
    prefix = '_boosterAI/archive-sync'
    snapshot = prefix + '/snapshots/' + COMMIT
    allowed_directories = {'.', '_boosterAI', prefix, prefix + '/snapshots', snapshot}
    allowed_files = {prefix + '/active.json', snapshot + '/index.json', snapshot + '/snapshot.json'}
    directories = []
    files = []
    pending = [root]
    while pending:
        path = pending.pop()
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        is_directory = stat.S_ISDIR(info.st_mode)
        identity = ordinary(path, is_directory)
        if not path.resolve(strict=True).is_relative_to(resolved_root):
            raise RuntimeError('fixture path escaped; preserved')
        if is_directory:
            if relative not in allowed_directories:
                raise RuntimeError('unknown fixture directory; preserved')
            directories.append((path, identity))
            with os.scandir(path) as children:
                pending.extend(Path(child.path) for child in children)
        else:
            if relative not in allowed_files:
                raise RuntimeError('unknown fixture file; preserved')
            files.append((path, identity))
    for path, identity in files:
        if ordinary(path, False) != identity or not path.resolve(strict=True).is_relative_to(resolved_root):
            raise RuntimeError('fixture file changed; preserved')
        path.unlink()
    for path, identity in sorted(directories, key=lambda item: len(item[0].parts), reverse=True):
        if ordinary(path, True) != identity or not path.resolve(strict=True).is_relative_to(resolved_root):
            raise RuntimeError('fixture directory changed; preserved')
        if path == root and identity != root_identity:
            raise RuntimeError('fixture root changed; preserved')
        path.rmdir()


class ArchiveManifestAcceptance(unittest.TestCase):
    def test_search_rejects_ambiguous_index_without_activation(self):
        """Run actual search on v1-v3 and reject duplicate root, escaped and nested names."""
        scratch = Path(os.environ['TEMP']).resolve(strict=True)
        self.assertTrue(scratch.is_relative_to(ROOT / '_temp-files'))
        root = Path(tempfile.mkdtemp(prefix='archive-index-', dir=scratch))
        self.addCleanup(remove_owned_fixture, root, scratch)
        work = root / '_boosterAI' / 'archive-sync'
        snapshot = work / 'snapshots' / COMMIT
        snapshot.mkdir(parents=True)
        active = work / 'active.json'
        index = snapshot / 'index.json'
        metadata = snapshot / 'snapshot.json'
        active.write_text(json.dumps({'commit': COMMIT}), encoding='utf-8')
        valid = (
            b'{"format":"boosterai-index-v1","entries":[]}',
            b'{"format":"boosterai-index-v2","entries":[],"categories":[],"tags":[]}',
            b'{"format":"boosterai-index-v3","entries":[],"categories":[],"tags":[],"relationships":[]}',
        )
        ambiguous = (
            b'{"format":"boosterai-index-v1","entries":[{"name":"hidden-record"}],"entries":[]}',
            b'{"format":"boosterai-index-v1","entries":[],"entries":[]}',
            b'{"format":"boosterai-index-v1","entri\\u0065s":[],"entries":[]}',
            b'{"format":"boosterai-index-v1","entries":[],"extra":{"private-name":1,"private-name":2}}',
        )
        for raw, accepted in [(item, True) for item in valid] + [(item, False) for item in ambiguous]:
            with self.subTest(accepted=accepted, index=(valid + ambiguous).index(raw)):
                index.write_bytes(raw)
                metadata.write_text(json.dumps({'commit': COMMIT, 'repository': REPOSITORY,
                                                'index_sha256': hashlib.sha256(raw).hexdigest()}),
                                    encoding='utf-8')
                before = {path: path.read_bytes() for path in (active, index, metadata)}
                result = subprocess.run(
                    [sys.executable, '-I', '-S', '-B', str(SCRIPT), 'search', '--root', str(root)],
                    cwd=ROOT, capture_output=True, text=True, timeout=15, check=False)
                self.assertEqual(result.returncode, 0 if accepted else 1, result.stderr)
                self.assertEqual(result.stderr, '')
                data = json.loads(result.stdout)
                if accepted:
                    self.assertEqual(data['commit'], COMMIT)
                    self.assertEqual(data['results'], [])
                    self.assertEqual(data['related_files_by_record'], {})
                    self.assertFalse(data['execution_authorized'])
                else:
                    self.assertEqual(data, {'status': 'FAILED', 'reason': 'duplicate archive manifest field'})
                    self.assertNotIn('hidden-record', result.stdout + result.stderr)
                    self.assertNotIn('private-name', result.stdout + result.stderr)
                self.assertEqual({path: path.read_bytes() for path in before}, before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
