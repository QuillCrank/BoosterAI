# Integrated single-executor harness

Product coding uses the Markdown skill and this harness together. The helper
retains Python 3.10+ compatibility; new work defaults to stable Python 3.12+. A
tool-capable host owns development, authorization, STOP, and meaningful
acceptance. The harness does not launch a Codex session, run plan prose as a
shell command, sandbox tests, or grant permissions. Project test code is trusted
and can have arbitrary side effects; inspect the exact test and authorize it in
the host before each execution. Never use this tool to repeat a denied action.

Load this reference when preparing an authorized run, interpreting run evidence,
or reviewing STOP and recovery. The host coordinates work with disjoint file
ownership. The harness keeps one serial executor for the shared project state.

## Combined startup check

Use preflight before an authorized new run with a complete source graph.

```text
python -I -S -B scripts/harness_5b7e9c31.py --root PROJECT --source PLAN.json preflight
```

This reads actual package resources, the current Python version, selected graph
and cooperative state. Acquiring the lock may create normal workspace/claim
guards; no task state is written and no acceptance test, agent or hook starts.
Active/uncertain work, project STOP, unknown task states or changed contracts
block a new run (exit 1); invalid input/runtime/package returns NOT_RUN (exit 2).
READY (exit 0) is a structural starting condition, not authorization, discovery,
product acceptance or proof of automatic continuation. Observed stale evidence
is reported without overwriting state; normal status/verify commands recheck it.
Use `--requested-python VERSION` only for an explicit user version request.
Existing start/verify APIs remain compatible; they do not mechanically require
proof that the host ran preflight. Automatic host hook invocation remains
NOT_RUN until separately exercised and evidenced. Do not infer it from this
command or create a duplicate host session to demonstrate it.

## Plan and run

Select one complete task-card JSON graph using the existing contract in the
operating reference. Store it as a project-relative `.json` file, or place
exactly one JSON code fence between `<!-- BOOSTERAI_HARNESS_SOURCE_BEGIN -->`
and `<!-- BOOSTERAI_HARNESS_SOURCE_END -->` in the existing algorithm Markdown.
The helper generates and validates all cards in that selection. It cannot find
omitted requirements or infer missing file dependencies. Declare every relevant
input and owned output in `read_files` and `file_owners`, including project code
used by the acceptance test; the explicitly named test file is included too.
Existing records are not replaced. One recognized `_boosterAI/harness-state.json`
tracks the selected source and its tasks; an unknown state or a different source
is preserved and rejected. Guard files implement cooperative claims separately.

For an authorized project with that plan and a reviewed real-target test:

```text
python -I -S -B scripts/harness_5b7e9c31.py --root . --source _boosterAI/algorithm.md start --task SLICE --test tests/test_real_acceptance.py --case Acceptance.test_real --max-attempts 3 --timeout 60 --max-elapsed 3600
python -I -S -B scripts/harness_5b7e9c31.py --root . --source _boosterAI/algorithm.md verify --task SLICE --run-id RUN_ID --test tests/test_real_acceptance.py --case Acceptance.test_real
python -I -S -B scripts/harness_5b7e9c31.py --root . --source _boosterAI/algorithm.md status
```

Use the actual `run_id` from `start`; the exact case is repeated explicitly in
`verify`. It must be `Class.test_method` in a project-relative `.py` file. Only
one selected unittest case is supported in version one; its assertions may
exercise multiple real product behaviors. Success requires exit zero and a
successful report for the exact case with one executed test and no failures,
errors, skips, expected failures or unexpected successes. Declared files and
tool and policy fingerprints must remain unchanged. The selected card must
still match and its prerequisites must have fresh acceptance. The host
determines whether those assertions establish the requested product acceptance.
The runner's structured report is not an authenticated attestation and trusted
test code can falsify it. Hashes detect changes to declared bytes only; they do
not discover undeclared code, environmental changes, remote effects or dishonesty.
The recorded revision is a working-file and verifier fingerprint, not a Git
commit identifier. Unrelated card changes do not stale an unchanged card;
changed prerequisites stale their dependents.

`status` acquires the cooperative lock and may create workspace/claim guards.
It may write `_boosterAI/harness-state.json` when it marks evidence `STALE` or
converts a persisted `VERIFYING` or `STOP_REQUESTED` record to `AMBIGUOUS`.
Use existing records or an authorized read-only snapshot for no-write inspection.

The host must set process-local `TEMP`, `TMP` and `TMPDIR` to its authorized
project-contained scratch; `BOOSTERAI_SCRATCH` selects an existing safe scratch
directory when the project uses one. A previously established project-tools
scratch guard must continue to match. Each harness test gets its own operation
directory. Unknown contents, changed identities and ambiguous live effects are
preserved for inspection; the helper does not run a background cleaner.

## Interruption, limits, and STOP

