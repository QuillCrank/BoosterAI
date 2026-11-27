"""Generate and check BoosterAI task cards against an explicit source graph.

The selected source is caller-supplied structured data, not all historical
Markdown tables. No task, test, shell command, network request or file write is
performed here. See the operating reference for the exact input contract and
for the limits of lexical path checks and declared file ownership.
"""

import argparse
from bisect import bisect_left
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys


_CORE_PATH = Path(__file__).resolve().with_name("continuity_plan_64e9b1c2.py")
_CORE_SPEC = importlib.util.spec_from_file_location("booster_continuity", _CORE_PATH)
_CORE = importlib.util.module_from_spec(_CORE_SPEC)
_CORE_SPEC.loader.exec_module(_CORE)
_SOURCE_FIELDS = {"revision", "nodes"}
_NODE_FIELDS = {"id", "depends_on", "slices"}
_SLICE_FIELDS = {"id", "owner", "contract", "inputs", "outputs", "read_files",
                 "file_owners", "depends_on", "acceptance", "real_test"}
_TEST_FIELDS = {"target", "action", "expected"}
_BUNDLE_FIELDS = {"source_revision", "source_sha256", "cards"}
_MAX_STDIN = 4 * 1024 * 1024
_MAX_NODES = 512
_MAX_SLICES = 1024
_MAX_EXPANDED_EDGES = 32768
_MAX_LIST_ITEMS = 4096
_MAX_FILE_REFERENCES = 8192
_MAX_PATH_LENGTH = 4096


def _require(condition, message):
    """Raise ValueError for a failed contract without modifying caller data.

    ``condition`` is truth-tested and ``message`` is the error description.
    Successful checks return None. This function performs no external I/O.
    """
    if not condition:
        raise ValueError(message)


def _text(value, name):
    """Return a required, trimmed text field or reject it as malformed.

    ``value`` must be a nonempty string without surrounding whitespace and
    without NUL; ``name`` labels errors. Nothing is read or written externally.
    """
    _require(isinstance(value, str) and value and value == value.strip()
             and "\x00" not in value, name + " must be nonempty text")
    return value


def _key(value):
    """Create a conservative Unicode/case comparison key for one string.

    ``value`` is already validated text. Return NFC-normalized case-folded text
    without accessing files. This lexical key cannot identify every filesystem
    alias, concurrent replacement, hardlink or reparse-point escape.
    """
    return _CORE._file_key(value)


def _fields(value, required, name):
    """Require exactly the documented mapping fields for a source or card input.

    ``value`` must be a dict whose keys equal ``required``; ``name`` identifies
    errors. Return None on success and raise ValueError on missing or extra keys.
    The supplied object is never changed or used to perform external actions.
    """
    _require(isinstance(value, dict) and set(value) == required,
             "invalid " + name + " fields")


def _unique_object(pairs):
    """Decode one JSON object while rejecting repeated keys at every depth.

    ``pairs`` is the ordered key/value sequence supplied by ``json.loads``.
    Return a new dict; raise ValueError before an earlier value can be hidden by
    a duplicate key. The caller remains responsible for strict parsing before
    passing an already-created Python mapping to the in-process API.
    """
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON object key")
        result[key] = value
    return result


def _texts(value, name, *, allow_empty=True, paths=False):
    """Validate an ordered text list, optionally using the existing path rules.

    ``value`` must be a list; ``allow_empty`` controls whether an empty list is
    valid and ``paths`` checks each project-relative path using BoosterAI's
    existing lexical validator. Duplicate values, including Unicode/case aliases,
    raise ValueError. Return a new list without reading or changing any files.
    """
    _require(isinstance(value, list) and (allow_empty or bool(value)),
             name + " must be " + ("a list" if allow_empty else "a nonempty list"))
    _require(len(value) <= _MAX_LIST_ITEMS, name + " exceeds item limit")
    result = []
    keys = set()
    for item in value:
        item = _text(item, name)
        if paths:
            _require(len(item) <= _MAX_PATH_LENGTH, "path length limit exceeded")
            _CORE._relative(item)
        key = _key(item)
        _require(key not in keys, "duplicate " + name)
        keys.add(key)
        result.append(item)
    return result


