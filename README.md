# Luotopia Contributors

This repository aggregates contributor source-line changes across the selected
ClosedWHU repositories using only local Git history inside GitHub Actions.
The repository itself is excluded from the target list. `Luotopia-i18n` and
`ClosedWHU-glossary` are also excluded; `WHU-Calendar` is included explicitly.

The ranking walks each repository's patches oldest-first. A deletion matches and
cancels one still-live identical addition from the same repository and file.
Both authors disappear from that matched pair. Unmatched deletions remain
negative changes because their original addition is outside the checked-out
history. Results show the remaining `+`, `-`, net change, cancellation count,
and rank by `abs(added - deleted)` in kSLOC.

The workflow checks out every target with full history, runs the local scanner,
and uploads JSON and CSV reports as the `contributor-sloc-report` artifact.
Private target repositories require the organization secret
`CROSS_REPO_TOKEN` with read access. Public repositories can use the workflow
token.

Run locally:

```sh
python3 tool/aggregate_local_git.py --sources sources --repos repos.json --output report
```
