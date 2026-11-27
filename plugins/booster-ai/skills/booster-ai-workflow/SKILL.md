---
name: booster-ai-workflow
description: Apply the bundled BoosterAI skill and harness in the current authorized project, keeping plugin resources separate from project state.
---

# BoosterAI workflow

Use this skill in the current authorized project. The directory containing this
file is the skill directory. Resolve the plugin root two levels above that
directory, then resolve its runtime directory as plugin-root/runtime. Keep the
consuming project root separate from the runtime. Read the consuming project's
applicable instructions and respect its user authorization, STOP and denials.

Read ../../runtime/SKILL.md and resolve its references from the runtime
directory. Apply that canonical skill to the consuming project. A consumer
project does not need a BoosterAI checkout, a BoosterAI SKILL.md, or BoosterAI
scripts in its own root. Use the absolute path of each bundled script; the
process working directory and every project-specific --root refer to the
authorized consuming project.

Before using the packaged runtime, verify its reviewed manifest and the canonical
skill package. Substitute the actual plugin and runtime paths in these examples.

    py -3.12 -I -S -B "<PLUGIN_ROOT>/verify_runtime_a84e3f19.py" --expected-manifest-sha256 facf07d0cd0f9dafcad3bd9fb1b6732c925559aee66e4541a0a30a6e3eb96f31
    py -3.12 -I -S -B "<RUNTIME>/scripts/booster.py" self-check

The runtime manifest pin identifies this packaged version and records its
published source commit. When an authorized BoosterAI source checkout is
available, add --source-root "<BOOSTERAI_CHECKOUT>" to compare this plugin with
the reviewed plugin package in that checkout. This checks the runtime manifest
and files, plugin metadata, wrapper, verifier and license.

To compare the runtime's source-derived files with the checkout's current
working files, add --checkout-root "<BOOSTERAI_CHECKOUT>". Pending checkout
changes can make this comparison fail without making the reviewed package
invalid. Python, Markdown, JSON and JavaScript text is normalized to LF for
comparison. These checks do not establish which plugin copy the host loaded.
An INVALID complete checkout comparison lists all observed mismatches in sorted
order. A filesystem error stops the comparison, reports its error type without
an absolute path, and does not claim the checkout comparison completed. Do not
retry a refused operation or treat an incomplete comparison as a match.

A valid manifest and self-check show package consistency. They do not prove host
skill discovery, installation freshness, or product acceptance.

Runtime 0.2.4 adds the QuillCrank repository namespace and retains the
published Unicode control label and duplicate JSON field fixes. Complete the reviewed package integrity check and scanner
acceptance before using its scanner for publication review.

Use the bundled harness with an explicit project root and a complete source plan
inside that project. Declare the consumer's applicable instruction files in a
card's read_files when they affect acceptance. The harness fingerprints bundled
rules automatically and fingerprints consumer files declared by the card.
Configure TEMP, TMP, TMPDIR and BOOSTERAI_SCRATCH within the consumer's
authorized scratch location before starting tests. Keep project
records and generated data in the authorized project, and treat the installed
plugin and runtime as read-only resources. A preflight may observe existing
evidence as STALE when its recorded runtime or policy fingerprint differs.
Review affected evidence and ownership before state-changing operations; do not
replay an earlier failed or ambiguous run.

For archive feedback, use the consuming project as the archive-sync state root.
The bundled solutions archive can be searched read-only. Publish or modify a
shared archive only through a separately authorized writable archive checkout,
never the installed plugin cache.

Record actual plugin selection, skill loading and target execution separately.
A preflight READY result or a passing fixture does not establish product
acceptance. Preserve the other project's existing files and report missing
permissions, host evidence or installation refresh as applicable.
