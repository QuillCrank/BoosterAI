"""Fixture-free checks for raw archive pointer reads and failure readback."""

from contextlib import nullcontext
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import archive_sync

WORK = ROOT / 'in-memory-pointer-readback-limits'
ACTIVE = WORK / 'active.json'
PREVIOUS = WORK / 'previous.json'


class ArchivePointerReadbackLimitsTest(unittest.TestCase):
    def test_exact_limit_and_empty_files_keep_their_raw_bytes(self):
        payload = b'\x00\xff\r\n'
        limit = len(payload)
        for content in (payload, b''):
            with self.subTest(size=len(content)):
                stream = Mock(wraps=BytesIO(content))
                with (
                    patch.object(archive_sync, 'MAX_METADATA', limit),
                    patch.object(Path, 'open', autospec=True,
                                 return_value=nullcontext(stream)) as open_file,
                ):
                    self.assertEqual(archive_sync.read_pointer(WORK, ACTIVE.name), content)
                open_file.assert_called_once_with(ACTIVE, 'rb')
                stream.read.assert_called_once_with(limit + 1)

    def test_oversized_pointer_raises_fixed_error_after_one_extra_byte(self):
        limit = 8
        with BytesIO(b' ' * (limit + 8)) as data:
            stream = Mock(wraps=data)
            with (
                patch.object(archive_sync, 'MAX_METADATA', limit),
                patch.object(Path, 'open', autospec=True, return_value=nullcontext(stream)),
            ):
                with self.assertRaisesRegex(ValueError, '^archive pointer too large$'):
                    archive_sync.read_pointer(WORK, PREVIOUS.name)
            stream.read.assert_called_once_with(limit + 1)
            self.assertEqual(data.tell(), limit + 1)

    def test_only_missing_pointer_returns_none(self):
        with patch.object(Path, 'open', autospec=True, side_effect=FileNotFoundError):
            self.assertIsNone(archive_sync.read_pointer(WORK, ACTIVE.name))
        denied = PermissionError('pointer access denied')
        with patch.object(Path, 'open', autospec=True, side_effect=denied):
            with self.assertRaises(PermissionError) as raised:
                archive_sync.read_pointer(WORK, ACTIVE.name)
        self.assertIs(raised.exception, denied)

        read_error = OSError('pointer input error')
        stream = Mock()
        stream.read.side_effect = read_error
        with patch.object(Path, 'open', autospec=True, return_value=nullcontext(stream)):
            with self.assertRaises(OSError) as raised:
                archive_sync.read_pointer(WORK, PREVIOUS.name)
        self.assertIs(raised.exception, read_error)

    def test_oversized_readback_marks_only_that_pointer_unknown(self):
        commit = 'a' * 40
        active = json.dumps({'commit': commit}).encode('utf-8')
        previous = json.dumps({'commit': 'b' * 40}).encode('utf-8')
        limit = len(active)
        for oversized, peer in ((ACTIVE, PREVIOUS), (PREVIOUS, ACTIVE)):
            with self.subTest(pointer=oversized.name):
                content = {ACTIVE: active, PREVIOUS: previous}
                content[oversized] += b' ' * 8
                streams = {path: Mock(wraps=BytesIO(raw)) for path, raw in content.items()}
                with (
                    patch.object(archive_sync, 'MAX_METADATA', limit),
                    patch.object(Path, 'open', autospec=True,
                                 side_effect=lambda path, mode: nullcontext(streams[path])) as open_file,
                ):
                    result = archive_sync.pointer_failure(
                        WORK, ValueError('readback observation'), 'SYNC_FAILED', active, previous,
                    )
                self.assertEqual(open_file.call_args_list, [call(ACTIVE, 'rb'), call(PREVIOUS, 'rb')])
                for stream in streams.values():
                    stream.read.assert_called_once_with(limit + 1)
                self.assertEqual(result['status'], 'SYNC_FAILED')
                self.assertEqual(result['reason'], 'readback observation')
                self.assertEqual(result[f'{oversized.stem}_pointer_effect'], 'UNKNOWN')
                self.assertIsNone(result[f'{oversized.stem}_pointer_preserved'])
                self.assertEqual(result[f'{peer.stem}_pointer_effect'], 'UNCHANGED')
                self.assertIs(result[f'{peer.stem}_pointer_preserved'], True)
                self.assertIs(result['reconciliation_required'], True)
                self.assertEqual(result['active_commit_readback'], None if oversized == ACTIVE else commit)


if __name__ == '__main__':
    unittest.main()