def _owners(value):
    """Validate declared project-relative writable files and their owners.

    ``value`` is a path-to-owner mapping; an explicit empty mapping means the
    slice declares no file writes. Return a new sorted mapping. Invalid paths,
    empty owners and duplicate normalized identities raise ValueError. These are
    declarations only: omitted writes and actual OS permissions are not inferred.
    """
    _require(isinstance(value, dict), "file_owners must be a mapping")
    _require(len(value) <= _MAX_LIST_ITEMS, "file_owners exceeds item limit")
    result = {}
    keys = set()
    for path, owner in value.items():
        path = _text(path, "file owner path")
        _require(len(path) <= _MAX_PATH_LENGTH, "path length limit exceeded")
        _CORE._relative(path)
        key = _key(path)
        _require(key not in keys, "duplicate claimed file")
        keys.add(key)
        result[path] = _text(owner, "file owner")
    return dict(sorted(result.items()))


def _slice(raw):
    """Normalize one explicitly declared atomic slice and its real-test contract.

    ``raw`` must contain every documented slice field, including an explicit
    input list, at least one described output, file claims and a target/action/
    expected test description. Every declared writable file must name the slice
    owner; delegated writes are not inferred. Return a fresh mapping; malformed
    values raise ValueError. Semantic atomicity and test execution are not proven.
    """
    _fields(raw, _SLICE_FIELDS, "slice")
    _fields(raw["real_test"], _TEST_FIELDS, "real_test")
    owner = _text(raw["owner"], "slice owner")
    file_owners = _owners(raw["file_owners"])
    _require(all(file_owner == owner for file_owner in file_owners.values()),
             "slice owner differs from declared file owner")
    return {
        "id": _text(raw["id"], "slice id"),
        "owner": owner,
        "contract": _text(raw["contract"], "contract"),
        "inputs": _texts(raw["inputs"], "inputs"),
        "outputs": _texts(raw["outputs"], "outputs", allow_empty=False),
        "read_files": sorted(_texts(raw["read_files"], "read_files", paths=True)),
        "file_owners": file_owners,
        "depends_on": sorted(_texts(raw["depends_on"], "slice dependencies")),
        "acceptance": _text(raw["acceptance"], "acceptance"),
        "real_test": {name: _text(raw["real_test"][name], "real_test " + name)
                      for name in sorted(_TEST_FIELDS)},
    }


