"""Exercise file-observed BoosterAI continuity in owned repository fixtures."""

import copy
import hashlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import repo_temp


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "continuity_plan", ROOT / "scripts" / "continuity_plan_64e9b1c2.py"
)
continuity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(continuity)


def digest(content):
    """Return the expected SHA-256 of fixture bytes without creating a file."""
    return hashlib.sha256(content).hexdigest()


def node(node_id, *, owner=None, inputs=(), output=None, dependencies=(), content=b"ok"):
    """Build one explicit, file-accepted test slice without touching the filesystem."""
    output = output or node_id + ".out"
    owner = owner or node_id + "-owner"
    return {
        "id": node_id,
        "owner": owner,
        "contract": "Produce the fixture output for " + node_id,
        "file_owners": {output: owner},
        "inputs": list(inputs),
        "outputs": [output],
        "depends_on": list(dependencies),
        "target": output,
        "acceptance": {"type": "sha256", "path": output, "sha256": digest(content)},
    }


class DependencyOrderTests(unittest.TestCase):
    """Exercise the public graph helper without filesystem or process effects."""

    def test_deterministic_order_and_transitive_ancestors(self):
        """Sort independent layers, retain all transitive prerequisites, and preserve input."""
        graph = {"c": ["b"], "a": [], "b": ["a"], "z": []}
        original = copy.deepcopy(graph)
        order, ancestors = continuity.dependency_order(graph)
        self.assertEqual(order, ["a", "z", "b", "c"])
        self.assertEqual(ancestors, {"a": set(), "z": set(), "b": {"a"},
                                     "c": {"a", "b"}})
        self.assertEqual(graph, original)
        self.assertEqual(continuity.dependency_order({}), ([], {}))

    def test_missing_duplicate_and_cyclic_dependencies_are_rejected(self):
        """Reject malformed graph contracts before a caller can select work."""
        bad_graphs = (
            {"a": ["missing"]},
            {"a": ["b"], "b": ["a"]},
            {"a": ["a"]},
            {"a": [], "b": ["a", "a"]},
            {"a": ()},
            {"": []},
        )
        for graph in bad_graphs:
            with self.subTest(graph=graph), self.assertRaises(ValueError):
                continuity.dependency_order(graph)


