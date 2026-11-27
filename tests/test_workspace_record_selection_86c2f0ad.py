"""Exercise workspace record selection with in-memory filesystem boundaries."""

import importlib.util
import json
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path, PurePath
from unittest.mock import patch


source = Path(__file__).resolve().parents[1] / "scripts" / "booster.py"
spec = importlib.util.spec_from_file_location("booster", source)
booster = importlib.util.module_from_spec(spec)
spec.loader.exec_module(booster)

ROOT_RECORD = PurePath("PROJECT_RECORDS.md")
NESTED_RECORD = PurePath("_boosterAI") / ROOT_RECORD


class MemoryPath:
    def __init__(self, project, path):
        self.project = project
        self.path = path

    def exists(self):
        return self.is_file()

    def is_file(self):
        return self.path in self.project.files

    def mkdir(self, *, exist_ok):
        self.project.mkdirs.append(self.path)

    def relative_to(self, root):
        return self.path.relative_to(root)

    @contextmanager
    def open(self, mode, *, encoding):
        self.project.opens.append((self.path, mode))
        if mode != "x":
            raise AssertionError(f"unexpected open mode {mode!r} for {self.path}")
        if self.exists():
            raise FileExistsError(self.path)
        with StringIO() as stream:
            yield stream
            self.project.files[self.path] = stream.getvalue().encode(encoding)


class MemoryProject:
    def __init__(self, files=()):
        self.root = PurePath()
        self.files = dict(files)
        self.mkdirs = []
        self.opens = []

    def checked(self, root, relative):
        return MemoryPath(self, root / relative)

    @contextmanager
    def filesystem_boundaries(self):
        with (
            patch.object(booster, "checked_root", return_value=self.root),
            patch.object(booster, "checked", side_effect=self.checked),
            patch.object(booster, "partials_init", return_value={}) as partials_init,
        ):
            yield partials_init

    def records(self):
        return {path: content for path, content in self.files.items()
                if path.name == ROOT_RECORD.name}

    def record_opens(self):
        return [(path, mode) for path, mode in self.opens
                if path.name == ROOT_RECORD.name]


class WorkspaceRecordSelectionTests(unittest.TestCase):
    def test_absent_records_create_one_nested_default_exclusively(self):
        project = MemoryProject()
        with project.filesystem_boundaries():
            report = booster.workspace_init(project.root, create_records=True)

        self.assertEqual(report["project_record"], str(NESTED_RECORD))
        self.assertEqual(report["records_status"], "selected")
        self.assertEqual(set(project.records()), {NESTED_RECORD})
        self.assertTrue(project.files[NESTED_RECORD])
        self.assertEqual(project.record_opens(), [(NESTED_RECORD, "x")])

    def test_lone_root_record_is_reused_in_place(self):
        self.assert_reuse({ROOT_RECORD: b"# Custom legacy ledger\r\n"},
                          ROOT_RECORD, create_records=True)

    def test_lone_nested_record_is_reused_in_place(self):
        self.assert_reuse({NESTED_RECORD: b"# Custom nested ledger\n"},
                          NESTED_RECORD, create_records=True)

    def test_both_defaults_reject_before_scaffolding(self):
        records = {ROOT_RECORD: b"root", NESTED_RECORD: b"nested"}
        project = MemoryProject(records)
        with project.filesystem_boundaries() as partials_init:
            with self.assertRaisesRegex(ValueError, "select one with --records-existing"):
                booster.workspace_init(project.root, create_records=True)

        partials_init.assert_not_called()
        self.assertEqual(project.mkdirs, [])
        self.assertEqual(project.opens, [])
        self.assertEqual(project.files, records)

    def test_explicit_selection_reuses_either_default_when_both_exist(self):
        records = {ROOT_RECORD: b"root", NESTED_RECORD: b"nested"}
        for selected in records:
            with self.subTest(selected=selected):
                self.assert_reuse(records, selected, records_existing=selected.as_posix())

    def test_duplicate_existing_selectors_fail_before_workspace_init(self):
        first = ROOT_RECORD.as_posix()
        for second in (first, NESTED_RECORD.as_posix()):
            args = [
                str(source), "workspace-init", "--root", ".",
                "--records-existing", first, f"--records-existing={second}",
            ]
            with (
                self.subTest(second=second),
                patch.object(booster.sys, "argv", args),
                patch.object(booster, "workspace_init") as initialize,
                redirect_stdout(StringIO()) as stdout,
                redirect_stderr(StringIO()) as stderr,
            ):
                with self.assertRaises(SystemExit) as error:
                    booster.main()

                self.assertEqual(error.exception.code, 2)
                self.assertEqual(stdout.getvalue(), "")
                diagnostic = stderr.getvalue()
                self.assertIn("--records-existing may only be supplied once", diagnostic)
                self.assertNotIn(first, diagnostic)
                self.assertNotIn(second, diagnostic)
                initialize.assert_not_called()

    def test_empty_existing_selector_cli_fails_before_resolving_root(self):
        args = [str(source), "workspace-init", "--root", ".", "--records-existing="]
        with (
            patch.object(booster.sys, "argv", args),
            patch.object(booster, "checked_root") as check_root,
            redirect_stdout(StringIO()) as stdout,
            redirect_stderr(StringIO()) as stderr,
        ):
            self.assertEqual(booster.main(), 1)

        self.assertEqual(json.loads(stdout.getvalue()), {"error": "empty project record selector"})
        self.assertEqual(stderr.getvalue(), "")
        check_root.assert_not_called()

    def test_omitted_and_empty_existing_selectors_are_distinct(self):
        records = {ROOT_RECORD: b"root record"}
        project = MemoryProject(records)
        with project.filesystem_boundaries():
            report = booster.workspace_init(project.root, records_existing=None)

        self.assertIsNone(report["project_record"])
        self.assertEqual(report["records_status"], "not_selected_inspect_existing_system")
        self.assertEqual(project.records(), records)
        self.assertEqual(project.record_opens(), [])

        with patch.object(booster, "checked_root") as check_root:
            for create_records in (False, True):
                with self.subTest(create_records=create_records):
                    with self.assertRaisesRegex(ValueError, "^empty project record selector$"):
                        booster.workspace_init(".", records_existing="", create_records=create_records)
        check_root.assert_not_called()

    def assert_reuse(self, records, selected, **options):
        project = MemoryProject(records)
        with project.filesystem_boundaries():
            report = booster.workspace_init(project.root, **options)

        self.assertEqual(report["project_record"], str(selected))
        self.assertEqual(report["records_status"], "selected")
        self.assertEqual(project.records(), records)
        self.assertEqual(project.record_opens(), [])


if __name__ == "__main__":
    unittest.main()
