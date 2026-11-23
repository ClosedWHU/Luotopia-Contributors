#!/usr/bin/env python3
"""Rank contributors by locally observed source-line diff changes."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
from collections import defaultdict
from datetime import datetime
from git_lineage import scan

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
CUMULATIVE_FIELDS = ('cumulative_added_lines', 'cumulative_modified_lines', 'cumulative_removed_lines')

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
    '2024302072042@whu.edu.cn': 'yangqk12@whu.edu.cn',
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
    'misa.subanxia@icloud.com': 'misakayuuki',
    'yangqk12@whu.edu.cn': 'laplaceyoung',
}

CANONICAL_REASONS = {
    'i@0x535a.cn': 'user-confirmed replacement for the blocked stephen-zeng account; the separate stephenzeng account is not merged',
    'yangqk12@whu.edu.cn': 'user-confirmed CauchYoung and laplace young are the same contributor, using LaplaceYoung',
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
    totals = defaultdict(lambda: {'name': '', 'repositories': set(), 'commits': 0, 'add': 0, 'delete': 0, 'cancel': 0, 'canceled_added': 0, 'canceled_deleted': 0})
    repo_summary = []
    unknowns = []

    for repository in repositories:
        root = args.sources / repository
        if not (root / '.git').exists():
            unknowns.append({'repository': repository, 'reason': 'checkout_missing'})
            continue
        def observe_author(author, name, timestamp, commit):
            row = totals[author]
            row['name'] = name
            row['repositories'].add(repository)
            row['commits'] += 1
            if not row.get('first_commit') or timestamp_key(timestamp) < timestamp_key(row['first_commit']):
                row['first_commit'] = timestamp
            if not row.get('last_commit') or timestamp_key(timestamp) > timestamp_key(row['last_commit']):
                row['last_commit'] = timestamp

        ledger = scan(root, source_path, author_key, observe_author)
        for field, ledger_key in zip(CUMULATIVE_FIELDS, (
                'cumulative_added_by_author', 'cumulative_modified_by_author', 'cumulative_removed_by_author')):
            for author, count in ledger[ledger_key].items():
                totals[author][field] = totals[author].get(field, 0) + count
        for author, count in ledger['retained_by_author'].items():
            totals[author]['add'] += count
        for author, count in ledger['canceled_added_by_author'].items():
            totals[author]['canceled_added'] += count
            totals[author]['cancel'] += count
        for author, count in ledger['canceled_deleted_by_author'].items():
            totals[author]['canceled_deleted'] += count
            totals[author]['cancel'] += count
        repo_summary.append({
            'repository': repository, 'head': ledger['head'], 'commits': ledger['commits'],
            'source_files': ledger['files'], 'introduced_lines': ledger['introduced'],
            'add': ledger['retained'], 'delete': 0, 'cancel': ledger['cancel'],
            'excluded_binary_paths': ledger['excluded_binary_paths'],
            'conservation_verified': True,
            **{field: sum(ledger[key].values()) for field, key in zip(CUMULATIVE_FIELDS, (
                'cumulative_added_by_author', 'cumulative_modified_by_author', 'cumulative_removed_by_author'))},
        })

    merged = defaultdict(lambda: {'name': '', 'repositories': set(), 'commits': 0, 'add': 0, 'delete': 0, 'cancel': 0, 'canceled_added': 0, 'canceled_deleted': 0, 'raw_ids': set()})
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
        target['canceled_added'] += row['canceled_added']
        target['canceled_deleted'] += row['canceled_deleted']
        for field in CUMULATIVE_FIELDS:
            target[field] = target.get(field, 0) + row.get(field, 0)
        target.setdefault('first_commit', row.get('first_commit'))
        if row.get('first_commit') and (not target.get('first_commit') or timestamp_key(row['first_commit']) < timestamp_key(target['first_commit'])):
            target['first_commit'] = row['first_commit']
        if row.get('last_commit') and (not target.get('last_commit') or timestamp_key(row['last_commit']) > timestamp_key(target['last_commit'])):
            target['last_commit'] = row['last_commit']

    result = []
    identity_aliases = []
    for key, row in merged.items():
        net = row['add'] - row['delete']
        result.append({'id': key, 'name': row['name'], 'type': 'bot' if is_bot(row['name'], key) else 'human', 'repositories': sorted(row['repositories']), 'commits': row['commits'], 'first_commit': row.get('first_commit'), 'last_commit': row.get('last_commit'), 'added_lines': row['add'], 'deleted_lines': row['delete'], 'net_lines': net, 'added_ksloc': round(row['add'] / 1000, 3), 'deleted_ksloc': round(row['delete'] / 1000, 3), 'net_ksloc': round(net / 1000, 3), 'rank_score_ksloc': round(abs(net) / 1000, 3), 'matched_cancellations': row['cancel'], 'canceled_added_lines': row['canceled_added'], 'canceled_deleted_lines': row['canceled_deleted']})
        result[-1].update({field: row[field] for field in CUMULATIVE_FIELDS})
        if len(row['raw_ids']) > 1:
            members = [
                {
                    'id': member,
                    'name': totals[member]['name'],
                    'commits': totals[member]['commits'],
                    'repositories': sorted(totals[member]['repositories']),
                    'first_commit': totals[member].get('first_commit'),
                    'last_commit': totals[member].get('last_commit'),
                }
                for member in sorted(row['raw_ids'])
            ]
            largest_member = max(members, key=lambda member: (member['commits'], member['last_commit'] or ''))
            latest_member = max(members, key=lambda member: timestamp_key(member['last_commit']))
            identity_aliases.append({
                'canonical_id': key,
                'canonical_name': row['name'],
                'largest_member': largest_member['id'],
                'latest_member': latest_member['id'],
                'members': members,
                'reason': CANONICAL_REASONS.get(key, 'same author identity by explicit name/email alias evidence; canonical record has the larger observed history'),
            })
    result.sort(key=lambda row: (row['type'], -row['added_lines'], -row['commits'], row['name']))
    identity_aliases.sort(key=lambda row: row['canonical_name'].lower())
    if unknowns:
        raise RuntimeError(f'Incomplete source checkouts: {unknowns}')
    payload = {
        'metric_version': 3,
        'metric': 'Cumulative non-merge line changes and Git commit-DAG provenance; rank source lines retained at HEAD',
        'method': {
            'merge_provenance': 'first-parent matching lines, then other parents; only new lines belong to merger',
            'merge_deletions': 'inherit parent removal; first parent wins independent-remover ties',
            'copies': 'new line occurrences belong to the introducing commit',
            'scope': 'eligible text source paths; exact Git histogram diff alignment with rename detection',
            'deleted_lines': 'zero: every final removal has an observed origin; no unmatched-deletion score',
            'limitations': ['Git-unrecognized moves and copies count as new occurrences',
                            'Repeated identical lines follow Git diff alignment; semantic equivalence is not inferred',
                            'Canceled lines are absent at HEAD, not necessarily invalid code'],
        },
        'repositories': repositories, 'repo_summary': repo_summary, 'unknowns': unknowns,
        'identity_aliases': identity_aliases, 'contributors': result,
        'human_contributors': [row for row in result if row['type'] == 'human'],
        'bot_contributors': [row for row in result if row['type'] == 'bot'],
    }
    payload['method']['cumulative_changes'] = {
        'scope': 'all reachable non-merge commits, including root commits; merge-only resolutions excluded',
        'pairing': 'per zero-context histogram diff block: modified=min(old,new); added=new-modified; removed=old-modified',
        'renames': 'Git-detected renames preserve alignment; copies and source-scope entry create additions',
        'ranking': 'unchanged: source lines retained at HEAD, not summed cumulative activity',
        'limitations': 'Replacement pairing is a syntactic estimate, not semantic line identity; repeated edits count repeatedly',
    }
    args.output.with_suffix('.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    with args.output.with_suffix('.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        fields = ['type', 'id', 'name', *CUMULATIVE_FIELDS, 'added_lines', 'deleted_lines', 'net_lines', 'added_ksloc', 'deleted_ksloc', 'net_ksloc', 'rank_score_ksloc', 'commits', 'matched_cancellations', 'canceled_added_lines', 'canceled_deleted_lines', 'repositories']
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
