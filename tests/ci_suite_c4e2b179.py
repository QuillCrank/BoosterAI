"""Run the portable, explicitly bounded unittest selection in an owned workspace."""

import argparse
import hashlib
import importlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_MODULES = {"test_find_dirs_partial.py", "test_continuity_hook.py",
                    "test_harness_unittest.py"}
EXCLUDED_CASE = "test_self_check_reports_missing_and_broken_registration"
PYTHON_310_URL = "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip"
PYTHON_310_BYTES = 8629277
PYTHON_310_SHA256 = "608619f8619075629c9c69f361352a0da6ed7e62f83a0e19c63e0ea32eb7629d"


class RejectRedirect(urllib.request.HTTPRedirectHandler):
    """Prevent any redirected request while fetching the fixed CI runtime URL."""

    def redirect_request(self, request, response, code, message, headers, new_url):
        """Reject the redirect before making a request to the new destination."""
        raise ValueError("official Python download redirected")


def test_workspace():
    """Return the existing or newly created safe repository-local temporary root.

    Reject symbolic links, Windows reparse points and paths resolving outside the
    checkout. The directory is only a parent for owned, unique operations; this
    function never deletes existing children or alters any global configuration.
    """
    base = ROOT / "_temp-files"
    base.mkdir(exist_ok=True)
    info = base.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
            or base.resolve().parent != ROOT):
        raise ValueError("temporary root must be an ordinary checkout directory")
    return base


def find_interpreter(requested, environment=None):
    """Find one requested Python minor version without downloading a runtime.

    Prefer the current executable when it matches. Otherwise inspect GitHub's
    read-only preinstalled tool cache for the highest stable x64 patch, then
    check an existing system interpreter. Never download or install Python.
    Missing or malformed versions raise ValueError; the selected interpreter
    verifies its own version before running tests. Windows launcher discovery
    uses the caller's operation-local environment and does not change files.
    """
    if re.fullmatch(r"3\.(?:10|11|12|13|14|15)", requested) is None:
        raise ValueError("unsupported CI Python version")
    major, minor = (int(part) for part in requested.split("."))
    if sys.version_info[:2] == (major, minor):
        return Path(sys.executable)
    environment = os.environ if environment is None else environment
    cache = environment.get("RUNNER_TOOL_CACHE")
    candidates = []
    versions = Path(cache) / "Python" if cache else None
    if versions is not None and versions.is_dir():
        for directory in versions.iterdir():
            match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", directory.name)
            if not match or tuple(map(int, match.groups()[:2])) != (major, minor):
                continue
            executable = directory / "x64" / ("python.exe" if os.name == "nt" else "bin/python3")
            if executable.is_file():
                candidates.append((int(match.group(3)), executable))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    if os.name == "nt":
        launcher = shutil.which("py", path=environment.get("PATH"))
        if launcher:
            probe = subprocess.run([launcher, "-" + requested, "-I", "-S", "-B", "-c",
                                    "import sys; print(sys.executable)"], cwd=ROOT,
                                   env=environment, capture_output=True, text=True, check=False)
            if probe.returncode == 0 and probe.stdout.strip():
                return Path(probe.stdout.strip())
    else:
        interpreter = shutil.which("python" + requested, path=environment.get("PATH"))
        if interpreter:
            return Path(interpreter)
    raise ValueError("requested Python is absent from the preinstalled cache and system")


def prepare_official_python_310(operation):
    """Prepare one pinned official Windows test runtime inside owned scratch.

    Download only the fixed Python.org embeddable distribution, enforcing its
    exact byte count and pinned SHA-256 before extracting. Reject redirects,
    unsafe or encrypted archive members, links and excessive extraction size.
    Return its executable; the caller owns cleanup after its child process exits.
    This optional CI-only fallback does not install or modify a global runtime.
    """
    base = test_workspace()
    operation = Path(operation).absolute()
    try:
        relative_operation = operation.relative_to(base)
    except ValueError as error:
        raise ValueError("official Python operation must stay inside checkout scratch") from error
    if not relative_operation.parts:
        raise ValueError("official Python operation must use a unique scratch directory")
    current = base
    for part in relative_operation.parts:
        current /= part
        info = current.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("unsafe official Python operation directory")
    if not operation.resolve().is_relative_to(base.resolve()):
        raise ValueError("official Python operation escaped checkout scratch")
    archive = operation / "python310.zip"
    digest = hashlib.sha256()
    size = 0
    opener = urllib.request.build_opener(RejectRedirect())
    with opener.open(PYTHON_310_URL, timeout=45) as response, archive.open("xb") as output:
        if response.geturl() != PYTHON_310_URL:
            raise ValueError("official Python download redirected")
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            size += len(chunk)
            if size > PYTHON_310_BYTES:
                raise ValueError("official Python download exceeds pinned size")
            digest.update(chunk)
            output.write(chunk)
    if size != PYTHON_310_BYTES or digest.hexdigest() != PYTHON_310_SHA256:
        raise ValueError("official Python download does not match the pinned digest")
    runtime = operation / "python310"
    runtime.mkdir()
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if not members or len(members) >= 200 or sum(item.file_size for item in members) >= 50000000:
            raise ValueError("official Python archive exceeds extraction limits")
        for member in members:
            relative = PurePosixPath(member.filename)
            if (relative.is_absolute() or ".." in relative.parts or ":" in member.filename
                    or "\\" in member.filename or stat.S_ISLNK(member.external_attr >> 16)
                    or member.flag_bits & 1):
                raise ValueError("unsafe official Python archive member")
            target = runtime.joinpath(*relative.parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=65536)
    if not (runtime / "LICENSE.txt").is_file() or not (runtime / "python.exe").is_file():
        raise ValueError("official Python runtime or its license is missing")
    return runtime / "python.exe"


