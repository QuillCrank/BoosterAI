# BoosterAI

A portable instruction skill for incremental, evidence-driven software delivery. It organizes work without pretending that more agents, a green build, or a status file constitute working software.

The practices BoosterAI brings together are core building blocks of
**Applied AI Engineering** and **Agentic Systems Architecture**. These
disciplines focus on turning trained large AI models into reliable systems
that work in the real world.

## Architecture and verification

BoosterAI brings a skill and an engineering harness together in one portable
package. The architecture moves procedures into on-demand references and keeps
existing script interfaces stable as the canonical skill becomes a shorter
router. [Canonical skill](SKILL.md)

- The host coordinates permitted work, checks the current owner of each file
  and dispatches independent agents within disjoint scopes. The Harness keeps
  one serial critical-path run, its verified dependencies, budgets, STOP and
  real acceptance. [Single-executor harness](references/optional-harness.md)
- The host workflow records manual delivery, recipient processing and target
  effects separately. Native hook acceptance requires supported event access,
  effective trust and a real test bound to the same eligible run. Source
  self-check, source CI, installed-byte checks and native execution each
  supply their own evidence. [Host integration and evidence](references/host-adapter.md)
- Independent acceptance uses the exact revision and actual product entry.
  The owner investigates failed or unverified criteria and a fresh reviewer
  checks the corrected scope. Scheduling and a general script system are
  future extension points. They use the same authority, ownership and
  acceptance path.

## Use
Read `SKILL.md` in your coding agent and explicitly ask it to apply BoosterAI to your authorized project. Product coding uses the Markdown skill and bundled Python standard-library harness together. Agent-specific installation and skill discovery differ; placing this repository on disk does **not** prove that an agent discovers or follows it. No hooks, global configuration, schedulers or other projects are changed by this package.

For Codex sessions started in this checkout, the repository-local `.agents/skills/booster-ai/SKILL.md` registers `booster-ai` and points to the single canonical root `SKILL.md`. Open or refresh a Codex session in this repository, then invoke `$booster-ai`. This registration is scoped to the checkout; it does not install a user-wide skill. The bundled self-check reports the Python version and hashes of the local canonical source and registration target; matching hashes verify their local relationship, not independent authenticity. Compare a trusted remote revision separately. Actual Codex discovery and a task run must also be verified separately.

The core loop is: current requirement -> smallest executable slice -> implementation -> real target observation -> evidence -> next slice. Revise only affected algorithm nodes when requirements change. Keep one critical-path executor; use helpers only for genuinely independent work. Optional cooperative claims and file-observation planning are described in the operating reference; neither replaces real target acceptance or controls processes that do not participate.

Prefer a suitable, authorized Codex CLI workflow without interrupting an existing user-selected session. Follow the research sequence in [SKILL.md](SKILL.md) for local and authorized GitHub examples, then use separate available subagents for Stack Overflow, GitHub and forum research. Continue independent authorized work when a source is unavailable. Preserve actual refusals across interfaces.

## Bundled tools
The combined workflow needs a capable host agent and defaults to stable Python 3.12+; bundled helper compatibility retains Python 3.10+ and the standard library only. No other skill, pip package, external service, global installation, API key or network access is required. The product under development may have its own runtime/build/device prerequisites; BoosterAI does not bundle those environments.

```text
python scripts/booster.py self-check
python scripts/booster.py workspace-init --root PROJECT --records-existing docs/status.md
python scripts/booster.py schema-status --root PROJECT
python scripts/project_tools.py refresh-project-index --root PROJECT
python scripts/project_tools.py search-project-index --root PROJECT --query directory
python scripts/booster.py validate path/to/plan.json
python scripts/booster.py gc-plan --root PROJECT/_boosterAI --manifest scratch-manifest.json --owner RUN_OWNER
python scripts/booster.py gc-apply --root PROJECT/_boosterAI --manifest scratch-manifest.json --owner RUN_OWNER
python -I -S -B scripts/find_directory.py --name SampleFolder --root .
```

See `references/operating-algorithm.md` for the plan contract and `references/owned-cleanup.md` for the owned-scratch manifest. Validation checks metadata, not the truth of evidence. The collector quarantines manifest-owned expired inactive files, never purges. Stop writers first: this is not a distributed locking service. Tests use unique directories under this repository's ignored `_temp-files/`, redirect Python and child-process temporary files there, and register cleanup before setup can fail. Successful and failed test runs remove their own fixtures.

For multiple local participants, `scripts/claim_guard_8d31f72a.py` provides process-lifetime cooperative slice and file claims. `scripts/continuity_plan_64e9b1c2.py` checks actual file fingerprints and dependencies so unchanged observations can be reused. Its observation readiness is not product acceptance or execution authorization, and each claimed writable file must name its slice owner. The host still establishes actual ownership, reconciles a crashed worker's effects and runs real acceptance tests. See the operating reference for their limits, compatibility change and API contracts.

