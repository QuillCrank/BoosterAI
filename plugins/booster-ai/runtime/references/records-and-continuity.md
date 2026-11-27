# Records, continuity and unavailable skills

Load this workflow at the start of each BoosterAI turn and when retaining an authorized source message, preparing a handoff, or reconciling interrupted work.

Before taking a dependent step or dispatching work, check the applicable current instructions and skill, the project record and latest checkpoint, and authorized relevant message records and behavior notes. Confirm active ownership through supported, bounded host readback. Report an inaccessible source or unconfirmed owner as unobserved and defer only the work that depends on it. Preserve original records and reconcile uncertain prior effects before retrying. Keep work affected by STOP, denial or refusal stopped across retries and alternate routes. Continue independent authorized work within the existing approval boundaries, including separate approval for root-instruction changes.

Use supported host readback to match an incoming item to its source and record its position in the recipient task. Record any active or terminal state at inspection separately from evidence of receipt, observed action and completed response.

Keep three scopes separate:
- Private coordinator notes: commitments and personal follow-up only. Read only with the authorized owner; never copy their contents into public skills or project prompts.
- Central shared rules: reusable project-independent conventions only, not project task histories.
- Project records: algorithm, current status, single backlog, decisions and evidence stay with the project. Reuse existing files and preserve prior content.

Minimum handoff: goal; scope revision; last real-verified slice and evidence; current owner and active run/message IDs; ambiguous effects; exact blocker; next command/action; required target/permissions; scratch items retained. Do not repeat completed work after a restart. A Markdown ACTIVE label is not a live process or lock: verify via supported host tools before changing ownership.

## Authorization scope

In the existing canonical project record, retain the non-sensitive source of each applicable user authorization and its approved actions, targets or destinations, scope, stated duration or expiry, and exclusions. Distinguish permission already granted from a pending request; update the record when scope changes or authorization is withdrawn. Do not copy credentials, private payloads or unnecessary personal information into this record.

When source-message retention is authorized, keep each occurrence in the approved private record with its own source identity, position in the original sequence, original SHA-256 digest, and applicable access restrictions. Keep BoosterAI's own project records in English. In other projects, follow their authorized record language and retention rules. In an English-only record, represent a non-English original with a faithful English translation and a reference to the original message source. Retrieve the exact original bytes from the authorized message source when needed, subject to the applicable access restrictions. For byte-identical retained original UTF-8 bodies, keep one exact copy and a source reference for each occurrence. Reconstruct these occurrences in source order and verify each against its original bytes and recorded digest. Accept only verified original bytes as byte-level replay evidence. Maintain behavior notes separately within their existing authorized scope. Apply a root-instruction change only within the user's separate approval for that change.

Keep authorized user messages in existing private `_boosterAI/` project
records. Merge recurring instructions with the same meaning into the
applicable existing behavior note, separate from the source records.
Update the note when a later authorized instruction refines the behavior.
Keep the original occurrences, their order, meaning, decisions, and
evidence recoverable while minimizing duplication of private content.

Honor a user's valid, sufficiently scoped authorization for a batch of routine related actions without repeatedly requesting the same approval. Batch authorization applies only within the granted scope and time; it does not create access, override an explicit refusal or replace a genuinely mandatory action-time confirmation. Record such exceptions and obtain any required confirmation for the concrete action at the required time. Never fabricate an approval or bypass a security or access denial. Continue independent authorized work while the affected action is blocked.

A single executor owns serial critical-path code and live input. Use separate helpers only for genuinely independent work with bounded output and distinct files. Integrate their result before dependent work. Message accepted, received, run started, change delivered and target accepted are different facts. Do not create a hierarchy of approval roles for ordinary engineering.

Continuity means progressing during the available turn and preserving restartable state. Use existing authorized completion notifications or schedules when available; do not create duplicate watchers or imply a missing event hook exists. If the host cannot wake/resume, disclose the limitation and provide the next supported entry point. A failed notification does not invalidate a successful product test, nor does a successful callback prove it.

Keep the product critical path moving. Record nonblocking side work and
unresolved negative results in the existing TODO, checklist, or other
appropriate project record. Use the project's `_boosterAI/deferred.md` for
deferrable details when it is the appropriate record. Update the existing
entry for the same issue. Link the observation or its evidence and record
the remaining condition, responsible owner, and next authorized action.
Revisit the item when the related work needs it or its recorded condition
changes.

Return to the next authorized product slice while a nonblocking item waits.
Keep necessary acceptance checks and real blockers visible on the affected
path. A security refusal, denied access or mandatory approval still stops
the affected action. Do not retry or route around it. Reconcile ambiguous
effects before any permitted resumption. Independent authorized work
continues. Do not use this deferral record as an archive-operation log.

No specialist skill is required. Use the bundled engineering procedures directly. If a BoosterAI file or helper is missing, report the exact missing component and affected capability; continue independent authorized work with the readable procedures. A missing host tool or target prerequisite is an environment limitation, not an invitation to install an external workflow package.

When context becomes noisy, prepare a small factual handoff and use a fresh supported session if appropriate. Preserve original history; do not delete a thread or restart active work just to clear context. Every correction should remove ambiguity or shorten the path, not add another universal gate.
