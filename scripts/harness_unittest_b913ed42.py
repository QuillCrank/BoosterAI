"""Run one explicitly selected, trusted project unittest for BoosterAI harness.

This helper executes project Python code and is not a sandbox. The host must
review and authorize the exact test, preserve STOP and other approval boundaries,
and decide whether its assertions represent meaningful product acceptance.
Only a sanitized count report is written; test output is managed by the caller.
"""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import unittest


# Isolated Python excludes the script directory from sys.path. Load the bundled
# path checks explicitly, without depending on a global installation.
_HELPER = Path(__file__).resolve().with_name("continuity_plan_64e9b1c2.py")
_SPEC = importlib.util.spec_from_file_location("boosterai_continuity", _HELPER)
_CORE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_CORE)
_CASE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\.(test_[A-Za-z0-9_]+)\Z")


def _paths(project, test, report):
    """Validate an explicit test and a new report inside the project's TEMP.

    ``project`` is an existing authorized root, ``test`` is a canonical
    project-relative Python file, and ``report`` must name TEMP/result.json.
    Existing components cannot be links or reparse points; test files must be
    regular single-linked files. Return checked root, test and report Paths.
    No file is created. Path validation is not a defense against hostile races.
    """
    root = _CORE._root(project)
    test_path, exists = _CORE._path(root, test)
    if not exists or test_path.suffix != ".py":
        raise ValueError("test must be an existing project Python file")
    raw_temp = os.environ.get("TEMP", "")
    temp = Path(raw_temp)
    report_path = Path(report)
    if not raw_temp or not temp.is_absolute() or not report_path.is_absolute():
        raise ValueError("TEMP and report must be absolute paths")
    temp = Path(os.path.abspath(temp))
    report_path = Path(os.path.abspath(report_path))
    if os.path.normcase(str(report_path)) != os.path.normcase(str(temp / "result.json")):
        raise ValueError("report must be TEMP/result.json")
    try:
        relative = report_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("report must be inside the project") from exc
    checked, already_exists = _CORE._path(root, relative)
    if already_exists or not temp.is_dir() or temp == root:
        raise ValueError("TEMP must exist and report must be new")
    return root, test_path, checked


def _empty_result(case):
    """Build a failure-default count report for the requested test case.

    ``case`` is the exact caller-selected identifier. Return a new, private-data
    free mapping; a missing/import-broken case remains a failure with zero tests.
    This function does not run code or touch the filesystem.
    """
    return {
        "format": "boosterai-harness-unittest-v1",
        "case": case,
        "tests_run": 0,
        "failures": 0,
        "errors": 1,
        "skipped": 0,
        "expected_failures": 0,
        "unexpected_successes": 0,
        "access_denied": False,
        "success": False,
    }


class _ClassifiedResult(unittest.TestResult):
    """Record only observed PermissionError types, never private exception text.

    Unittest captures fixture, test and subtest errors internally. Intercept the
    supplied exception before formatting it so the host can block a detected
    access denial. Caught or transformed exceptions, service responses, policy
    decisions and effects outside this test remain outside this classification.
    """

    def __init__(self):
        """Start with no observed access denial and standard unittest counts."""
        super().__init__()
        self.access_denied = False

    def _observe(self, error):
        """Remember a directly observed exception type without its message."""
        if error is not None and isinstance(error[1], PermissionError):
            self.access_denied = True

    def addError(self, test, error):
        """Classify an error before preserving unittest's ordinary accounting."""
        self._observe(error)
        super().addError(test, error)

    def addFailure(self, test, error):
        """Classify a failure before preserving unittest's ordinary accounting."""
        self._observe(error)
        super().addFailure(test, error)

    def addSubTest(self, test, subtest, error):
        """Inspect a subtest exception that unittest otherwise records directly."""
        self._observe(error)
        super().addSubTest(test, subtest, error)

    def addExpectedFailure(self, test, error):
        """Do not hide a permission denial behind an expected-failure marker."""
        self._observe(error)
        super().addExpectedFailure(test, error)


def run_case(root, test_path, case):
    """Execute exactly one named unittest and return sanitized actual counts.

    ``root`` and ``test_path`` are validated Paths. ``case`` is exactly
    ``TestClass.test_method`` with ASCII identifiers. The selected file is
    imported and its TestCase instance runs in this process. Missing classes,
    bad imports and missing methods produce a zero-test error report. Failure,
    error, skip, expected failure or unexpected success can never report success.
    Project test code is trusted, can have arbitrary effects, and is not sandboxed.
    A directly observed PermissionError sets ``access_denied`` without disclosing
    its details. Caught or transformed errors and other kinds of denial are not
    identified. The caller is responsible for authorization and timeout control.
    """
    report = _empty_result(case)
    match = _CASE.fullmatch(case)
    if match is None:
        return report
    os.chdir(root)
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(test_path.parent))
    try:
        spec = importlib.util.spec_from_file_location("boosterai_selected_test", test_path)
        if spec is None or spec.loader is None:
            return report
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        test_class = getattr(module, match.group(1), None)
        if not isinstance(test_class, type) or not issubclass(test_class, unittest.TestCase):
            return report
        if not callable(getattr(test_class, match.group(2), None)):
            return report
        test = test_class(match.group(2))
        actual = _ClassifiedResult()
        # Suite execution also runs module/class setup and teardown fixtures.
        unittest.TestSuite((test,)).run(actual)
    except PermissionError:
        report["access_denied"] = True
        return report
    except Exception:
        return report
    finally:
        sys.path.pop(0)
        sys.path.pop(0)
        sys.modules.pop("boosterai_selected_test", None)
    report.update({
        "tests_run": actual.testsRun,
        "failures": len(actual.failures),
        "errors": len(actual.errors),
        "skipped": len(actual.skipped),
        "expected_failures": len(actual.expectedFailures),
        "unexpected_successes": len(actual.unexpectedSuccesses),
        "access_denied": actual.access_denied,
    })
    report["success"] = (not report["access_denied"] and report["tests_run"] == 1 and all(
        report[name] == 0 for name in (
            "failures", "errors", "skipped", "expected_failures", "unexpected_successes"
        )
    ))
    return report


def _write_report(path, report):
    """Create a single sanitized JSON report exclusively at a validated path.

    ``path`` must be the new path returned by ``_paths`` and ``report`` a mapping
    returned by ``run_case``. Open with exclusive creation and no-follow where
    available; flush and fsync before returning. Existing files are never
    overwritten. Ordinary I/O failures propagate to the caller; path validation
    and exclusive creation do not prevent all hostile filesystem races.
    """
    data = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NOFOLLOW", 0))
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        info = os.fstat(output.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("report must be a single-linked regular file")
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def main(argv=None):
    """Validate arguments, run one trusted case, and publish its count report.

    Return zero only for exactly one passing, non-skipped case with no recorded
    failures or errors. Return one for a recorded unsuccessful case, 77 for a
    directly observed PermissionError during path checks or report I/O, and two
    for other invalid paths or report I/O. The fixed code 77 contains no error
    detail and cannot detect every policy or service denial. A process interruption
    may produce no report; the parent must treat other missing or malformed reports
    as ambiguous. No exception text, traceback or test output enters the report.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--test", required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    try:
        root, test, report = _paths(args.root, args.test, args.report)
        result = run_case(root, test, args.case)
        # Test code may have changed a directory or created the report path.
        # Recheck containment and nonexistence after it returns, before writing.
        if _paths(args.root, args.test, args.report) != (root, test, report):
            raise ValueError("paths changed during test")
        _write_report(report, result)
    except PermissionError:
        return 77
    except (OSError, ValueError):
        return 2
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