class ContinuityPlanTests(unittest.TestCase):
    """Verify actual file reads, bounded reuse, graph safety and stop behavior."""

    def setUp(self):
        """Create one owned operation directory with registered success/failure cleanup."""
        self.project = repo_temp.workspace(self)

    def write(self, relative, content):
        """Write only one regular fixture file beneath this test's owned directory."""
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def graph(self):
        """Create a three-slice dependency chain plus one independent real target."""
        self.write("source.txt", b"first")
        self.write("other.txt", b"stable")
        for name in ("a", "b", "c", "d"):
            self.write(name + ".out", name.encode("ascii"))
        return {"nodes": [
            node("c", inputs=("b.out",), dependencies=("b",), content=b"c"),
            node("a", inputs=("source.txt",), content=b"a"),
            node("d", inputs=("other.txt",), content=b"d"),
            node("b", inputs=("a.out",), dependencies=("a",), content=b"b"),
        ]}

    def test_real_target_read_is_required_and_receipts_are_not_evidence(self):
        """A claimed pass cannot verify a missing or mismatched actual output file."""
        plan = {"nodes": [node("a", content=b"expected")]}
        original = copy.deepcopy(plan)
        absent = continuity.evaluate(self.project, plan, observed=("a",))
        self.assertEqual(absent["nodes"]["a"]["state"], "PLANNED")
        self.assertFalse(absent["nodes"]["a"]["target_file_read"])
        self.assertEqual(absent["ready"], ["a"])
        self.write("a.out", b"wrong")
        wrong = continuity.evaluate(self.project, plan, observed=("a",))
        self.assertEqual(wrong["nodes"]["a"]["state"], "PLANNED")
        self.assertTrue(wrong["nodes"]["a"]["target_file_read"])
        self.assertFalse(wrong["nodes"]["a"]["target_matches_expected"])
        with self.assertRaisesRegex(ValueError, "invalid previous evidence"):
            continuity.evaluate(self.project, plan, {"a": {"agent_says_pass": True}})
        self.write("a.out", b"expected")
        accepted = continuity.evaluate(self.project, plan, observed=("a",))
        self.assertEqual(accepted["nodes"]["a"]["state"], "TARGET_MATCHED")
        self.assertTrue(accepted["nodes"]["a"]["target_matches_expected"])
        self.assertFalse(accepted["continuation_permitted"])
        self.assertEqual(plan, original)
        self.assertTrue(any("do not prove code execution" in text for text in accepted["limitations"]))

    def test_reuse_and_input_change_invalidate_only_dependent_slices(self):
        """Unchanged results are reread and reused; a changed input stales its descendants."""
        plan = self.graph()
        first = continuity.evaluate(self.project, plan, observed=("a", "b", "c", "d"))
        self.assertTrue(all(row["state"] == "TARGET_MATCHED" for row in first["nodes"].values()))
        reorder = copy.deepcopy(plan)
        reorder["nodes"].reverse()
        repeated = continuity.evaluate(self.project, reorder, previous=first["evidence"])
        self.assertEqual(repeated["order"], ["a", "d", "b", "c"])
        self.assertTrue(all(row["state"] == "TARGET_REUSED" for row in repeated["nodes"].values()))
        self.write("source.txt", b"changed")
        changed = continuity.evaluate(self.project, plan, previous=first["evidence"])
        self.assertEqual({key: value["state"] for key, value in changed["nodes"].items()},
                         {"a": "STALE", "b": "STALE", "c": "STALE", "d": "TARGET_REUSED"})
        self.assertEqual(changed["ready"], ["a"])
        resumed = continuity.evaluate(self.project, plan, previous=changed["evidence"], observed=("a",))
        self.assertEqual(resumed["nodes"]["a"]["state"], "TARGET_MATCHED")
        self.assertEqual(resumed["nodes"]["b"]["state"], "STALE")
        self.assertEqual(resumed["ready"], ["b"])

    def test_matching_file_observation_does_not_authorize_product_execution(self):
        """A matching file can make a dependent observation ready, not accepted.

        The real file contents are read by the evaluator; no task or acceptance
        test is run. Fresh and reused observation states must both report that
        product acceptance and execution authorization were not established.
        """
        self.write("a.out", b"a")
        plan = {"nodes": [node("a", content=b"a"), node("b", dependencies=("a",))]}
        observed = continuity.evaluate(self.project, plan, observed=("a",))
        self.assertEqual(observed["nodes"]["a"]["state"], "TARGET_MATCHED")
        self.assertEqual(observed["ready"], ["b"])
        self.assertTrue(observed["continuation_permitted"])
        self.assertIs(observed["product_acceptance_verified"], False)
        self.assertIs(observed["execution_authorized"], False)
        reused = continuity.evaluate(self.project, plan, previous=observed["evidence"])
        self.assertEqual(reused["nodes"]["a"]["state"], "TARGET_REUSED")
        self.assertEqual(reused["ready"], ["b"])
        self.assertIs(reused["product_acceptance_verified"], False)
        self.assertIs(reused["execution_authorized"], False)

    def test_contract_output_and_dependency_changes_stale_affected_nodes(self):
        """Changing contract, output bytes or dependency edges invalidates relevant evidence."""
        plan = self.graph()
        first = continuity.evaluate(self.project, plan, observed=("a", "b", "c", "d"))
        changed_contract = copy.deepcopy(plan)
        next(item for item in changed_contract["nodes"] if item["id"] == "a")["contract"] += " revised"
        check = continuity.evaluate(self.project, changed_contract, previous=first["evidence"])
        self.assertEqual([check["nodes"][item]["state"] for item in ("a", "b", "c", "d")],
                         ["STALE", "STALE", "STALE", "TARGET_REUSED"])
        changed_dependency = copy.deepcopy(plan)
        next(item for item in changed_dependency["nodes"] if item["id"] == "d")["depends_on"] = ["a"]
        check = continuity.evaluate(self.project, changed_dependency, previous=first["evidence"])
        self.assertEqual(check["nodes"]["d"]["state"], "STALE")
        self.assertEqual(check["nodes"]["a"]["state"], "TARGET_REUSED")
        self.write("a.out", b"different")
        check = continuity.evaluate(self.project, plan, previous=first["evidence"], observed=("a",))
        self.assertFalse(check["nodes"]["a"]["target_matches_expected"])
        self.assertEqual([check["nodes"][item]["state"] for item in ("a", "b", "c", "d")],
                         ["STALE", "STALE", "STALE", "TARGET_REUSED"])

    def test_missing_input_blocks_ready_and_missing_output_cannot_be_reused(self):
        """Absent inputs block execution, and a removed output invalidates prior acceptance."""
        plan = {"nodes": [node("a", inputs=("source.txt",), content=b"a")]}
        result = continuity.evaluate(self.project, plan, observed=("a",))
        self.assertEqual(result["ready"], [])
        self.assertEqual(result["nodes"]["a"]["missing_inputs"], ["source.txt"])
        self.write("source.txt", b"input")
        output = self.write("a.out", b"a")
        result = continuity.evaluate(self.project, plan, observed=("a",))
        output.unlink()
        result = continuity.evaluate(self.project, plan, previous=result["evidence"])
        self.assertEqual(result["nodes"]["a"]["state"], "STALE")
        self.assertEqual(result["nodes"]["a"]["missing_outputs"], ["a.out"])
        self.assertEqual(result["ready"], ["a"])

    def test_ready_and_parallel_ready_distinguish_shared_owners(self):
        """Independent files stay ready, while one owner receives one parallel slice."""
        a, b = node("a", owner="alpha"), node("b", owner="bravo")
        c, d = node("c", owner="alpha"), node("d", owner="delta")
        plan = {"nodes": [d, b, c, a]}
        result = continuity.evaluate(self.project, plan)
        self.assertEqual(result["ready"], ["a", "b", "c", "d"])
        self.assertEqual(result["parallel_ready"], ["a", "b", "d"])
        self.assertEqual(result["deferred_ready"], ["c"])

    def test_unordered_shared_claims_are_rejected_before_any_file_read(self):
        """Different or identical task owners cannot make unordered shared claims."""
        for second_owner, second_path, error in (("alpha", "shared.txt", "unordered shared file claims"),
                                                 ("bravo", "SHARED.TXT", "conflicting owners for file")):
            a, b = node("a", owner="alpha"), node("b", owner=second_owner)
            a["file_owners"]["shared.txt"] = "alpha"
            b["file_owners"][second_path] = second_owner
            with self.subTest(owner=second_owner, path=second_path):
                with patch.object(continuity, "_read_hash") as read:
                    with self.assertRaisesRegex(ValueError, error):
                        continuity.evaluate(self.project, {"nodes": [a, b]})
                    read.assert_not_called()

    def test_direct_and_transitive_shared_claims_have_explicit_order(self):
        """A direct or transitive dependency permits the same claimed file in order."""
        for dependency in ("a", "b"):
            a, b = node("a", owner="custodian"), node("b", dependencies=("a",))
            c = node("c", owner="custodian", dependencies=(dependency,))
            a["file_owners"]["shared.txt"] = "custodian"
            c["file_owners"]["SHARED.TXT"] = "custodian"
            with self.subTest(dependency=dependency):
                result = continuity.evaluate(self.project, {"nodes": [c, b, a]})
                self.assertEqual(result["order"], ["a", "b", "c"])
                self.assertEqual(result["ready"], ["a"])

    def test_unicode_equivalent_claims_require_dependency(self):
        """Treat composed and decomposed Unicode names as one claimed file."""
        a, b = node("a", owner="custodian"), node("b", owner="custodian")
        a["file_owners"]["caf\u00e9.txt"] = "custodian"
        b["file_owners"]["cafe\u0301.txt"] = "custodian"
        plan = {"nodes": [b, a]}
        with self.assertRaisesRegex(ValueError, "unordered shared file claims"):
            continuity._normal_plan(plan)
        b["depends_on"] = ["a"]
        _, order = continuity._normal_plan(plan)
        self.assertEqual(order, ["a", "b"])

    def test_denial_blocks_only_descendants_and_never_reads_their_files(self):
        """Simulated denials stop a node and descendants while independent work remains ready."""
        plan = {"nodes": [node("a"), node("b", dependencies=("a",)), node("c")]}
        original_read = continuity._read_hash

        def read_independent(root, path):
            """Fail if the evaluator tries reading the simulated denied subtree."""
            self.assertNotIn(path, {"a.out", "b.out"})
            return original_read(root, path)

        with patch.object(continuity, "_read_hash", side_effect=read_independent):
            result = continuity.evaluate(self.project, plan, denied=("a",), observed=("a",))
        self.assertTrue(result["continuation_permitted"])
        self.assertIs(result["product_acceptance_verified"], False)
        self.assertIs(result["execution_authorized"], False)
        self.assertEqual(result["nodes"]["a"]["state"], "DENIED")
        self.assertEqual(result["nodes"]["b"]["state"], "BLOCKED")
        self.assertEqual(result["blocked_descendants"], ["b"])
        self.assertEqual(result["ready"], ["c"])

    def test_stop_returns_no_read_or_continuation(self):
        """Global STOP halts all slices even if the project root is unavailable."""
        result = continuity.evaluate(self.project / "not-created", {"nodes": [node("a")]}, stopped=True)
        self.assertFalse(result["continuation_permitted"])
        self.assertIs(result["product_acceptance_verified"], False)
        self.assertIs(result["execution_authorized"], False)
        self.assertEqual(result["ready"], [])
        self.assertEqual(result["nodes"]["a"]["state"], "HALTED")

    def test_invalid_graph_conflicting_owners_and_non_file_receipts_are_rejected(self):
        """Reject missing nodes, cycles, file-owner conflicts and unsupported acceptances."""
        missing = node("a", dependencies=("missing",))
        cycle_a, cycle_b = node("a", dependencies=("b",)), node("b", dependencies=("a",))
        owner_a, owner_b = node("a", owner="first"), node("b", owner="second")
        owner_a["file_owners"]["shared"] = "first"
        owner_b["file_owners"]["shared"] = "second"
        receipt = node("a")
        receipt["acceptance"] = {"agent_says_pass": True}
        for nodes in ([missing], [cycle_a, cycle_b], [owner_a, owner_b], [receipt]):
            with self.subTest(nodes=nodes), self.assertRaises(ValueError):
                continuity.evaluate(self.project, {"nodes": nodes})

    def test_slice_file_owner_mismatch_is_rejected_before_any_file_read(self):
        """A node cannot claim another owner's file, including an extra claim."""
        for output_claim in (True, False):
            candidate = node("a", owner="alice")
            if output_claim:
                candidate["file_owners"]["a.out"] = "bob"
            else:
                candidate["file_owners"]["other.txt"] = "bob"
            with self.subTest(output_claim=output_claim):
                with patch.object(continuity, "_read_hash") as read:
                    with self.assertRaisesRegex(ValueError, "slice owner differs from declared file owner"):
                        continuity.evaluate(self.project, {"nodes": [candidate]})
                    read.assert_not_called()

    def test_generated_input_must_have_an_ordered_producer(self):
        """Avoid scheduling a consumer alongside an unrelated producer of its input."""
        producer, consumer = node("a"), node("b", inputs=("a.out",))
        with self.assertRaisesRegex(ValueError, "not ordered by dependencies"):
            continuity.evaluate(self.project, {"nodes": [producer, consumer]})
        consumer["depends_on"] = ["a"]
        result = continuity.evaluate(self.project, {"nodes": [consumer, producer]})
        self.assertEqual(result["ready"], ["a"])
        consumer["depends_on"] = []
        producer["depends_on"] = ["b"]
        with self.assertRaisesRegex(ValueError, "not ordered by dependencies"):
            continuity.evaluate(self.project, {"nodes": [consumer, producer]})

    def test_project_root_dot_is_not_a_file_input_output_or_claim(self):
        """Reject the project root wherever a project-relative file is required."""
        input_slice = node("input", inputs=(".",))
        output_slice = node("output", output=".")
        claim_slice = node("claim")
        claim_slice["file_owners"]["."] = "custodian"
        for label, candidate in (("input", input_slice), ("output", output_slice),
                                 ("claim", claim_slice)):
            with self.subTest(field=label), self.assertRaises(ValueError):
                continuity._normal_plan({"nodes": [candidate]})

    def test_traversal_absolute_stream_and_hardlinks_are_rejected(self):
        """Reject out-of-scope paths, Windows streams and linked fixture files."""
        for bad in ("../outside", "/absolute", "C:/outside", "name:stream", "folder/../file"):
            invalid = node("a")
            invalid["inputs"] = [bad]
            with self.subTest(path=bad), self.assertRaises(ValueError):
                continuity.evaluate(self.project, {"nodes": [invalid]})
        self.write("original.txt", b"owned")
        (self.project / "linked.txt").hardlink_to(self.project / "original.txt")
        with self.assertRaisesRegex(ValueError, "single-linked"):
            continuity.evaluate(self.project, {"nodes": [node("a", inputs=("linked.txt",))]})

