"""Read-only, file-observed continuity planning for BoosterAI project slices.

``evaluate`` accepts an in-memory plan and returns its ordered state; it never
edits the project's canonical plan or executes code. Each node has ``id``,
``owner``, a nonempty ``file_owners`` mapping, ``contract``, ``inputs``,
``outputs``, ``depends_on``, ``target``, and ``acceptance``. Paths use project-
relative forward slashes. Every output must be a claimed file, and acceptance
must be ``{'type': 'sha256', 'path': TARGET, 'sha256': LOWERCASE_HEX}``.

Pass a prior result's ``evidence`` as ``previous``. To request a new file
observation, include its node ID in ``observed``. A file observation matches
only after actual input and output reads, a matching target hash, and matching
dependency observations. A prior observation is reused only after those reads
and a matching fingerprint. A changed input, contract, dependency or output
makes the node and dependent observations stale. Neither matching observations
nor the legacy ready/continuation fields establish product acceptance or
authorize execution; the result explicitly reports both as false.

File bytes alone cannot prove code ran, that the expected hash is correct, or
that a prior record is authentic. The caller must establish those facts and
honor authorization and STOP/denial across calls. File identity checks detect
ordinary concurrent changes but are not a sandbox against hostile replacement;
do not assess files being actively modified by an uncoordinated writer.
"""

from bisect import bisect_left
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import unicodedata


_HASH = re.compile(r"[0-9a-f]{64}\Z")
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    prefix + digit for prefix in ("COM", "LPT") for digit in "123456789¹²³"
}
_FIELDS = frozenset({
    "id", "owner", "file_owners", "contract", "inputs", "outputs",
    "depends_on", "target", "acceptance",
})


def _require(condition, message):
    """Enforce a validation precondition without changing the caller's data.

    ``condition`` is any truth-tested value and ``message`` describes a failure.
    Return None when the condition is true; otherwise raise ValueError with that
    message. No files or other external state are read or modified.
    """
    if not condition:
        raise ValueError(message)


def _text(value, name):
    """Validate an identifier or contract text and return the original string.

    ``value`` must be a nonempty string with no surrounding whitespace; ``name``
    identifies the field in a ValueError when validation fails. This performs
    no filesystem access and does not modify either argument.
    """
    _require(isinstance(value, str) and value.strip() == value and bool(value),
             name + " must be a nonempty trimmed string")
    return value


def _relative(value):
    """Validate a portable project-relative file path without accessing that file.

    ``value`` must be a nonempty canonical forward-slash path; return that same
    string on success. Raise ValueError for absolute paths, streams, traversal,
    backslashes, reserved Windows names or ambiguous components. Reserved stems
    include space-padded names before the first extension. This lexical check
    performs no filesystem I/O and does not establish file ownership.
    """
    _text(value, "path")
    _require("\\" not in value and ":" not in value and "\x00" not in value,
             "unsafe path")
    path = PurePosixPath(value)
    windows = PureWindowsPath(value)
    _require(not path.is_absolute() and not windows.is_absolute() and not windows.drive,
             "absolute path forbidden")
    _require(bool(path.parts) and path.as_posix() == value
             and all(p not in (".", "..") for p in path.parts),
             "noncanonical path or traversal forbidden")
    for part in path.parts:
        device_stem = part.partition(".")[0].rstrip(" ").upper()
        _require(not part.endswith((".", " "))
                 and device_stem not in _RESERVED_NAMES
                 and not any(ord(character) < 32 or character in '<>"|?*' for character in part),
                 "ambiguous path component")
    return value


def _root(root):
    """Resolve an existing project root only after checking every original ancestor.

    Accept ``root`` as a path-like value and return its resolved Path. Reading
    filesystem metadata can raise OSError; links, reparse points and non-directory
    ancestors raise ValueError. No files are changed. Another process can still
    replace a component after inspection, so this check is not a sandbox.
    """
    path = Path(root).absolute()
    for component in (path, *path.parents):
        info = component.lstat()
        _require(not stat.S_ISLNK(info.st_mode)
                 and not (getattr(info, "st_file_attributes", 0) & 0x400),
                 "root link or reparse point forbidden")
        _require(stat.S_ISDIR(info.st_mode), "root ancestor is not a directory")
    return path.resolve(strict=True)


