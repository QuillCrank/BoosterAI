"""Derive unresolved planning cards from a real Mermaid control-flow source.

This companion reuses BoosterAI's narrow Mermaid parser and task-card validator.
It does not execute actions, assume alternative branches all occurred, infer
semantic atomicity, or turn a cyclic control-flow graph into an execution DAG.
"""

import argparse
import importlib.util
import json
from pathlib import Path
import sys


def _load(name, filename):
    """Load one bundled sibling module without changing global configuration.

    ``name`` is its in-process name and ``filename`` is a fixed sibling file.
    Return the loaded module; missing or invalid package files raise their normal
    import error. This reads code from the current package and writes no files.
    """
    path = Path(__file__).resolve().with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SCHEMA = _load("booster_flow_schema", "flow_schema_42d8e671.py")
_TASKS = _load("booster_task_cards", "task_cards_a61e2c97.py")
parse_mermaid = _SCHEMA.parse_mermaid
_BUNDLE_FIELDS = {"format", "source_sha256", "diagram_sha256", "cards"}
_EDGE_FIELDS = {"from", "to", "label"}
_MAX_STDIN = 4 * 1024 * 1024
_MAX_ROUTE_EDGES = 1024


def _require(condition, message):
    """Reject an invalid graph-derived planning request without side effects.

    ``condition`` is truth-tested and ``message`` describes the failure.
    Return None on success or raise ValueError; no tasks or files are changed.
    """
    if not condition:
        raise ValueError(message)


def _edge_key(edge):
    """Validate and return the identity of one declared control transition.

    ``edge`` must contain exactly string ``from``/``to`` IDs and a string or
    null ``label``. Return a comparable tuple, or raise ValueError for malformed
    input. This does not decide whether the branch occurred or may be executed.
    """
    _require(isinstance(edge, dict) and set(edge) == _EDGE_FIELDS,
             "invalid transition fields")
    _require(isinstance(edge["from"], str) and isinstance(edge["to"], str)
             and (edge["label"] is None or isinstance(edge["label"], str)),
             "invalid transition values")
    return edge["from"], edge["to"], edge["label"]


def generate_drafts(document):
    """Produce one unresolved candidate card for every parsed source node.

    ``document`` is the actual selected Markdown source. Return a deterministic
    bundle bound to its complete text and diagram hashes. Each card preserves all
    incoming and outgoing control transitions and explicitly lists the metadata,
    decision-domain or loop information the source does not establish. Invalid
    or unsupported source raises ValueError. Candidates are not proven atomic,
    executable, branch-tested, or accepted, and no source action is run.
    """
    graph = parse_mermaid(document)
    cyclic = {item for component in graph["cyclic_components"] for item in component}
    cards = []
    for node in graph["nodes"]:
        node_id = node["id"]
        unresolved = {"atomicity", "inputs", "outputs", "owner", "read_files",
                      "file_owners", "acceptance", "real_target_test"}
        if node["kind"] == "decision":
            unresolved.add("decision_domain")
        if node_id in cyclic:
            unresolved.update(("loop_budget", "changed_hypothesis"))
        cards.append({
            "id": node_id,
            "label": node["label"],
            "kind": node["kind"],
            "incoming": [dict(edge) for edge in graph["edges"] if edge["to"] == node_id],
            "outgoing": [dict(edge) for edge in graph["edges"] if edge["from"] == node_id],
            "unresolved": sorted(unresolved),
            "status": "UNRESOLVED",
            "execution_allowed": False,
        })
    return {"format": "boosterai-flow-drafts-v1", "source_sha256": graph["source_sha256"],
            "diagram_sha256": graph["diagram_sha256"], "cards": cards}


