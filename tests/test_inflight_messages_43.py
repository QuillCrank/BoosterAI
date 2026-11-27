"""Requirement 43 text/link contracts; not a live host dispatch test."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InflightMessageTests(unittest.TestCase):
    def test_exact_owner_wording_and_link(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        heading = "## Requirement 43: new messages during active work"
        self.assertEqual(text.count(heading), 1)
        section = text.split(heading, 1)[1].split("\n## ", 1)[0].strip()
        expected = (
            "When a local task using BoosterAI receives new messages during active work, "
            "it assesses how they relate to the current work. It incorporates details "
            "that refine the current work, using a separate subagent to review them "
            "when appropriate. It delegates independent new work separately to suitable "
            "autonomous subagents. This lets the main task continue while the independent "
            "work progresses in parallel."
            "\n\n"
            "Respect dependencies and file ownership. "
            "Schedule potentially conflicting work in sequence."
        )
        self.assertEqual(section.split("\n\nApply ", 1)[0], expected)
        self.assertIn("(references/in-flight-messages.md)", section)
        self.assertTrue((ROOT / "references/in-flight-messages.md").is_file())

    def test_routing_boundaries_and_evidence_are_explicit(self):
        text = (ROOT / "references/in-flight-messages.md").read_text(encoding="utf-8")
        for required in (
            "do not dispatch the same request twice", "STOP or cancellation takes precedence",
            "read-only review", "never overlap conflicting writes", "verified prerequisites",
            "process it serially at a safe checkpoint", "received, dispatched, started, delivered and verified",
            "real-target acceptance tests", "mandatory approvals", "not an autonomous listener",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