def _prepare(source):
    """Validate the selected source graph and derive canonical cards in memory.

    ``source`` contains a revision and nonempty source nodes, each with explicit
    slices. Return ``(canonical_source, cards, order)`` after validating node and
    slice dependency graphs using the existing BoosterAI graph helper. Reject
    missing/duplicate IDs, conflicting owners, unordered declared read/write or
    write/write overlaps, and malformed contracts. No external state is read or
    written; correctness depends on complete declarations by the source owner.
    """
    _fields(source, _SOURCE_FIELDS, "source")
    revision = _text(source["revision"], "source revision")
    _require(isinstance(source["nodes"], list) and source["nodes"],
             "source nodes must be a nonempty list")
    _require(len(source["nodes"]) <= _MAX_NODES, "source node limit exceeded")
    nodes = {}
    node_keys = set()
    slices = {}
    slice_keys = set()
    slice_nodes = {}
    owners = {}
    file_references = 0
    for raw in source["nodes"]:
        _fields(raw, _NODE_FIELDS, "node")
        node_id = _text(raw["id"], "node id")
        _require(_key(node_id) not in node_keys, "duplicate source node")
        node_keys.add(_key(node_id))
        node_dependencies = sorted(_texts(raw["depends_on"], "node dependencies"))
        _require(isinstance(raw["slices"], list) and raw["slices"],
                 "every source node needs at least one slice")
        _require(len(slices) + len(raw["slices"]) <= _MAX_SLICES,
                 "source slice limit exceeded")
        node_slices = []
        for candidate in raw["slices"]:
            item = _slice(candidate)
            item_id = item["id"]
            _require(_key(item_id) not in slice_keys, "duplicate slice id")
            slice_keys.add(_key(item_id))
            slices[item_id] = item
            slice_nodes[item_id] = node_id
            node_slices.append(item_id)
            file_references += len(item["file_owners"]) + len(item["read_files"])
            _require(file_references <= _MAX_FILE_REFERENCES, "file reference limit exceeded")
            for path, owner in item["file_owners"].items():
                key = _key(path)
                _require(key not in owners or owners[key] == owner,
                         "conflicting file owners")
                owners[key] = owner
        nodes[node_id] = {"id": node_id, "depends_on": node_dependencies,
                          "slices": sorted(node_slices)}
    ordered_paths = sorted(set(owners) | {
        _key(path) for item in slices.values() for path in item["read_files"]
    })
    for path in ordered_paths:
        prefix = path + "/"
        index = bisect_left(ordered_paths, prefix)
        _require(index == len(ordered_paths) or not ordered_paths[index].startswith(prefix),
                 "declared file cannot contain another declared file")
    node_order, node_ancestors = _CORE.dependency_order({
        item: nodes[item]["depends_on"] for item in nodes
    })
    expanded_edge_upper_bound = 0
    for node_id in node_order:
        inherited = sum(len(nodes[dependency]["slices"])
                        for dependency in nodes[node_id]["depends_on"])
        for item_id in nodes[node_id]["slices"]:
            expanded_edge_upper_bound += inherited + len(slices[item_id]["depends_on"])
            _require(expanded_edge_upper_bound <= _MAX_EXPANDED_EDGES,
                     "expanded dependency edge limit exceeded")
    dependencies = {}
    for node_id in node_order:
        for item_id in nodes[node_id]["slices"]:
            item = slices[item_id]
            expanded = set(item["depends_on"])
            for dependency in item["depends_on"]:
                _require(dependency in slices, "unknown slice dependency")
                dependency_node = slice_nodes[dependency]
                _require(dependency_node == node_id or dependency_node in node_ancestors[node_id],
                         "cross-node slice dependency missing from source graph")
            for dependency_node in nodes[node_id]["depends_on"]:
                expanded.update(nodes[dependency_node]["slices"])
            dependencies[item_id] = sorted(expanded)
    order, ancestors = _CORE.dependency_order(dependencies)
    writes = {item: {_key(path) for path in slices[item]["file_owners"]} for item in order}
    reads = {item: {_key(path) for path in slices[item]["read_files"]} for item in order}
    for index, first in enumerate(order):
        for second in order[index + 1:]:
            conflict = (writes[first] & (writes[second] | reads[second])
                        or writes[second] & reads[first])
            _require(not conflict or first in ancestors[second] or second in ancestors[first],
                     "unordered declared file conflict: " + first + ", " + second)
    canonical_nodes = []
    for node_id in sorted(nodes):
        canonical_nodes.append({"id": node_id, "depends_on": nodes[node_id]["depends_on"],
                                "slices": [slices[item] for item in nodes[node_id]["slices"]]})
    canonical_source = {"revision": revision, "nodes": canonical_nodes}
    cards = []
    for item_id in order:
        card = dict(slices[item_id])
        card["source_node"] = slice_nodes[item_id]
        card["depends_on"] = dependencies[item_id]
        cards.append(card)
    return canonical_source, cards, order