def audit_drafts(document, bundle):
    """Compare candidate cards with the full graph parsed from the source again.

    ``document`` is the currently selected source; ``bundle`` is a proposed
    result of ``generate_drafts``. Return source/card and transition counts only
    after every node, incoming edge, outgoing edge and unchanged field matches.
    Missing, extra, duplicate, stale or altered data raises ValueError. A valid
    result means structural coverage of this supported diagram only; it neither
    authorizes execution nor proves any branch or target test was exercised.
    """
    expected = generate_drafts(document)
    _require(isinstance(bundle, dict) and set(bundle) == _BUNDLE_FIELDS,
             "invalid draft bundle fields")
    for key in ("format", "source_sha256", "diagram_sha256"):
        _require(bundle[key] == expected[key], "source or draft format differs")
    _require(isinstance(bundle["cards"], list), "draft cards must be a list")
    proposed = {}
    for card in bundle["cards"]:
        _require(isinstance(card, dict) and isinstance(card.get("id"), str),
                 "invalid draft card")
        _require(card["id"] not in proposed, "duplicate draft card")
        proposed[card["id"]] = card
    expected_cards = {card["id"]: card for card in expected["cards"]}
    missing = sorted(set(expected_cards) - set(proposed))
    extra = sorted(set(proposed) - set(expected_cards))
    _require(not missing and not extra,
             "draft coverage differs: missing=" + repr(missing) + "; extra=" + repr(extra))
    for node_id, card in expected_cards.items():
        _require(type(proposed[node_id].get("execution_allowed")) is bool
                 and proposed[node_id]["execution_allowed"] is False,
                 "invalid draft execution flag")
        _require(proposed[node_id] == card, "draft differs from source node: " + node_id)
    edges = [edge for card in expected["cards"] for edge in card["outgoing"]]
    return {"valid": True, "source_sha256": expected["source_sha256"],
            "diagram_sha256": expected["diagram_sha256"],
            "source_node_count": len(expected_cards), "card_count": len(proposed),
            "source_edge_count": len(edges), "covered_edge_count": len(edges),
            "branch_edge_count": sum(edge["label"] is not None for edge in edges),
            "execution_allowed": False, "branch_execution_verified": False,
            "acceptance_test_executed": False}


def preview_route(document, transitions):
    """Check a proposed control path without treating it as an executed route.

    ``document`` is the current supported source; ``transitions`` is a nonempty,
    bounded list of exact ``from``/``to``/``label`` source edges. Require a start
    at a graph entry and a continuous path. Return visited and unvisited nodes,
    unvisited edges, repeated nodes and whether the preview ends at a terminal.
    Invalid paths raise ValueError. Repetition is reported, not unrolled into an
    executable DAG or granted a retry budget; terminal does not mean success.
    """
    graph = parse_mermaid(document)
    _require(isinstance(transitions, list) and 0 < len(transitions) <= _MAX_ROUTE_EDGES,
             "transitions must be a nonempty bounded list")
    source_edges = {_edge_key(edge) for edge in graph["edges"]}
    path = [_edge_key(edge) for edge in transitions]
    _require(path[0][0] in graph["entry_nodes"], "route must start at an entry node")
    _require(all(edge in source_edges for edge in path), "route contains an unknown transition")
    _require(all(path[index][1] == path[index + 1][0] for index in range(len(path) - 1)),
             "route is not continuous")
    visited = [path[0][0]] + [edge[1] for edge in path]
    seen = set()
    repeated = set()
    for node in visited:
        if node in seen:
            repeated.add(node)
        seen.add(node)
    used = set(path)
    return {"source_sha256": graph["source_sha256"], "diagram_sha256": graph["diagram_sha256"],
            "visited_nodes": visited,
            "unvisited_nodes": sorted(node["id"] for node in graph["nodes"] if node["id"] not in seen),
            "unvisited_edges": [dict(edge) for edge in graph["edges"] if _edge_key(edge) not in used],
            "terminal_reached": visited[-1] in graph["terminal_nodes"],
            "repeated_nodes": sorted(repeated), "execution_allowed": False,
            "branch_execution_verified": False, "acceptance_test_executed": False}