For task decomposition, `scripts/task_cards_a61e2c97.py` generates cards from an explicitly selected structured source graph and validates a proposed bundle against every source node and slice. It checks graph ordering and declared file conflicts before returning a valid result. Use `generate` with source JSON on stdin, then `validate` with both the current source and bundle on stdin; the exact contract is in `references/operating-algorithm.md`. This is a planning check, not an executor, a test result or proof that the selected source included an omitted requirement.

The [single-executor harness](references/optional-harness.md) connects a selected task card to one explicitly named, trusted Python acceptance test. The host still develops the code and approves the actual test; a real, non-skipped passing case and unchanged declared code and verifier bytes are required for `VERIFIED`. A single local state file records attempts and interruption recovery. The harness does not launch Codex or sandbox project tests, control outsiders, authenticate arbitrary evidence, or measure model cost. Run the combined harness `preflight` before registering product work. Legacy direct APIs remain compatible, but do not establish that the host used preflight or an automatic hook.

For the documented narrow Mermaid profile, `scripts/flow_schema_42d8e671.py` extracts the actual source nodes, control transitions, decisions and cyclic components. `scripts/flow_cards_f930b1a6.py` derives unresolved node candidates, checks source coverage, previews explicitly supplied routes and can bind explicit slice metadata to one action. Branches, loops, missing details and actual execution remain separate; the profile and unsupported source formats are described in the operating reference.

`find_directory.py` searches for an exact folder name without case sensitivity and does not follow symbolic links or junctions. Specify each authorized `--root` explicitly; on Windows, `--all-local-drives` selects local drives with assigned letters and requires authorization for that scope. Network drives, unrecognized drive types and mount points behind links are not searched. The JSON result includes matches, access errors, skipped links and a `complete` flag. Exit status is 0 for a complete search with matches, 1 for a complete search with no matches, 2 for an incomplete or invalid search, and 130 if interrupted. A skipped link or inaccessible directory makes the result incomplete: absence cannot then be established. This is not a sandbox against concurrent changes to filesystem links.

## Guarantees and boundaries
Routine authorized engineering decisions do not need repeated approval. This skill cannot waive host permissions, mandatory confirmations, actual service denials or a user's stop. Missing prerequisites must be distinguished from denials. A missing skill cannot report its own absence; the invoking host must detect that condition. Continuous execution requires an available agent/runtime and any separately authorized scheduling; no perpetual daemon is included.

Build checks, fixture tests, helper runtime tests, behavioral evaluations and real product acceptance are reported separately. This package's own tests do not prove improved speed or success in every project. Scratch cleanup is recoverable and bounded, not universal garbage collection. Daily logs use a separate exact-batch permanent-deletion path requiring fresh action-time confirmation. No private records or account data belong in this public repository.

## Self-contained operation
The bundled engineering procedures cover source inspection, debugging, implementation, review, verification, task division and dynamic planning. Source links document design history only; they are not fetched at runtime. Use the bundled self-check instead of any external skill validator. It checks this package's narrow frontmatter and local-resource contract, not general YAML or agent discovery. Python itself and a tool-capable agent remain explicit prerequisites, so this is not a standalone operating system or autonomous service.

## License
This repository and newly accepted archive contributions use only the [MIT License](LICENSE), SPDX identifier `MIT`. Contributions require the source owner's authority to offer the material under MIT. Preserve required copyright and attribution notices; do not relabel third-party material or historical snapshots to remove their existing obligations. Material that cannot be offered under MIT stays local and is not admitted to this archive.

## Development
Keep the skill concise. Add a regression scenario for each demonstrated failure. First run the helper tests, then evaluate the instructions on a fresh realistic task using an independent agent. Preserve failed/blocked results, and do not represent a simulated target as real product verification. Changes to the algorithm must invalidate affected stale acceptance evidence.

The GitHub workflow checks Python 3.10–3.15 on Linux and Windows. Python 3.10 uses older hosted images because it was absent from newer images. If Windows lacks 3.10, that CI job alone downloads the official Python.org embeddable runtime into owned temporary storage, verifies a pinned SHA-256 and removes it after testing; the skill itself remains independent of that optional CI network dependency. The hash checks the exact bytes but is not independent signature verification. Other missing runtimes fail explicitly. It runs `tests/ci_suite_c4e2b179.py`, an explicitly selected suite that excludes the previously refused local candidate modules, link-creation cases and a package-copy case. The workflow is not full unittest discovery, and a workflow definition alone is not evidence that its remote jobs passed.

