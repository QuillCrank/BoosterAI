# Host integration and evidence

Load this workflow when routing work through the current host, selecting an
adapter interface, or verifying a hook or recipient. Start with the authorized
project, the selected host, the current owner and plan, and the exact request
identity. For an executable slice, also identify the allowed effects, current
revision, permitted target and acceptance test.

## Observe the selected interface

Inspect the interfaces available to the selected session. On the observed local
Codex Desktop surface, `send_message_to_thread` sends an authorized message to
an existing task. `read_thread` exposes recent turns, incoming items, visible tool
results and a completed response. `wait_threads` provides a bounded progress
snapshot. Use exact host and thread identities. The inspected task tools expose
manual thread and turn information. Native hook trust controls and the natural
hook lifecycle stream were unavailable through these tools. Record those
unobserved native stages as NOT_RUN.

| Observation | Evidence to record |
| --- | --- |
| The send call returns the task identity | Transport returned an acknowledgement. Check receipt next. |
| The recipient turn contains the matching incoming item | The intended task received this specific instruction. |
| A new turn begins and reports its assigned scope | The task started and interpreted the request. |
| A real target action and completed result appear | Check the effect and current revision against the assigned acceptance. |
| Native events are absent or outside the current interface | Record which interface was inspected. Mark the unobserved hook or delivery stage NOT_RUN. |

For a manual handoff, read back the exact recipient item, then verify the
recorded action and its meaningful result. With Codex Desktop `read_thread`,
select the authorized thread, use a bounded `turnLimit`, and follow the returned
page cursor if the needed turn is older. `includeOutputs: true` can expose the
incoming text. Match the returned turn and item identities, and bound output to
the evidence needed. Record only the necessary identities, revision and evidence
references in the existing project records. Follow
[in-flight messages](in-flight-messages.md) for STOP, duplicate delivery,
related reviews and independent work.

If a supported send or independent slot is unavailable, checkpoint the
affected request in the existing record. Use the current user channel for the
specific blocker and continue an authorized nonconflicting slice in the current
session where possible. An uncertain send needs effect reconciliation before
another attempt. A new host or transport never renews a refusal. When an OS
permission blocks work, assess the exact operation, the least privilege needed,
the expected benefit and the risks. Obtain any mandatory action-time approval
through the supported path.

## Keep execution with its owner

The host owns user authorization, exact target selection, agent/file ownership
and permission to start a test or effect. The
[single-executor harness](optional-harness.md) owns the selected serial run,
attempt and elapsed budgets, evidence freshness, STOP and reconciliation in its
existing state record. The owner checks dependencies and live claims before
dispatch. Collaborating agents may work on independent targets under disjoint
ownership.

Use the existing preflight before a separately authorized new run. Preflight
can acquire cooperative guards. A READY result establishes structural
readiness only. Start once through the supported entry, record the returned
run ID, and verify the named acceptance test on that same eligible run. The
status operation may update stale or interrupted run records. For an inventory
that must be read-only, inspect the persisted state and label it as a stored
observation.

### Reconcile the outgoing scope

Before transferring work, identify the outgoing task and turn, the exact
slice and writable files, and the current acceptance revision. Use the
supported task readback to observe the outgoing action and its recorded
result. Compare the attributable file or target effects with the recorded
before and after state. A task's idle or completed status describes its
turn. Confirm any required child outcome, external effect and release of
the scope separately. For a cooperative claim, check evidence from the
actual holder and claim lifecycle.

Keep dependent writes pending while necessary effects or ownership remain
uncertain. Preserve STOP, refusals and unresolved records. When the scope
is reconciled, assign it once to the authorized recipient and match the
incoming item and actual result to that assignment. Reuse earlier
acceptance only within its current revision, dependencies, target and
conditions.

## Future scheduled requests and script operations

A future scheduler offers due work through the host's supported request path.
Use one source-scoped request identity for each due occurrence and keep it
stable on redelivery. In the existing project record, bind that identity to
the authorized project root, current plan revision, one slice and its allowed
target and effects. On redelivery, compare the binding and read the recorded
pending, active or terminal state and any receipt, run and result. Continue
a pending request through the existing eligibility checks once you confirm
it has never been dispatched. For an existing dispatch, keep its identity
and report the observed state or current matching result. Hold a conflicting
binding, uncertain send or unresolved effect for reconciliation.
Follow [in-flight messages](in-flight-messages.md) for the existing
deduplication and routing procedure.

The scheduler chooses when to offer work. Before dispatch the host checks
current authority, any required action-time approvals, the actual owner,
fresh prerequisites, STOP and refusals. An expired grant needs a fresh
applicable decision. The [single-executor harness](optional-harness.md) controls
one eligible serial run and its acceptance evidence. Record the offer, host
receipt, dispatch, transport acknowledgement, recipient delivery, processing
and accepted result as distinct observations in the existing records.

Describe each future script operation with one selected
[Task Card](operating-algorithm.md#source-complete-task-cards). Bind the stable
operation identity to the authorized root and target, the exact project-relative
entry point, effective code revision and arguments. Declare inputs, expected
outputs and effects, read files, file ownership, dependencies and acceptance.
Set the approved attempt, timeout and elapsed limits for the selected run.

Specify the real target observation that distinguishes an already applied
effect from pending work. Reconcile an unresolved outcome using the existing
recovery rules before considering another attempt. Preserve STOP, refusals
and an unresolved attempted verifier's executor slot. Verify the declared
real-target test on that same run and retain the result with its working-file
and verifier fingerprints.

Keep [daily archive feedback](daily-archive-sync.md) on its invocation-time
rules. Acceptance of a future integration must observe redelivery of the same
due identity, one eligible launch, handling of prior or unresolved effects,
and same-run real acceptance. Record unobserved automated stages as NOT_RUN.

## Accept a native adapter only on observed effects

On a selected host exposing a supported native interface, bind the actual
source hook session, turn and applicable tool-use identity to the reviewed
finding, selected plan, task, run and source revision. Verify the exact
recipient session and turn, user opt-in, effective hook trust, run eligibility
and budget. Preserve inherited hook declarations and obtain approval as
required for the exact definition. Keep STOP, denial and ambiguous effects in
the existing owner and state path.

A read-only matcher can establish agreement between supplied metadata and a
bounded plan/run observation. A command projection produces the reviewed
arguments for the supported caller. For cooperating emitters using receipt
deduplication, assign one owner to an explicitly authorized project-local store,
preserve existing records, and reconcile an uncertain claim or send before
attempting delivery again. A committed receipt establishes its store effect.

Record effective registration/trust, natural hook start and completion,
actual delivery, one-time recipient processing, real same-run acceptance,
needed continuation and ordinary final stop as distinct observations. Use the
host's event readback only for the events and identities it actually exposes.
When the proof is incomplete, checkpoint the exact gap and label the unobserved
stage NOT_RUN. Configuration, command text, a manual handoff and a receipt each
retain their measured scope.

Send an actual failed or unverified independent result to the responsible
developer with the exact unmet criteria, reproduction steps or missing
prerequisite, and evidence at the reviewed revision. For native review, send
once and observe recipient processing while the same run is still ACTIVE,
before final verification or STOP. Keep the original finding and revision
bound to the actual source and recipient. The owner corrects the demonstrated
cause or resolves the missing prerequisite, and a fresh reviewer repeats the
real acceptance before a successful final verification. Later readback uses
the single observed delivery. Keep each result scoped to its actual entry
and effects.