def bind_node(document, bundle, node_id, slices):
    """Bind explicit task metadata to one action after auditing the whole source.

    ``document`` and ``bundle`` must pass full source-to-draft coverage;
    ``node_id`` selects an existing action, and ``slices`` supplies the existing
    BoosterAI task-card metadata for that one node. Return its validated local
    card bundle, original control edges and every other unbound source node.
    Invalid/missing fields, decisions and conflicts raise ValueError. This does
    not establish semantic coverage, satisfy incoming branch conditions, grant
    execution permission, or prove any acceptance test occurred.
    """
    audit_drafts(document, bundle)
    graph = parse_mermaid(document)
    nodes = {node["id"]: node for node in graph["nodes"]}
    _require(isinstance(node_id, str) and node_id in nodes, "unknown source node")
    _require(nodes[node_id]["kind"] == "action", "decision binding is not supported")
    source = {"revision": graph["source_sha256"] + ":" + node_id,
              "nodes": [{"id": node_id, "depends_on": [], "slices": slices}]}
    cards = _TASKS.generate_cards(source)
    _TASKS.validate_cards(source, cards)
    return {"node_id": node_id, "source_sha256": graph["source_sha256"],
            "diagram_sha256": graph["diagram_sha256"], "card_source": source,
            "card_bundle": cards,
            "incoming": [dict(edge) for edge in graph["edges"] if edge["to"] == node_id],
            "outgoing": [dict(edge) for edge in graph["edges"] if edge["from"] == node_id],
            "other_source_nodes_unbound": sorted(set(nodes) - {node_id}),
            "execution_allowed": False, "semantic_coverage_verified": False,
            "branch_execution_verified": False, "acceptance_test_executed": False}


def _audit_request_fits(document, bundle):
    """Check that the CLI can audit its generated draft with the same source.

    ``document`` and ``bundle`` form the normal compact JSON audit request.
    Return False as soon as its UTF-8 size would exceed the CLI input bound;
    otherwise return True. This computes size incrementally and does not write,
    transmit, or execute the source. Optional Unicode escaping or whitespace
    added by a caller can still make its JSON request exceed the input limit.
    """
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"))
    size = 0
    for part in encoder.iterencode({"document": document, "bundle": bundle}):
        size += len(part.encode("utf-8"))
        if size > _MAX_STDIN:
            return False
    return True


def main(argv=None):
    """Expose bounded, non-executing source/draft operations on standard input.

    ``drafts`` reads UTF-8 Markdown directly. ``audit``, ``preview`` and ``bind``
    read strict UTF-8 JSON containing the document and their named arguments.
    Input is bounded in bytes and decoded independently of the Windows console
    code page. A bind result must fit a compact Task Cards validate request.
    Return JSON with exit 0 on success or 2 on invalid input. Source text and
    task descriptions are never written to files or executed.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("drafts", "audit", "preview", "bind"))
    args = parser.parse_args(argv)
    try:
        raw_bytes = sys.stdin.buffer.read(_MAX_STDIN + 1)
        _require(len(raw_bytes) <= _MAX_STDIN, "input too large")
        raw = raw_bytes.decode("utf-8")
        if args.mode == "drafts":
            result = generate_drafts(raw)
            _require(_audit_request_fits(raw, result),
                     "source and draft exceed compact audit request limit")
        else:
            request = json.loads(raw, object_pairs_hook=_TASKS._unique_object)
            fields = {"audit": {"document", "bundle"},
                      "preview": {"document", "transitions"},
                      "bind": {"document", "bundle", "node_id", "slices"}}[args.mode]
            _require(isinstance(request, dict) and set(request) == fields, "invalid request fields")
            if args.mode == "audit":
                result = audit_drafts(request["document"], request["bundle"])
            elif args.mode == "preview":
                result = preview_route(request["document"], request["transitions"])
            else:
                result = bind_node(request["document"], request["bundle"],
                                   request["node_id"], request["slices"])
                _require(
                    _TASKS._validation_request_fits(result["card_source"], result["card_bundle"]),
                    "bound source and cards exceed compact validation request limit",
                )
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        print(json.dumps({"valid": False, "error": str(error)}, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
