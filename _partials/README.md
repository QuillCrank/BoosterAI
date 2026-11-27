# Verified functional partials

This index tracks executable functions, not a claim that files or builds are working products.
Reuse the project algorithm and backlog; do not create a second authoritative plan here.
Language priority: explicit request > existing module language > Python.
Prior partials in `_boosterAI/_partials/`, if present, stay in place and must be inspected before creating replacements.

| ID | Function / contract | Code file | Dependencies | Owner / active run | Target / revision | Evidence / test state | Integration | Optional backup |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

## Current slice
- Algorithm node and acceptance:
- Actual prerequisites / target:
- Next action or exact blocker:

## Rules
Implement the first smallest independently invocable function. Keep one function's code in its own file when compatible with the existing module; preserve project conventions and working code. Use a short descriptive filename with a unique suffix, and a detailed function comment or docstring.
Test through its real target before implementing the next dependent function. Use NOT_RUN, FAIL, AMBIGUOUS or VERIFIED with evidence, never infer success from a build.
The consuming project's owner handles any needed assembly or later refactoring of archive pieces; the skill does not assume those tasks. Verify the resulting product through its actual entry point when integration is requested.
If per-step GitHub backup was requested and authorized, record remote/branch/commit and verified result; a failed push is BACKUP_FAILED, not success.
