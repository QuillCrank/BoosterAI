# Publication privacy review

Before publication, inspect the proposed public file list and its contents for private project terms, personal paths, credential-shaped text and references to old Git commits. The bundled offline scanner accepts a JSON object on stdin with exactly `files`, `private_terms` and `old_commits` arrays:

```
python -I -S -B scripts/publication_scan_91c4e6b2.py --root PROJECT < MANIFEST.json
```

The CLI requires exactly one explicit `--root` with a nonblank value and exits with usage error code `2` before reading stdin if the option is omitted, repeated, or given an empty or whitespace-only value.

`files` must be a nonempty list of project-root-relative UTF-8 files to scan. `private_terms` is a caller-supplied list of sensitive names or terms; none is hardcoded. `old_commits` is a caller-supplied list of full 40-hex prior Git commit IDs. Both lists may be empty when explicitly supplied; the reviewer must judge whether they are complete. Supply private input through stdin or an authorized temporary location and keep it out of public files and logs. The scanner does not run Git, inspect other projects, access the network, execute listed files, or write a report.

The scanner checks listed filenames and contents case-insensitively for private terms. It also flags common credential and personal absolute-path patterns and standalone references matching the first 7–40 hex digits of a supplied old commit. Contiguous 64-hex SHA-256 values are not treated as shortened Git commit IDs. Input paths must stay beneath the selected root and refer to regular single-link files; links, junctions, reparse points and escapes are rejected. Missing files, unsupported encoding, oversize input, invalid IDs and access denials fail closed. A finding reports only its zero-based file index and category, never the private value or path; use the input list locally to inspect it. The result includes counts of supplied terms and commit IDs, not their values. An exit code of 1 means findings; 2 means an error.

`NO_MATCH_REVIEW_REQUIRED` means only that these specified checks found nothing in these specified files. It does not prove privacy, source rights, the completeness of the file list or private-term list, and it does not detect every secret or project-specific clue. Review the entire intended public tree and any non-text artifacts independently before publishing. The scanner checks containment and file identity but is not an OS sandbox against hostile concurrent filesystem replacement.

## Source and package publication

For a source-only release, review the exact source diff, parent and proposed refs against independent acceptance of the changed slice. Keep unrelated working changes, private evidence and unaccepted package or marketplace artifacts outside the release.

For a package release, review the final built artifacts and the exact staged marketplace registration, including their metadata and non-text files. Match the source revision, package files and version, runtime manifest and marketplace registration to the scope covered by independent acceptance. Identify installed-copy and live host observations by their actual targets.

For either release, identify the exact source repository or fork and pin the parent and proposed ref tips to immutable commits. Review file paths and contents, commit messages and metadata in every commit reachable from each proposed ref. A scanner result or a sanitized tip does not verify reachable history. Keep private terms and project evidence out of every public artifact.

Verify the actual commit graph and remote refs using supported Git and host tools. Review the updated scope if the proposed content, parent or refs change. When a clean Git history is required, verify parentage and check the destination for earlier branches, tags and commits.

Use only the exact destination and action authorized by the actual owner. Satisfy all applicable safety requirements and obtain any mandatory action-time approvals. Do not force-push or rewrite history without specific authority for that action. Do not delete anything without exact owner authorization for the deletion.

For each authorized BoosterAI release, record local test results,
observed real-target behavior, the publication outcome, and
post-publication readback of the exact remote ref and commit as
separate observations in the existing project evidence record.
When CI applies, record its result for the exact published commit separately.
Record installed-copy and native-host observations separately for their actual
targets.
