"""Synthetic graph fixtures for source-derived flow cards and route previews.

These tests do not claim that a task or branch was executed in its real target.
The actual repository algorithm is verified separately by the task owner.
"""

import copy
import hashlib
import io
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import repo_temp


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "booster_flow_cards", ROOT / "scripts" / "flow_cards_f930b1a6.py"
)
flow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(flow)


SYNTHETIC_DOCUMENT = """# Synthetic graph fixture

## Algorithm format

```mermaid
flowchart TD
  A[Inspect] --> B{Choose?}
  B -->|yes| C[Prepare]
  B -->|no| D[Skip preparation]
  C --> E{Retry?}
  D --> E
  E -->|again| C
  E -->|done| F[Finish]
```

This prose is part of the selected document, but is not a graph node.
"""


def edge(source, target, label=None):
    """Return an independent edge object for a synthetic graph transition."""
    return {"from": source, "to": target, "label": label}


def edge_keys(edges):
    """Return comparable transition tuples while checking no edge was duplicated."""
    values = [(item["from"], item["to"], item["label"]) for item in edges]
    if len(values) != len(set(values)):
        raise AssertionError("edge collection contains a duplicate transition")
    return set(values)


def task_slice(identifier, *, depends_on=(), output_path=None):
    """Build explicit slice metadata without claiming real task execution.

    The returned dict satisfies the existing task-card schema, including a
    declared owner, intended files, acceptance text and real-target test plan.
    Distinct output paths avoid accidental write conflicts in the test fixture.
    """
    output_path = output_path or "work/" + identifier + ".py"
    return {
        "id": identifier,
        "owner": "worker",
        "contract": "Perform the specified synthetic piece for " + identifier,
        "inputs": ["Declared input for " + identifier],
        "outputs": ["Declared result for " + identifier],
        "read_files": [],
        "file_owners": {output_path: "worker"},
        "depends_on": list(depends_on),
        "acceptance": "The declared result is observed",
        "real_test": {
            "target": "Synthetic permitted runtime",
            "action": "Invoke the declared action",
            "expected": "Observe its declared result",
        },
    }


ALL_EDGES = [
    edge("A", "B"), edge("B", "C", "yes"), edge("B", "D", "no"),
    edge("C", "E"), edge("D", "E"), edge("E", "C", "again"),
    edge("E", "F", "done"),
]
GENERAL_UNRESOLVED = {
    "atomicity", "inputs", "outputs", "read_files", "file_owners", "owner",
    "acceptance", "real_target_test"
}


