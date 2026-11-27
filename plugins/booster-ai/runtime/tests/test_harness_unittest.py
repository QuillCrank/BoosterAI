"""Exercise the harness's explicit, trusted unittest subprocess contract."""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from repo_temp import workspace


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/harness_unittest_b913ed42.py"


class HarnessUnittestTests(unittest.TestCase):
    def setUp(self):
        self.project = workspace(self)
        self.fixture = self.project / "acceptance.py"
        self.fixture.write_text(
            "import os\n"
            "import unittest\n"
            "class Acceptance(unittest.TestCase):\n"
            "    def test_pass(self):\n"
            "        self.assertEqual(2 + 2, 4)\n"
            "    def test_fail(self):\n"
            "        self.fail('private failure detail')\n"
            "    @unittest.skip('private skip detail')\n"
            "    def test_skip(self):\n"
            "        pass\n"
            "    @unittest.expectedFailure\n"
            "    def test_expected_failure(self):\n"
            "        self.fail()\n"
            "    @unittest.expectedFailure\n"
            "    def test_unexpected_success(self):\n"
            "        pass\n"
            "    def test_zero_exit_without_report(self):\n"
            "        os._exit(0)\n"
            "class ClassFixture(unittest.TestCase):\n"
            "    @classmethod\n"
            "    def setUpClass(cls):\n"
            "        cls.initialized = True\n"
            "    def test_requires_setup(self):\n"
            "        self.assertTrue(self.initialized)\n",
            encoding="utf-8",
        )
        self.runs = 0

    def invoke(self, case="Acceptance.test_pass", *, test="acceptance.py", edit=None):
        """Run the real isolated runner in one freshly owned child workspace."""
        self.runs += 1
        temp = self.project / ("run-" + str(self.runs))
        temp.mkdir()
        report = temp / "result.json"
        args = [sys.executable, "-I", "-S", "-B", str(RUNNER),
                "--root", str(self.project), "--test", test, "--case", case,
                "--report", str(report)]
        env = os.environ.copy()
        for name in ("TEMP", "TMP", "TMPDIR"):
            env[name] = str(temp)
        if edit:
            args, env, report = edit(args, env, report)
        process = subprocess.run(args, cwd=self.project, env=env, capture_output=True,
                                 text=True, timeout=30)
        return process, report

    def test_one_selected_case_runs_and_reports_its_actual_counts(self):
        process, path = self.invoke()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {
            "format": "boosterai-harness-unittest-v1", "case": "Acceptance.test_pass",
            "tests_run": 1, "failures": 0, "errors": 0, "skipped": 0,
            "expected_failures": 0, "unexpected_successes": 0,
            "access_denied": False, "success": True,
        })

    def test_failure_skip_and_expected_outcomes_never_succeed(self):
        cases = {
            "test_fail": "failures", "test_skip": "skipped",
            "test_expected_failure": "expected_failures",
            "test_unexpected_success": "unexpected_successes",
        }
        for method, field in cases.items():
            with self.subTest(method=method):
                process, path = self.invoke("Acceptance." + method)
                report_text = path.read_text(encoding="utf-8")
                report = json.loads(report_text)
                self.assertEqual(process.returncode, 1)
                self.assertEqual((report["tests_run"], report[field], report["success"]),
                                 (1, 1, False))
                self.assertNotIn("private", report_text)
                self.assertNotIn("traceback", report_text.lower())

    def test_class_setup_runs_for_the_selected_case(self):
        process, path = self.invoke("ClassFixture.test_requires_setup")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(json.loads(path.read_text(encoding="utf-8"))["success"])

    def test_missing_and_import_error_do_not_count_as_executed_tests(self):
        process, path = self.invoke("Acceptance.test_missing")
        self.assertEqual(process.returncode, 1)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["tests_run"], 0)
        self.fixture.write_text("raise ValueError('private import detail')\n", encoding="utf-8")
        process, path = self.invoke()
        self.assertEqual(process.returncode, 1)
        report_text = path.read_text(encoding="utf-8")
        self.assertEqual(json.loads(report_text)["errors"], 1)
        self.assertNotIn("private", report_text)

    def test_zero_process_exit_without_report_is_not_a_passing_result(self):
        process, path = self.invoke("Acceptance.test_zero_exit_without_report")
        self.assertEqual(process.returncode, 0)
        self.assertFalse(path.exists())

    def test_rejects_escaping_or_non_python_test(self):
        for test in ("../outside.py", "acceptance.txt", str(self.fixture)):
            with self.subTest(test=test):
                process, path = self.invoke(test=test)
                self.assertEqual(process.returncode, 2)
                self.assertFalse(path.exists())

    def test_rejects_existing_or_misplaced_report_without_overwriting(self):
        def existing(args, env, report):
            report.write_text("preserve", encoding="utf-8")
            return args, env, report

        process, path = self.invoke(edit=existing)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(path.read_text(encoding="utf-8"), "preserve")

        def misplaced(args, env, report):
            other = self.project / "not-result.json"
            args[-1] = str(other)
            return args, env, other

        process, path = self.invoke(edit=misplaced)
        self.assertEqual(process.returncode, 2)
        self.assertFalse(path.exists())

    def test_rejects_temp_outside_the_authorized_project(self):
        def outside(args, env, report):
            external_temp = self.project.parent
            env["TEMP"] = str(external_temp)
            external_report = external_temp / "result.json"
            args[-1] = str(external_report)
            return args, env, external_report

        process, _ = self.invoke(edit=outside)
        self.assertEqual(process.returncode, 2)


if __name__ == "__main__":
    unittest.main()
