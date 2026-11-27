"""Check canonical branch-label uniqueness at the real flow-card CLI."""

import json
from pathlib import Path
import subprocess
import sys
import unittest


ENTRY = Path(__file__).resolve().parents[1] / "scripts" / "flow_cards_f930b1a6.py"


def draft_branches(second_label):
    """Send a two-branch document over stdin and return the exit code and JSON.

    The first branch uses Greek small iota with dialytika and tonos. The
    second label varies by test; no source or fixture file is written.
    """
    document = ("```mermaid\nflowchart TD\n"
                "  B{Choose?} -->|\u0390| C[Revise]\n"
                f"  B -->|{second_label}| D[Continue]\n"
                "```\n")
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(ENTRY), "drafts"],
        input=document.encode("utf-8"), capture_output=True, timeout=15,
    )
    if result.stderr:
        raise AssertionError(result.stderr.decode("utf-8", "replace"))
    return result.returncode, json.loads(result.stdout)


class BranchLabelCliTests(unittest.TestCase):
    """Unicode spellings must not create conflicting decision branches."""

    def test_equivalent_labels_cannot_point_to_different_targets(self):
        """Capital iota with dialytika and acute matches the first label."""
        self.assertEqual(draft_branches("\u03aa\u0301"),
                         (2, {"valid": False, "error": "duplicate decision branch"}))

    def test_distinct_labels_keep_their_original_spelling(self):
        """Without the acute accent, the second label remains a distinct choice."""
        code, bundle = draft_branches("\u03aa")
        self.assertEqual(code, 0)
        decision = next(card for card in bundle["cards"] if card["id"] == "B")
        self.assertEqual({edge["to"]: edge["label"] for edge in decision["outgoing"]},
                         {"C": "\u0390", "D": "\u03aa"})
        self.assertIs(decision["execution_allowed"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
