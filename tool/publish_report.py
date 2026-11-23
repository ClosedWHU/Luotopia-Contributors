#!/usr/bin/env python3
"""Publish the report, then retain the newest report releases and workflow runs."""

import gzip
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timedelta, timezone

RETAIN = 10
WORKFLOW = 'contributors.yml'


def gh(*arguments):
    return subprocess.check_output(['gh', *arguments], text=True, encoding='utf-8')


def release_metadata(report, now, run_id):
    local = now.astimezone(timezone(timedelta(hours=8)))
    stamp = local.strftime('%Y-%m-%d %H:%M:%S')
    humans = len(report['human_contributors'])
    bots = len(report['bot_contributors'])
    repositories = report['repo_summary']
    retained = sum(row['add'] for row in repositories)
    introduced = sum(row['introduced_lines'] for row in repositories)
    canceled = introduced - retained
    commits = sum(row['commits'] for row in repositories)
    title = (f'{stamp} UTC+8 | {humans} humans + {bots} bots | '
             f'{retained:,} retained | {introduced:,} introduced | {commits:,} commits')
    notes = f'''## Contributor source-line report

Generated **{stamp} UTC+8** from {len(repositories)} repositories.

| Metric | Total |
| --- | ---: |
| Contributors (human + automation) | {humans + bots:,} |
| Human contributors | {humans:,} |
| Automation identities | {bots:,} |
| Reachable commits across repositories | {commits:,} |
| Source lines retained at recorded HEADs | {retained:,} |
| Source lines introduced over history | {introduced:,} |
| Introduced lines no longer retained | {canceled:,} |

Metric v2 replays Git line provenance. Introduced and canceled lines are
historical occurrence counts, not gross edits or a code-quality score. Canceled
additions and deletions are two attributions of the same removed lines and must
not be added together. See the JSON report for per-repository HEADs and details.

Workflow run: {run_id}. Retention: newest {RETAIN} report releases and workflow runs.
'''
    return f'report-{local:%Y%m%d-%H%M%S}-{run_id}', title, notes


def obsolete_releases(releases):
    reports = sorted(
        (row for row in releases
         if row['tag_name'].startswith('report-') and not row['draft'] and not row['prerelease']),
        key=lambda row: (row['published_at'], row['id']), reverse=True,
    )
    return reports[RETAIN:]


def obsolete_runs(runs, current_run_id):
    ordered = sorted(runs, key=lambda row: (row['created_at'], row['id']), reverse=True)
    # Keep active runs even outside the window; never cancel work to prune logs.
    return [row for row in ordered[RETAIN:]
            if row['status'] == 'completed' and str(row['id']) != str(current_run_id)]


def main():
    repo = os.environ['GITHUB_REPOSITORY']
    run_id = os.environ['GITHUB_RUN_ID']
    report = json.loads(Path('contributor-sloc.json').read_text(encoding='utf-8'))
    tag, title, notes = release_metadata(report, datetime.now(timezone.utc), run_id)
    notes_path = Path('release-notes.md')
    notes_path.write_text(notes, encoding='utf-8')
    assets = [Path(f'contributor-sloc.{extension}') for extension in ('json', 'csv', 'ndjson')]
    for asset in assets.copy():
        compressed = Path(f'{asset}.gz')
        compressed.write_bytes(gzip.compress(asset.read_bytes()))
        assets.append(compressed)
    print(gh('release', 'create', tag, *(str(asset) for asset in assets),
             '--repo', repo, '--title', title, '--notes-file', str(notes_path), '--latest'))
    # All assets must be published successfully before any history is pruned.
    pages = json.loads(gh('api', '--paginate', '--slurp', f'repos/{repo}/releases?per_page=100'))
    releases = [row for page in pages for row in page]
    for release in obsolete_releases(releases):
        print(f"Deleting old report: {release['tag_name']}", flush=True)
        gh('release', 'delete', release['tag_name'], '--repo', repo, '--yes', '--cleanup-tag')
    pages = json.loads(gh('api', '--paginate', '--slurp',
                         f'repos/{repo}/actions/workflows/{WORKFLOW}/runs?per_page=100'))
    runs = [row for page in pages for row in page['workflow_runs']]
    for run in obsolete_runs(runs, run_id):
        print(f"Deleting old workflow run: {run['id']}", flush=True)
        gh('api', '--method', 'DELETE', f"repos/{repo}/actions/runs/{run['id']}")
    print(title)


if __name__ == '__main__':
    main()
