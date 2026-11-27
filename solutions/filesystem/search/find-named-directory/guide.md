# Find an exact directory name

This MIT-licensed Python standard-library example searches explicitly selected roots for a folder name, ignores case and never intentionally descends into symbolic links or Windows junctions.

```text
python -I -S -B solutions/filesystem/search/find-named-directory/revisions/0002/find-directory-0a138fca.py --name SampleFolder --root .
```

Repeat `--root` to scan multiple authorized paths. On Windows, `--all-local-drives` instead selects local drives with assigned letters; it excludes network drives and does not enumerate mount points behind links. Use that option only when the whole selected scope is authorized. The JSON result contains matches, access errors, skipped links and a `complete` flag; exit 0 means complete with matches, 1 complete without matches, 2 incomplete or invalid and 130 interrupted. Unreadable or skipped locations prevent a complete result. This is not a security sandbox against concurrent changes to filesystem links.
