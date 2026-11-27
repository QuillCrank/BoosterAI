# Project workspace and existing records

Load this workflow when choosing project records, setting up a workspace, locating standard or legacy partials, copying a project-local schema, or checking schema or index freshness. Record the revision and target of each verification, including whether it ran on the local checkout or in a source CI workspace.

Create `<project-root>/_boosterAI/` for durable BoosterAI-owned work: the working algorithm, evidence and handoffs. Create or reuse `<project-root>/_boosterAI/_partials/` for functional pieces and their README index. By default scratch, cleanup manifests, quarantine and helper locks live under `_boosterAI/`. An explicit user-selected temporary directory inside the authorized project overrides that default for temporary artifacts. Product integration remains the consuming project's responsibility; this workspace convention never authorizes moving existing product files or accessing another project.

Inspect the existing record system before choosing a record option. Preserve its files and formats in place. Select the existing canonical record with `--records-existing PATH`, including one with a custom name or location. Use `--create-records` to provision a record only after confirming that the project has no existing record system. New records belong in `_boosterAI/PROJECT_RECORDS.md`. The generated record has sections for goal and acceptance, current state and next action, backlog, and decisions and evidence. It refers to the working `algorithm.md`.

Use at most one record option.

```
python scripts/booster.py workspace-init --root PROJECT --records-existing docs/status.md
python scripts/booster.py workspace-init --root PROJECT --create-records
```

- `--records-existing PATH` selects an existing project-relative record file in place, including one maintained under a different name or format.
- `--create-records` checks the project-root `PROJECT_RECORDS.md` and `_boosterAI/PROJECT_RECORDS.md`. If neither path exists, it creates the nested record. If just one exists as a file, it reuses it in place. If both paths exist, it stops before creating any part of the scaffold. Choose the intended file with `--records-existing PATH`.
- With neither option, the helper scaffolds owned work and reports `records_status` as `not_selected_inspect_existing_system`.

The scaffold creates `_boosterAI/algorithm.md`, `_boosterAI/README.md` and `_boosterAI/_partials/README.md` when absent, preserving existing files. Existing nested partials and their README are reused without overwriting them. Report an existing project-root `_partials` as the legacy location and inspect its contents when appropriate; leave that location, its files and history in place. Do not silently move, delete, overwrite or duplicate an implementation. Preserve either existing README unchanged during initialization: its older scaffold guidance may predate the current standard. The initializer's `directory` and `index` report the nested standard, and `legacy_root_location` reports an existing root `_partials`. For compatibility, the old `legacy_location` field still reports whether `_boosterAI/_partials` existed before the call; that path is now the standard, so new callers should use `legacy_root_location` for the root location.

## Read-only consumer boundary

Code examples in project-root `_partials/` and `_boosterAI/_partials/` are standalone, read-only learning references for consuming tasks. Read them for guidance and implement the required behavior in the project's own production files outside both directories. Never import, link, include, dynamically load, build against, or create any runtime dependency on those examples. Reading does not authorize editing an example. Explicitly authorized example authors and maintainers may create or update examples separately; that does not make them production dependencies. This is a workflow contract, not an OS permission change or a universal dependency scanner. Index paths are discovery metadata, not import targets. Existing readmes are preserved during initialization even if their older guidance predates this rule; the current skill contract applies.

## Project-local partial and experience index

Use `search-project-index` to find entries in the stored project index. It reads the snapshot and reports `source_freshness_checked: false`.

```text
python scripts/project_tools.py search-project-index --root PROJECT --query legacy --kind partial
```

To check freshness, select the exact paths of indexed files you are authorized to read. The read-only freshness command compares their current bytes with the recorded size and SHA-256. Run these Windows examples from the BoosterAI checkout.

```powershell
py -3.12 -I -S -B scripts/project_index_freshness_a62e9d47.py --root . --path _boosterAI/_partials/README.md
py -3.12 -I -S -B scripts/project_index_freshness_a62e9d47.py --root . --path _partials/README.md --path _boosterAI/_partials/README.md
py -3.12 -I -S -B scripts/project_index_freshness_a62e9d47.py --root . --path _partials/README.md --path _boosterAI/_partials/README.md --require-current
```

`CURRENT` means both the size and SHA-256 match. `STALE` means at least one differs. One `--path` returns the entry itself. Repeated `--path` options return an `entries` array ordered by recorded path. The overall result is `STALE` if any selected entry is stale. By default, both `CURRENT` and `STALE` are successful comparisons and exit with code `0`.

