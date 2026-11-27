# Design evidence and attribution

Reviewed 2026-11-14. These sources inform design choices, not a claim that this skill has reproduced their results.

- [Anthropic, Effective harnesses for long-running agents (2025-11-26)](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents): incremental features, explicit progress artifacts and end-to-end testing address context loss and premature completion. Adopted here as small verified slices and compact handoffs. This is a vendor experiment, not a universal guarantee.
- [Cursor, Scaling long-running autonomous coding (2026-01-14)](https://cursor.com/blog/scaling-agents): their flat coordination and locking approach created bottlenecks; clearer responsibilities and less orchestration complexity helped. Adopted here as explicit ownership and optional independent helpers, not an assertion that one executor fits all workloads.
- Local workflow guidance and installed verification/debugging skills informed separation of general policies from project evidence, verification before completion claims, and evidence-led diagnosis. Their private content, project details and source code are not redistributed in this package.

BoosterAI's synthesis: minimize time to the next verified product behavior, not agent count, document volume or uninterrupted activity. Measure improvements against a comparable baseline before making speed claims. Community anecdotes can suggest experiments but are not acceptance evidence.
