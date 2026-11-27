"""Check task-card coverage and scheduling contracts using owned test workspaces."""

import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import repo_temp


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "booster_task_cards", ROOT / "scripts" / "task_cards_a61e2c97.py"
)
cards = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cards)


def task_slice(identifier, *, owner=None, depends_on=(), reads=(), writes=None):
    """Return one source-owned slice with explicit contract and target-test fields.

    The default output and writable path are unique to ``identifier``. Callers
    may override read and write paths to exercise independent and conflicting
    work. The helper returns a new mapping and has no filesystem side effects.
    """
    owner = owner or identifier + "-owner"
    if writes is None:
        writes = {"work/" + identifier + ".py": owner}
    return {
        "id": identifier,
        "owner": owner,
        "contract": "Produce the result for " + identifier,
        "inputs": ["Authorized inputs for " + identifier],
        "outputs": ["Observable result for " + identifier],
        "read_files": list(reads),
        "file_owners": dict(writes),
        "depends_on": list(depends_on),
        "acceptance": "The observed result satisfies the contract for " + identifier,
        "real_test": {
            "target": "The authorized runtime for " + identifier,
            "action": "Invoke the target behavior for " + identifier,
            "expected": "Observe the expected result for " + identifier,
        },
    }


def source_graph():
    """Build a source graph with split, dependent and independent slices.

    The first node has two ordered slices, the second depends on the first and
    has two independent slices, and the final independent node owns other files.
    All source IDs and task IDs are explicit and globally unambiguous.
    """
    return {
        "revision": "r1",
        "nodes": [
            {"id": "root", "depends_on": [], "slices": [
                task_slice("root-first"),
                task_slice("root-second", depends_on=("root-first",)),
            ]},
            {"id": "dependent", "depends_on": ["root"], "slices": [
                task_slice("dep-first"), task_slice("dep-second"),
            ]},
            {"id": "independent", "depends_on": [], "slices": [
                task_slice("independent-only"),
            ]},
        ],
    }