def generate_cards(source):
    """Generate deterministic cards only from a valid explicit source graph.

    ``source`` is the selected revision and its complete source-node/slice data.
    Return a new bundle containing its revision, canonical SHA-256 and one card
    per declared slice. Node dependencies are expanded to all predecessor slices.
    Invalid or conflicting source graphs raise ValueError before a bundle exists.
    Cards describe tests; this function executes no work and cannot authenticate
    source completeness beyond the caller-supplied graph.
    """
    canonical, cards, _ = _prepare(source)
    raw = json.dumps(canonical, sort_keys=True, ensure_ascii=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return {"source_revision": canonical["revision"],
            "source_sha256": hashlib.sha256(raw).hexdigest(), "cards": cards}


def validate_cards(source, bundle):
    """Compare a proposed bundle with every node and slice in its actual source.

    ``source`` is the explicit authoritative selection, not a separate list of
    expected card IDs; ``bundle`` is the submitted card bundle. Return a compact
    coverage/order report only after revision, source digest, unique card IDs,
    exact slice fields, dependencies and node coverage agree. Missing, duplicate,
    extra or altered cards and invalid source graphs raise ValueError. No task or
    acceptance test is executed, and caller authority or semantic atomicity is
    not authenticated by this comparison.
    """
    expected = generate_cards(source)
    _fields(bundle, _BUNDLE_FIELDS, "bundle")
    _require(bundle["source_revision"] == expected["source_revision"]
             and bundle["source_sha256"] == expected["source_sha256"],
             "source revision or digest differs")
    _require(isinstance(bundle["cards"], list), "cards must be a list")
    proposed = {}
    keys = set()
    for card in bundle["cards"]:
        _require(isinstance(card, dict), "card must be a mapping")
        card_id = _text(card.get("id"), "card id")
        _require(_key(card_id) not in keys, "duplicate card id")
        keys.add(_key(card_id))
        proposed[card_id] = card
    expected_cards = {card["id"]: card for card in expected["cards"]}
    missing = sorted(set(expected_cards) - set(proposed))
    extra = sorted(set(proposed) - set(expected_cards))
    _require(not missing and not extra,
             "card coverage differs: missing=" + repr(missing) + "; extra=" + repr(extra))
    for item_id, card in expected_cards.items():
        _require(proposed[item_id] == card, "card differs from source: " + item_id)
    source_ids = {node["id"] for node in source["nodes"]}
    covered = {card["source_node"] for card in proposed.values()}
    _require(covered == source_ids, "source node coverage differs")
    return {"valid": True, "source_revision": expected["source_revision"],
            "source_sha256": expected["source_sha256"],
            "source_node_count": len(source_ids), "card_count": len(expected_cards),
            "order": [card["id"] for card in expected["cards"]],
            "acceptance_test_executed": False}


def _validation_request_fits(source, bundle):
    """Return whether the CLI can validate the generated bundle with its source.

    Count the decoded characters of compact UTF-8 JSON incrementally. Escape
    any isolated surrogate as JSON so it is representable in UTF-8. Whitespace
    or extra escapes added by a caller still count toward the input limit.
    Neither the source nor the bundle is modified.
    """
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"))
    count = 0
    for fragment in encoder.iterencode({"source": source, "bundle": bundle}):
        if not fragment.isascii():
            fragment = fragment.encode("utf-8", "backslashreplace").decode("utf-8")
        count += len(fragment)
        if count > _MAX_STDIN:
            return False
    return True


def main(argv=None):
    """Handle a bounded JSON request through the real, non-executing CLI.

    ``generate`` reads a UTF-8 source object from stdin; ``validate`` reads an object
    with exactly ``source`` and ``bundle``. Emit JSON to stdout and return 0 on
    success or 2 for malformed input or invalid plans. Payloads are never logged,
    written to files, sent to a network, or interpreted as shell commands.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("generate", "validate"))
    args = parser.parse_args(argv)
    try:
        # Keep the character limit and avoid the platform's default pipe codec.
        stream = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="strict")
        try:
            raw = stream.read(_MAX_STDIN + 1)
        finally:
            stream.detach()
        _require(len(raw) <= _MAX_STDIN, "input too large")
        request = json.loads(raw, object_pairs_hook=_unique_object)
        if args.mode == "generate":
            result = generate_cards(request)
            _require(_validation_request_fits(request, result),
                     "source and bundle exceed compact validation request limit")
        else:
            _fields(request, {"source", "bundle"}, "validation request")
            result = validate_cards(request["source"], request["bundle"])
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        print(json.dumps({"valid": False, "error": str(error)}, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