def _path(root, relative):
    """Inspect each existing path component without following links or hardlinks.

    ``root`` is a previously checked Path and ``relative`` a project-relative
    filename. Return ``(path, exists)``; absent files are allowed. Unsafe paths,
    links, non-regular files and hardlinks raise ValueError, while filesystem
    access errors propagate. The function reads metadata but changes no files.
    """
    current = root
    parts = PurePosixPath(_relative(relative)).parts
    missing = False
    for index, part in enumerate(parts):
        current = current / part
        if missing:
            continue
        try:
            info = current.lstat()
        except FileNotFoundError:
            missing = True
            continue
        _require(not stat.S_ISLNK(info.st_mode)
                 and not (getattr(info, "st_file_attributes", 0) & 0x400),
                 "link or reparse point forbidden: " + relative)
        if index < len(parts) - 1:
            _require(stat.S_ISDIR(info.st_mode), "path parent is not a directory: " + relative)
        else:
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                     "file must be regular and single-linked: " + relative)
    _require(current.resolve().is_relative_to(root), "path escapes project root")
    return current, not missing


def _identity(info):
    """Return stable path/descriptor fields for detecting ordinary file replacement.

    Given a stat-like ``info`` object, return its device, inode, size, modification
    time and link count as a tuple; missing attributes raise AttributeError. No
    filesystem access or mutation occurs. Windows can report different ctime for
    the same path and descriptor, so ctime is deliberately excluded.
    """
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink)


def _read_hash(root, relative):
    """Read and hash one actual regular file, or return None if it is absent.

    ``root`` is a checked project directory and ``relative`` is the contained
    filename. Return the lowercase SHA-256 of its bytes, or None if it is absent.
    Open/read errors propagate; unsafe or observably changed files raise
    ValueError. The function opens and reads the file without writing it. Identity
    checks do not prevent hostile races, so coordinate concurrent writers.
    """
    path, exists = _path(root, relative)
    if not exists:
        return None
    before = path.lstat()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        _require(stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1
                 and _identity(opened) == _identity(before), "file changed before read: " + relative)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        checked_path, still_exists = _path(root, relative)
        _require(still_exists and checked_path == path
                 and _identity(after) == _identity(before)
                 and _identity(path.lstat()) == _identity(before),
                 "file changed during read: " + relative)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _file_key(path):
    """Return a portable comparison key for one already validated file path.

    ``path`` is a project-relative string. Return its Unicode NFC-normalized,
    case-folded form without accessing the filesystem. This conservatively
    catches common spelling aliases, but cannot detect every physical alias or
    protect against concurrent replacement of path components.
    """
    return unicodedata.normalize("NFC", path).casefold()


def _paths(value, name, nonempty=False):
    """Validate and sort a unique list of portable project-relative file paths.

    ``value`` must be a list, ``name`` labels validation errors, and ``nonempty``
    requires at least one element when true. Return a new sorted list. Invalid,
    unsafe or case-insensitively duplicate paths raise ValueError; this performs
    no filesystem access and leaves the supplied list unchanged.
    """
    _require(isinstance(value, list) and (bool(value) or not nonempty),
             name + " must be a " + ("nonempty " if nonempty else "") + "list")
    paths = [_relative(item) for item in value]
    _require(len({_file_key(item) for item in paths}) == len(paths),
             "duplicate " + name + " path")
    return sorted(paths)