class TaskCardTests(unittest.TestCase):
    """Test source coverage and graph safety, separate from actual target execution."""

    def setUp(self):
        """Register one isolated repository-owned workspace before each test."""
        self.project = repo_temp.workspace(self)

    def test_complete_coverage_and_expanded_dependencies(self):
        """Every source slice becomes a card with the node's required predecessors."""
        source = source_graph()
        original = copy.deepcopy(source)
        bundle = cards.generate_cards(source)
        result = cards.validate_cards(source, bundle)
        self.assertEqual(source, original)
        self.assertTrue(result["valid"])
        self.assertFalse(result["acceptance_test_executed"])
        self.assertEqual((result["source_node_count"], result["card_count"]), (3, 5))
        self.assertEqual(bundle["source_revision"], "r1")
        self.assertRegex(bundle["source_sha256"], r"^[0-9a-f]{64}$")
        indexed = {card["id"]: card for card in bundle["cards"]}
        self.assertEqual(set(indexed), {
            "root-first", "root-second", "dep-first", "dep-second", "independent-only"
        })
        self.assertEqual(indexed["root-first"]["source_node"], "root")
        self.assertEqual(indexed["dep-first"]["source_node"], "dependent")
        self.assertEqual(set(indexed["root-second"]["depends_on"]), {"root-first"})
        for identifier in ("dep-first", "dep-second"):
            self.assertEqual(set(indexed[identifier]["depends_on"]), {"root-first", "root-second"})
        self.assertEqual(indexed["independent-only"]["depends_on"], [])
        order = result["order"]
        self.assertEqual(set(order), set(indexed))
        self.assertLess(order.index("root-first"), order.index("root-second"))
        for identifier in ("dep-first", "dep-second"):
            self.assertLess(order.index("root-second"), order.index(identifier))
        self.assertEqual(source, original, "generation and validation must not mutate the source")

    def test_deterministic_generation_and_no_mutation(self):
        """Identical source values yield identical cards and retain both input objects."""
        source = source_graph()
        original = copy.deepcopy(source)
        first = cards.generate_cards(source)
        snapshot = copy.deepcopy(first)
        self.assertEqual(first, cards.generate_cards(source))
        cards.validate_cards(source, first)
        self.assertEqual(source, original)
        self.assertEqual(first, snapshot)

    def test_node_and_slice_order_do_not_change_the_bundle(self):
        """Permuting source nodes and their slices preserves the complete bundle.

        Source ordering is not a dependency declaration. The same node and slice
        contracts, explicitly stated edges, revision and paths must therefore
        yield identical cards and the same source digest without mutating either
        source object.
        """
        source = source_graph()
        permuted = copy.deepcopy(source)
        permuted["nodes"].reverse()
        for node in permuted["nodes"]:
            node["slices"].reverse()
        original_before = copy.deepcopy(source)
        permuted_before = copy.deepcopy(permuted)
        expected = cards.generate_cards(source)
        self.assertEqual(cards.generate_cards(permuted), expected)
        self.assertEqual(source, original_before)
        self.assertEqual(permuted, permuted_before)
        self.assertTrue(cards.validate_cards(permuted, expected)["valid"])

    def test_missing_extra_and_duplicate_cards_fail_closed(self):
        """Omitted, added or duplicated slices cannot pass source coverage."""
        source = source_graph()
        bundle = cards.generate_cards(source)
        missing = copy.deepcopy(bundle)
        missing["cards"].pop()
        duplicate = copy.deepcopy(bundle)
        duplicate["cards"].append(copy.deepcopy(duplicate["cards"][0]))
        extra = copy.deepcopy(bundle)
        extra_card = copy.deepcopy(extra["cards"][0])
        extra_card["id"] = "unknown-extra"
        extra["cards"].append(extra_card)
        for invalid in (missing, duplicate, extra):
            with self.subTest(count=len(invalid["cards"])), self.assertRaises(ValueError):
                cards.validate_cards(source, invalid)

    def test_card_fields_and_expanded_edges_cannot_disagree_with_source(self):
        """A modified card cannot claim coverage of an unchanged source contract."""
        source = source_graph()
        valid = cards.generate_cards(source)
        mutations = {
            "source_node": lambda row: row.update(source_node="wrong-node"),
            "owner": lambda row: row.update(owner="other-owner"),
            "contract": lambda row: row.update(contract="different contract"),
            "inputs": lambda row: row.update(inputs=["different input"]),
            "outputs": lambda row: row.update(outputs=["different output"]),
            "read_files": lambda row: row.update(read_files=["unexpected.txt"]),
            "file_owners": lambda row: row.update(file_owners={"other.py": "other-owner"}),
            "acceptance": lambda row: row.update(acceptance="different condition"),
            "real_test": lambda row: row["real_test"].update(expected="different observation"),
            "depends_on": lambda row: row.update(depends_on=[]),
        }
        for field, mutate in mutations.items():
            invalid = copy.deepcopy(valid)
            row = next(item for item in invalid["cards"] if item["id"] == "dep-first")
            mutate(row)
            with self.subTest(field=field), self.assertRaises(ValueError):
                cards.validate_cards(source, invalid)
        missing_field = copy.deepcopy(valid)
        del missing_field["cards"][0]["source_node"]
        with self.assertRaises(ValueError):
            cards.validate_cards(source, missing_field)
        extra_field = copy.deepcopy(valid)
        extra_field["cards"][0]["acceptance_test_executed"] = True
        with self.assertRaises(ValueError):
            cards.validate_cards(source, extra_field)

    def test_revision_and_source_content_changes_invalidate_bundle(self):
        """A bundle belongs to its exact source content and revision, not just its IDs."""
        source = source_graph()
        bundle = cards.generate_cards(source)
        changed_revision = copy.deepcopy(source)
        changed_revision["revision"] = "r2"
        changed_contract = copy.deepcopy(source)
        changed_contract["nodes"][0]["slices"][0]["contract"] = "Changed source contract"
        changed_graph = copy.deepcopy(source)
        changed_graph["nodes"][2]["depends_on"] = ["root"]
        for changed in (changed_revision, changed_contract, changed_graph):
            with self.subTest(change=changed), self.assertRaises(ValueError):
                cards.validate_cards(changed, bundle)
        wrong_hash = copy.deepcopy(bundle)
        wrong_hash["source_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            cards.validate_cards(source, wrong_hash)
        wrong_revision = copy.deepcopy(bundle)
        wrong_revision["source_revision"] = "other"
        with self.assertRaises(ValueError):
            cards.validate_cards(source, wrong_revision)

    def test_missing_required_details_and_empty_result_are_rejected(self):
        """Each slice must declare inputs, outputs, owners, acceptance and real test."""
        source = source_graph()
        for field in ("owner", "contract", "inputs", "outputs", "read_files", "file_owners",
                      "depends_on", "acceptance", "real_test"):
            invalid = copy.deepcopy(source)
            del invalid["nodes"][0]["slices"][0][field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                cards.generate_cards(invalid)
        for field in ("target", "action", "expected"):
            invalid = copy.deepcopy(source)
            invalid["nodes"][0]["slices"][0]["real_test"][field] = ""
            with self.subTest(empty_real_test=field), self.assertRaises(ValueError):
                cards.generate_cards(invalid)
            missing_test_field = copy.deepcopy(source)
            del missing_test_field["nodes"][0]["slices"][0]["real_test"][field]
            with self.subTest(missing_real_test=field), self.assertRaises(ValueError):
                cards.generate_cards(missing_test_field)
        for field, value in (("acceptance", ""), ("outputs", []), ("outputs", [""]),
                             ("inputs", "not-a-list"), ("file_owners", [])):
            invalid = copy.deepcopy(source)
            invalid["nodes"][0]["slices"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                cards.generate_cards(invalid)

    def test_explicit_no_inputs_and_no_file_writes_are_valid(self):
        """An explicitly input-free and file-write-free slice is still meaningful."""
        source = {"revision": "r1", "nodes": [
            {"id": "inspect", "depends_on": [], "slices": [
                task_slice("inspect-only", writes={})
            ]}
        ]}
        source["nodes"][0]["slices"][0]["inputs"] = []
        result = cards.validate_cards(source, cards.generate_cards(source))
        self.assertTrue(result["valid"])
        self.assertFalse(result["acceptance_test_executed"])

    def test_bad_source_graph_and_duplicate_slice_ids_are_rejected(self):
        """Reject missing nodes, cycles, invalid IDs and duplicate source identities."""
        missing_node = source_graph()
        missing_node["nodes"][1]["depends_on"] = ["absent"]
        node_cycle = source_graph()
        node_cycle["nodes"][0]["depends_on"] = ["dependent"]
        self_node = source_graph()
        self_node["nodes"][0]["depends_on"] = ["root"]
        duplicate_node = source_graph()
        duplicate_node["nodes"][2]["id"] = "root"
        duplicate_slice = source_graph()
        duplicate_slice["nodes"][2]["slices"][0]["id"] = "root-first"
        empty_node = source_graph()
        empty_node["nodes"][0]["slices"] = []
        missing_slice = source_graph()
        missing_slice["nodes"][0]["slices"][0]["depends_on"] = ["absent-slice"]
        slice_cycle = source_graph()
        slice_cycle["nodes"][0]["slices"][0]["depends_on"] = ["root-second"]
        for invalid in (missing_node, node_cycle, self_node, duplicate_node, duplicate_slice,
                        empty_node, missing_slice, slice_cycle):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                cards.generate_cards(invalid)

    def test_undeclared_cross_node_slice_dependency_is_rejected(self):
        """A slice cannot create a cross-node edge absent from the source graph."""
        source = source_graph()
        independent = next(node for node in source["nodes"] if node["id"] == "independent")
        independent["slices"][0]["depends_on"] = ["root-first"]
        with self.assertRaises(ValueError):
            cards.generate_cards(source)

    def test_unknown_source_fields_are_not_silently_ignored(self):
        """Reject undeclared source data instead of claiming full source coverage."""
        source = source_graph()
        for location in ("root", "node", "slice"):
            invalid = copy.deepcopy(source)
            target = {"root": invalid, "node": invalid["nodes"][0],
                      "slice": invalid["nodes"][0]["slices"][0]}[location]
            target["unmodeled_requirement"] = "Must remain visible to the owner"
            with self.subTest(location=location), self.assertRaises(ValueError):
                cards.generate_cards(invalid)

    def test_unordered_case_variant_writers_are_rejected(self):
        """Case variants of the same path cannot be written by independent cards."""
        source = {"revision": "r1", "nodes": [
            {"id": "first", "depends_on": [], "slices": [
                task_slice("one", owner="custodian", writes={"src/Shared.txt": "custodian"})
            ]},
            {"id": "second", "depends_on": [], "slices": [
                task_slice("two", owner="custodian", writes={"src/shared.txt": "custodian"})
            ]},
        ]}
        with self.assertRaises(ValueError):
            cards.generate_cards(source)

    def test_ordered_writers_through_transitive_dependency_are_allowed(self):
        """Transitive node order protects a shared owned path without false conflict."""
        source = {"revision": "r1", "nodes": [
            {"id": "first", "depends_on": [], "slices": [
                task_slice("one", owner="custodian", writes={"src/shared.txt": "custodian"})
            ]},
            {"id": "middle", "depends_on": ["first"], "slices": [task_slice("middle-card")]},
            {"id": "last", "depends_on": ["middle"], "slices": [
                task_slice("last-card", owner="custodian", writes={"src/SHARED.txt": "custodian"})
            ]},
        ]}
        result = cards.validate_cards(source, cards.generate_cards(source))
        order = result["order"]
        self.assertLess(order.index("one"), order.index("middle-card"))
        self.assertLess(order.index("middle-card"), order.index("last-card"))

    def test_unordered_reader_writer_conflict_but_ordered_is_valid(self):
        """A read/write pair requires an explicit order in either safe direction."""
        source = {"revision": "r1", "nodes": [
            {"id": "writer", "depends_on": [], "slices": [
                task_slice("write", owner="custodian", writes={"src/shared.txt": "custodian"})
            ]},
            {"id": "reader", "depends_on": [], "slices": [
                task_slice("read", reads=("src/SHARED.txt",))
            ]},
        ]}
        with self.assertRaises(ValueError):
            cards.generate_cards(source)
        for later, earlier in (("reader", "writer"), ("writer", "reader")):
            ordered = copy.deepcopy(source)
            next(node for node in ordered["nodes"] if node["id"] == later)["depends_on"] = [earlier]
            result = cards.validate_cards(ordered, cards.generate_cards(ordered))
            self.assertTrue(result["valid"])
            index = {node["id"]: node["slices"][0]["id"] for node in ordered["nodes"]}
            self.assertLess(result["order"].index(index[earlier]), result["order"].index(index[later]))

    def test_unordered_nested_reader_writer_paths_are_rejected(self):
        """A file and a claimed descendant cannot be handled as independent paths.

        Check both ancestor directions, case variants and canonically equivalent
        Unicode spellings without creating filesystem links or touching targets.
        A lexical path collision must not become an executable task plan.
        """
        cases = (
            ("src/item", "src/item/child"),
            ("src/item/child", "src/item"),
            ("SRC/Item", "src/item/child"),
            ("src/ITEM/child", "Src/item"),
            ("src/caf\u00e9", "src/cafe\u0301/child"),
            ("src/cafe\u0301/child", "src/caf\u00e9"),
        )
        for read_path, write_path in cases:
            source = {"revision": "r1", "nodes": [
                {"id": "reader", "depends_on": [], "slices": [
                    task_slice("read", reads=(read_path,), writes={})
                ]},
                {"id": "writer", "depends_on": [], "slices": [
                    task_slice("write", owner="custodian", writes={write_path: "custodian"})
                ]},
            ]}
            with self.subTest(read=read_path, write=write_path), self.assertRaises(ValueError):
                cards.generate_cards(source)

    def test_unordered_nested_writers_are_rejected(self):
        """Independent writers cannot claim a file and its normalized descendant."""
        for first_path, second_path in (
            ("src/a", "src/a/b"),
            ("src/A/b", "src/a"),
            ("src/caf\u00e9", "src/cafe\u0301/b"),
        ):
            source = {"revision": "r1", "nodes": [
                {"id": "first", "depends_on": [], "slices": [
                    task_slice("one", owner="custodian", writes={first_path: "custodian"})
                ]},
                {"id": "second", "depends_on": [], "slices": [
                    task_slice("two", owner="custodian", writes={second_path: "custodian"})
                ]},
            ]}
            with self.subTest(first=first_path, second=second_path), self.assertRaises(ValueError):
                cards.generate_cards(source)

    def test_unsafe_paths_are_rejected_without_accessing_them(self):
        """Card path declarations cannot escape the project or name Windows streams."""
        for unsafe in ("../outside", "/absolute", "C:/outside", "folder/../file", "name:stream"):
            for field in ("read_files", "file_owners"):
                source = source_graph()
                row = source["nodes"][0]["slices"][0]
                row[field] = [unsafe] if field == "read_files" else {unsafe: row["owner"]}
                with self.subTest(path=unsafe, field=field), self.assertRaises(ValueError):
                    cards.generate_cards(source)

    def test_project_root_is_not_a_file_declaration(self):
        """The project root cannot masquerade as an owned or read file path."""
        for field in ("read_files", "file_owners"):
            source = source_graph()
            row = source["nodes"][0]["slices"][0]
            row[field] = ["."] if field == "read_files" else {".": row["owner"]}
            with self.subTest(field=field), self.assertRaises(ValueError):
                cards.generate_cards(source)

    def test_slice_cannot_claim_a_different_file_owner(self):
        """A writer cannot silently claim a file assigned to another owner."""
        source = source_graph()
        row = source["nodes"][0]["slices"][0]
        path = next(iter(row["file_owners"]))
        row["file_owners"][path] = "different-owner"
        with self.assertRaises(ValueError):
            cards.generate_cards(source)

    def test_expanded_dependency_edge_budget_rejects_before_returning_cards(self):
        """A node dependency expanding beyond the supported edge cap is invalid.

        The source is entirely in memory and has distinct write paths. Its 182
        predecessor slices and 182 dependent slices imply 33,124 expanded edges,
        exceeding 32,768 without constructing or returning a card bundle.
        """
        source = {"revision": "r1", "nodes": [
            {"id": "earlier", "depends_on": [], "slices": [
                task_slice("prior-%03d" % index) for index in range(182)
            ]},
            {"id": "later", "depends_on": ["earlier"], "slices": [
                task_slice("next-%03d" % index) for index in range(182)
            ]},
        ]}
        original = copy.deepcopy(source)
        with self.assertRaisesRegex(ValueError, "expanded dependency edge limit"):
            cards.generate_cards(source)
        self.assertEqual(source, original)

    def test_total_declared_file_budget_rejects_before_returning_cards(self):
        """The combined read and write declarations cannot exceed 8,192 entries.

        This case supplies 8,191 distinct lexical reads across two slices plus
        two declared writes. Each list remains within the separate per-list cap,
        so rejection must specifically reflect the combined 8,193 references.
        It creates no paths, target files or external work.
        """
        source = {"revision": "r1", "nodes": [
            {"id": "one", "depends_on": [], "slices": [
                task_slice("first"), task_slice("second")
            ]}
        ]}
        source["nodes"][0]["slices"][0]["read_files"] = [
            "read/first-%05d.txt" % index for index in range(4096)
        ]
        source["nodes"][0]["slices"][1]["read_files"] = [
            "read/second-%05d.txt" % index for index in range(4095)
        ]
        original = copy.deepcopy(source)
        with self.assertRaisesRegex(ValueError, "file reference limit"):
            cards.generate_cards(source)
        self.assertEqual(source, original)

    def test_cli_rejects_duplicate_nested_json_keys(self):
        """The real card CLI must not silently overwrite duplicate JSON keys.

        Supply one repeated source-level nodes key and one repeated nested file
        owner path. Both payloads would otherwise resolve to a valid last value
        under Python's default JSON parser. No tasks or target tests are executed.
        """
        source = source_graph()
        encoded = json.dumps(source, separators=(",", ":"))
        nodes_key = '"nodes":['
        self.assertIn(nodes_key, encoded)
        duplicate_nodes = encoded.replace(nodes_key, '"nodes":[],"nodes":[', 1)
        row = source["nodes"][0]["slices"][0]
        path, owner = next(iter(row["file_owners"].items()))
        owner_mapping = json.dumps(row["file_owners"], separators=(",", ":"))
        original_field = '"file_owners":' + owner_mapping
        duplicate_mapping = (
            '{' + json.dumps(path) + ':' + json.dumps("different-owner") + ','
            + json.dumps(path) + ':' + json.dumps(owner) + '}'
        )
        self.assertIn(original_field, encoded)
        duplicate_owner = encoded.replace(original_field, '"file_owners":' + duplicate_mapping, 1)
        for name, payload in (("nodes", duplicate_nodes), ("file_owners", duplicate_owner)):
            with self.subTest(case=name):
                result = subprocess.run(
                    [sys.executable, "-I", "-S", "-B", str(ROOT / "scripts/task_cards_a61e2c97.py"), "generate"],
                    input=payload, text=True, cwd=ROOT, capture_output=True, timeout=15,
                )
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                outcome = json.loads(result.stdout)
                self.assertFalse(outcome["valid"])
                self.assertNotIn("cards", outcome)

    def test_package_self_check_requires_the_new_planner_files(self):
        """An absent card helper or its graph dependency fails the package check."""
        spec = importlib.util.spec_from_file_location("booster_package", ROOT / "scripts/booster.py")
        package = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(package)
        original = package.checked
        for missing in ("scripts/task_cards_a61e2c97.py", "scripts/continuity_plan_64e9b1c2.py"):
            def select(root, relative):
                """Simulate just one absent package path without changing real files."""
                return self.project / "absent" if relative == missing else original(root, relative)
            with self.subTest(missing=missing), patch.object(package, "checked", side_effect=select):
                with self.assertRaisesRegex(ValueError, "missing package file"):
                    package.self_check(ROOT)


class TaskCardWindowsPathTests(unittest.TestCase):
    """Exercise declared Windows paths using only in-memory task-card data."""

    def test_space_padded_device_stems_are_rejected(self):
        """Reject reserved filenames and directory components in both path fields."""
        for path in ("NUL .txt", "reports/cOm1  .json", "reports/lpt\u00b2 .metadata/result.txt"):
            for field in ("read_files", "file_owners"):
                source = source_graph()
                row = source["nodes"][0]["slices"][0]
                row[field] = [path] if field == "read_files" else {path: row["owner"]}
                with (
                    self.subTest(path=path, field=field),
                    self.assertRaisesRegex(ValueError, "^ambiguous path component$"),
                ):
                    cards.generate_cards(source)

    def test_ordinary_spaces_in_path_declarations_are_preserved(self):
        """Keep ordinary spaces and nonreserved stems in generated cards."""
        source = source_graph()
        row = source["nodes"][0]["slices"][0]
        read_path = "reports/NUL plan.json"
        write_path = "reports draft/COM10 .json"
        row["read_files"] = [read_path]
        row["file_owners"] = {write_path: row["owner"]}

        bundle = cards.generate_cards(source)
        card = next(card for card in bundle["cards"] if card["id"] == row["id"])
        self.assertEqual(card["read_files"], [read_path])
        self.assertEqual(card["file_owners"], {write_path: row["owner"]})
        self.assertTrue(cards.validate_cards(source, bundle)["valid"])


class TaskCardStdinTests(unittest.TestCase):
    def test_real_cli_reads_utf8_independent_of_locale(self):
        owner = "M\u00fcnchen"
        task = task_slice("review", owner=owner, writes={"work/caf\u00e9.py": owner})
        task["acceptance"] = "The result preserves \u00e7a\u011fla"
        source = {"revision": "r1", "nodes": [
            {"id": "review", "depends_on": [], "slices": [task]},
        ]}
        bundle = cards.generate_cards(source)
        command = [sys.executable, "-I", "-S", "-B", "-X", "utf8=0",
                   str(ROOT / "scripts/task_cards_a61e2c97.py")]
        for mode, request, expected in (
            ("generate", source, bundle),
            ("validate", {"source": source, "bundle": bundle}, cards.validate_cards(source, bundle)),
        ):
            with self.subTest(mode=mode):
                actual = subprocess.run(
                    [*command, mode], input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
                    cwd=ROOT, capture_output=True, timeout=15,
                )
                self.assertEqual(actual.returncode, 0, actual.stdout + actual.stderr)
                self.assertEqual(actual.stderr, b"")
                self.assertEqual(json.loads(actual.stdout), expected)

        invalid_input = json.dumps(source, ensure_ascii=False).encode("utf-8")
        invalid_input = invalid_input.replace(task["acceptance"].encode("utf-8"), b"\xff", 1)
        invalid = subprocess.run([*command, "generate"], input=invalid_input,
                                 cwd=ROOT, capture_output=True, timeout=15)
        self.assertEqual(invalid.returncode, 2, invalid.stdout + invalid.stderr)
        report = json.loads(invalid.stdout)
        self.assertFalse(report["valid"])
        self.assertIn("utf-8", report["error"])
        self.assertEqual(invalid.stderr, b"")

    def _call_with_character_limit(self, mode, data, limit):
        input_stream = SimpleNamespace(buffer=io.BytesIO(data))
        output = io.StringIO()
        with (patch.object(sys, "stdin", input_stream), patch.object(sys, "stdout", output),
              patch.object(cards, "_MAX_STDIN", limit)):
            result = cards.main([mode])
        self.assertFalse(input_stream.buffer.closed)
        return result, json.loads(output.getvalue())

    def test_input_limit_still_counts_decoded_characters(self):
        source = source_graph()
        source["revision"] = "caf\u00e9"
        bundle = cards.generate_cards(source)
        document = json.dumps({"source": source, "bundle": bundle}, ensure_ascii=False)
        data = document.encode("utf-8")
        self.assertGreater(len(data), len(document))
        self.assertEqual(self._call_with_character_limit("validate", data, len(document)),
                         (0, cards.validate_cards(source, bundle)))
        self.assertEqual(self._call_with_character_limit("validate", data + b" ", len(document)),
                         (2, {"valid": False, "error": "input too large"}))

    def test_generate_requires_a_fitting_compact_validation_request(self):
        source = source_graph()
        source["revision"] = "caf\u00e9\ud800"
        bundle = cards.generate_cards(source)
        document = json.dumps({"source": source, "bundle": bundle},
                              ensure_ascii=False, separators=(",", ":"))
        characters = len(document.encode("utf-8", "backslashreplace").decode("utf-8"))
        self.assertGreater(characters, len(document))
        data = json.dumps(source).encode("utf-8")
        self.assertLess(len(data), characters - 1)
        self.assertEqual(self._call_with_character_limit("generate", data, characters),
                         (0, bundle))
        self.assertEqual(self._call_with_character_limit("generate", data, characters - 1),
                         (2, {"valid": False, "error":
                              "source and bundle exceed compact validation request limit"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
