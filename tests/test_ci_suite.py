"""Exercise the optional CI runtime preparation without network or execution."""

import hashlib
import importlib.util
import io
from email.message import Message
from pathlib import Path
import stat
import unittest
import urllib.request
from unittest.mock import patch
import zipfile

from repo_temp import workspace


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ci_suite_tested', ROOT / 'tests/ci_suite_c4e2b179.py')
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


def zip_bytes(members):
    """Build an in-memory ZIP containing the supplied controlled member entries.

    ``members`` is a sequence of ``(name_or_info, bytes)`` pairs. No filesystem
    objects or actual links are created, and the resulting bytes are inert.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members:
            archive.writestr(name, content)
    return buffer.getvalue()


class FixtureResponse(io.BytesIO):
    """Represent a bounded, network-free response with an explicit final URL."""

    def __init__(self, payload, url):
        """Keep the caller's inert bytes and final URL in memory."""
        super().__init__(payload)
        self.url = url

    def geturl(self):
        """Return the fake final URL without making any request."""
        return self.url


class CIRuntimePreparationTests(unittest.TestCase):
    """Verify integrity, scope and extraction barriers in owned fixtures."""

    def setUp(self):
        """Register immediate cleanup of a unique repository-local fixture."""
        self.operation = workspace(self)

    def opener_for(self, payload, final_url=None):
        """Return a mock opener supplying bytes without touching the network."""
        response = FixtureResponse(payload, final_url or ci.PYTHON_310_URL)
        opener = unittest.mock.Mock()
        opener.open.return_value = response
        return opener

    def test_matching_fixture_extracts_but_does_not_execute(self):
        """A digest-matching, contained test ZIP returns inert runtime bytes."""
        payload = zip_bytes([('LICENSE.txt', b'fixture license'), ('python.exe', b'not executable')])
        opener = self.opener_for(payload)
        with patch.object(ci, 'PYTHON_310_BYTES', len(payload)), \
             patch.object(ci, 'PYTHON_310_SHA256', hashlib.sha256(payload).hexdigest()), \
             patch.object(ci.urllib.request, 'build_opener', return_value=opener) as build, \
             patch.object(ci.subprocess, 'run') as execute:
            executable = ci.prepare_official_python_310(self.operation)
        self.assertEqual(executable, self.operation / 'python310/python.exe')
        self.assertEqual(executable.read_bytes(), b'not executable')
        self.assertEqual((executable.parent / 'LICENSE.txt').read_bytes(), b'fixture license')
        self.assertIsInstance(build.call_args.args[0], ci.RejectRedirect)
        opener.open.assert_called_once_with(ci.PYTHON_310_URL, timeout=45)
        execute.assert_not_called()

    def test_digest_mismatch_stops_before_runtime_extraction(self):
        """Tampered data is refused before a runtime or child process is created."""
        payload = b'tampered bytes'
        opener = self.opener_for(payload)
        with patch.object(ci, 'PYTHON_310_BYTES', len(payload)), \
             patch.object(ci.urllib.request, 'build_opener', return_value=opener), \
             patch.object(ci.subprocess, 'run') as execute:
            with self.assertRaisesRegex(ValueError, 'pinned digest'):
                ci.prepare_official_python_310(self.operation)
        self.assertFalse((self.operation / 'python310').exists())
        opener.open.assert_called_once()
        execute.assert_not_called()

    def test_oversized_response_stops_before_runtime_extraction(self):
        """The reader stops once actual bytes exceed the fixed archive bound."""
        payload = b'five!'
        opener = self.opener_for(payload)
        with patch.object(ci, 'PYTHON_310_BYTES', len(payload) - 1), \
             patch.object(ci.urllib.request, 'build_opener', return_value=opener), \
             patch.object(ci.subprocess, 'run') as execute:
            with self.assertRaisesRegex(ValueError, 'exceeds pinned size'):
                ci.prepare_official_python_310(self.operation)
        self.assertFalse((self.operation / 'python310').exists())
        opener.open.assert_called_once()
        execute.assert_not_called()

    def test_redirect_is_rejected_without_following_target(self):
        """A real urllib redirect chain makes no request to the second host."""
        requests = []

        class RedirectResponse(FixtureResponse):
            """Expose a synthetic 302 response to urllib's own redirect handler."""

            def __init__(self, url):
                """Return an empty 302 response pointing at a non-routable host."""
                super().__init__(b'', url)
                self.code = 302
                self.status = 302
                self.msg = 'Found'
                self.headers = Message()
                self.headers['Location'] = 'https://unexpected.invalid/runtime.zip'

            def info(self):
                """Return synthetic redirect headers without a network access."""
                return self.headers

        class MemoryHTTPSHandler(urllib.request.HTTPSHandler):
            """Supply one fake redirect and record every attempted request."""

            def https_open(self, request):
                """Record the requested URL and return a local 302 response."""
                requests.append(request.full_url)
                return RedirectResponse(request.full_url)

        opener = urllib.request.build_opener(ci.RejectRedirect(), MemoryHTTPSHandler())
        with patch.object(ci.urllib.request, 'build_opener', return_value=opener), \
             patch.object(ci.subprocess, 'run') as execute:
            with self.assertRaisesRegex(ValueError, 'redirected'):
                ci.prepare_official_python_310(self.operation)
        self.assertEqual(requests, [ci.PYTHON_310_URL])
        self.assertFalse((self.operation / 'python310').exists())
        execute.assert_not_called()

    def test_zip_traversal_and_link_entries_are_rejected(self):
        """Malicious member metadata cannot escape or create a filesystem link."""
        link = zipfile.ZipInfo('link-entry')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for name in ('../escape.txt', link):
            with self.subTest(member=str(name)):
                operation = self.operation / ('traversal' if isinstance(name, str) else 'link')
                operation.mkdir()
                payload = zip_bytes([(name, b'not executable')])
                opener = self.opener_for(payload)
                with patch.object(ci, 'PYTHON_310_BYTES', len(payload)), \
                     patch.object(ci, 'PYTHON_310_SHA256', hashlib.sha256(payload).hexdigest()), \
                     patch.object(ci.urllib.request, 'build_opener', return_value=opener), \
                     patch.object(ci.subprocess, 'run') as execute:
                    with self.assertRaisesRegex(ValueError, 'unsafe official Python archive member'):
                        ci.prepare_official_python_310(operation)
                self.assertFalse((operation / 'escape.txt').exists())
                self.assertFalse((operation / 'python310/link-entry').exists())
                execute.assert_not_called()

    def test_checkout_root_is_rejected_before_any_request(self):
        """A caller cannot put the runtime directly into the repository root."""
        with patch.object(ci.urllib.request, 'build_opener') as opener:
            with self.assertRaisesRegex(ValueError, 'inside checkout scratch'):
                ci.prepare_official_python_310(ROOT)
        opener.assert_not_called()


if __name__ == '__main__':
    unittest.main()