def dependency_order(
    dependencies: dict[str, list[str]],
) -> tuple[list[str], dict[str, set[str]]]:
    """Validate a dependency graph and return its stable order and ancestors.

    ``dependencies`` maps each nonempty node ID to a list of its prerequisite
    IDs. All listed prerequisites must be present, distinct, and acyclic. The
    result contains a deterministic topological order and a map from each ID to
    all of its transitive prerequisites. An empty mapping returns two empty
    results. Invalid IDs or graphs raise ValueError. The input is not mutated;
    no file is read, no task is run, and no claimed evidence is authenticated.
    """
    _require(isinstance(dependencies, dict), "dependencies must be a mapping")
    for item, required in dependencies.items():
        _text(item, "node id")
        _require(isinstance(required, list), "dependencies must be lists")
        for dependency in required:
            _text(dependency, "dependency")
        _require(len(set(required)) == len(required), "duplicate dependency")
        _require(all(dependency in dependencies for dependency in required),
                 "missing dependency of " + item)
    order = []
    remaining = set(dependencies)
    while remaining:
        ready = sorted(item for item in remaining
                       if all(dependency in order for dependency in dependencies[item]))
        _require(bool(ready), "dependency cycle")
        order.extend(ready)
        remaining.difference_update(ready)
    ancestors = {}
    for item in order:
        ancestors[item] = set(dependencies[item])
        for dependency in dependencies[item]:
            ancestors[item].update(ancestors[dependency])
    return order, ancestors


def _normal_plan(plan):
    """Validate slice contracts, ownership, and dependency order without writing.

    ``plan`` is an in-memory mapping containing a nonempty ``nodes`` list. Return
    a normalized node mapping and deterministic topological order without changing
    the input or reading files. Malformed contracts, missing dependencies, cycles,
    conflicting file owners, slice/file-owner mismatches, unordered shared claims
    or producer/consumer pairs raise ValueError. Every claimed writable file must
    name the slice owner; delegated writes require a separate supported handoff.
    Paths use Unicode/case-folded comparison. A declared file cannot contain
    another declared file. A dependency permits shared claims in sequence.
    """
    _require(isinstance(plan, dict) and isinstance(plan.get("nodes"), list)
             and bool(plan["nodes"]), "plan must contain a nonempty nodes list")
    nodes = {}
    owners = {}
    for raw in plan["nodes"]:
        _require(isinstance(raw, dict) and set(raw) == _FIELDS, "invalid node fields")
        node_id = _text(raw["id"], "node id")
        _require(node_id not in nodes, "duplicate node id: " + node_id)
        owner = _text(raw["owner"], "owner")
        contract = _text(raw["contract"], "contract")
        inputs = _paths(raw["inputs"], "inputs")
        outputs = _paths(raw["outputs"], "outputs", nonempty=True)
        _require(isinstance(raw["file_owners"], dict) and bool(raw["file_owners"]),
                 "file_owners must be a nonempty path-to-owner mapping")
        file_owners = {}
        for path, file_owner in raw["file_owners"].items():
            path = _relative(path)
            file_owner = _text(file_owner, "file owner")
            _require(file_owner == owner, "slice owner differs from declared file owner")
            key = _file_key(path)
            _require(key not in (_file_key(item) for item in file_owners), "duplicate claimed file")
            if key in owners:
                _require(owners[key] == file_owner, "conflicting owners for file: " + path)
            owners[key] = file_owner
            file_owners[path] = file_owner
        _require(all(output in file_owners for output in outputs),
                 "every output needs an explicit file owner")
        _require(isinstance(raw["depends_on"], list), "depends_on must be a list")
        dependencies = [_text(item, "dependency") for item in raw["depends_on"]]
        _require(len(set(dependencies)) == len(dependencies), "duplicate dependency")
        target = _relative(raw["target"])
        _require(target in outputs, "target must be an output")
        acceptance = raw["acceptance"]
        _require(isinstance(acceptance, dict) and set(acceptance) == {"type", "path", "sha256"}
                 and acceptance["type"] == "sha256" and acceptance["path"] == target
                 and isinstance(acceptance["sha256"], str) and _HASH.fullmatch(acceptance["sha256"]),
                 "acceptance must specify target and lowercase SHA-256")
        nodes[node_id] = {
            "id": node_id, "owner": owner, "contract": contract,
            "file_owners": dict(sorted(file_owners.items())), "inputs": inputs,
            "outputs": outputs, "depends_on": sorted(dependencies),
            "target": target, "acceptance": dict(acceptance),
        }
    file_paths = sorted(owners.keys() | {
        _file_key(path) for node in nodes.values() for path in node["inputs"]
    })
    for path in file_paths:
        prefix = path + "/"
        following = bisect_left(file_paths, prefix)
        _require(following == len(file_paths) or not file_paths[following].startswith(prefix),
                 "declared files cannot contain one another")
    order, ancestors = dependency_order({
        item: node["depends_on"] for item, node in nodes.items()
    })
    for index, first in enumerate(order):
        first_claims = {_file_key(path) for path in nodes[first]["file_owners"]}
        for second in order[index + 1:]:
            shared = first_claims & {_file_key(path) for path in nodes[second]["file_owners"]}
            _require(not shared or first in ancestors[second] or second in ancestors[first],
                     "unordered shared file claims: " + first + ", " + second)
    for item in order:
        for producer in nodes:
            if producer != item and {_file_key(p) for p in nodes[item]["inputs"]} & {
                    _file_key(p) for p in nodes[producer]["outputs"]}:
                _require(producer in ancestors[item],
                         "input producer is not ordered by dependencies: " + item)
    return nodes, order


