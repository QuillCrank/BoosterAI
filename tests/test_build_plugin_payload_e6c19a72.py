"""Exercise the standalone plugin payload builder with in-memory inputs."""

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import unittest
from zipfile import ZIP_STORED, ZipFile


BUILDER = Path(__file__).resolve().parents[1] / 'scripts' / 'build_plugin_payload_e6c19a72.py'
PIN_OPTION = '--recipe-sha256'
ENVELOPE_PIN_OPTION = '--envelope-sha256'
PLUGIN_METADATA_PATH = '.codex-plugin/plugin.json'
WRAPPER_PATH = 'skills/booster-ai-workflow/SKILL.md'
WRAPPER_PIN_OPTION = '--expected-manifest-sha256'


def manifest_for(files):
    return {
        'format': 'boosterai-plugin-runtime-v1',
        'source_commit': '1' * 40,
        'files': {
            path: sha256(content.encode('utf-8')).hexdigest()
            for path, content in files.items()
        },
    }


def canonical_manifest(manifest):
    return (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode('utf-8')


def request_bytes(manifest, files, envelope=None):
    request = {'manifest': manifest, 'files': files}
    if envelope is not None:
        request['envelope'] = envelope
    return json.dumps(request, ensure_ascii=False).encode('utf-8')


def envelope_pin(envelope):
    return sha256((json.dumps(envelope, sort_keys=True, indent=2) + '\n').encode('utf-8')).hexdigest()


class BuildPluginPayloadTests(unittest.TestCase):
    def setUp(self):
        self.files = {'scripts/sample.py': 'print("sample")\n', 'README.md': 'Caf\u00e9\r\n'}
        self.manifest = manifest_for(self.files)
        self.manifest_bytes = canonical_manifest(self.manifest)
        self.pin = sha256(self.manifest_bytes).hexdigest()

    def run_builder(self, request, *arguments):
        return subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(BUILDER), *arguments],
            input=request,
            capture_output=True,
            timeout=10,
        )

    def assert_rejected(self, request, *arguments):
        result = self.run_builder(request, *arguments)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b'')
        self.assertIn(result.stderr, (b'invalid runtime build input\n', b'invalid runtime build input\r\n'))

    def test_builds_deterministic_stored_zip(self):
        first = self.run_builder(request_bytes(self.manifest, self.files), PIN_OPTION, self.pin)
        reordered_files = dict(reversed(list(self.files.items())))
        reordered_manifest = {
            'files': manifest_for(reordered_files)['files'],
            'source_commit': self.manifest['source_commit'],
            'format': self.manifest['format'],
        }
        second = self.run_builder(request_bytes(reordered_manifest, reordered_files), PIN_OPTION, self.pin)
        for result in (first, second):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, b'')
        self.assertEqual(first.stdout, second.stdout)

        with ZipFile(BytesIO(first.stdout)) as archive:
            expected_names = ['runtime/manifest.json', *(f'runtime/{path}' for path in self.files)]
            self.assertCountEqual(archive.namelist(), expected_names)
            self.assertTrue(all(entry.compress_type == ZIP_STORED for entry in archive.infolist()))
            self.assertEqual(archive.read('runtime/manifest.json'), self.manifest_bytes)
            for path, content in self.files.items():
                self.assertEqual(archive.read(f'runtime/{path}'), content.encode('utf-8'))

    def test_rejects_changed_content_under_original_manifest(self):
        files = {**self.files, 'README.md': 'changed content'}
        self.assert_rejected(request_bytes(self.manifest, files), PIN_OPTION, self.pin)

    def test_rejects_wrong_recipe_pin(self):
        self.assert_rejected(request_bytes(self.manifest, self.files), PIN_OPTION, 'e' * 64)

    def test_rejects_invalid_cli_arguments_with_fixed_diagnostic(self):
        request = request_bytes(self.manifest, self.files)
        selection = (PIN_OPTION, self.pin)
        cases = {
            'unknown option': (*selection, '--private-flag-marker=private-value-marker'),
            'abbreviated pin option': ('--recipe-sha', self.pin),
            'identical repeated pin': (*selection, *selection),
            'different repeated pin': (PIN_OPTION, 'e' * 64, *selection),
        }
        for case, arguments in cases.items():
            with self.subTest(case=case):
                self.assert_rejected(request, *arguments)

    def test_rejects_missing_and_extra_files(self):
        cases = {
            'missing': {'README.md': self.files['README.md']},
            'extra': {**self.files, 'extra.md': 'extra content'},
        }
        for case, files in cases.items():
            with self.subTest(case=case):
                self.assert_rejected(request_bytes(self.manifest, files), PIN_OPTION, self.pin)

    def test_rejects_duplicate_decoded_property(self):
        request = request_bytes(self.manifest, self.files)
        duplicate = b'{"f\\u0069les":"first value",' + request[1:]
        self.assert_rejected(duplicate, PIN_OPTION, self.pin)

    def test_rejects_invalid_paths_even_with_matching_pin(self):
        oversized_member = '/'.join(['a' * 255] * 255 + ['a' * 248])
        self.assertEqual(len('runtime/' + oversized_member), 65536)
        cases = {
            'parent traversal': ('../outside.md',),
            'absolute path': ('/absolute.md',),
            'backslash separator': ('nested\\separator.md',),
            'ZIP member exceeds 65535 bytes': (oversized_member,),
            'path component exceeds 255 bytes': ('nested/' + 'a' * 256,),
            'directory has case aliases': ('Shared/one.md', 'shared/two.md'),
            'file and directory collide': ('shared', 'Shared/one.md'),
        }
        for case, paths in cases.items():
            with self.subTest(case=case):
                files = {path: 'sample' for path in paths}
                manifest = manifest_for(files)
                pin = sha256(canonical_manifest(manifest)).hexdigest()
                self.assert_rejected(request_bytes(manifest, files), PIN_OPTION, pin)

    def draft_envelope(self):
        metadata = {
            'name': 'booster-ai',
            'version': '0.2.3',
            'license': 'MIT',
            'repository': 'https://github.com/QuillCrank/BoosterAI',
            'skills': './skills/',
            'onboardingSkill': './' + WRAPPER_PATH,
        }
        return {
            '.gitattributes': '* text=auto eol=lf\n',
            PLUGIN_METADATA_PATH: json.dumps(metadata, indent=2) + '\n',
            'LICENSE': 'Copyright \u00a9 Example\r\n',
            'verify_runtime_a84e3f19.py': 'raise AssertionError("builder must not execute verifier text")\n',
            WRAPPER_PATH: (
                '# BoosterAI Workflow\n\n'
                '    py -3.12 -I -S -B "<PLUGIN_ROOT>/verify_runtime_a84e3f19.py" '
                f'{WRAPPER_PIN_OPTION} {self.pin}\n'
            ),
        }

    def assert_draft_rejected(self, envelope):
        self.assert_rejected(
            request_bytes(self.manifest, self.files, envelope),
            PIN_OPTION, self.pin, ENVELOPE_PIN_OPTION, envelope_pin(envelope),
        )

    def test_builds_deterministic_full_draft_with_exact_bytes(self):
        envelope = self.draft_envelope()
        arguments = (PIN_OPTION, self.pin, ENVELOPE_PIN_OPTION, envelope_pin(envelope))
        first = self.run_builder(request_bytes(self.manifest, self.files, envelope), *arguments)
        second_request = json.dumps({
            'envelope': dict(reversed(list(envelope.items()))),
            'files': dict(reversed(list(self.files.items()))),
            'manifest': dict(reversed(list(self.manifest.items()))),
        }).encode('utf-8')
        second = self.run_builder(second_request, *arguments)
        for result in (first, second):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, b'')
        self.assertEqual(first.stdout, second.stdout)

        expected = {
            'runtime/manifest.json': self.manifest_bytes,
            **{f'runtime/{path}': content.encode('utf-8') for path, content in self.files.items()},
            **{path: content.encode('utf-8') for path, content in envelope.items()},
        }
        with ZipFile(BytesIO(first.stdout)) as archive:
            self.assertCountEqual(archive.namelist(), expected)
            self.assertTrue(all(entry.compress_type == ZIP_STORED for entry in archive.infolist()))
            for path, content in expected.items():
                self.assertEqual(archive.read(path), content)

    def test_current_owner_is_accepted_for_the_reviewed_envelope(self):
        """A new repository envelope keeps the exact plugin identity and bytes."""
        envelope = self.draft_envelope()
        metadata = json.loads(envelope[PLUGIN_METADATA_PATH])
        metadata['repository'] = 'https://github.com/QuillCrank/BoosterAI'
        envelope[PLUGIN_METADATA_PATH] = json.dumps(metadata, indent=2) + '\n'
        result = self.run_builder(request_bytes(self.manifest, self.files, envelope),
                                  PIN_OPTION, self.pin, ENVELOPE_PIN_OPTION, envelope_pin(envelope))
        self.assertEqual(result.returncode, 0, result.stderr)
        with ZipFile(BytesIO(result.stdout)) as archive:
            self.assertEqual(archive.read(PLUGIN_METADATA_PATH), envelope[PLUGIN_METADATA_PATH].encode())

    def test_requires_one_matching_envelope_pin_only_for_draft(self):
        envelope = self.draft_envelope()
        recipe_arguments = (PIN_OPTION, self.pin)
        envelope_arguments = (ENVELOPE_PIN_OPTION, envelope_pin(envelope))
        request = request_bytes(self.manifest, self.files, envelope)
        cases = {
            'draft without envelope pin': (request, recipe_arguments),
            'envelope pin without draft': (
                request_bytes(self.manifest, self.files), (*recipe_arguments, *envelope_arguments),
            ),
            'wrong envelope pin': (request, (*recipe_arguments, ENVELOPE_PIN_OPTION, 'e' * 64)),
            'identical repeated envelope pin': (
                request, (*recipe_arguments, *envelope_arguments, *envelope_arguments),
            ),
            'different repeated envelope pin': (
                request, (*recipe_arguments, ENVELOPE_PIN_OPTION, 'e' * 64, *envelope_arguments),
            ),
            'changed content under original pin': (
                request_bytes(self.manifest, self.files, {**envelope, 'LICENSE': 'edited\n'}),
                (*recipe_arguments, *envelope_arguments),
            ),
        }
        for case, (case_request, arguments) in cases.items():
            with self.subTest(case=case):
                self.assert_rejected(case_request, *arguments)

    def test_rejects_missing_and_extra_draft_envelope_files(self):
        envelope = self.draft_envelope()
        for missing in envelope:
            with self.subTest(missing=missing):
                self.assert_draft_rejected({path: text for path, text in envelope.items() if path != missing})
        self.assert_draft_rejected({**envelope, 'extra.md': 'extra content'})

    def test_rejects_incompatible_draft_metadata_even_with_matching_pin(self):
        envelope = self.draft_envelope()
        metadata = json.loads(envelope[PLUGIN_METADATA_PATH])
        wrong_values = {
            'name': 'other-plugin',
            'version': '0.2.3-rc.1',
            'license': 'Apache-2.0',
            'repository': 'https://example.com/BoosterAI',
            'skills': 'skills/',
            'onboardingSkill': './skills/other/SKILL.md',
        }
        for key, value in wrong_values.items():
            with self.subTest(key=key):
                changed = json.dumps({**metadata, key: value})
                self.assert_draft_rejected({**envelope, PLUGIN_METADATA_PATH: changed})

    def test_rejects_nonstandard_draft_metadata_json_even_with_matching_pin(self):
        envelope = self.draft_envelope()
        metadata = json.loads(envelope[PLUGIN_METADATA_PATH])
        malformed = json.dumps({**metadata, 'description': float('nan')})
        self.assert_draft_rejected({**envelope, PLUGIN_METADATA_PATH: malformed})

    def test_requires_one_correct_wrapper_pin_at_end_of_line(self):
        envelope = self.draft_envelope()
        wrapper = envelope[WRAPPER_PATH]
        cases = {
            'missing': '# BoosterAI Workflow\n',
            'wrong': wrapper.replace(self.pin, 'e' * 64),
            'identical repeated': wrapper + f'{WRAPPER_PIN_OPTION} {self.pin}\n',
            'different repeated': wrapper + f'{WRAPPER_PIN_OPTION} {"e" * 64}\n',
            'trailing text': wrapper.rstrip('\n') + ' --another-option\n',
        }
        for case, changed in cases.items():
            with self.subTest(case=case):
                self.assert_draft_rejected({**envelope, WRAPPER_PATH: changed})

    def test_rejects_wrapper_pin_or_command_hidden_in_html_comment(self):
        envelope = self.draft_envelope()
        wrapper = envelope[WRAPPER_PATH]
        pin = f'{WRAPPER_PIN_OPTION} {self.pin}'
        unpinned = wrapper.removesuffix(f' {pin}\n')
        heading, command = wrapper.split('\n\n', 1)
        cases = {
            'hidden pin with visible unpinned command': f'{unpinned}\n\n<!--\n{pin}\n-->\n',
            'entire command hidden': f'{heading}\n\n<!--\n{command}-->\n',
            'inline comment splits command': wrapper.replace('py -3.12', 'py -3.<!-- note -->12'),
        }
        for case, changed in cases.items():
            with self.subTest(case=case):
                self.assert_draft_rejected({**envelope, WRAPPER_PATH: changed})


if __name__ == '__main__':
    unittest.main()
