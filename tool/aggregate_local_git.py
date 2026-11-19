#!/usr/bin/env python3
"""Rank contributors by locally observed source-line diff changes."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
import subprocess
from collections import defaultdict

SOURCE_EXTENSIONS = {
    '.c', '.cc', '.cpp', '.cs', '.dart', '.go', '.h', '.hpp', '.java', '.js',
    '.jsx', '.kt', '.kts', '.m', '.mm', '.php', '.py', '.rb', '.rs', '.sh',
    '.swift', '.ts', '.tsx', '.vue', '.xml', '.yaml', '.yml',
}
EXCLUDED_PARTS = {'.dart_tool', '.git', 'build', 'coverage', 'generated', 'node_modules', 'vendor', 'Pods', 'dist'}
EXCLUDED_NAMES = {'pubspec.lock', 'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml'}
MARKER = re.compile(r'^@@@([0-9a-f]+)\x00([^\x00]*)\x00([^\x00]*)$')


def source_path(path: str) -> bool:
    path = path.replace('\\', '/')
    if set(path.split('/')) & EXCLUDED_PARTS or Path(path).name in EXCLUDED_NAMES:
        return False
    return Path(path).suffix.lower() in SOURCE_EXTENSIONS


def author_key(name: str, email: str) -> tuple[str, str]:
    match = re.search(r'\+([^@]+)@users\.noreply\.github\.com$', email.lower())
    if match:
        return match.group(1), match.group(1)
    label = name.strip() or email.strip() or 'anonymous'
    return email.lower().strip() or label.lower(), label


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--repos', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repositories = json.loads(args.repos.read_text(encoding='utf-8'))
    totals = defaultdict(lambda: {'name': '', 'repositories': set(), 'commits': 0, 'add': 0, 'delete': 0, 'cancel': 0})
    repo_summary = []
    unknowns = []

    for repository in repositories:
        root = args.sources / repository
        if not (root / '.git').exists():
            unknowns.append({'repository': repository, 'reason': 'checkout_missing'})
            continue
        command = ['git', 'log', '--reverse', '--format=@@@%H%x00%an%x00%ae', '--patch', '--find-renames', '--find-copies']
        process = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
        live = defaultdict(list)
        current = None
        add_count = delete_count = cancel_count = commit_count = 0
        for raw in process.stdout:
            line = raw.rstrip('\n')
            marker = MARKER.match(line)
            if marker:
                current = author_key(marker.group(2), marker.group(3))
                totals[current[0]]['name'] = current[1]
                totals[current[0]]['repositories'].add(repository)
                totals[current[0]]['commits'] += 1
                commit_count += 1
                continue
            if line.startswith('diff --git '):
                fields = line.split()
                path = fields[3][2:] if len(fields) >= 4 and fields[3].startswith('b/') else ''
                active = source_path(path)
                continue
            if current is None or not line.startswith(('+++', '---', '+', '-')):
                continue
            if line.startswith('+++ b/'):
                path = line[6:]
                active = source_path(path)
                continue
            if line.startswith('--- a/') or line.startswith('+++ /dev/null') or line.startswith('--- /dev/null'):
                continue
            if 'active' not in locals() or not active or line.startswith(('+++', '---')):
                continue
            text = line[1:]
            key = (path, text)
            if line.startswith('-'):
                if live[key]:
                    previous = live[key].pop()
                    totals[previous]['add'] -= 1
                    totals[previous]['cancel'] += 1
                    totals[current[0]]['cancel'] += 1
                    cancel_count += 1
                else:
                    totals[current[0]]['delete'] += 1
                    delete_count += 1
            elif line.startswith('+'):
                live[key].append(current[0])
                totals[current[0]]['add'] += 1
                add_count += 1
        process.wait()
        repo_summary.append({'repository': repository, 'commits': commit_count, 'add': add_count, 'delete': delete_count, 'cancel': cancel_count})

    result = []
    for key, row in totals.items():
        net = row['add'] - row['delete']
        result.append({'id': key, 'name': row['name'], 'repositories': sorted(row['repositories']), 'commits': row['commits'], 'added_ksloc': round(row['add'] / 1000, 3), 'deleted_ksloc': round(row['delete'] / 1000, 3), 'net_ksloc': round(net / 1000, 3), 'rank_score_ksloc': round(abs(net) / 1000, 3), 'matched_cancellations': row['cancel']})
    result.sort(key=lambda row: (-abs(row['net_ksloc']), -(row['added_ksloc'] + row['deleted_ksloc']), row['name']))
    payload = {'metric': 'local source-line diff cancellation; rank abs(added - deleted)', 'repositories': repositories, 'repo_summary': repo_summary, 'unknowns': unknowns, 'contributors': result}
    args.output.with_suffix('.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    with args.output.with_suffix('.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        fields = ['id', 'name', 'added_ksloc', 'deleted_ksloc', 'net_ksloc', 'rank_score_ksloc', 'commits', 'matched_cancellations', 'repositories']
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in result:
            writer.writerow({field: ','.join(row['repositories']) if field == 'repositories' else row[field] for field in fields})
    print(json.dumps({'contributors': len(result), 'unknowns': len(unknowns), 'json': str(args.output.with_suffix('.json')), 'csv': str(args.output.with_suffix('.csv'))}, ensure_ascii=False))
    for index, row in enumerate(result, 1):
        print(f"{index}. {row['name']} +{row['added_ksloc']}k -{row['deleted_ksloc']}k net={row['net_ksloc']}k cancel={row['matched_cancellations']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
