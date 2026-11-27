"""Check runtime request selection without creating workspace files."""

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("booster_runtime_requests", ROOT / "scripts/booster.py")
BOOSTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOSTER)
RUNTIME_ARGV = ["booster.py", "runtime-check", "--language", "python"]


class RuntimeRequestConflictTests(unittest.TestCase):
    def test_distinct_languages_fail_before_runtime_verification(self):
        for first, second in (("python", "dotnet"), ("dotnet", "python")):
            argv = ["booster.py", "runtime-check", "--language", first, f"--language={second}"]
            with (
                self.subTest(first=first, second=second),
                patch.object(sys, "argv", argv),
                patch.object(BOOSTER, "runtime_check") as check,
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(io.StringIO()) as stderr,
                self.assertRaises(SystemExit) as error,
            ):
                BOOSTER.main()
            check.assert_not_called()
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                stderr.getvalue().splitlines()[-1],
                "booster.py runtime-check: error: conflicting --language values",
            )

    def test_single_and_identical_languages_reach_runtime_verification_once(self):
        report = {"valid": True}
        for language, observed in (("python", "3.12.7"), ("dotnet", "10.0.100")):
            for repeated in ((), (f"--language={language}",)):
                argv = ["booster.py", "runtime-check", "--language", language,
                        *repeated, "--observed-version", observed]
                with (
                    self.subTest(language=language, repeated=repeated),
                    patch.object(sys, "argv", argv),
                    patch.object(BOOSTER, "runtime_check", return_value=report) as check,
                    redirect_stdout(io.StringIO()) as stdout,
                    redirect_stderr(io.StringIO()) as stderr,
                ):
                    status = BOOSTER.main()
                check.assert_called_once_with(language, observed, None)
                self.assertEqual(status, 0)
                self.assertEqual(json.loads(stdout.getvalue()), report)
                self.assertEqual(stderr.getvalue(), "")

    def test_distinct_versions_fail_before_runtime_verification(self):
        for first, second in (("3.11", "3.12.0"), ("3.12.0", "3.11")):
            argv = [*RUNTIME_ARGV, "--requested-version", first, f"--requested-version={second}"]
            with (
                self.subTest(first=first, second=second),
                patch.object(sys, "argv", argv),
                patch.object(BOOSTER, "runtime_check") as check,
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(io.StringIO()) as stderr,
                self.assertRaises(SystemExit) as error,
            ):
                BOOSTER.main()
            check.assert_not_called()
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertTrue(stderr.getvalue().splitlines()[-1].endswith(
                "runtime-check: error: conflicting --requested-version values"))
            self.assertNotIn(first, stderr.getvalue())
            self.assertNotIn(second, stderr.getvalue())

    def test_default_single_and_identical_requests_keep_runtime_results(self):
        matching = ".".join(str(part) for part in sys.version_info[:3])
        mismatching = "999.999"
        cases = (
            (None, []),
            (matching, ["--requested-version", matching]),
            (mismatching, ["--requested-version", mismatching]),
            (matching, ["--requested-version", matching, f"--requested-version={matching}"]),
            (mismatching, ["--requested-version", mismatching, f"--requested-version={mismatching}"]),
        )
        for requested, options in cases:
            expected = BOOSTER.runtime_check("python", requested_version=requested)
            with (
                self.subTest(options=options),
                patch.object(sys, "argv", [*RUNTIME_ARGV, *options]),
                patch.object(BOOSTER, "runtime_check", wraps=BOOSTER.runtime_check) as check,
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(io.StringIO()) as stderr,
            ):
                status = BOOSTER.main()
            check.assert_called_once_with("python", None, requested)
            self.assertEqual(status, int(not expected["valid"]))
            self.assertEqual(json.loads(stdout.getvalue()), expected)
            self.assertEqual(stderr.getvalue(), "")

    def test_distinct_observations_fail_before_runtime_verification(self):
        cases = (
            ("python", "3.11.9", "3.12.7"),
            ("python", "3.12.7", "3.11.9"),
            ("dotnet", "9.0.101", "10.0.100"),
            ("dotnet", "10.0.100", "9.0.101"),
        )
        for language, first, second in cases:
            argv = ["booster.py", "runtime-check", "--language", language,
                    "--observed-version", first, f"--observed-version={second}"]
            with (
                self.subTest(language=language, first=first, second=second),
                patch.object(sys, "argv", argv),
                patch.object(BOOSTER, "runtime_check") as check,
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(io.StringIO()) as stderr,
                self.assertRaises(SystemExit) as error,
            ):
                BOOSTER.main()
            check.assert_not_called()
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertTrue(stderr.getvalue().splitlines()[-1].endswith(
                "runtime-check: error: conflicting --observed-version values"))
            self.assertNotIn(first, stderr.getvalue())
            self.assertNotIn(second, stderr.getvalue())

    def test_requested_conflict_takes_precedence_over_observed_conflict(self):
        argv = [*RUNTIME_ARGV, "--observed-version", "3.11.9", "--observed-version=3.12.7",
                "--requested-version", "3.11", "--requested-version=3.12"]
        with (
            patch.object(sys, "argv", argv),
            patch.object(BOOSTER, "runtime_check") as check,
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()) as stderr,
            self.assertRaises(SystemExit) as error,
        ):
            BOOSTER.main()
        check.assert_not_called()
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertTrue(stderr.getvalue().splitlines()[-1].endswith(
            "runtime-check: error: conflicting --requested-version values"))
        self.assertNotIn("3.11", stderr.getvalue())
        self.assertNotIn("3.12", stderr.getvalue())

    def test_default_single_and_identical_observations_keep_runtime_results(self):
        cases = (
            ("python", None, []),
            ("python", "3.12.7", ["--observed-version", "3.12.7"]),
            ("python", "3.12.7", ["--observed-version", "3.12.7", "--observed-version=3.12.7"]),
            ("dotnet", "9.0.101", ["--observed-version", "9.0.101"]),
            ("dotnet", "9.0.101", ["--observed-version", "9.0.101", "--observed-version=9.0.101"]),
        )
        for language, observed, options in cases:
            expected = BOOSTER.runtime_check(language, observed)
            argv = ["booster.py", "runtime-check", "--language", language, *options]
            with (
                self.subTest(language=language, options=options),
                patch.object(sys, "argv", argv),
                patch.object(BOOSTER, "runtime_check", wraps=BOOSTER.runtime_check) as check,
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(io.StringIO()) as stderr,
            ):
                status = BOOSTER.main()
            check.assert_called_once_with(language, observed, None)
            self.assertEqual(status, int(not expected["valid"]))
            self.assertEqual(json.loads(stdout.getvalue()), expected)
            self.assertEqual(stderr.getvalue(), "")

    def test_empty_observation_is_forwarded_to_runtime_verification(self):
        cases = (["--observed-version="], ["--observed-version", "", "--observed-version="])
        for options in cases:
            with (
                self.subTest(options=options),
                patch.object(sys, "argv", [*RUNTIME_ARGV, *options]),
                patch.object(BOOSTER, "runtime_check", wraps=BOOSTER.runtime_check) as check,
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(io.StringIO()) as stderr,
            ):
                status = BOOSTER.main()
            check.assert_called_once_with("python", "", None)
            self.assertEqual(status, 1)
            self.assertEqual(json.loads(stdout.getvalue()), {
                "error": "version must be a stable numeric major.minor or major.minor.patch",
            })
            self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