## BoosterAI repository maintenance
Maintain BoosterAI within its own working directory, without depending on other local projects or working directories. The only GitHub repository used for this maintenance is [QuillCrank/BoosterAI](https://github.com/QuillCrank/BoosterAI). In every work turn, use the owner's messages to identify and implement the applicable scoped improvements to the skill and its repository. Do not invent unrelated changes or create empty commits when a message requires no product change.

Apply BoosterAI itself during maintenance and follow `AGENTS.md`. All maintenance work stays inside this checkout. Store every temporary file, cache, download and staging artifact in `_temp-files/`; configure process-local temp paths before execution. Check that directory before and after each turn, clean known disposable artifacts on success and failure, and reconcile interrupted work before creating more scratch. Preserve required evidence in `_boosterAI/` and summarize validation here in `VALIDATION.md`. Unknown or active files are preserved and reported, never silently deleted. No global configuration, external project or background cleaner is involved.

After every BoosterAI code or behavior-documentation change, rerun the relevant tests, review the public diff for private data, and update the public BoosterAI GitHub repository on its main branch. Verify the remote commit before reporting it published. If publishing is blocked, report the blocker and keep the validated local changes; never claim synchronization without verification. This instruction applies to maintaining BoosterAI itself, not to projects that use the skill.

Review the exact publishable files and all history reachable from the proposed refs for private project details, not just the latest snapshot. The optional offline scanner helps check supplied private terms and known commit identifiers; it cannot prove complete privacy or inspect a Git graph by itself. See `references/publication-privacy.md` before publishing.

## Functional partials
For a coding request, BoosterAI creates or reuses the project's `_boosterAI/_partials/` directory and README index. It also maintains a separate project-local searchable index, including any existing root `_partials/` and project experiences, without replacing the shared archive index. It decomposes the algorithm into independently invocable functions, gives new pieces short descriptive names with unique suffixes and detailed function documentation, and requires real-target evidence before advancing. The consuming project owns any needed assembly of archived pieces and later refactoring. Existing files, legacy partials, language conventions and ownership are preserved. See references/functional-partials.md for the index contract, language precedence and optional authorized per-step GitHub backups.

The project index discovers the standard `_boosterAI/_partials/`, any legacy root `_partials/`, `_boosterAI/experiences/` and helper metadata. Other existing experience files under `_boosterAI/` can be added explicitly with `--experience`. Search matches recorded paths and reports that source freshness was not checked; refresh before relying on current fingerprints. The index contains local paths and hashes and is not automatically shared.

## Project workspace
Durable BoosterAI-owned work is kept in the using project's `_boosterAI/`, including `algorithm.md` and functional pieces in `_boosterAI/_partials/`. Existing root partials remain in place and discoverable. An explicit user-selected temporary directory inside that project overrides the default scratch location. Existing project records, including a root `PROJECT_RECORDS.md`, stay in place. When no record system exists, `--create-records` creates `_boosterAI/PROJECT_RECORDS.md`. If both standard record paths exist, select one with `--records-existing`. Use --schema for a non-overwriting, provenance-tracked copy of an existing project-local schema. Read references/project-workspace.md before initializing or reconciling existing work. schema-status applies only when copied-schema provenance exists.

## Logs and reusable helpers
The bundled project_tools.py records general UTC checkpoint statuses inside a project's `_boosterAI/`, excluding product details and archive transactions. At each invocation it plans exact owned log deletion beyond a strict rolling seven days. Actual permanent deletion requires fresh confirmation of that batch; without approval it explicitly leaves logs in place, with no quarantine fallback. It is not a daemon and cannot promise unattended permanent cleanup. It also registers immutable, versioned copies of project-specific tested helper code and checks code/evidence integrity before reuse. See references/logs-and-helpers.md for commands, privacy boundaries, concurrency preconditions and recovery limits.

## Successful generalized experience archive
Archive each first verified successful experience as generalized working code with real-test evidence; improve the matching record on recurrence. Failed or code-free experiences remain local. Nested categories, tags, concise uniquely suffixed filenames and detailed function documentation make code retrievable. A CodeMeta/SoftwareSourceCode-inspired JSON Schema profile and deterministic `index.json` describe current records; prior revisions remain available. The bundled validator supports only the profile's documented subset. No archive-operation logs are written. When the owner has authorized this repository and generalized-content scope, proceed without routine reapproval. Rights, privacy and mandatory safety boundaries still apply. The helper stages locally; the host publishes and verifies the remote result. Measure costs before claiming savings. Core runtime remains offline/stdlib. See references/solution-archive.md.

Related verified files, including implementations in different programming languages, can be associated many-to-many with a finite strength between 0 and 1. Local and synced archive searches show both directions. A relationship expresses reviewed relevance, not identical behavior or proof that either implementation works in another environment.

An optional community contribution requires the contributor's explicit permission, an authorized GitHub link and target, rights and privacy review, and any mandatory approval. The offline `scripts/contribution_preflight_5c294f8d.py` reports missing inputs but cannot authenticate permission or create a PR. A real evaluation PR must be verified remotely; no contribution or merge is implied without one. See references/community-contributions.md.
## Daily archive feedback
`python scripts/archive_sync.py sync --root BOOSTERAI_CHECKOUT` reads the pinned QuillCrank/BoosterAI main archive at a resolved immutable commit. It stores validated inert snapshots, never overwrites local solutions or executes downloaded code, and keeps rollback history. Search them with the same script's `search` command. Daily means best effort at the first available skill invocation, not an installed timer. A real missing index/offline error is reported and the prior snapshot retained. See `references/daily-archive-sync.md` for trust/approval, size limits, rollback and verification boundaries.
