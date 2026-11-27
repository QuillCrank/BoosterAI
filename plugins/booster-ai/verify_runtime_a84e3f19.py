"""Verify the packaged BoosterAI runtime without modifying the installation.

A supplied manifest digest connects this installation to the reviewed source
package. The manifest hashes detect changed or missing files but do not establish
the trustworthiness of an untrusted manifest or authorize its code to execute.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys


_FORMAT = "boosterai-plugin-runtime-v1"
_DIGEST = re.compile(r"[0-9a-f]{64}")
_MANIFEST_LIMIT = 1024 * 1024
_FILE_LIMIT = 8 * 1024 * 1024
_TOTAL_LIMIT = 32 * 1024 * 1024


class CheckoutMismatchError(ValueError):
    """Carry all observed checkout differences while preserving the first error.

    The names are relative manifest paths in deterministic order. This error is
    raised only after every declared checkout source was read successfully; a
    filesystem failure instead propagates without a completeness claim.
    """

    def __init__(self, names):
        self.names = tuple(names)
        super().__init__("checkout source differs: " + self.names[0])


def _unique_object(pairs):
    """Reject duplicate keys, including duplicates nested inside the manifest."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest key")
        result[key] = value
    return result


def _regular(path):
    """Require an ordinary file or directory without a link or reparse point."""
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("runtime contains a link or reparse point")
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise ValueError("runtime contains a special file")
    return info


