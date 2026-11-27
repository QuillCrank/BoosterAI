"""Exercise Unicode label boundaries through the real flow-card CLI.

The actual operating document is read without modification. Negative and
multilingual variants exist only in memory; no workflow or fixture is executed.
"""

import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "scripts" / "flow_cards_f930b1a6.py"
SOURCE = ROOT / "references" / "operating-algorithm.md"
ACTION = "Read request and current state"
BRANCH = "B -->|yes| C[Revise affected graph"


def run_cli(mode, payload):
    """Send in-memory UTF-8 input to the actual CLI and return its JSON result.

    ``mode`` selects a supported operation and ``payload`` is its UTF-8 bytes.
    No shell, output file or temporary fixture is created by this helper.
    """
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(ENTRY), mode],
        input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, timeout=15,
    )
    if result.stderr:
        raise AssertionError("unexpected CLI stderr: " + result.stderr.decode("utf-8", "replace"))
    return result.returncode, json.loads(result.stdout.decode("utf-8"))


class UnicodeControlCliTests(unittest.TestCase):
    """Check both node and branch labels without executing source actions."""

    @classmethod
    def setUpClass(cls):
        """Read the selected source exactly once and verify its mutation anchors."""
        cls.document = SOURCE.read_bytes().decode("utf-8")
        if cls.document.count(ACTION) != 1 or cls.document.count(BRANCH) != 1:
            raise AssertionError("selected operating graph changed")

    def test_control_characters_are_rejected_in_nodes_and_branches(self):
        """C0, DEL and C1 input must fail, even if parsing rejects it earlier."""
        for control in ("\x00", "\x1f", "\x7f", "\x80", "\x85", "\x9f"):
            variants = (
                self.document.replace(ACTION, "Read" + control + " request", 1),
                self.document.replace(BRANCH, "B -->|ye" + control + "s| C[Revise affected graph", 1),
            )
            for document in variants:
                with self.subTest(codepoint=ord(control), branch=document != variants[0]):
                    status, result = run_cli("drafts", document.encode("utf-8"))
                    self.assertEqual(status, 2)
                    self.assertIs(result.get("valid"), False)

    def test_actual_source_and_multilingual_variants_round_trip(self):
        """Printable text, combining marks, ZWNJ and ZWJ survive the CLI audit."""
        action = ("G\u00f6zlem \u6e2c\u5b9a \u0627\u0644\u0639\u0631\u0628\u064a\u0629 "
                  "\u0641\u0627\u0631\u0633\u06cc\u200c\u0632\u0628\u0627\u0646 "
                  "\u0395\u03bb\u03bb\u03b7\u03bd\u03b9\u03ba\u03ac cafe\u0301 "
                  "\U0001f469\u200d\U0001f4bb")
        branch = "\u0628\u0644\u0647\u200c\u062a\u0627\u06cc\u06cc\u062f"
        variants = (
            (self.document, ACTION, "yes"),
            (self.document.replace(ACTION, action, 1).replace(
                BRANCH, "B -->|" + branch + "| C[Revise affected graph", 1), action, branch),
        )
        for document, expected_action, expected_branch in variants:
            with self.subTest(multilingual=expected_action == action):
                status, bundle = run_cli("drafts", document.encode("utf-8"))
                self.assertEqual(status, 0)
                node_a = next(card for card in bundle["cards"] if card["id"] == "A")
                node_b = next(card for card in bundle["cards"] if card["id"] == "B")
                self.assertEqual(node_a["label"], expected_action)
                self.assertIn(expected_branch, [edge["label"] for edge in node_b["outgoing"]])
                self.assertTrue(all(card["execution_allowed"] is False for card in bundle["cards"]))
                request = json.dumps({"document": document, "bundle": bundle},
                                     ensure_ascii=True).encode("utf-8")
                status, report = run_cli("audit", request)
                self.assertEqual(status, 0)
                self.assertIs(report["valid"], True)
                for flag in ("execution_allowed", "branch_execution_verified", "acceptance_test_executed"):
                    self.assertIs(report[flag], False)

    def test_whitespace_and_nfc_duplicate_branches_still_fail(self):
        """The control-character fix must not relax existing label rules."""
        whitespace = self.document.replace(ACTION, " " + ACTION, 1)
        collision = ("```mermaid\nflowchart TD\n"
                     "Q{Choose?} -->|\u00e9| A[One]\n"
                     "Q -->|e\u0301| B[Two]\n```\n")
        for document in (whitespace, collision):
            status, result = run_cli("drafts", document.encode("utf-8"))
            self.assertEqual(status, 2)
            self.assertIs(result.get("valid"), False)


if __name__ == "__main__":
    unittest.main(verbosity=2, failfast=True)
