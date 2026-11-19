# Luotopia Contributors

This repository aggregates contributor source-line changes across the selected
ClosedWHU repositories using only local Git history inside GitHub Actions.
The repository itself is excluded from the target list. `Luotopia-i18n` and
`ClosedWHU-glossary` are also excluded; `WHU-Calendar` is included explicitly.
Distribution-only and documentation-only repositories are also excluded:
`Luotopia-Certificates`, `Luotopia-Dist`, `Luotopia-Docs`, `Luotopia-Skills`,
and `homebrew-luotopia`.

Metric version 2 replays the complete commit DAG in parent-before-child order.
Every commit has its own file snapshot. Git's histogram diff aligns unchanged
line occurrences, and Git's rename detection carries their provenance across
file moves. Paths are read from NUL-delimited raw metadata, including spaces and
non-ASCII names. Blank lines count; binary blobs and non-regular files do not.

At merges, unchanged lines retain the first parent's provenance; new regions
can inherit from other parents. Only lines absent from all parent alignments
belong to the merger. A copy creates new occurrences rather than duplicating a
single provenance token. Git-unrecognized moves/rewrite/copy operations count
as new occurrences; matching does not infer semantic equivalence.

The report ranks authors by source lines retained at the recorded HEAD:

- `added_lines` / `added_ksloc`: source lines still present at HEAD.
- `canceled_added_lines`: distinct introduced lines absent at HEAD, credited to
  their original authors.
- `canceled_deleted_lines`: those same absent lines, credited to their remover.
  Merges inherit a parent's existing removal attribution; when two parents
  independently removed the same origin, the first parent's attribution wins.
  If a merge discards a line still present in its parents, the merger receives
  the removal. These are final-state counts, not gross historical edit events.
- `deleted_lines` / `deleted_ksloc`: zero by construction. Complete replay has
  an origin for every removal; unmatched removals are not a contribution score.
- `net_lines` equals retained lines. `matched_cancellations` is the sum of the
  author's canceled additions and canceled deletions.

Each repository verifies that introduced lines equal retained plus canceled
lines, that no final occurrence has duplicate provenance, and that retained
and removed sets are disjoint. Report generation fails if these checks fail,
history is shallow, or a selected checkout is missing. Canceled lines are not
necessarily invalid code, and these counts do not measure code quality.

All identities are counted during diff processing. The final report separates
human and automation identities. Claude, Codex, Dependabot, GitHub Actions,
release bots, Fossa/Cloudflare bots, and self-hosted runners are classified as
`bot` and receive their own ranking instead of being mixed with people.

The workflow checks out every target with full history, runs the local scanner,
and uploads JSON, CSV, NDJSON and their gzip versions as a persistent Release
and the `contributor-sloc-report` artifact.
Private target repositories require the organization secret
`CROSS_REPO_TOKEN` with read access. Public repositories can use the workflow
token.

Run locally:

```sh
python3 tool/aggregate_local_git.py --sources sources --repos repos.json --output report
```

Behavior checks create isolated Git histories (branches, merge inheritance,
duplicate content, line deletion and renames) and run the production scanner:

```sh
python3 -B -m unittest discover -s tool -p test_git_lineage.py -v
```
