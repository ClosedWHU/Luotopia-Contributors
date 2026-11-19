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
from datetime import datetime

SOURCE_EXTENSIONS = {
    '.c', '.cc', '.cpp', '.cs', '.dart', '.go', '.h', '.hpp', '.java', '.js',
    '.jsx', '.kt', '.kts', '.m', '.mm', '.php', '.py', '.rb', '.rs', '.sh',
    '.swift', '.ts', '.tsx', '.vue', '.xml', '.yaml', '.yml',
}
EXCLUDED_PARTS = {'.dart_tool', '.git', 'build', 'coverage', 'generated', 'node_modules', 'vendor', 'Pods', 'dist'}
EXCLUDED_NAMES = {'pubspec.lock', 'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml'}
EXCLUDED_REPOSITORIES = {
    'Luotopia-Certificates', 'Luotopia-Dist', 'Luotopia-Docs',
    'Luotopia-Skills', 'homebrew-luotopia',
}
BOT_MARKERS = ('bot', 'dependabot', 'renovate', 'claude', 'codex', 'fossabot', 'cloudflare', 'runner@', 'github-actions')
MARKER = re.compile(r'^@@@([0-9a-f]+)\x00([^\x00]*)\x00([^\x00]*)\x00([^\x00]*)$')

# These are author identities observed in the repository histories. The merge is deliberately
# explicit: similar names alone are not enough to prove that two people are the same person.
IDENTITY_ALIASES = {
    'github-actions[bot]@users.noreply.github.com': 'github-actions[bot]',
    'hsukqilee@users.noreply.github.com': 'team@tsinbei.com',
    'a@0x535a.cn': 'i@0x535a.cn',
    'enchograph': 'ender0213@outlook.com',
    'gugumur': '2221533105@qq.com',
    'johnson-tang2019': 'johnson-tang2019@outlook.com',
    'johnson@johnsons-mac-pro.local': 'johnson-tang2019@outlook.com',
    'littleweak_duck@littledemacbook-pro.local': 'yinjiab@gmail.com',
    'supertjz123@foxmail.com': 'fqwqf@fqwqf.xyz',
}

CANONICAL_NAMES = {
    'github-actions[bot]': 'github-actions[bot]',
    'team@tsinbei.com': 'Hsukqi Lee',
    'i@0x535a.cn': '0x535A',
    'ender0213@outlook.com': 'Enchograph',
    '2221533105@qq.com': 'GuGuMur',
    'johnson-tang2019@outlook.com': 'Johnson-Tang2019',
    'yinjiab@gmail.com': 'Little_WeakDuck',
    'fqwqf@fqwqf.xyz': 'fQwQf',
}

CANONICAL_REASONS = {
    'i@0x535a.cn': 'user-confirmed replacement for the blocked Stephen Zeng account; 0x535A is the current account',
}


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


def is_bot(name: str, email: str) -> bool:
    return any(marker in f'{name} {email}'.lower() for marker in BOT_MARKERS)


def canonical_identity(identity: str) -> str:
    return IDENTITY_ALIASES.get(identity, identity)