class ContinuityFileContractTests(unittest.TestCase):
    """Check declared files before any hash observations are attempted."""

    def test_declared_file_cannot_also_be_another_declared_files_directory(self):
        """Reject impossible input and claim layouts regardless of declaration order."""
        plans = (
            [node("produce", output="Folder/File"),
             node("consume", inputs=("folder/file/entry.txt",), dependencies=("produce",))],
            [node("produce", inputs=("folder/file",), output="folder/file/entry.txt")],
            [node("read", inputs=("folder/file", "folder/file-sibling", "folder/file/entry.txt"))],
        )
        for nodes in plans:
            with self.subTest(nodes=nodes), patch.object(continuity, "_read_hash") as observe:
                with self.assertRaisesRegex(ValueError, "declared files cannot contain one another"):
                    continuity.evaluate(ROOT, {"nodes": nodes})
                observe.assert_not_called()

    def test_exact_producer_output_is_a_valid_consumer_input(self):
        """Keep an exact file dependency valid without treating readiness as execution."""
        plan = {"nodes": [
            node("produce", output="Folder/File"),
            node("consume", inputs=("Folder/File",), dependencies=("produce",)),
        ]}
        with patch.object(continuity, "_read_hash", return_value=None):
            result = continuity.evaluate(ROOT, plan)
        self.assertEqual(result["ready"], ["produce"])
        self.assertFalse(result["execution_authorized"])
        self.assertFalse(result["product_acceptance_verified"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
