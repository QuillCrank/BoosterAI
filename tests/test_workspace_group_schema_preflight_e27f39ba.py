"""Exercise workspace schema preflight with in-memory filesystem effects."""

from contextlib import ExitStack, contextmanager
import datetime as dt
import hashlib
import importlib.util
from io import BytesIO, StringIO
import json
from pathlib import Path, PurePosixPath
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'booster.py'
SPEC = importlib.util.spec_from_file_location('booster_workspace_schema_preflight', SCRIPT)
BOOSTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOSTER)

SCHEMA = 'references/operating-algorithm.md'
ALGORITHM = '_boosterAI/algorithm.md'
PROVENANCE = '_boosterAI/algorithm.provenance.json'
PARTIALS = '_boosterAI/_partials'
RECORD = '_boosterAI/PROJECT_RECORDS.md'
ORIGIN_MTIME_NS = 123456789


class MemoryWorkspace:
    def __init__(self, files=(), source_read_error=None):
        self.files = dict(files)
        self.directories = {'.'}
        for name in self.files:
            self.directories.update(parent.as_posix() for parent in PurePosixPath(name).parents)
        self.source_read_error = source_read_error
        self.events = []

    def name(self, path):
        return path.relative_to(ROOT).as_posix()

    def exists(self, path):
        name = self.name(path)
        return name in self.files or name in self.directories

    def is_file(self, path):
        return self.name(path) in self.files

    def lstat(self, path):
        if not self.exists(path):
            raise FileNotFoundError(self.name(path))
        kind = stat.S_IFREG if self.is_file(path) else stat.S_IFDIR
        return SimpleNamespace(st_mode=kind, st_nlink=1, st_mtime_ns=ORIGIN_MTIME_NS)

    def stat(self, path):
        self.events.append(('stat', self.name(path)))
        return self.lstat(path)

    def read_bytes(self, path):
        name = self.name(path)
        self.events.append(('read', name))
        if name == SCHEMA and self.source_read_error is not None:
            raise self.source_read_error
        return self.files[name]

    def mkdir(self, path, *, parents=False, exist_ok=False):
        name = self.name(path)
        self.events.append(('mkdir', name))
        if self.is_file(path) or (name in self.directories and not exist_ok):
            raise FileExistsError(name)
        if parents:
            self.directories.update(parent.as_posix() for parent in PurePosixPath(name).parents)
        elif PurePosixPath(name).parent.as_posix() not in self.directories:
            raise FileNotFoundError(name)
        self.directories.add(name)

    @contextmanager
    def open(self, path, mode, *, encoding=None, newline=None):
        name = self.name(path)
        self.events.append(('open', name, mode))
        if mode not in ('x', 'xb'):
            raise AssertionError('workspace_init must use exclusive creation')
        if self.exists(path):
            raise FileExistsError(name)
        if PurePosixPath(name).parent.as_posix() not in self.directories:
            raise FileNotFoundError(name)
        with BytesIO() if mode == 'xb' else StringIO() as stream:
            yield stream
            data = stream.getvalue()
            self.files[name] = data if mode == 'xb' else data.encode(encoding)

    def mutations(self):
        return [event for event in self.events if event[0] in ('mkdir', 'open')]

    @contextmanager
    def filesystem(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(BOOSTER, 'checked_root', return_value=ROOT))
            stack.enter_context(patch.object(Path, 'resolve', lambda path: path))
            for name in ('exists', 'is_file', 'lstat', 'stat', 'read_bytes', 'mkdir', 'open'):
                handler = getattr(self, name)
                stack.enter_context(patch.object(
                    Path, name, lambda path, *args, call=handler, **kwargs: call(path, *args, **kwargs),
                ))
            yield