def timestamp_key(value: str | None) -> datetime:
    return datetime.fromisoformat(value.replace('Z', '+00:00')) if value else datetime.min.replace(tzinfo=None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--repos', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repositories = [
        repository for repository in json.loads(args.repos.read_text(encoding='utf-8'))
        if repository not in EXCLUDED_REPOSITORIES
    ]
    totals = defaultdict(lambda: {'name': '', 'repositories': set(), 'commits': 0, 'add': 0, 'delete': 0, 'cancel': 0})
    repo_summary = []
    unknowns = []

    for repository in repositories:
        root = args.sources / repository
        if not (root / '.git').exists():
            unknowns.append({'repository': repository, 'reason': 'checkout_missing'})
            continue
        command = ['git', 'log', '--reverse', '--format=@@@%H%x00%an%x00%ae%x00%aI', '--patch', '--find-renames', '--find-copies']
        process = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
        live = defaultdict(list)
        current = None
        add_count = delete_count = cancel_count = commit_count = 0
        for raw in process.stdout:
            line = raw.rstrip('\n')
            marker = MARKER.match(line)
            if marker:
                current = author_key(marker.group(2), marker.group(3))
                current_is_bot = is_bot(marker.group(2), marker.group(3))
                totals[current[0]]['name'] = current[1]
                totals[current[0]].setdefault('first_commit', marker.group(4))
                totals[current[0]]['last_commit'] = marker.group(4)
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
        if process.returncode != 0:
            error = process.stderr.read().strip()
            raise RuntimeError(f'git log failed for {repository}: {error or "unknown git error"}')
        if commit_count == 0:
            raise RuntimeError(f'git log returned no commits for {repository}')
        repo_summary.append({'repository': repository, 'commits': commit_count, 'add': add_count, 'delete': delete_count, 'cancel': cancel_count})

    merged = defaultdict(lambda: {'name': '', 'repositories': set(), 'commits': 0, 'add': 0, 'delete': 0, 'cancel': 0, 'raw_ids': set()})
    for key, row in totals.items():
        canonical = canonical_identity(key)
        target = merged[canonical]
        target['raw_ids'].add(key)
        target['name'] = CANONICAL_NAMES.get(canonical, target['name'] or row['name'])
        target['repositories'].update(row['repositories'])
        target['commits'] += row['commits']
        target['add'] += row['add']
        target['delete'] += row['delete']
        target['cancel'] += row['cancel']
        target.setdefault('first_commit', row.get('first_commit'))
        if row.get('first_commit') and (not target.get('first_commit') or timestamp_key(row['first_commit']) < timestamp_key(target['first_commit'])):
            target['first_commit'] = row['first_commit']
        if row.get('last_commit') and (not target.get('last_commit') or timestamp_key(row['last_commit']) > timestamp_key(target['last_commit'])):
            target['last_commit'] = row['last_commit']

    result = []
    identity_aliases = []
    for key, row in merged.items():
        net = row['add'] - row['delete']
        result.append({'id': key, 'name': row['name'], 'type': 'bot' if is_bot(row['name'], key) else 'human', 'repositories': sorted(row['repositories']), 'commits': row['commits'], 'first_commit': row.get('first_commit'), 'last_commit': row.get('last_commit'), 'added_ksloc': round(row['add'] / 1000, 3), 'deleted_ksloc': round(row['delete'] / 1000, 3), 'net_ksloc': round(net / 1000, 3), 'rank_score_ksloc': round(abs(net) / 1000, 3), 'matched_cancellations': row['cancel']})
        if len(row['raw_ids']) > 1:
            identity_aliases.append({
                'canonical_id': key,
                'canonical_name': row['name'],
                'members': [
                    {
                        'id': member,
                        'name': totals[member]['name'],
                        'commits': totals[member]['commits'],
                        'repositories': sorted(totals[member]['repositories']),
                        'first_commit': totals[member].get('first_commit'),
                        'last_commit': totals[member].get('last_commit'),
                    }
                    for member in sorted(row['raw_ids'])
                ],
                'reason': CANONICAL_REASONS.get(key, 'same author identity by explicit name/email alias evidence; canonical record has the larger observed history'),
            })
    result.sort(key=lambda row: (row['type'], -abs(row['net_ksloc']), -(row['added_ksloc'] + row['deleted_ksloc']), row['name']))
    identity_aliases.sort(key=lambda row: row['canonical_name'].lower())
    payload = {'metric': 'local source-line diff cancellation; rank abs(added - deleted)', 'repositories': repositories, 'repo_summary': repo_summary, 'unknowns': unknowns, 'identity_aliases': identity_aliases, 'contributors': result, 'human_contributors': [row for row in result if row['type'] == 'human'], 'bot_contributors': [row for row in result if row['type'] == 'bot']}
    args.output.with_suffix('.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    with args.output.with_suffix('.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        fields = ['type', 'id', 'name', 'added_ksloc', 'deleted_ksloc', 'net_ksloc', 'rank_score_ksloc', 'commits', 'matched_cancellations', 'repositories']
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in result:
            writer.writerow({field: ','.join(row['repositories']) if field == 'repositories' else row[field] for field in fields})
    with args.output.with_suffix('.ndjson').open('w', encoding='utf-8') as handle:
        for row in result:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')
    print(json.dumps({'contributors': len(result), 'unknowns': len(unknowns), 'json': str(args.output.with_suffix('.json')), 'csv': str(args.output.with_suffix('.csv'))}, ensure_ascii=False))
    for index, row in enumerate(result, 1):
        print(f"{index}. [{row['type']}] {row['name']} +{row['added_ksloc']}k -{row['deleted_ksloc']}k net={row['net_ksloc']}k cancel={row['matched_cancellations']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
