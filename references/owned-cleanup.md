# Owned scratch cleanup

Load this workflow when choosing project scratch, reconciling an interrupted operation, or reviewing an exact owned disposable set. Use the [daily log retention workflow](logs-and-helpers.md#permanent-deletion-with-action-time-approval) for daily log eligibility and fresh approval of an exact permanent-deletion batch.

For a project using BoosterAI, the default cleanup root is PROJECT/_boosterAI, not the project root. An explicit user-selected scratch directory inside the project takes precedence. Keep manifests, .booster-tmp, quarantine and collector locks inside that selected directory; see project-workspace.md.

Prevent accumulation before collection: reuse a run-specific directory; bound output sizes/counts; avoid duplicating unchanged reports; retain one compact current index with links to necessary evidence. Review scratch at meaningful completion/checkpoint boundaries, not via a new recurring daemon. Preserve unresolved failure evidence and active artifacts.

Register cleanup before fallible work, including test setup, and configure child-process temp/cache paths before launching children. Under established authorization to clean disposable work, remove the exact owned run artifacts after success or failure once necessary evidence is retained. Test fixtures do not acquire the separate daily-log approval requirement. Check file count and bytes before and after work; completed runs must not leave new disposable output. After interruption reconcile known leftovers before adding more. If ownership, active use or a cleanup failure prevents this, report it and stop the affected producer rather than allowing growth. For a known retained batch whose deletion is blocked, record its exact fixed inventory and size; do not duplicate or expand it. These are invocation-time procedures, not an operating-system sandbox or continuous disk monitor.

Classify files as source/deliverable, acceptance evidence, active scratch, or obsolete owned scratch. Never infer garbage from age or an unfamiliar filename. Require known creator, purpose, retention deadline, no active owner/reference and unchanged content. User files and another task's files are excluded.

The helper is deliberately narrow: only individually registered regular files under `<root>/.booster-tmp/` may be moved to `<root>/.booster-quarantine/`. It never traverses for deletion, never removes directories, never purges quarantine, never follows links/reparse points, and rejects hard links. It does not inspect OS temp or other projects.

Create a manifest only for scratch this task actually created. Fields:
```
{"owner":"your-current-task-id","files":[
 {"path":".booster-tmp/run/output.txt","owner":"your-current-task-id",
  "sha256":"SHA256_OF_CONTENT","expires_at":"2026-01-01T00:00:00Z",
  "inactive":true,"retain":false}
]}
```
`inactive` is an assertion to verify against the real owner/run; not a technical lock. Before applying, stop writing those exact files and verify no other writer can use them. Unknown ownership or concurrency means do not apply.

```
python scripts/booster.py gc-plan --root PATH --manifest manifest.json --owner ID
python scripts/booster.py gc-apply --root PATH --manifest manifest.json --owner ID
```
The dry plan lists eligible paths. Apply rechecks containment, age, owner, hash and file identity, uses an exclusive collector lock, moves one file at a time, and writes a recovery map in quarantine. The collector verifies the lock's single-link identity and random ownership token before each move and before release; a missing, modified, replaced or linked lock is preserved when present and stops the operation. Access denials remain visible. This is best-effort crash recovery, not a transaction spanning other processes or an OS sandbox against concurrent path replacement. A `gc-apply` error reports `effect: UNKNOWN` and `reconciliation_required: true`: inspect the lock, source, quarantine and recovery map before deciding what happened; never blindly retry. Restore by the recorded original relative path only after verifying the destination is absent and within root. Quarantine still occupies disk: report its size, and request any mandatory permanent-deletion approval rather than claiming disk space was reclaimed. A verified retained equivalent copy may support normal policy rules, but this helper never purges it.

For other cleanup use the platform's supported recoverable trash only within explicit scope. Permanent deletion, unknown files, system folders and active processes are outside this default. Cleanup permission never authorizes killing unrelated processes.
