"""Assemble a reproducible BoosterAI archive for release preparation.

Read UTF-8 JSON with a reviewed "manifest" and a "files" object mapping each
manifest path to its exact UTF-8 text. Write the runtime ZIP to stdout.
For a draft plugin ZIP, add an "envelope" object with the five reviewed
outer files and supply its independent canonical hash with --envelope-sha256.
Use sorted JSON keys, two-space indentation, and a final LF for both pins.
Release verification, marketplace registration, and installation are separate steps.
"""

import argparse
import hashlib
import io
import json
import re
import sys
import zipfile


FORMAT = "boosterai-plugin-runtime-v1"
INPUT_LIMIT = 16 * 1024 * 1024
HASH = re.compile(r"[0-9a-f]{64}")
PATH_COMPONENT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*")
WINDOWS_NAMES = {"AUX", "CON", "NUL", "PRN"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(10)
}
ERROR = "invalid runtime build input\n"
PLUGIN_REPOSITORIES = ('https://github.com/QuillCrank/BoosterAI',)
PLUGIN_MANIFEST = ".codex-plugin/plugin.json"
WORKFLOW = "skills/booster-ai-workflow/SKILL.md"
ENVELOPE_PATHS = {".gitattributes", PLUGIN_MANIFEST, "LICENSE",
                  "verify_runtime_a84e3f19.py", WORKFLOW}
VERIFIER = '"<PLUGIN_ROOT>/verify_runtime_a84e3f19.py"'
WRAPPER_PIN = "--expected-manifest-sha256"


class BuildArgumentParser(argparse.ArgumentParser):
    """Use the fixed failure diagnostic for malformed CLI arguments."""

    def error(self, message):
        """Exit without exposing the supplied arguments in the diagnostic."""
        self.exit(2, ERROR)


def unique_object(pairs):
    """Read a JSON object while rejecting ambiguous repeated member names."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate member")
        result[key] = value
    return result


def reject_constant(value):
    """Reject non-JSON number spellings accepted by the default decoder."""
    raise ValueError("invalid JSON constant")


def canonical_json(value):
    """Serialize a recipe or envelope in the format used for reviewed pins."""
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def package_envelope(envelope, envelope_hash, recipe_hash):
    """Bind the reviewed outer files, plugin identity and workflow recipe pin."""
    if (not isinstance(envelope, dict) or set(envelope) != ENVELOPE_PATHS
            or any(not isinstance(body, str) or not body for body in envelope.values())
            or hashlib.sha256(canonical_json(envelope)).hexdigest() != envelope_hash):
        raise ValueError("envelope input differs")
    plugin = json.loads(envelope[PLUGIN_MANIFEST], object_pairs_hook=unique_object,
                        parse_constant=reject_constant)
    identity = {
        "name": "booster-ai",
        "license": "MIT",
        "skills": "./skills/",
        "onboardingSkill": "./" + WORKFLOW,
    }
    if (not isinstance(plugin, dict) or plugin.get("repository") not in PLUGIN_REPOSITORIES
            or any(plugin.get(k) != v for k, v in identity.items())):
        raise ValueError("invalid plugin identity")
    version = plugin.get("version")
    if (not isinstance(version, str)
            or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2}", version)):
        raise ValueError("invalid plugin release version")
    wrapper = envelope[WORKFLOW]
    # A marker keeps comments from joining surrounding text into a command.
    visible = re.sub(r"<!--.*?(?:-->|$)", "\x00", wrapper, flags=re.DOTALL)
    command = f"    py -3.12 -I -S -B {VERIFIER} {WRAPPER_PIN} {recipe_hash}"
    if (wrapper.count(WRAPPER_PIN) != 1 or visible.count(VERIFIER) != 1
            or not any(line.rstrip(" \t") == command for line in visible.splitlines())):
        raise ValueError("workflow recipe pin differs")
    return {path: body.encode("utf-8") for path, body in envelope.items()}


def build_payload(raw, recipe_hash, envelope_hash=None):
    """Return immutable ZIP bytes from bounded UTF-8 input and reviewed hash.

    Require the exact recipe membership and matching input bytes. Complete
    construction in memory before returning any output to the caller.
    """
    if len(raw) > INPUT_LIMIT or not HASH.fullmatch(recipe_hash):
        raise ValueError("invalid input boundary")
    request = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                         parse_constant=reject_constant)
    fields = {"manifest", "files"}
    if envelope_hash is not None:
        fields.add("envelope")
        if not HASH.fullmatch(envelope_hash):
            raise ValueError("invalid envelope pin")
    if not isinstance(request, dict) or set(request) != fields:
        raise ValueError("invalid input fields")
    manifest, sources = request["manifest"], request["files"]
    if not isinstance(manifest, dict) or set(manifest) != {
        "format", "source_commit", "files"
    }:
        raise ValueError("invalid recipe fields")
    commit, expected = manifest["source_commit"], manifest["files"]
    if (manifest["format"] != FORMAT or not isinstance(commit, str)
            or not re.fullmatch(r"[0-9a-f]{40}", commit)
            or not isinstance(expected, dict) or not expected
            or not isinstance(sources, dict) or set(sources) != set(expected)):
        raise ValueError("invalid recipe membership")

    recipe = canonical_json(manifest)
    if hashlib.sha256(recipe).hexdigest() != recipe_hash:
        raise ValueError("recipe pin mismatch")

    files = {"runtime/manifest.json": recipe}
    seen = {"manifest.json"}
    directories = {}
    for path, digest in expected.items():
        parts = path.split("/")
        if (len(path) > 65535 - len("runtime/")
                or any(len(part) > 255 or not PATH_COMPONENT.fullmatch(part)
                       or part.endswith(".")
                       or part.split(".")[0].upper() in WINDOWS_NAMES for part in parts)):
            raise ValueError("invalid payload path")
        identity = path.lower()
        if identity in seen:
            raise ValueError("duplicate payload path")
        seen.add(identity)
        for index in range(1, len(parts)):
            directory = "/".join(parts[:index])
            if directories.setdefault(directory.lower(), directory) != directory:
                raise ValueError("ambiguous directory spelling")
        if (not isinstance(digest, str) or not HASH.fullmatch(digest)
                or not isinstance(sources[path], str)):
            raise ValueError("invalid payload material")
        content = sources[path].encode("utf-8")
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("payload source differs")
        files["runtime/" + path] = content
    if seen.intersection(directories):
        raise ValueError("file and directory conflict")
    if envelope_hash is not None:
        files.update(package_envelope(request["envelope"], envelope_hash, recipe_hash))

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for path, content in sorted(files.items()):
            entry = zipfile.ZipInfo(path)
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, content)
    return output.getvalue()


def main(argv=None):
    """Read the release material from stdin and stream an archive or a fixed error."""
    parser = BuildArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--recipe-sha256", required=True, action="append")
    parser.add_argument("--envelope-sha256", action="append")
    args = parser.parse_args(argv)
    envelope_pins = args.envelope_sha256 or []
    if len(args.recipe_sha256) != 1 or len(envelope_pins) > 1:
        parser.error("exactly one reviewed pin per selected input is required")
    try:
        payload = build_payload(sys.stdin.buffer.read(INPUT_LIMIT + 1),
                                args.recipe_sha256[0],
                                envelope_pins[0] if envelope_pins else None)
    except (ValueError, RecursionError):
        sys.stderr.write(ERROR)
        return 2
    sys.stdout.buffer.write(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