Use `--require-current` when all selected entries must be current. The flag returns the same JSON report with exit code `0` when every selected entry is `CURRENT` and `3` when any is `STALE`.

Duplicate or unindexed paths are rejected before reading any selected file. These rejections, failed reads and observed index changes return `ERROR` with exit code `1` and no partial results. Argument parsing errors exit with code `2`.

Use `refresh-project-index` when you need to populate the index or update its stored records.

```
python scripts/project_tools.py refresh-project-index --root PROJECT
python scripts/project_tools.py refresh-project-index --root PROJECT --experience _boosterAI/records/observation.md
```

`refresh-project-index` discovers regular files in standard `_boosterAI/_partials/` and legacy project-root `_partials/`, local `_boosterAI/experiences/` files, and `metadata.json` files in `_boosterAI/helpers/`. An optional repeatable `--experience` adds specific existing project-relative files under `_boosterAI/`; a previously indexed experience remains included on later refreshes. It writes a separate `_boosterAI/project-index.json`, never either human-maintained partials README, existing project records or the shared `solutions/index.json`. Each sorted entry contains only its relative path, kind, size and SHA-256, not file contents or free-form summaries. Both locations retain full relative paths, including when filenames coincide. Paths and hashes can themselves be sensitive; keep this index local unless independently reviewed and authorized for sharing. No indexed code is executed, and indexing does not authenticate experience or evidence.

An existing index is updated only if it matches this helper's exact recognized format; an unknown/custom index or schema is left untouched. A missing previously indexed source stops refresh instead of silently dropping a record. If the legacy root `_partials` is a file rather than a directory, initialization preserves it and index refresh stops without changing the existing index. Symlinks, junctions, hard-linked files, special files and observed source or index changes stop the affected operation. Writers using `project_tools.py` share the project's existing lock; independent nonparticipating processes can still race these best-effort checks, so avoid concurrent external edits. If an interrupted initial creation leaves an incomplete index, preserve and reconcile it before retrying. Refresh preserves the existing partials and experience files.

## Existing algorithms and freshness
To make an authorized working copy of a project-local schema, add `--schema path/to/existing.md` on initialization. The source is read only, copied byte-for-byte into `_boosterAI/algorithm.md`, and its relative path, SHA-256, modification time and copy time are recorded in `algorithm.provenance.json`. An existing algorithm or provenance prevents overwrite. Copying preserves provenance, not acceptance: inspect the source and current requirements before trusting any old done status.

Before reusing a copied schema, check source freshness with `python scripts/booster.py schema-status --root PROJECT`. The command compares source and working-copy hashes to the captured origin. If `_boosterAI/algorithm.provenance.json` is absent, the command reports `schema provenance record is missing` and exits with code `1`. A changed source means reconcile against current requirements and invalidate affected evidence; a changed working copy may be intentional, not an instruction to replace either file. Keep the origin metadata immutable as historical provenance. Record reconciliations and scope revisions in the algorithm and existing project records. The helper does not infer truth, freshness of an external target or file ownership from hashes.

A schema outside the authorized project must not be guessed or copied by this helper. Use the host's authorized read/copy path, retain explicit source/revision/hash provenance, and never relocate the original without authorization. On a partial failure, inspect created files; exclusive creation prevents blind replacement but is not a cross-file transaction.

## Scratch tools
Pass `--root PROJECT/_boosterAI` to cleanup helpers by default. When the owner selects `PROJECT/_temp-files`, pass that directory as the cleanup root instead; `.booster-tmp`, quarantine, lock and manifest must all stay beneath it. The collector still only handles registered files in its `.booster-tmp` child; it is not a general directory cleaner. Do not feed the project root or unrelated temporary directories to routine cleanup.

Configure process-local `TEMP`, `TMP`, `TMPDIR` and relevant cache/staging options before invoking tools that write temporary files. For `project_tools.py`, set the process-local `BOOSTERAI_SCRATCH` to the owner's stable selected scratch root on first use; individual operation temp directories may vary beneath it. Do not bind the shared project lock to a short-lived child directory. Disable unnecessary bytecode/cache writes. Use unique owned run directories and cleanup on success and failure; keep only necessary durable evidence in the established record location. Inspect leftover scratch before a new run and after completion. Quarantine is still disk usage, not cleanup completion. Scope, ownership, recoverability and real verification rules remain unchanged.