def leaves(suite):
    """Yield individual unittest cases from a loader-produced nested suite.

    This only traverses the in-memory suite; no tests run and no files change.
    """
    for case in suite:
        if isinstance(case, unittest.TestSuite):
            yield from leaves(case)
        else:
            yield case


def run_selected():
    """Run permitted discovered tests while preserving excluded and unknown work.

    Exclude locally rejected candidate modules, the harness runner regression
    whose combined test/cleanup command was refused, link-creation cases and
    the package-copy case that could copy those candidates. Test fixtures and
    child process temporary files stay inside a unique checkout-local operation.
    Return zero only when the selected suite succeeds; do not call this full
    discovery or infer acceptance of any excluded case.
    """
    base = test_workspace()
    with tempfile.TemporaryDirectory(prefix="ci-suite-", dir=base) as operation:
        for name in ("TEMP", "TMP", "TMPDIR"):
            os.environ[name] = operation
        tempfile.tempdir = operation
        check = subprocess.run([sys.executable, "-I", "-S", "-B",
                                str(ROOT / "scripts/booster.py"), "self-check",
                                "--root", str(ROOT)], cwd=ROOT, check=False)
        if check.returncode != 0:
            return check.returncode
        tests = ROOT / "tests"
        sys.path.insert(0, str(tests))
        loader = unittest.TestLoader()
        selected = unittest.TestSuite()
        excluded_cases = 0
        for path in sorted(tests.glob("test_*.py")):
            if path.name in EXCLUDED_MODULES:
                continue
            module = importlib.import_module(path.stem)
            for case in leaves(loader.loadTestsFromModule(module)):
                name = case.id().lower()
                if "symlink" in name or "junction" in name or name.endswith(EXCLUDED_CASE):
                    excluded_cases += 1
                else:
                    selected.addTest(case)
        print("Selected %d tests; excluded %d individual cases and %d candidate modules."
              % (selected.countTestCases(), excluded_cases, len(EXCLUDED_MODULES)), flush=True)
        result = unittest.TextTestRunner(verbosity=1).run(selected)
        return 0 if result.wasSuccessful() else 1


def main():
    """Validate and run a requested preinstalled interpreter without host changes.

    ``--python`` names a supported Python minor version. A child process checks
    that its actual interpreter matches before executing the selected tests.
    Missing runtimes fail explicitly unless the caller has opted into the
    fixed, pinned Windows/Python 3.10 CI-only download. No version is skipped.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if args.execute:
            actual = "%d.%d" % sys.version_info[:2]
            if actual != args.python:
                raise ValueError("resolved interpreter does not match requested version")
            print("Running selected tests on Python %s (%s)." % (sys.version.split()[0], sys.platform), flush=True)
            return run_selected()
        with tempfile.TemporaryDirectory(prefix="ci-launch-", dir=test_workspace()) as operation:
            environment = dict(os.environ)
            environment.update({name: operation for name in ("TEMP", "TMP", "TMPDIR")})
            try:
                interpreter = find_interpreter(args.python, environment)
            except ValueError as error:
                permitted = (str(error) == "requested Python is absent from the preinstalled cache and system"
                             and os.name == "nt" and args.python == "3.10"
                             and environment.get("BOOSTERAI_CI_ALLOW_OFFICIAL_PYTHON") == "1")
                if not permitted:
                    raise
                interpreter = prepare_official_python_310(operation)
                print("Using the pinned official Python 3.10 runtime for this CI operation.", flush=True)
            command = [str(interpreter), "-I", "-S", "-B", str(Path(__file__)),
                       "--python", args.python, "--execute"]
            return subprocess.run(command, cwd=ROOT, env=environment, check=False).returncode
    except (OSError, ValueError) as error:
        print("CI runner error: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