class FlowCardTests(unittest.TestCase):
    """Exercise synthetic coverage and route integrity without executing work."""

    def setUp(self):
        """Register cleanup for this test's isolated, repository-owned scratch."""
        self.workspace = repo_temp.workspace(self)

    def test_parse_graph_preserves_nodes_decisions_edges_and_cycle(self):
        """Parse all six fixture nodes, seven edges and the C/E cycle exactly."""
        graph = flow.parse_mermaid(SYNTHETIC_DOCUMENT)
        nodes = {node["id"]: node for node in graph["nodes"]}
        self.assertEqual(set(nodes), set("ABCDEF"))
        self.assertEqual(nodes["A"]["label"], "Inspect")
        self.assertEqual(nodes["B"]["label"], "Choose?")
        self.assertEqual(nodes["E"]["label"], "Retry?")
        self.assertEqual(nodes["B"]["kind"], "decision")
        self.assertEqual(nodes["E"]["kind"], "decision")
        for identifier in ("A", "C", "D", "F"):
            self.assertEqual(nodes[identifier]["kind"], "action")
        self.assertEqual(edge_keys(graph["edges"]), edge_keys(ALL_EDGES))
        self.assertEqual(graph["entry_nodes"], ["A"])
        self.assertEqual(graph["terminal_nodes"], ["F"])
        self.assertEqual({frozenset(group) for group in graph["cyclic_components"]},
                         {frozenset({"C", "E"})})

    def test_one_unresolved_draft_per_source_node_with_exact_edges(self):
        """Every node has a draft carrying its exact incoming and outgoing edges."""
        original = SYNTHETIC_DOCUMENT
        bundle = flow.generate_drafts(original)
        self.assertEqual(bundle["format"], "boosterai-flow-drafts-v1")
        self.assertRegex(bundle["source_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(bundle["diagram_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(bundle, flow.generate_drafts(original))
        drafts = {card["id"]: card for card in bundle["cards"]}
        self.assertEqual(set(drafts), set("ABCDEF"))
        for identifier, card in drafts.items():
            self.assertEqual(card["status"], "UNRESOLVED")
            self.assertFalse(card["execution_allowed"])
            self.assertEqual(card["unresolved"], sorted(set(card["unresolved"])))
            self.assertTrue(GENERAL_UNRESOLVED.issubset(card["unresolved"]))
            self.assertEqual(edge_keys(card["incoming"]), edge_keys(
                [item for item in ALL_EDGES if item["to"] == identifier]))
            self.assertEqual(edge_keys(card["outgoing"]), edge_keys(
                [item for item in ALL_EDGES if item["from"] == identifier]))
        for identifier in ("B", "E"):
            self.assertIn("decision_domain", drafts[identifier]["unresolved"])
        for identifier in ("C", "E"):
            self.assertIn("loop_budget", drafts[identifier]["unresolved"])
            self.assertIn("changed_hypothesis", drafts[identifier]["unresolved"])
        self.assertEqual(SYNTHETIC_DOCUMENT, original)

    def test_full_coverage_never_claims_execution_or_branch_verification(self):
        """A complete draft collection proves structural coverage only."""
        bundle = flow.generate_drafts(SYNTHETIC_DOCUMENT)
        snapshot = copy.deepcopy(bundle)
        report = flow.audit_drafts(SYNTHETIC_DOCUMENT, bundle)
        self.assertTrue(report["valid"])
        self.assertEqual(report["source_node_count"], 6)
        self.assertEqual(report["card_count"], 6)
        self.assertEqual(report["source_edge_count"], 7)
        self.assertEqual(report["covered_edge_count"], 7)
        self.assertEqual(report["branch_edge_count"], 4)
        self.assertFalse(report["execution_allowed"])
        self.assertFalse(report["branch_execution_verified"])
        self.assertEqual(bundle, snapshot)

    def test_missing_extra_duplicate_or_altered_cards_fail_closed(self):
        """A source node cannot disappear or be replaced by an invented card."""
        bundle = flow.generate_drafts(SYNTHETIC_DOCUMENT)
        missing = copy.deepcopy(bundle)
        missing["cards"].pop()
        duplicate = copy.deepcopy(bundle)
        duplicate["cards"].append(copy.deepcopy(duplicate["cards"][0]))
        extra = copy.deepcopy(bundle)
        new_card = copy.deepcopy(extra["cards"][0])
        new_card["id"] = "unknown"
        extra["cards"].append(new_card)
        changed_label = copy.deepcopy(bundle)
        changed_label["cards"][0]["label"] = "Invented action"
        changed_kind = copy.deepcopy(bundle)
        changed_kind["cards"][0]["kind"] = "invented"
        changed_status = copy.deepcopy(bundle)
        changed_status["cards"][0]["status"] = "COMPLETE"
        changed_execution = copy.deepcopy(bundle)
        changed_execution["cards"][0]["execution_allowed"] = True
        removed_owner = copy.deepcopy(bundle)
        removed_owner["cards"][0]["unresolved"].remove("owner")
        for invalid in (missing, duplicate, extra, changed_label, changed_kind,
                        changed_status, changed_execution, removed_owner):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                flow.audit_drafts(SYNTHETIC_DOCUMENT, invalid)

    def test_removed_duplicate_or_changed_transitions_fail_coverage(self):
        """Removing, repeating or relabeling an edge cannot pass an audit."""
        bundle = flow.generate_drafts(SYNTHETIC_DOCUMENT)
        missing = copy.deepcopy(bundle)
        next(card for card in missing["cards"] if card["id"] == "B")["outgoing"].pop()
        duplicated = copy.deepcopy(bundle)
        edges = next(card for card in duplicated["cards"] if card["id"] == "B")["outgoing"]
        edges.append(copy.deepcopy(edges[0]))
        relabeled = copy.deepcopy(bundle)
        edges = next(card for card in relabeled["cards"] if card["id"] == "B")["outgoing"]
        edges[0]["label"] = "invented"
        missing_incoming = copy.deepcopy(bundle)
        next(card for card in missing_incoming["cards"] if card["id"] == "E")["incoming"].pop()
        for invalid in (missing, duplicated, relabeled, missing_incoming):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                flow.audit_drafts(SYNTHETIC_DOCUMENT, invalid)

    def test_numeric_execution_flags_are_not_boolean_source_claims(self):
        """Neither 0 nor 1 can replace the explicit JSON false authorization flag."""
        bundle = flow.generate_drafts(SYNTHETIC_DOCUMENT)
        for number in (0, 1):
            invalid = copy.deepcopy(bundle)
            invalid["cards"][0]["execution_allowed"] = number
            with self.subTest(value=number), self.assertRaises(ValueError):
                flow.audit_drafts(SYNTHETIC_DOCUMENT, invalid)

    def test_cli_drafts_reject_when_full_audit_request_would_exceed_limit(self):
        """Draft output must fit with its source in the bounded audit envelope.

        Patch the byte limit to a small number instead of allocating a large
        document or creating files. A source below the limit whose compact
        source-and-bundle audit request exceeds it must fail closed.
        """
        document = SYNTHETIC_DOCUMENT
        bundle = flow.generate_drafts(document)
        raw_document = document.encode("utf-8")
        audit_request = json.dumps({"document": document, "bundle": bundle},
                                   ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        limit = (len(raw_document) + len(audit_request)) // 2
        self.assertLess(len(raw_document), limit)
        self.assertGreater(len(audit_request), limit)

        class FakeInput:
            """Expose one bounded synthetic stdin buffer without touching disk."""

            def __init__(self, value):
                self.buffer = io.BytesIO(value)

        output = io.StringIO()
        with patch.object(flow, "_MAX_STDIN", limit), patch.object(sys, "stdin", FakeInput(raw_document)), \
                patch.object(sys, "stdout", output):
            result = flow.main(["drafts"])
        self.assertEqual(result, 2)
        self.assertFalse(json.loads(output.getvalue())["valid"])

    def test_changed_graph_or_document_cannot_reuse_prior_drafts(self):
        """The audit binds cards to current source content and diagram content."""
        bundle = flow.generate_drafts(SYNTHETIC_DOCUMENT)
        for changed in (
            SYNTHETIC_DOCUMENT.replace("C[Prepare]", "C[Prepare differently]"),
            SYNTHETIC_DOCUMENT.replace("# Synthetic graph fixture", "# Changed source context"),
            SYNTHETIC_DOCUMENT.replace("E -->|done| F[Finish]", "E -->|later| F[Finish]"),
        ):
            with self.subTest(source=changed), self.assertRaises(ValueError):
                flow.audit_drafts(changed, bundle)

    def test_preview_alternative_branch_does_not_require_untaken_predecessor(self):
        """B=no reaches E through D without requiring the untaken C branch."""
        path = [edge("A", "B"), edge("B", "D", "no"), edge("D", "E"),
                edge("E", "F", "done")]
        original = copy.deepcopy(path)
        report = flow.preview_route(SYNTHETIC_DOCUMENT, path)
        self.assertEqual(report["visited_nodes"], ["A", "B", "D", "E", "F"])
        self.assertEqual(set(report["unvisited_nodes"]), {"C"})
        self.assertEqual(edge_keys(report["unvisited_edges"]), edge_keys(
            [edge("B", "C", "yes"), edge("C", "E"), edge("E", "C", "again")]))
        self.assertTrue(report["terminal_reached"])
        self.assertFalse(report["repeated_nodes"])
        self.assertFalse(report["execution_allowed"])
        self.assertFalse(report["branch_execution_verified"])
        self.assertEqual(path, original)

    def test_preview_loop_retains_repeated_nodes_and_does_not_execute(self):
        """A retry path is represented faithfully without inventing a loop budget."""
        path = [edge("A", "B"), edge("B", "C", "yes"), edge("C", "E"),
                edge("E", "C", "again"), edge("C", "E"), edge("E", "F", "done")]
        report = flow.preview_route(SYNTHETIC_DOCUMENT, path)
        self.assertEqual(report["visited_nodes"], ["A", "B", "C", "E", "C", "E", "F"])
        self.assertEqual(set(report["repeated_nodes"]), {"C", "E"})
        self.assertEqual(set(report["unvisited_nodes"]), {"D"})
        self.assertEqual(edge_keys(report["unvisited_edges"]),
                         edge_keys([edge("B", "D", "no"), edge("D", "E")]))
        self.assertTrue(report["terminal_reached"])
        self.assertFalse(report["execution_allowed"])
        self.assertFalse(report["branch_execution_verified"])

    def test_unknown_wrong_or_disconnected_transitions_fail_closed(self):
        """Only a continuous, source-declared path beginning at entry is valid."""
        bad_paths = (
            [edge("B", "C", "yes")],
            [edge("A", "B"), edge("B", "D", "yes")],
            [edge("A", "B"), edge("C", "E")],
            [edge("A", "B"), edge("B", "unknown", "no")],
            [edge("A", "B"), {"from": "B", "to": "D"}],
        )
        for path in bad_paths:
            with self.subTest(path=path), self.assertRaises(ValueError):
                flow.preview_route(SYNTHETIC_DOCUMENT, path)

    def test_bind_action_node_reuses_slice_cards_without_claiming_completion(self):
        """Binding one action retains every other source node as unbound."""
        bundle = flow.generate_drafts(SYNTHETIC_DOCUMENT)
        slices = [task_slice("prepare-first"),
                  task_slice("prepare-second", depends_on=("prepare-first",))]
        original_bundle = copy.deepcopy(bundle)
        original_slices = copy.deepcopy(slices)
        result = flow.bind_node(SYNTHETIC_DOCUMENT, bundle, "C", slices)
        self.assertEqual(result["node_id"], "C")
        self.assertEqual(set(result["other_source_nodes_unbound"]), set("ABDEF"))
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["semantic_coverage_verified"])
        self.assertFalse(result["branch_execution_verified"])
        self.assertFalse(result["acceptance_test_executed"])
        bound = result["card_bundle"]
        self.assertRegex(bound["source_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual({item["id"] for item in bound["cards"]},
                         {"prepare-first", "prepare-second"})
        later = next(item for item in bound["cards"] if item["id"] == "prepare-second")
        self.assertIn("prepare-first", later["depends_on"])
        self.assertEqual(bundle, original_bundle)
        self.assertEqual(slices, original_slices)

    def test_bind_rejects_incomplete_source_unknown_or_decision_node(self):
        """One slice binding cannot conceal a missing draft or select a decision."""
        bundle = flow.generate_drafts(SYNTHETIC_DOCUMENT)
        incomplete = copy.deepcopy(bundle)
        incomplete["cards"].pop()
        tampered = copy.deepcopy(bundle)
        tampered["cards"][0]["status"] = "COMPLETE"
        for source, drafts, node_id in (
            (SYNTHETIC_DOCUMENT, incomplete, "C"),
            (SYNTHETIC_DOCUMENT, tampered, "C"),
            (SYNTHETIC_DOCUMENT, bundle, "unknown"),
            (SYNTHETIC_DOCUMENT, bundle, "B"),
            (SYNTHETIC_DOCUMENT.replace("C[Prepare]", "C[Changed]"), bundle, "C"),
        ):
            with self.subTest(node=node_id), self.assertRaises(ValueError):
                flow.bind_node(source, drafts, node_id, [task_slice("piece")])

    def test_bind_rejects_missing_or_conflicting_slice_metadata(self):
        """Slice binding must honor all existing task-card validation rules."""
        bundle = flow.generate_drafts(SYNTHETIC_DOCUMENT)
        for field in ("owner", "inputs", "outputs", "acceptance", "real_test"):
            invalid = task_slice("piece")
            del invalid[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                flow.bind_node(SYNTHETIC_DOCUMENT, bundle, "C", [invalid])
        for invalid in ([], [task_slice("same"), task_slice("same")],
                        [task_slice("one", output_path="work/shared.py"),
                         task_slice("two", output_path="work/SHARED.py")],
                        [task_slice("one", depends_on=("unknown",))]):
            with self.subTest(slices=invalid), self.assertRaises(ValueError):
                flow.bind_node(SYNTHETIC_DOCUMENT, bundle, "C", invalid)

    def test_unsupported_or_incomplete_diagram_syntax_is_not_silently_ignored(self):
        """Unmodeled Mermaid lines and undefined node labels invalidate the source."""
        bad_documents = (
            SYNTHETIC_DOCUMENT.replace("flowchart TD", "flowchart LR"),
            SYNTHETIC_DOCUMENT.replace("  C --> E", "  C -.-> E"),
            SYNTHETIC_DOCUMENT.replace("  C --> E", "  subgraph unexplained\n  C --> E"),
            SYNTHETIC_DOCUMENT.replace("A[Inspect]", "A"),
            SYNTHETIC_DOCUMENT.replace("B -->|no| D[Skip preparation]",
                                       "B --> D[Skip preparation]"),
            SYNTHETIC_DOCUMENT.replace("C --> E", "C --> E --> F"),
        )
        for document in bad_documents:
            with self.subTest(document=document), self.assertRaises(ValueError):
                flow.generate_drafts(document)

    def test_cli_decodes_utf8_bytes_without_console_codepage_conversion(self):
        """A real CLI process preserves the supplied UTF-8 source and rejects invalid bytes."""
        document = SYNTHETIC_DOCUMENT.replace("A[Inspect]", "A[Inspect café — state]")
        raw = document.encode("utf-8")
        command = [sys.executable, "-I", "-S", "-B", str(ROOT / "scripts/flow_cards_f930b1a6.py"), "drafts"]
        actual = subprocess.run(command, cwd=ROOT, input=raw, capture_output=True, timeout=15)
        self.assertEqual(actual.returncode, 0, actual.stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(actual.stdout)["source_sha256"], hashlib.sha256(raw).hexdigest())
        invalid = subprocess.run(command, cwd=ROOT, input=b"\xff", capture_output=True, timeout=15)
        self.assertEqual(invalid.returncode, 2)
        self.assertFalse(json.loads(invalid.stdout)["valid"])

    def test_cli_audit_rejects_duplicate_json_keys(self):
        """The existing strict JSON parser applies to real CLI audit requests."""
        bundle = flow.generate_drafts(SYNTHETIC_DOCUMENT)
        request = '{"document":' + json.dumps(SYNTHETIC_DOCUMENT) + ',"bundle":{},"bundle":' + json.dumps(bundle) + '}'
        command = [sys.executable, "-I", "-S", "-B", str(ROOT / "scripts/flow_cards_f930b1a6.py"), "audit"]
        actual = subprocess.run(command, cwd=ROOT, input=request.encode("utf-8"), capture_output=True, timeout=15)
        self.assertEqual(actual.returncode, 2)
        self.assertFalse(json.loads(actual.stdout)["valid"])

    def test_package_self_check_requires_flow_modules(self):
        """A missing bundled parser or card helper invalidates the package."""
        spec = importlib.util.spec_from_file_location("booster_package", ROOT / "scripts/booster.py")
        package = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(package)
        original = package.checked
        for missing in ("scripts/flow_schema_42d8e671.py", "scripts/flow_cards_f930b1a6.py"):
            def select(root, relative):
                """Simulate one missing path without modifying the actual package."""
                return self.workspace / "absent" if relative == missing else original(root, relative)
            with self.subTest(missing=missing), patch.object(package, "checked", side_effect=select):
                with self.assertRaisesRegex(ValueError, "missing package file"):
                    package.self_check(ROOT)


class FlowAuditBudgetTests(unittest.TestCase):
    @staticmethod
    def call(mode, data, limit):
        output = io.StringIO()
        with io.TextIOWrapper(io.BytesIO(data), encoding="utf-8") as source:
            with (patch.object(sys, "stdin", source), patch.object(sys, "stdout", output),
                  patch.object(flow, "_MAX_STDIN", limit)):
                code = flow.main([mode])
        return code, json.loads(output.getvalue())

    def test_drafts_and_audit_use_the_compact_utf8_budget(self):
        """Accept a fitting UTF-8 envelope even if an escaped version is larger."""
        document = SYNTHETIC_DOCUMENT + "\n" + "\u011f\U0001f642" * 40 + "\n"
        raw = document.encode("utf-8")
        bundle = flow.generate_drafts(document)
        request = {"document": document, "bundle": bundle}
        compact = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        escaped = json.dumps(request, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        limit = len(compact)
        self.assertLess(len(raw), limit - 1)
        self.assertLess(limit, len(escaped))

        self.assertEqual(self.call("drafts", raw, limit), (0, bundle))
        code, report = self.call("audit", compact, limit)
        self.assertEqual(code, 0)
        self.assertTrue(report["valid"])
        self.assertFalse(report["execution_allowed"])
        self.assertEqual(self.call("drafts", raw, limit - 1),
                         (2, {"valid": False, "error": "source and draft exceed compact audit request limit"}))
        self.assertEqual(self.call("audit", escaped, limit),
                         (2, {"valid": False, "error": "input too large"}))


class FlowBindBudgetTests(unittest.TestCase):
    def test_cli_bind_uses_compact_task_card_validation_character_budget(self):
        """Accept the exact compact character limit and reject its first excess."""
        piece = task_slice("prepare")
        piece["contract"] = "Prepare \u011f\U0001f642"
        drafts = flow.generate_drafts(SYNTHETIC_DOCUMENT)
        slices = [piece]
        bound = flow.bind_node(SYNTHETIC_DOCUMENT, drafts, "C", slices)
        payload = json.dumps(
            {"document": SYNTHETIC_DOCUMENT, "bundle": drafts, "node_id": "C", "slices": slices},
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        compact = json.dumps(
            {"source": bound["card_source"], "bundle": bound["card_bundle"]},
            ensure_ascii=False, separators=(",", ":"),
        )
        limit = len(compact)
        self.assertLess(limit, len(compact.encode("utf-8")))

        with patch.object(flow._TASKS, "_MAX_STDIN", limit):
            self.assertEqual(FlowAuditBudgetTests.call("bind", payload, len(payload)), (0, bound))
        with patch.object(flow._TASKS, "_MAX_STDIN", limit - 1):
            self.assertEqual(
                FlowAuditBudgetTests.call("bind", payload, len(payload)),
                (2, {"valid": False,
                     "error": "bound source and cards exceed compact validation request limit"}),
            )
            self.assertEqual(flow.bind_node(SYNTHETIC_DOCUMENT, drafts, "C", slices), bound)


if __name__ == "__main__":
    unittest.main(verbosity=2)