def _fingerprint(node, inputs, outputs, dependencies):
    """Create a deterministic fingerprint for one normalized slice observation.

    ``node`` is its contract, ``inputs`` and ``outputs`` map paths to observed
    hashes or None, and ``dependencies`` maps IDs to fingerprints. Return a
    lowercase SHA-256 of their canonical JSON. JSON serialization errors propagate;
    this helper neither accesses files nor mutates the supplied mappings.
    """
    value = {"contract": node, "inputs": inputs, "outputs": outputs,
             "dependencies": dependencies}
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evaluate(root, plan, previous=None, observed=(), *, stopped=False, denied=()):
    """Assess BoosterAI slices by reading their actual files under an authorized root.

    ``root`` is an authorized project directory; ``plan`` supplies explicit slice
    contracts; ``previous`` is a prior returned evidence map; and ``observed``
    selects IDs for fresh file observations. A true ``stopped`` halts all work;
    each ID in ``denied`` blocks only that slice and descendants without reading
    their files. Return ordered states, ready and nonconflicting parallel-ready
    IDs, retained evidence and limitations. Missing inputs or blocked dependencies
    prevent readiness. Invalid plans or unsafe paths raise ValueError; file access
    errors propagate. This function reads files but never writes or runs code. It
    cannot authenticate prior evidence or prove the expected hash is correct.
    Ready and continuation fields concern file observations only. Every returned
    result explicitly sets product_acceptance_verified and execution_authorized
    to False; the responsible host must separately establish those facts.
    """
    _require(type(stopped) is bool, "stopped must be a boolean")
    nodes, order = _normal_plan(plan)
    _require(previous is None or isinstance(previous, dict), "previous must be an evidence map")
    previous = previous or {}
    _require(isinstance(observed, (tuple, list, set, frozenset)), "observed must contain node ids")
    _require(all(isinstance(item, str) and item in nodes for item in observed), "unknown observation")
    observed = set(observed)
    _require(isinstance(denied, (tuple, list, set, frozenset)), "denied must contain node ids")
    _require(all(isinstance(item, str) and item in nodes for item in denied), "unknown denial")
    denied = set(denied)
    for item, record in previous.items():
        _require(isinstance(item, str) and isinstance(record, dict)
                 and set(record) == {"fingerprint", "target", "target_sha256"}
                 and isinstance(record["fingerprint"], str) and _HASH.fullmatch(record["fingerprint"])
                 and isinstance(record["target_sha256"], str) and _HASH.fullmatch(record["target_sha256"])
                 and isinstance(record["target"], str), "invalid previous evidence")
    limitations = [
        "File reads do not prove code execution or that the expected hash is correct.",
        "Prior evidence and owner authorization are not authenticated by this helper.",
        "Coordinate active writers; path and identity checks are not an OS sandbox.",
        "The host must preserve STOP, denials and mandatory approvals across calls.",
    ]
    if stopped:
        return {"continuation_permitted": False, "halt_reason": "STOP",
                "product_acceptance_verified": False, "execution_authorized": False,
                "order": order, "ready": [], "parallel_ready": [], "deferred_ready": [],
                "nodes": {item: {"state": "HALTED"} for item in order},
                "denied": sorted(denied), "blocked_descendants": [],
                "evidence": dict(previous), "limitations": limitations}
    base = _root(root)
    results, evidence, fingerprints, seen_files = {}, {}, {}, {}
    for item in order:
        node = nodes[item]
        if item in denied or any(results[dep]["state"] in ("DENIED", "BLOCKED")
                                 for dep in node["depends_on"]):
            results[item] = {"state": "DENIED" if item in denied else "BLOCKED"}
            if item in previous:
                evidence[item] = dict(previous[item])
            continue
        for path in node["file_owners"]:
            _path(base, path)
        def capture(path):
            """Read a project-relative file and track its observation within this call.

            Given ``path``, return the lowercase content hash or None if absent,
            and update the enclosing ``seen_files`` map. Raise ValueError if a
            repeated read changes; propagate filesystem errors from the read. This
            helper reads the file but does not modify it.
            """
            value = _read_hash(base, path)
            key = _file_key(path)
            _require(key not in seen_files or seen_files[key] == value,
                     "file changed between slice reads: " + path)
            seen_files[key] = value
            return value
        inputs = {path: capture(path) for path in node["inputs"]}
        outputs = {path: capture(path) for path in node["outputs"]}
        dependencies = {dep: fingerprints[dep] for dep in node["depends_on"]}
        fingerprint = _fingerprint(node, inputs, outputs, dependencies)
        fingerprints[item] = fingerprint
        target_hash = outputs[node["target"]]
        target_matches = target_hash is not None and target_hash == node["acceptance"]["sha256"]
        files_present = all(value is not None for value in (*inputs.values(), *outputs.values()))
        dependencies_verified = all(results[dep]["state"] in ("TARGET_MATCHED", "TARGET_REUSED")
                                    for dep in node["depends_on"])
        old = previous.get(item)
        reused = (old is not None and old["fingerprint"] == fingerprint
                  and old["target"] == node["target"] and old["target_sha256"] == target_hash)
        if files_present and target_matches and dependencies_verified and item in observed:
            state = "TARGET_MATCHED"
        elif files_present and target_matches and dependencies_verified and reused:
            state = "TARGET_REUSED"
        else:
            state = "STALE" if old is not None else "PLANNED"
        if state in ("TARGET_MATCHED", "TARGET_REUSED"):
            evidence[item] = {"fingerprint": fingerprint, "target": node["target"],
                              "target_sha256": target_hash}
        elif old is not None:
            evidence[item] = dict(old)
        results[item] = {
            "state": state, "fingerprint": fingerprint,
            "target_file_read": target_hash is not None,
            "target_matches_expected": target_matches,
            "missing_inputs": [path for path, value in inputs.items() if value is None],
            "missing_outputs": [path for path, value in outputs.items() if value is None],
            "dependencies_verified": dependencies_verified,
        }
    ready = [item for item in order if results[item]["state"] in ("PLANNED", "STALE")
             and results[item]["dependencies_verified"] and not results[item]["missing_inputs"]]
    parallel_ready, used_files, used_owners = [], set(), set()
    for item in ready:
        node = nodes[item]
        claimed = {_file_key(path) for path in node["file_owners"]}
        if node["owner"] not in used_owners and not (claimed & used_files):
            parallel_ready.append(item)
            used_owners.add(node["owner"])
            used_files.update(claimed)
    return {"continuation_permitted": bool(ready),
            "product_acceptance_verified": False, "execution_authorized": False,
            "denied": sorted(denied),
            "blocked_descendants": [item for item in order if results[item]["state"] == "BLOCKED"],
            "order": order, "ready": ready,
            "parallel_ready": parallel_ready,
            "deferred_ready": [item for item in ready if item not in parallel_ready],
            "nodes": results, "evidence": evidence, "limitations": limitations}