def _relative_name(value):
    """Validate one canonical manifest path beneath the runtime directory."""
    if not isinstance(value, str) or not value or chr(92) in value or ":" in value:
        raise ValueError("invalid manifest path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in (".", "..") for part in path.parts):
        raise ValueError("invalid manifest path")
    if path.as_posix() != value or value == "manifest.json":
        raise ValueError("invalid manifest path")
    return value


def _file_digest(root, relative):
    """Hash an exact regular source file while rejecting redirected components."""
    path = Path(root)
    if not path.is_absolute() or not stat.S_ISDIR(_regular(path).st_mode):
        raise ValueError("source root must be an existing absolute directory")
    for part in PurePosixPath(_relative_name(relative)).parts:
        path = path / part
        info = _regular(path)
    if not stat.S_ISREG(info.st_mode) or info.st_size > _FILE_LIMIT:
        raise ValueError("source file is invalid or too large: " + relative)
    with path.open("rb") as stream:
        content = stream.read(_FILE_LIMIT + 1)
    if len(content) > _FILE_LIMIT:
        raise ValueError("source file exceeds the size limit: " + relative)
    if path.suffix.lower() in {".py", ".md", ".json", ".js"}:
        content = content.replace(b"\r\n", b"\n")
    return hashlib.sha256(content).hexdigest()


def verify_runtime(runtime, expected_manifest_sha256=None, source_root=None, checkout_root=None):
    """Check a bounded manifest and every declared runtime file without writes.

    The optional reviewed manifest hash binds the checked bytes to the source
    release. Walk without following links and fail on extra files, invalid paths,
    missing files, changed content, or resource limits. Return relative paths
    only. An optional source root checks the reviewed plugin package. A separate
    checkout root checks its current source files. Errors never trigger repair.
    """
    root = Path(runtime)
    if not stat.S_ISDIR(_regular(root).st_mode):
        raise ValueError("runtime directory is unavailable")
    manifest_path = root / "manifest.json"
    manifest_info = _regular(manifest_path)
    if not stat.S_ISREG(manifest_info.st_mode):
        raise ValueError("manifest is not a regular file")
    if manifest_info.st_size > _MANIFEST_LIMIT:
        raise ValueError("manifest exceeds the size limit")
    with manifest_path.open("rb") as stream:
        raw = stream.read(_MANIFEST_LIMIT + 1)
    if len(raw) > _MANIFEST_LIMIT:
        raise ValueError("manifest exceeds the size limit")
    manifest_sha256 = hashlib.sha256(raw).hexdigest()
    if expected_manifest_sha256 is not None:
        if not isinstance(expected_manifest_sha256, str) or not _DIGEST.fullmatch(expected_manifest_sha256):
            raise ValueError("expected manifest hash is invalid")
        if manifest_sha256 != expected_manifest_sha256:
            raise ValueError("manifest hash does not match the reviewed source")
    manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(manifest, dict) or set(manifest) != {"format", "files", "source_commit"}:
        raise ValueError("invalid manifest")
    if manifest["format"] != _FORMAT or not isinstance(manifest["files"], dict):
        raise ValueError("unsupported manifest")
    if (not isinstance(manifest["source_commit"], str)
            or not re.fullmatch(r"[0-9a-f]{40}", manifest["source_commit"])):
        raise ValueError("invalid source commit")
    files = manifest["files"]
    if not files:
        raise ValueError("manifest contains no files")
    for name, digest in files.items():
        _relative_name(name)
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise ValueError("invalid file hash")

    actual = set()
    total = 0

    def fail_walk(error):
        raise error

    for directory, names, filenames in os.walk(root, followlinks=False, onerror=fail_walk):
        parent = Path(directory)
        for name in names:
            if not stat.S_ISDIR(_regular(parent / name).st_mode):
                raise ValueError("runtime directory is invalid")
        for name in filenames:
            path = parent / name
            info = _regular(path)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("runtime file is invalid")
            relative = path.relative_to(root).as_posix()
            if relative == "manifest.json":
                continue
            if relative not in files:
                raise ValueError("unexpected runtime file: " + relative)
            if info.st_size > _FILE_LIMIT:
                raise ValueError("runtime file exceeds the size limit")
            with path.open("rb") as stream:
                content = stream.read(_FILE_LIMIT + 1)
            total += len(content)
            if total > _TOTAL_LIMIT or len(content) > _FILE_LIMIT:
                raise ValueError("runtime exceeds the size limit")
            if hashlib.sha256(content).hexdigest() != files[relative]:
                raise ValueError("runtime file hash differs: " + relative)
            actual.add(relative)
    if actual != set(files):
        raise ValueError("a declared runtime file is missing")

    source_match = None
    if source_root is not None:
        source_prefix = "plugins/booster-ai/"
        if _file_digest(source_root, source_prefix + "runtime/manifest.json") != manifest_sha256:
            raise ValueError("plugin source manifest differs")
        for name, digest in files.items():
            if _file_digest(source_root, source_prefix + "runtime/" + name) != digest:
                raise ValueError("plugin runtime source differs: " + name)
        plugin_root = root.parent
        for name in (
            ".gitattributes",
            ".codex-plugin/plugin.json",
            "LICENSE",
            "verify_runtime_a84e3f19.py",
            "skills/booster-ai-workflow/SKILL.md",
        ):
            if _file_digest(plugin_root, name) != _file_digest(source_root, source_prefix + name):
                raise ValueError("plugin source differs: " + name)
        source_match = True

    checkout_match = None
    if checkout_root is not None:
        checkout_mismatches = []
        for name in sorted(files):
            if name not in {"AGENTS.md", "README.md"}:
                if _file_digest(checkout_root, name) != files[name]:
                    checkout_mismatches.append(name)
        if checkout_mismatches:
            raise CheckoutMismatchError(checkout_mismatches)
        checkout_match = True

    return {
        "status": "VERIFIED",
        "manifest_sha256": manifest_sha256,
        "source_commit": manifest["source_commit"],
        "expected_manifest_match": (
            None if expected_manifest_sha256 is None else True
        ),
        "file_count": len(actual),
        "source_match": source_match,
        "checkout_match": checkout_match,
    }


def main(argv=None):
    """Print one JSON result and exit nonzero when verification cannot complete."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--source-root", help="absolute checkout containing the reviewed plugin package")
    parser.add_argument("--checkout-root", help="absolute checkout containing the current source files")
    args = parser.parse_args(argv)
    try:
        runtime = Path(__file__).resolve().parent / "runtime"
        result = verify_runtime(runtime, args.expected_manifest_sha256, args.source_root, args.checkout_root)
    except CheckoutMismatchError as error:
        print(json.dumps({
            "status": "INVALID",
            "error": str(error),
            "checkout_mismatches": error.names,
            "checkout_comparison_complete": True,
        }, ensure_ascii=True, sort_keys=True))
        return 1
    except OSError as error:
        print(json.dumps({
            "status": "INVALID",
            "error": "filesystem access failed",
            "error_type": type(error).__name__,
        }, ensure_ascii=True, sort_keys=True))
        return 1
    except (UnicodeError, ValueError, RecursionError) as error:
        print(json.dumps({"status": "INVALID", "error": str(error)}, ensure_ascii=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
