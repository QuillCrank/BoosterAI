# Bundled engineering procedures

These procedures are part of BoosterAI, not calls to another skill or service. Read only the relevant section. They are adaptable reasoning instructions, not an autonomous runtime.

## Search before change
Inspect the current entry point, owner, nearby code and existing tests. Trace one actual input to its observed effect before adding an abstraction. Search existing source and records with available host tools.

For each distinct coding question, first look for relevant standalone examples in the project's authorized partials locations. Then use a subagent to check for corresponding examples in the authorized BoosterAI GitHub repository's partials locations. Read examples as research material under their existing ownership and license.

When available and authorized, assign separate subagents in parallel to research current developer experience on Stack Overflow, within the authorized GitHub scope, and on developer forums. Use generic technical terms and keep private project names, paths, code, credentials and user data out of queries. Check dates and relevance, corroborate material claims with primary sources and record how the finding affected the actual work. Report unavailable sources or delegation and continue independent authorized work. The core workflow remains usable offline.

Keep local and authorized archive examples in their established locations. Check useful advice on the real target. If the defect cannot be reproduced, preserve the observation and record that the cause remains unknown.

## Evidence-led debugging
1. Capture exact failure, environment, code revision and what already happened. Hide secrets from logs.
2. Distinguish missing prerequisite, refused action, failed execution and ambiguous side effect.
3. Trace the first divergence between expected and actual behavior. Form one falsifiable cause.
4. Run the smallest authorized discriminating experiment. Do not change many variables or blindly replay a non-idempotent action.
5. Fix that cause in the product, add a focused regression check and repeat the original real path. After two unchanged failures, change the hypothesis or report the precise dependency.

## Design and implementation
Choose the smallest coherent behavior that can be invoked and observed. Keep external effects at explicit boundaries; validate inputs; use cancellation, bounded retries and recoverable state where the observed failure needs them. Do not add universal recovery frameworks or speculative features. Language-specific choices follow the existing codebase, not a mandatory external patterns skill.

Use the explicitly requested language first, the existing module language next, and Python as the fallback.

Keep production functions small, separate, and reusable. Give new source
files short, descriptive names with a stable unique suffix where the
language and build permit it. Preserve established module paths and
interfaces. Document each function's purpose, inputs, result, and
important limits in a docstring or immediately preceding comment.

## Runtime selection and verification

For new Python work, select Python 3.12 or newer. For new .NET work, select
.NET SDK 10 or newer. An explicit user-requested version takes precedence.
Check existing project constraints before changing an established runtime.
Make any authorized installation, upgrade, retargeting, or global configuration
change explicit. If a suitable authorized runtime is unavailable, report the
dependency and continue independent work. These product defaults are separate
from the older runtimes retained in BoosterAI helper compatibility tests.

Use `python -I -S -B scripts/booster.py runtime-check --language python` to
check the interpreter actually executing the helper. For .NET, first obtain
the selected SDK version through the authorized host and supply it with
`runtime-check --language dotnet --observed-version VERSION`. The .NET check
reports the supplied SDK version as an unauthenticated observation. The SDK
and .NET runtime versions describe different components. Pass
`--requested-version VERSION` only when the user explicitly requested that
version. A `major.minor` request matches its series. A full version must
match exactly. The helper performs a read-only check of stable numeric
versions. Product acceptance and native Codex integration require their own
evidence.

## Package and delivery language
Write all BoosterAI project files and packaged content in English, including
source comments, documentation, tests, project records, harness files, and
hooks. This applies to Requirements 42 and 43. Follow the [user-delivery
workflow](user-delivery.md) when communicating in the user's language. Follow
the existing approval process for root-instruction changes and publication.
Apply the [publication privacy workflow](publication-privacy.md) before publishing.

## Verification and review
Run applicable syntax/build checks and focused tests, then exercise the real entry point in the actual target. Separate local unit checks from acceptance. Compare actual observations with the predeclared contract; bind evidence to revision and target. Review the diff for unexpected files, lost work, secrets, unnecessary complexity, unbounded loops and inaccurate completion claims. Fix the current slice before advancing. Existing project build tools are target prerequisites, not bundled BoosterAI dependencies.

## Task division
One owner controls the critical path, serial dependencies and shared live input. Identify independent, useful ready work and, when the user's instructions, host capabilities, available capacity and security boundaries allow, use the most efficient available parallelism for it. Give each delegate explicit scope, acceptance, handoff and a non-overlapping file or resource ownership. Do not create duplicate tasks or speculative work merely to occupy slots. Preserve existing owners and active work; do not change the user's selected model, reasoning effort or speed to dispatch helpers.

Integrate and test delivered changes before dependent work. If safe parallel work or delegation tools are unavailable, proceed serially rather than installing a worker system or duplicating work. Assess the result by useful progress, observed cycle time and rework, not worker count.

## Dynamic planning
Keep input, precondition, effect, acceptance, failure branch and recovery for each node. On changed requirements, update affected nodes and invalidate their dependent evidence; preserve unaffected verified work. Store only project facts in project records and general conventions in any existing shared records. Neither a central service nor a private notes folder is required: one local project record is sufficient.
