"""Exercise the generalized JavaScript example against this real checkout."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from repo_temp import workspace


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node.js is required to exercise the JavaScript example')
class CrossLanguageExampleTests(unittest.TestCase):
    def test_real_child_directory_search_and_invalid_inputs(self):
        """Run the archived function in Node.js without writing to the target."""
        index = json.loads((ROOT / 'solutions/index.json').read_text(encoding='utf-8'))
        entry = next(item for item in index['entries'] if item['name'] == 'find-child-directories-js')
        source = ROOT / entry['code']
        probe = r'''
const assert = require('node:assert/strict');
const path = require('node:path');
const { findChildDirectories } = require(process.argv[1]);
const root = process.argv[2];
assert.deepEqual(findChildDirectories(root, 'SOLUTIONS'), [path.join(root, 'solutions')]);
assert.deepEqual(findChildDirectories(root, 'nonexistent-child-0c284f'), []);
for (const name of ['', '.', '..', '../outside', 'a/b', 'a\\b', '\0']) {
    assert.throws(() => findChildDirectories(root, name), TypeError);
}
assert.throws(() => findChildDirectories(path.join(root, 'README.md'), 'any'));
console.log('verified');
'''
        result = subprocess.run(['node', '-e', probe, str(source), str(ROOT)],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), 'verified')

    @unittest.skipUnless(sys.platform == 'win32', 'Windows junction test')
    def test_actual_junction_is_skipped_and_rejected_as_a_root(self):
        """Check a real owned junction without traversing or changing its target."""
        fixture = workspace(self)
        target = fixture / 'target'
        target.mkdir()
        marker = target / 'keep.txt'
        marker.write_text('preserve', encoding='utf-8')
        junction = fixture / 'linked'
        def remove_owned_junction():
            if junction.exists():
                self.assertTrue(target.resolve().is_relative_to(fixture.resolve()))
                self.assertEqual(junction.resolve(), target.resolve())
                self.assertTrue(getattr(junction.lstat(), 'st_file_attributes', 0) & 1024)
                junction.rmdir()
        self.addCleanup(remove_owned_junction)
        created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(target)],
                                 capture_output=True, text=True)
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        index = json.loads((ROOT / 'solutions/index.json').read_text(encoding='utf-8'))
        source = ROOT / next(item['code'] for item in index['entries']
                             if item['name'] == 'find-child-directories-js')
        probe = r'''
const assert = require('node:assert/strict');
const { findChildDirectories } = require(process.argv[1]);
assert.deepEqual(findChildDirectories(process.argv[2], 'linked'), []);
assert.throws(() => findChildDirectories(process.argv[3], 'target'));
'''
        result = subprocess.run(['node', '-e', probe, str(source), str(fixture), str(junction)],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(marker.read_text(encoding='utf-8'), 'preserve')


if __name__ == '__main__':
    unittest.main(verbosity=2)
