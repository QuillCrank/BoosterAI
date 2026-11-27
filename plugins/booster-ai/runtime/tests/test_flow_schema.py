"""Test the narrow Mermaid parser and distinguish real source from fixtures."""

import hashlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "booster_flow_schema", ROOT / "scripts" / "flow_schema_42d8e671.py"
)
schema = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(schema)


def document(edges):
    """Wrap fixture edge lines in the supported Markdown/Mermaid profile.

    ``edges`` is a list of text lines. Return an in-memory Markdown string;
    nothing is written to a file or executed as Mermaid.
    """
    return "# Fixture\n\n```mermaid\nflowchart TD\n" + "\n".join(edges) + "\n```\n"


class RealOperatingSourceTests(unittest.TestCase):
    """Observe the actual checked-in operating-algorithm source separately."""

    def test_real_mermaid_inventory_preserves_every_node_and_edge(self):
        """Read the actual source bytes and verify its known graph structure."""
        raw = (ROOT / "references/operating-algorithm.md").read_bytes()
        text = raw.decode("utf-8")
        result = schema.parse_mermaid(text)
        self.assertEqual(result["format"], "boosterai-mermaid-td-v1")
        self.assertEqual(result["source_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual([row["id"] for row in result["nodes"]], list("ABCDEFGHIJKLMNO"))
        self.assertEqual({row["id"] for row in result["nodes"] if row["kind"] == "decision"},
                         {"B", "E", "J", "L"})
        expected = {
            ("A", "B", None), ("B", "C", "yes"), ("B", "D", "no"),
            ("C", "D", None), ("D", "E", None), ("E", "F", "yes"),
            ("E", "G", "no"), ("F", "H", None), ("H", "I", None),
            ("I", "J", None), ("J", "K", "yes"), ("K", "L", None),
            ("L", "D", "no"), ("L", "M", "yes"), ("J", "N", "failed"),
            ("N", "H", None), ("J", "O", "ambiguous"), ("O", "G", None),
        }
        self.assertEqual({(edge["from"], edge["to"], edge["label"]) for edge in result["edges"]},
                         expected)
        self.assertEqual(len(result["edges"]), 18)
        self.assertEqual(sum(edge["label"] is not None for edge in result["edges"]), 9)
        self.assertEqual(result["entry_nodes"], ["A"])
        self.assertEqual(result["terminal_nodes"], ["G", "M"])
        self.assertEqual(result["cyclic_components"], [["D", "E", "F", "H", "I", "J", "K", "L", "N"]])
        self.assertNotIn("ready", result)
        self.assertNotIn("verified", result)


class MermaidFixtureTests(unittest.TestCase):
    """Use in-memory synthetic graphs to check acceptance and rejection cases."""

    def test_branching_fixture_preserves_labels_and_deterministic_order(self):
        """A decision's alternatives are edges, not simultaneous dependencies."""
        text = document(["S[Start] --> Q{Proceed?}", "Q -->|yes| A[Act]",
                         "Q -->|no| W[Wait]"])
        result = schema.parse_mermaid(text)
        self.assertEqual(result, schema.parse_mermaid(text))
        self.assertEqual([node["id"] for node in result["nodes"]], ["A", "Q", "S", "W"])
        self.assertEqual(result["entry_nodes"], ["S"])
        self.assertEqual(result["terminal_nodes"], ["A", "W"])
        self.assertEqual(result["cyclic_components"], [])
        self.assertEqual({(edge["from"], edge["to"], edge["label"]) for edge in result["edges"]},
                         {("S", "Q", None), ("Q", "A", "yes"), ("Q", "W", "no")})

    def test_loops_stay_in_the_control_graph(self):
        """A back edge and its cyclic component are preserved, never unrolled."""
        result = schema.parse_mermaid(document([
            "S[Start] --> A[Try]", "A --> Q{Done?}",
            "Q -->|again| A", "Q -->|done| Z[Finish]",
        ]))
        self.assertEqual(result["cyclic_components"], [["A", "Q"]])
        self.assertIn({"from": "Q", "to": "A", "label": "again"}, result["edges"])

    def test_action_self_loop_is_recorded_not_claimed_successful(self):
        """A self-loop is cyclic even though it has one valid control edge."""
        result = schema.parse_mermaid(document(["A[Repeat] --> A"]))
        self.assertEqual(result["cyclic_components"], [["A"]])
        self.assertEqual(result["entry_nodes"], [])
        self.assertEqual(result["terminal_nodes"], [])

    def test_raw_source_and_diagram_hashes_are_separate(self):
        """Non-diagram changes affect the source digest but not diagram bytes."""
        source = document(["A[Read] --> B[Done]"])
        altered = source.replace("# Fixture", "# Changed fixture")
        first, second = schema.parse_mermaid(source), schema.parse_mermaid(altered)
        diagram = "flowchart TD\nA[Read] --> B[Done]\n"
        self.assertEqual(first["diagram_sha256"], hashlib.sha256(diagram.encode()).hexdigest())
        self.assertEqual(first["diagram_sha256"], second["diagram_sha256"])
        self.assertNotEqual(first["source_sha256"], second["source_sha256"])
        crlf = source.replace("\n", "\r\n")
        self.assertEqual(schema.parse_mermaid(crlf)["source_sha256"], hashlib.sha256(crlf.encode()).hexdigest())
        self.assertNotEqual(schema.parse_mermaid(crlf)["diagram_sha256"], first["diagram_sha256"])

    def test_undefined_or_redefined_nodes_are_rejected(self):
        """Every referenced ID has one explicit, noncontradictory definition."""
        examples = [
            ["A[Start] --> B"],
            ["A[Start] --> B[First]", "B[Second] --> C[End]"],
            ["A[Start] --> B[First]", "B[First] --> C[End]"],
        ]
        for edges in examples:
            with self.subTest(edges=edges), self.assertRaises(ValueError):
                schema.parse_mermaid(document(edges))

    def test_decisions_need_distinct_labeled_branches(self):
        """Reject missing labels, duplicate labels and incomplete decisions."""
        examples = [
            ["Q{Ready?} --> A[Act]", "Q -->|no| B[Wait]"],
            ["Q{Ready?} -->|yes| A[Act]", "Q -->|YES| B[Wait]"],
            ["Q{Ready?} -->|yes| A[Act]"],
        ]
        for edges in examples:
            with self.subTest(edges=edges), self.assertRaises(ValueError):
                schema.parse_mermaid(document(edges))

    def test_action_edges_cannot_branch_or_have_labels(self):
        """An action has at most one unlabeled outgoing control edge."""
        examples = [
            ["A[Start] -->|yes| B[End]"],
            ["A[Start] --> B[One]", "A --> C[Two]"],
            ["A[Start] --> B[One]", "A --> B"],
        ]
        for edges in examples:
            with self.subTest(edges=edges), self.assertRaises(ValueError):
                schema.parse_mermaid(document(edges))

    def test_unknown_mermaid_syntax_is_never_silently_dropped(self):
        """Reject unrecognized graph forms, directives and malformed edges."""
        examples = [
            "```mermaid\ngraph TD\nA[Start] --> B[End]\n```\n",
            "```mermaid\nflowchart LR\nA[Start] --> B[End]\n```\n",
            document(["A[Start] --> B[End]", "C[Not connected]"]),
            document(["A[Start] --> B[End]", "%% hidden directive"]),
            document(["A[Start] -.-> B[End]"]),
            document(["A[Start] --> B[End] --> C[Next]"]),
            document(["A((Unsupported)) --> B[End]"]),
        ]
        for source in examples:
            with self.subTest(source=source), self.assertRaises(ValueError):
                schema.parse_mermaid(source)

    def test_one_complete_exact_mermaid_fence_is_required(self):
        """Reject missing, duplicate, unsupported and unterminated blocks."""
        good = document(["A[Start] --> B[End]"])
        examples = ["# No graph\n", good + good,
                    good.replace("```mermaid", "```MERMAID"),
                    good.replace("```mermaid", "```mermaid title"),
                    good.replace("```mermaid", "~~~mermaid"),
                    good.rsplit("```", 1)[0]]
        for source in examples:
            with self.subTest(source=source[:30]), self.assertRaises(ValueError):
                schema.parse_mermaid(source)

    def test_markdown_fence_starts_only_after_physical_line_endings(self):
        """A separator within prose cannot start a Mermaid block."""
        source = document(["A[Start] --> B[End]"])
        opening = "```mermaid"
        diagram = "flowchart TD\nA[Start] --> B[End]\n"
        prefaced = source.replace(opening, "Plain text\n" + opening, 1)
        for ending in ("\n", "\r\n", "\r"):
            with self.subTest(ending=ending):
                text = prefaced.replace("\n", ending)
                result = schema.parse_mermaid(text)
                expected_diagram = diagram.replace("\n", ending).encode("utf-8")
                self.assertEqual(result["diagram_sha256"], hashlib.sha256(expected_diagram).hexdigest())
        for separator in ("\u2028", "\u0085"):
            malformed = source.replace(opening, "Plain text" + separator + opening, 1)
            with self.subTest(separator=separator), self.assertRaises(ValueError):
                schema.parse_mermaid(malformed)

    def test_mermaid_statements_require_physical_line_endings(self):
        """Reject nonphysical separators in Mermaid headers, edges and labels."""
        edges = ["A[Start] --> B[Step]", "B --> C[End]"]
        source = document(edges)
        limit = max(map(len, edges))
        for ending in ("\n", "\r\n", "\r"):
            with self.subTest(ending=ending), patch.object(schema, "_MAX_LINE_CHARS", limit):
                self.assertEqual(len(schema.parse_mermaid(source.replace("\n", ending))["edges"]), 2)
        for separator in ("\u2028", "\u0085"):
            cases = (
                ("edges", source.replace("\n".join(edges), separator.join(edges), 1)),
                ("header", source.replace("flowchart TD\n", "flowchart TD" + separator, 1)),
                ("label", source.replace("[Start]", "[Start" + separator + " here]", 1)),
            )
            for location, malformed in cases:
                with self.subTest(location=location, separator=separator), self.assertRaises(ValueError):
                    schema.parse_mermaid(malformed)

    def test_mermaid_line_length_covers_header_blank_and_edge(self):
        """Check the physical character limit before trimming or skipping."""
        original = document(["", "A[Ba\u015fla] --> B[End]"]).split("\n")
        header = original.index("flowchart TD")
        expected_edges = [{"from": "A", "to": "B", "label": None}]
        for location, index in (("header", header), ("blank", header + 1), ("edge", header + 2)):
            at_limit = original.copy()
            at_limit[index] = at_limit[index].ljust(2048)
            over_limit = at_limit.copy()
            over_limit[index] += " "
            for ending in ("\n", "\r\n", "\r"):
                with self.subTest(location=location, ending=ending):
                    result = schema.parse_mermaid(ending.join(at_limit))
                    self.assertEqual(result["edges"], expected_edges)
                    with self.assertRaisesRegex(ValueError, "^diagram line length limit exceeded$"):
                        schema.parse_mermaid(ending.join(over_limit))

    def test_mermaid_line_limit_preserves_error_order(self):
        """Report an earlier invalid statement before a later oversized blank."""
        oversized_blank = " " * 2049
        invalid_edge = "unrecognized edge"
        with self.assertRaisesRegex(ValueError, "^unsupported Mermaid edge syntax$"):
            schema.parse_mermaid(document([invalid_edge, oversized_blank]))
        with self.assertRaisesRegex(ValueError, "^diagram line length limit exceeded$"):
            schema.parse_mermaid(document([oversized_blank, invalid_edge]))

    def test_mermaid_lookalike_inside_another_fence_is_not_a_graph(self):
        """A nested code example cannot impersonate a top-level source graph."""
        decoy = ("````python\n```mermaid\nflowchart TD\n"
                 "X[Decoy] --> Y[Decoy end]\n```\n````\n")
        actual = document(["A[Actual] --> B[End]"])
        result = schema.parse_mermaid(decoy + actual)
        self.assertEqual([node["id"] for node in result["nodes"]], ["A", "B"])
        with self.assertRaises(ValueError):
            schema.parse_mermaid(decoy)

    def test_container_mermaid_is_rejected_even_beside_a_real_graph(self):
        """A quoted or listed additional graph cannot disappear from coverage."""
        actual = document(["A[Actual] --> B[End]"])
        openings = ("> ```mermaid", "- ```mermaid", "1. ```mermaid",
                    "> - ```mermaid", "> > ```mermaid")
        for opening in openings:
            with self.subTest(opening=opening), self.assertRaisesRegex(ValueError, "container"):
                schema.parse_mermaid(actual + opening + "\n")
        inside_python = "````python\n> ```mermaid\n- ```mermaid\n````\n"
        result = schema.parse_mermaid(inside_python + actual)
        self.assertEqual([node["id"] for node in result["nodes"]], ["A", "B"])

    def test_unterminated_unrelated_fence_invalidates_the_document(self):
        """An ambiguous open code fence cannot be silently ignored."""
        actual = document(["A[Actual] --> B[End]"])
        for source in ("```python\nprint('open')\n" + actual,
                       actual + "```python\nprint('open')\n"):
            with self.subTest(source=source[:15]), self.assertRaises(ValueError):
                schema.parse_mermaid(source)

    def test_html_comment_context_is_rejected_rather_than_misread(self):
        """A graph-shaped HTML comment is outside this narrow Markdown profile."""
        actual = document(["A[Actual] --> B[End]"])
        for source in ("<!--\n" + actual + "-->\n", "<!-- comment -->\n" + actual):
            with self.subTest(source=source[:20]), self.assertRaisesRegex(ValueError, "comment"):
                schema.parse_mermaid(source)

    def test_raw_html_blocks_in_top_level_or_containers_are_rejected(self):
        """Raw HTML must not make a Mermaid example look like a real graph."""
        actual = document(["A[Actual] --> B[End]"])
        for opening in ("<pre>", "<script type='text/plain'>", "<div>",
                        "> <pre>", "- <div>", "> - <script>"):
            with self.subTest(opening=opening), self.assertRaisesRegex(ValueError, "HTML"):
                schema.parse_mermaid(opening + "\n" + actual + "</pre>\n")
        inside_python = "````python\n<pre>\n<div>\n````\n"
        result = schema.parse_mermaid(inside_python + actual)
        self.assertEqual([node["id"] for node in result["nodes"]], ["A", "B"])

    def test_two_top_level_graphs_and_ambiguous_nesting_fail(self):
        """Reject duplicate graphs or a nested fence inside Mermaid source."""
        actual = document(["A[Actual] --> B[End]"])
        nested = document(["A[Actual] --> B[End]", "```python", "print(1)"])
        for source in (actual + actual, nested):
            with self.subTest(source=source[:25]), self.assertRaises(ValueError):
                schema.parse_mermaid(source)

    def test_label_whitespace_and_control_characters_are_rejected(self):
        """Labels cannot be normalized silently or contain control characters."""
        for bad in (" A", "A ", "A\tB"):
            with self.subTest(label=bad), self.assertRaises(ValueError):
                schema.parse_mermaid(document(["S[" + bad + "] --> E[End]"]))

    def test_quoted_or_escaped_node_and_branch_labels_are_unsupported(self):
        """Do not treat Mermaid quoting/escape syntax as plain source labels."""
        examples = (
            ["A[quoted \"label\"] --> B[End]"],
            ["A[escaped \\ label] --> B[End]"],
            ["Q{Proceed?} -->|\"yes\"| A[Act]", "Q -->|no| B[Wait]"],
            ["Q{Proceed?} -->|yes\\value| A[Act]", "Q -->|no| B[Wait]"],
        )
        for edges in examples:
            with self.subTest(edges=edges), self.assertRaises(ValueError):
                schema.parse_mermaid(document(edges))

    def test_node_label_entity_escapes_are_not_treated_as_plain_text(self):
        """Reject Mermaid numeric and named entity escapes in node labels."""
        for label in ("Issue #35;", "Quote #quot;"):
            with self.subTest(label=label), self.assertRaises(ValueError):
                schema.parse_mermaid(document(["A[" + label + "] --> B[End]"]))

    def test_branch_label_entity_escapes_are_not_treated_as_plain_text(self):
        """Reject Mermaid numeric and named entity escapes in decision branches."""
        for label in ("#35;", "#quot;"):
            edges = ["Q{Proceed?} -->|" + label + "| A[Act]", "Q -->|no| B[Wait]"]
            with self.subTest(label=label), self.assertRaises(ValueError):
                schema.parse_mermaid(document(edges))

    def test_document_graph_and_line_limits_fail_without_partial_results(self):
        """Reject oversized inputs rather than returning a truncated inventory."""
        source = document(["S[Start] --> Q{Proceed?}", "Q -->|yes| A[Act]",
                           "Q -->|no| W[Wait]"])
        for name, limit in (("_MAX_DOCUMENT_CHARS", len(source) - 1),
                            ("_MAX_NODES", 3), ("_MAX_EDGES", 2),
                            ("_MAX_LINE_CHARS", 8)):
            with self.subTest(limit=name), patch.object(schema, name, limit):
                with self.assertRaisesRegex(ValueError, "limit|oversized"):
                    schema.parse_mermaid(source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