`ACTIVE` records host-owned development; a verifier records `VERIFYING` before
starting its child. A failed or skipped case is `FAILED`; changed code is
`STALE`. A missing report, conflicting result, zero-test result, timeout or
interrupted verifier is `AMBIGUOUS`, not an automatic retry. After a failure or
interruption, run `reconcile` once to observe declared files and inspect actual
effects. Only after the host has reviewed those effects may it run
`reconcile ... --effects-reviewed`; the file observations must still match. This
is a host declaration, not proof of external effects or permission. If an
attempted verifier has an unknown outcome, it remains `AMBIGUOUS` and retains
the executor slot even after file review. A late report is preserved as observed
evidence, not permission to replay. A late report declaring access denial records
a no-retry marker while retaining `AMBIGUOUS`: it does not prove child reaping
and must not release the executor slot. Even a late successful or failed
report cannot prove the child was reaped or exclude a later refusal whose state
write failed. This deliberately restricts automatic recovery: a durably recorded
`FAILED` still has its separate changed-condition retry path, but an attempted
`AMBIGUOUS` run has no force-recovery command. This
does not provide durable denial recording when the initial state write itself
failed. The host must preserve that refusal outside the failed operation and
must not retry it. Following
`FAILED`, a declared file or verifier must also change after the failure's own
post-test snapshot before an explicitly requested retry is allowed. A test's own
side effects do not count as that change. External-only changes cannot satisfy
this conservative gate automatically; they need a separately supported handoff.

When a prerequisite becomes stale, a dependent may yield the serial slot only
through the existing two-step `reconcile` / `reconcile --effects-reviewed` path
and unchanged observed file effects. Two eligible cases are supported:

- Never-run work: zero verification attempts, no verifier report or scratch
  operation, and no retry-change obligation.
- A durably completed `FAILED` attempt: a recorded FAILED outcome, retained
  failed-file and verifier snapshots, and an outstanding retry-change obligation.

A reviewed eligible dependent becomes `WAITING_PREREQUISITE`, allowing the
prerequisite to be repaired and verified first. Observation alone is not review:
never-run work remains `AMBIGUOUS`, and a completed failure remains `FAILED`.
Once prerequisite acceptance is fresh, explicitly `verify` the waiting task
with its original run ID and test. Attempts, elapsed limits, failure history
and failed snapshots are preserved. A failed task still requires changed
inputs or verifier since its failure; yielding alone does not permit replay.
This does not stop outside host work or authenticate external effects.
Attempted `AMBIGUOUS` verification, missing failure metadata, changed contracts,
DENIED and STOP barriers are not released by this recovery path.

The runner classifies a directly observed Python `PermissionError` as `DENIED`
without recording its message or path. If that happens before a report can be
written, its reserved exit code 77 also stops the task; arbitrary test code can
produce that code, so it is not independent proof of an OS denial. Older reports
without the denial field remain unrecognized and need manual review. This task
cannot be reconciled into a retry by the harness, while independently eligible
tasks may proceed. A caught, transformed, service or policy denial may not be
recognized; the host must carry forward every actual refusal. A host-side
permission error is separately reported as `ACCESS_DENIED` and must not be
retried, including when the state itself cannot be written. An observed failure
report is never proof that external effects are absent. Sanitized attempt
summaries are bounded by the attempt limit, while older records can lack that
history. The helper cannot guarantee exactly-once effects or determine whether
orphaned descendants or external services are still active.

`stop --task SLICE --run-id RUN_ID` records a project-wide stopped barrier for
future harness operations when no verifier holds the cooperative lock. To stop
a running verifier, start `verify` with `--listen-stop` and send the exact line
`STOP` followed by a newline to that same verifier's stdin. The harness requests
termination of its own direct child after recording the STOP request, records
`STOPPED` when the child is reaped, and prevents new harness work. The child
receives closed input in this mode. The host must relay a user
STOP; this is not automatic chat integration. A control-stream read error also
stops conservatively. Child descendants and external effects may continue, and
state-write or termination failures remain ambiguous. A denied cleanup is
reported separately, retains its owned path and is never automatically retried.
There is no built-in
resume of a stopped record. The host must enforce STOP for all other work too.
File claims cover cooperating processes only and are held during harness
operations and acceptance, not while the host edits code between commands.
Nonparticipating writers can race these checks.

Attempts, a per-test timeout and a wall-clock elapsed gate are bounded at start;
the child timeout is limited by remaining wall time. A timeout can leave child
descendants or side effects, so it is ambiguous and requires review. Clock changes
and an unresponsive host mean these are not a hard system-wide execution limit.
The harness performs no model calls and cannot enforce or report model tokens or
monetary cost. It records cost as unavailable unless the host obtains trustworthy
per-run measurements separately. Compare like-for-like tasks before claiming
speed or cost improvements.
