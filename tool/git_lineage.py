"""Replay source-line provenance on the commit DAG, using Git's own diff alignment."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
import re
import subprocess


HUNK = re.compile(rb'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', re.M)
REGULAR_MODES = {b'100644', b'100755'}


@dataclass(frozen=True)
class File:
    blob: bytes
    tokens: tuple[int, ...]


@dataclass
class Snapshot:
    files: dict[bytes, File]
    removed: dict[int, str]


@dataclass(frozen=True)
class Change:
    old_path: bytes
    new_path: bytes
    blob: bytes
    mode: bytes
    status: bytes


def git(root, *arguments: str) -> bytes:
    result = subprocess.run(
        ['git', '-c', 'core.quotePath=false', *arguments], cwd=root,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise RuntimeError(f'Git failed in {root}: {result.stderr.decode("utf-8", "replace")}')
    return result.stdout


def changes(root, commit: str, parent: str | None) -> list[Change]:
    arguments = ['diff-tree', '--no-commit-id', '-r', '--raw', '-z', '--no-abbrev', '--find-renames']
    arguments += [parent, commit] if parent else ['--root', commit]
    fields = iter(git(root, *arguments).split(b'\0'))
    result = []
    for header in fields:
        if not header:
            continue
        old_mode, mode, old_blob, blob, status = header.split()
        old_path = next(fields)
        new_path = next(fields) if status.startswith((b'R', b'C')) else old_path
        result.append(Change(old_path, new_path, blob, mode, status))
    return result


def scan(root, source_path, author_key, observe_author):
    """Return one conserved ledger for HEAD. Parent order resolves merge ambiguities.

    Each token has one creator. Each token absent at HEAD has exactly one remover.
    A merge inherits an existing removal from its parents (first parent wins a tie),
    or credits the merge author if the merge itself discards a still-live line.
    """
    if git(root, 'rev-parse', '--is-shallow-repository').strip() != b'false':
        raise RuntimeError(f'{root}: complete history is required')
    head = git(root, 'rev-parse', 'HEAD').decode().strip()
    commits = []
    for row in git(root, 'log', '--reverse', '--topo-order', '--format=%H%x00%P%x00%an%x00%ae%x00%aI', head).splitlines():
        commit, parents, name, email, timestamp = row.decode('utf-8', 'replace').split('\0')
        commits.append((commit, parents.split(), name, email, timestamp))
    references = Counter(parent for _, parents, *_ in commits for parent in parents)
    references[head] += 1
    snapshots = {}
    owners = []
    binary_paths = set()
    batch = subprocess.Popen(
        ['git', 'cat-file', '--batch'], cwd=root,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    )

    @lru_cache(maxsize=128)
    def lines(blob):
        batch.stdin.write(blob + b'\n')
        batch.stdin.flush()
        header = batch.stdout.readline().split()
        if len(header) != 3 or header[1] != b'blob':
            raise RuntimeError(f'{root}: missing blob {blob!r}')
        size = int(header[2])
        data = batch.stdout.read(size)
        terminator = batch.stdout.read(1)
        if len(data) != size or terminator != b'\n':
            raise RuntimeError(f'{root}: truncated blob {blob!r}')
        if b'\0' in data:
            return None
        if not data:
            return ()
        result = data.split(b'\n')
        if data.endswith(b'\n'):
            result.pop()
        return tuple(result)

    def align(previous: File, blob: bytes, count: int):
        if previous.blob == blob:
            return previous.tokens
        patch = git(root, 'diff', '--no-ext-diff', '--no-textconv', '--no-color',
                    '--unified=0', '--diff-algorithm=histogram',
                    previous.blob.decode(), blob.decode())
        result = [None] * count
        old_cursor = new_cursor = 0
        for match in HUNK.finditer(patch):
            old_start, old_size, new_start, new_size = match.groups()
            old_size = int(old_size) if old_size is not None else 1
            new_size = int(new_size) if new_size is not None else 1
            old_start = int(old_start) - (1 if old_size else 0)
            new_start = int(new_start) - (1 if new_size else 0)
            if old_start - old_cursor != new_start - new_cursor:
                raise RuntimeError(f'{root}: inconsistent Git diff alignment')
            result[new_cursor:new_start] = previous.tokens[old_cursor:old_start]
            old_cursor = old_start + old_size
            new_cursor = new_start + new_size
        if len(previous.tokens) - old_cursor != count - new_cursor:
            raise RuntimeError(f'{root}: incomplete Git diff alignment')
        result[new_cursor:] = previous.tokens[old_cursor:]
        return result

    try:
        for commit, parents, name, email, timestamp in commits:
            author, label = author_key(name, email)
            observe_author(author, label, timestamp, commit)
            parent_states = [snapshots[parent] for parent in parents]
            deltas = [changes(root, commit, parent) for parent in parents] if parents else [changes(root, commit, None)]
            files = dict(parent_states[0].files) if parents else {}
            removed = dict(parent_states[0].removed) if parents else {}
            for state in parent_states[1:]:
                for token, remover in state.removed.items():
                    removed.setdefault(token, remover)
            # The first-parent diff defines the result tree. Other parent diffs
            # provide rename and provenance candidates, never another tree.
            specs = {path: file.blob for path, file in files.items()}
            for delta in deltas[0]:
                if delta.status.startswith((b'D', b'R')):
                    specs.pop(delta.old_path, None)
                if delta.mode in REGULAR_MODES and source_path(delta.new_path.decode('utf-8', 'surrogateescape')):
                    specs[delta.new_path] = delta.blob
                else:
                    specs.pop(delta.new_path, None)

            touched = {path for delta in deltas for item in delta for path in (item.old_path, item.new_path)}
            for state in parent_states[1:]:
                touched.update(path for path, file in state.files.items()
                               if file != files.get(path))
            old_tokens = set()
            for state in parent_states:
                for path in touched:
                    previous = state.files.get(path)
                    if previous:
                        old_tokens.update(previous.tokens)
            for path in touched:
                files.pop(path, None)
            used = {token for file in files.values() for token in file.tokens} if len(parents) > 1 else set()
            for path in sorted(touched):
                blob = specs.get(path)
                if blob is None:
                    continue
                content = lines(blob)
                if content is None:
                    binary_paths.add(path)
                    continue
                provenance = [None] * len(content)
                for index, state in enumerate(parent_states):
                    previous_path = next((item.old_path for item in deltas[index]
                                          if item.status.startswith(b'R') and item.new_path == path), path)
                    previous = state.files.get(previous_path)
                    if previous is None:
                        continue
                    for number, token in enumerate(align(previous, blob, len(content))):
                        if provenance[number] is None and token is not None:
                            provenance[number] = token
                for number, token in enumerate(provenance):
                    # Divergent renames can retain the same origin at two paths.
                    # The extra occurrence is a copy introduced by this commit.
                    if token is None or token in used:
                        token = len(owners)
                        owners.append(author)
                        provenance[number] = token
                    used.add(token)
                files[path] = File(blob, tuple(provenance))
            for token in old_tokens - used:
                removed.setdefault(token, author)
            # A line kept by either side of a merge is live even if the other
            # parent deleted it. It must not remain in the final removal ledger.
            if len(parents) > 1:
                for token in used:
                    removed.pop(token, None)
            snapshots[commit] = Snapshot(files, removed)
            for parent in parents:
                references[parent] -= 1
                if not references[parent]:
                    del snapshots[parent]

        final = snapshots[head]
        # Independently enumerate HEAD's actual text blobs: conservation alone
        # must not hide a parser that silently omitted an eligible final file.
        expected = {}
        for entry in git(root, 'ls-tree', '-r', '-z', head).split(b'\0'):
            if not entry:
                continue
            metadata, path = entry.split(b'\t', 1)
            mode, kind, blob = metadata.split()
            if mode not in REGULAR_MODES or not source_path(path.decode('utf-8', 'surrogateescape')):
                continue
            content = lines(blob)
            if content is not None:
                expected[path] = (blob, len(content))
        observed = {path: (file.blob, len(file.tokens)) for path, file in final.files.items()}
        if observed != expected:
            raise RuntimeError(f'{root}: reconstructed files differ from the actual HEAD tree')
        alive = [token for file in final.files.values() for token in file.tokens]
        if len(alive) != len(set(alive)):
            raise RuntimeError(f'{root}: a final line has duplicate provenance')
        if set(alive) & final.removed.keys() or len(alive) + len(final.removed) != len(owners):
            raise RuntimeError(f'{root}: source-line conservation failed')
        retained = Counter(owners[token] for token in alive)
        canceled_added = Counter(owners[token] for token in final.removed)
        canceled_deleted = Counter(final.removed.values())
        return {
            'head': head, 'commits': len(commits), 'files': len(final.files),
            'introduced': len(owners), 'retained': len(alive), 'cancel': len(final.removed),
            'retained_by_author': retained, 'canceled_added_by_author': canceled_added,
            'canceled_deleted_by_author': canceled_deleted,
            'excluded_binary_paths': sorted(path.decode('utf-8', 'replace') for path in binary_paths),
        }
    finally:
        batch.stdin.close()
        batch.stdout.close()
        batch.wait()
