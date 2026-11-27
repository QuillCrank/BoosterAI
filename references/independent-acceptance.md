# Independent acceptance

Load this workflow at the close of each work turn and after correcting a
review finding. Use it before closing an acceptance scope or releasing a step
that depends on that scope.

## Brief a fresh reviewer

Assign a fresh independent reviewer who has not seen the developer's
conversation or a claim of success. Use the supported host capability to keep
that history out of the assignment. Give the reviewer just the goal, mandatory
acceptance criteria, exact revision and files, target environment, actual
invocation, allowed effects and authority limits. Identify the required
negative and regression cases. Confirm the reviewer can reach the authorized
target before treating the review as started.

If a fresh reviewer or required target is unavailable, record the affected
criterion as NOT VERIFIED and name the missing capability or prerequisite.
Continue independent authorized work when it can be accepted separately.

## Exercise the real entry

Review the changed behavior through its relevant code and instructions, then
run the actual file or public entry in its intended environment. Match the
result to each predeclared criterion and the exact reviewed revision. Exercise
the applicable failure cases and regressions through their actual entries as
well. Inspect the test and its intended effects before running it. Preserve
existing permissions, data, STOP and refusal boundaries.

Report the selected revision, invocation, environment and observed result
with enough evidence to reproduce or independently read it back. Give
controlled fixtures, source CI, package checks, installed behavior, native
events and real product acceptance their measured scope. A passing test, file
digest or exit code supplies only the observation it actually measures. A
Harness-run result still needs the meaningful target observation. [Single-executor test contract](optional-harness.md)

## Decide and route the result

| Verdict | Required evidence |
| --- | --- |
| PASS | Every mandatory criterion in the declared scope passed on concrete evidence. |
| FAIL | At least one mandatory criterion has an observed failure. Name it and preserve the reproduction and evidence. |
| NOT VERIFIED | One or more mandatory criteria could not be tested or lack sufficient evidence. Name the unavailable test or prerequisite. |

Give each criterion its own result. When a scope contains an observed failure,
record FAIL for that scope and keep any separately unverified criteria
visible. Reserve completion and any percentage claim for the defined,
observed scope.

Send a FAIL or NOT VERIFIED result to the responsible developer. Include the
unmet criteria, the reproduction steps or exact missing prerequisite, the
observations and the reviewed revision. Keep private inputs out of shared
reports. Check recipient processing separately from transport and receipt.

For native feedback, use the supported hook and Harness route. Bind the
original finding and revision to the authorized task, the same eligible ACTIVE
run and the actual recipient. Observe one delivery and recipient processing
before final verification or STOP. Later readback uses that delivery record.
Record manual follow-up as its own evidence when the native path is
unavailable. [Host integration and evidence](host-adapter.md)

Honor STOP for the affected action when received. If an attempt has already
failed or its effects are ambiguous, follow the existing Harness recovery
gates and preserve the run history and budgets.

## Repair and review again

Give the exact file owner the observed failure or verification gap. Investigate
the cause, choose an evidence-based correction, and verify affected
dependencies against the changed revision. Obtain a different fresh
independent review of the corrected scope. Keep earlier acceptance for
unaffected work only when its conditions still match.

When STOP, a refusal, missing authority or an actual blocker prevents an
action, checkpoint its observed state and next prerequisite in the existing
records. Preserve uncertain effects for reconciliation and continue eligible
independent work. Release a dependent action only after its required current
acceptance passes. [Reconciliation and evidence](records-and-continuity.md)