class WorkspaceSchemaPreflightTests(unittest.TestCase):
    def test_invalid_schema_rejects_before_scaffold_or_record_creation(self):
        original = b'# source\r\n'
        cases = (
            ('missing source', SCHEMA, {}, None, 'schema must be an ordinary file'),
            ('generated index source', PARTIALS + '/README.md', {}, None, 'schema must be an ordinary file'),
            ('generated record source', RECORD, {}, None, 'schema must be an ordinary file'),
            ('algorithm conflict', SCHEMA, {SCHEMA: original, ALGORITHM: b'keep'}, None,
             'algorithm or provenance exists'),
            ('provenance conflict', SCHEMA, {SCHEMA: original, PROVENANCE: b'keep'}, None,
             'algorithm or provenance exists'),
            ('source read failure', SCHEMA, {SCHEMA: original}, OSError('source read failed'), 'source read failed'),
        )
        for name, schema, files, failure, message in cases:
            with self.subTest(case=name):
                workspace = MemoryWorkspace(files, failure)
                directories = workspace.directories.copy()
                error_type = OSError if failure is not None else ValueError
                with workspace.filesystem(), self.assertRaisesRegex(error_type, message):
                    BOOSTER.workspace_init(ROOT, create_records=True, schema=schema)
                self.assertEqual(workspace.mutations(), [])
                self.assertEqual(workspace.files, files)
                self.assertEqual(workspace.directories, directories)

    def test_valid_schema_copies_raw_bytes_with_provenance_and_selects_nested_record(self):
        for payload in (b'', b'\xff# Raw schema\r\nline\n'):
            with self.subTest(size=len(payload)):
                workspace = MemoryWorkspace({SCHEMA: payload})
                with workspace.filesystem():
                    report = BOOSTER.workspace_init(ROOT, create_records=True, schema=SCHEMA)

                self.assertEqual(workspace.events[:3], [('stat', SCHEMA), ('read', SCHEMA), ('stat', SCHEMA)])
                self.assertEqual([event for event in workspace.events if event[0] == 'read'], [('read', SCHEMA)])
                self.assertEqual(workspace.files[SCHEMA], payload)
                self.assertEqual(workspace.files[ALGORITHM], payload)
                self.assertEqual(report['algorithm'], 'copied_with_provenance')
                self.assertEqual(report['records_status'], 'selected')
                self.assertEqual(report['project_record'], str(Path(RECORD)))
                self.assertIn(RECORD, workspace.files)
                self.assertNotIn('PROJECT_RECORDS.md', workspace.files)
                self.assertTrue(report['created'])
                self.assertIn(PARTIALS + '/README.md', workspace.files)
                self.assertIn(('open', ALGORITHM, 'xb'), workspace.events)
                self.assertIn(('open', PROVENANCE, 'x'), workspace.events)
                origin = json.loads(workspace.files[PROVENANCE])
                copied_at = dt.datetime.fromisoformat(origin.pop('copied_at'))
                self.assertEqual(copied_at.utcoffset(), dt.timedelta(0))
                self.assertEqual(origin, {
                    'source': SCHEMA,
                    'source_sha256': hashlib.sha256(payload).hexdigest(),
                    'source_mtime_ns': ORIGIN_MTIME_NS,
                    'relationship': 'working-copy; source preserved; compare hash before reuse',
                })

    def test_omitted_or_empty_schema_uses_normal_scaffolding(self):
        record = b'# Existing record\r\n'
        cases = (
            ({'VALIDATION.md': record}, 'created_empty_not_verified', [('open', ALGORITHM, 'x')]),
            ({'VALIDATION.md': record, ALGORITHM: b'keep', PROVENANCE: b'keep'}, 'preserved_existing', []),
        )
        for files, status, algorithm_opens in cases:
            for schema in (None, ''):
                with self.subTest(schema=schema, status=status):
                    workspace = MemoryWorkspace(files)
                    with workspace.filesystem():
                        report = BOOSTER.workspace_init(ROOT, records_existing='VALIDATION.md', schema=schema)

                    self.assertEqual(report['project_record'], 'VALIDATION.md')
                    self.assertEqual(report['algorithm'], status)
                    self.assertEqual({path: workspace.files[path] for path in files}, files)
                    self.assertEqual(PROVENANCE in workspace.files, PROVENANCE in files)
                    self.assertEqual([event for event in workspace.events if event[:2] == ('open', ALGORITHM)],
                                     algorithm_opens)
                    self.assertFalse(any(event[0] in ('stat', 'read') for event in workspace.events))

    def test_late_provenance_conflict_preserves_existing_record_and_stops_copy(self):
        record = b'# Existing record\r\n'
        workspace = MemoryWorkspace({SCHEMA: b'schema', 'VALIDATION.md': record})

        def concurrent_provenance(path, **kwargs):
            workspace.mkdir(path, **kwargs)
            if workspace.name(path) == PARTIALS:
                workspace.files[PROVENANCE] = b'concurrent provenance'

        with workspace.filesystem(), patch.object(Path, 'mkdir', concurrent_provenance):
            with self.assertRaisesRegex(ValueError, 'algorithm or provenance exists'):
                BOOSTER.workspace_init(ROOT, records_existing='VALIDATION.md', schema=SCHEMA)

        self.assertEqual(workspace.files['VALIDATION.md'], record)
        self.assertEqual(workspace.files[PROVENANCE], b'concurrent provenance')
        self.assertNotIn(ALGORITHM, workspace.files)
        self.assertNotIn(('open', ALGORITHM, 'xb'), workspace.events)
        self.assertNotIn(('open', PROVENANCE, 'x'), workspace.events)


if __name__ == '__main__':
    unittest.main()
