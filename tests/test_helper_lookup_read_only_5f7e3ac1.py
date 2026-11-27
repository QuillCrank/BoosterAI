"""Check the helper registry reader and workspace creation boundary."""

from contextlib import redirect_stderr, redirect_stdout
from errno import ENOENT
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, call, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "project_tools_helper_lookup", ROOT / "scripts/project_tools.py",
)
PROJECT_TOOLS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROJECT_TOOLS)


class HelperLookupReadOnlyTests(unittest.TestCase):
    def test_missing_records_stop_before_metadata_lookup(self):
        records = Mock(spec=Path)
        records.is_dir.return_value = False
        records.mkdir.side_effect = AssertionError("helper lookup tried to create records")

        with patch.object(PROJECT_TOOLS, "safe", return_value=records) as safe:
            with self.assertRaisesRegex(ValueError, "^project records directory missing$"):
                PROJECT_TOOLS.helper_check(ROOT, "example", "1.0.0")

        safe.assert_called_once_with(ROOT, "_boosterAI")
        records.mkdir.assert_not_called()

    def test_existing_helper_checks_integrity_without_creating_records(self):
        metadata = {
            "format": "boosterai-helper-v1",
            "name": "example",
            "version": "1.0.0",
            "code": "helpers/example/1.0.0/example_3b7e41a9.py",
            "evidence": "evidence/example.json",
            "code_sha256": "a" * 64,
            "evidence_sha256": "b" * 64,
            "tested_target": "Python CLI",
            "evidence_authenticated": False,
        }
        records = Mock(spec=Path)
        records.is_dir.return_value = True
        records.mkdir.side_effect = AssertionError("helper lookup tried to create records")
        metadata_path = Mock(spec=Path)
        metadata_path.read_text.return_value = json.dumps(metadata)
        code_path = Mock(spec=Path)
        code_path.is_file.return_value = True
        evidence_path = Mock(spec=Path)
        evidence_path.is_file.return_value = True

        with (
            patch.object(PROJECT_TOOLS, "safe", side_effect=[
                records, metadata_path, code_path, evidence_path,
            ]) as safe,
            patch.object(PROJECT_TOOLS, "sha", side_effect=[
                metadata["code_sha256"], metadata["evidence_sha256"],
            ]) as sha,
        ):
            result = PROJECT_TOOLS.helper_check(ROOT, "example", "1.0.0")

        records.mkdir.assert_not_called()
        metadata_path.read_text.assert_called_once_with(encoding="utf-8")
        self.assertEqual(safe.call_args_list, [
            call(ROOT, "_boosterAI"),
            call(records, "helpers/example/1.0.0/metadata.json"),
            call(records, metadata["code"]),
            call(records, metadata["evidence"]),
        ])
        self.assertEqual(sha.call_args_list, [call(code_path), call(evidence_path)])
        self.assertEqual(result, {
            "path": metadata["code"],
            "metadata": metadata,
            "actual_runtime_retest_required_when_target_changes": True,
        })

    def test_default_workspace_still_requests_creation(self):
        records = Mock(spec=Path)
        with patch.object(PROJECT_TOOLS, "safe", return_value=records) as safe:
            result = PROJECT_TOOLS.workspace(ROOT)

        self.assertIs(result, records)
        safe.assert_called_once_with(ROOT, "_boosterAI")
        records.mkdir.assert_called_once_with(exist_ok=True)

    def test_cli_forwards_nonblank_root_unchanged(self):
        root = " selected project "
        argv = ["project_tools.py", "check-helper", "--root", root,
                "--name", "example", "--version", "1.0.0"]
        report = {"path": "helpers/example/1.0.0/example_3b7e41a9.py"}
        with (
            patch.object(sys, "argv", argv),
            patch.object(PROJECT_TOOLS, "helper_check", return_value=report) as check,
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()) as stderr,
        ):
            self.assertEqual(PROJECT_TOOLS.main(), 0)

        check.assert_called_once_with(root, "example", "1.0.0")
        self.assertEqual(json.loads(stdout.getvalue()), report)
        self.assertEqual(stderr.getvalue(), "")

    def test_cli_reports_missing_helper_file_without_path(self):
        root, name, version = " selected project ", "example", "1.0.0"
        argv = ["project_tools.py", "check-helper", "--root", root,
                "--name", name, "--version", version]
        path_sentinel = "synthetic-private-helper-path"
        missing_path = str(Path("synthetic-project") / path_sentinel / "metadata.json")
        error = FileNotFoundError(ENOENT, "No such file or directory", missing_path)

        with (
            patch.object(sys, "argv", argv),
            patch.object(PROJECT_TOOLS, "helper_check", side_effect=error) as check,
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()) as stderr,
        ):
            self.assertEqual(PROJECT_TOOLS.main(), 1)

        check.assert_called_once_with(root, name, version)
        output = stdout.getvalue()
        self.assertEqual(json.loads(output), {"error": "required helper file is missing"})
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn(missing_path, output)
        self.assertNotIn(path_sentinel, output)

    def test_cli_rejects_invalid_roots_before_helper_lookup(self):
        first, second = "first-private-root", "second-private-root"
        cases = (
            ["--root="],
            ["--root= "],
            ["--root=\t"],
            ["--root", first, f"--root={first}"],
            ["--root", first, "--root", second],
            ["--root", second, "--root", first],
        )
        for roots in cases:
            argv = ["project_tools.py", "check-helper", *roots,
                    "--name", "example", "--version", "1.0.0"]
            with (
                self.subTest(roots=roots),
                patch.object(sys, "argv", argv),
                patch.object(PROJECT_TOOLS, "helper_check") as check,
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(io.StringIO()) as stderr,
                self.assertRaises(SystemExit) as error,
            ):
                PROJECT_TOOLS.main()

            check.assert_not_called()
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(stdout.getvalue(), "")
            output = stderr.getvalue()
            self.assertTrue(output.splitlines()[-1].endswith(
                "check-helper: error: exactly one nonblank --root is required"
            ), output)
            self.assertNotIn(first, output)
            self.assertNotIn(second, output)

    def test_cli_rejects_conflicting_versions_before_helper_lookup(self):
        root = "private-project-root"
        first, second = "1.2.3", "4.5.6"
        for before, after in ((first, second), (second, first)):
            with self.subTest(versions=(before, after)):
                argv = ["project_tools.py", "check-helper", "--root", root,
                        "--name", "example", "--version", before, f"--version={after}"]
                with (
                    patch.object(sys, "argv", argv),
                    patch.object(PROJECT_TOOLS, "helper_check") as check,
                    redirect_stdout(io.StringIO()) as stdout,
                    redirect_stderr(io.StringIO()) as stderr,
                    self.assertRaises(SystemExit) as error,
                ):
                    PROJECT_TOOLS.main()

                check.assert_not_called()
                self.assertEqual(error.exception.code, 2)
                self.assertEqual(stdout.getvalue(), "")
                output = stderr.getvalue()
                self.assertTrue(output.splitlines()[-1].endswith(
                    "check-helper: error: conflicting --version values"
                ), output)
                self.assertNotIn(root, output)
                self.assertNotIn(first, output)
                self.assertNotIn(second, output)

    def test_cli_forwards_identical_repeated_versions_once(self):
        root, version = "selected project", "01.0.0"
        argv = ["project_tools.py", "check-helper", "--root", root,
                "--name", "example", "--version", version, f"--version={version}"]
        report = {"path": "helpers/example/01.0.0/example_3b7e41a9.py"}
        with (
            patch.object(sys, "argv", argv),
            patch.object(PROJECT_TOOLS, "helper_check", return_value=report) as check,
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()) as stderr,
        ):
            self.assertEqual(PROJECT_TOOLS.main(), 0)

        check.assert_called_once_with(root, "example", version)
        self.assertEqual(json.loads(stdout.getvalue()), report)
        self.assertEqual(stderr.getvalue(), "")

    def test_cli_rejects_conflicting_names_before_helper_lookup(self):
        root, version = "private-project-root", "1.2.3"
        first, second = "first-private-name", "second-private-name"
        cases = ((first, second), (second, first), ("", first), (first, ""))
        for before, after in cases:
            with self.subTest(names=(before, after)):
                argv = ["project_tools.py", "check-helper", "--root", root,
                        "--name", before, f"--name={after}", "--version", version]
                with (
                    patch.object(sys, "argv", argv),
                    patch.object(PROJECT_TOOLS, "helper_check") as check,
                    redirect_stdout(io.StringIO()) as stdout,
                    redirect_stderr(io.StringIO()) as stderr,
                    self.assertRaises(SystemExit) as error,
                ):
                    PROJECT_TOOLS.main()

                check.assert_not_called()
                self.assertEqual(error.exception.code, 2)
                self.assertEqual(stdout.getvalue(), "")
                output = stderr.getvalue()
                self.assertEqual(
                    output.splitlines()[-1],
                    "project_tools.py check-helper: error: conflicting --name values",
                )
                for value in (root, first, second, version):
                    self.assertNotIn(value, output)

    def test_cli_forwards_identical_repeated_names_once(self):
        root, name, version = " selected project ", "example-01", "01.0.0"
        argv = ["project_tools.py", "check-helper", "--root", root,
                "--name", name, f"--name={name}", "--version", version]
        report = {"path": "helpers/example-01/01.0.0/example_3b7e41a9.py"}
        with (
            patch.object(sys, "argv", argv),
            patch.object(PROJECT_TOOLS, "helper_check", return_value=report) as check,
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()) as stderr,
        ):
            self.assertEqual(PROJECT_TOOLS.main(), 0)

        check.assert_called_once_with(root, name, version)
        self.assertEqual(json.loads(stdout.getvalue()), report)
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
