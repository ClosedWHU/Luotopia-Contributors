"""Behavior checks against isolated, real Git repositories."""

import json
import csv
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from aggregate_local_git import author_key, source_path
from git_lineage import scan


class LineageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'sample'
        self.root.mkdir()
        self.git('init', '-b', 'main')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def commit(self, name):
        self.git('add', '-A')
        environment = dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=f'{name}@example.org',
                           GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=f'{name}@example.org')
        subprocess.run(['git', 'commit', '--allow-empty', '-m', name], cwd=self.root,
                       env=environment, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def ledger(self):
        return scan(self.root, source_path, author_key, lambda *args: None)

    def test_repeated_lines_are_distinct_and_removal_has_an_origin(self):
        (self.root / 'a.py').write_text('same\nsame\n', encoding='utf-8')
        self.commit('alice')
        (self.root / 'a.py').write_text('same\n', encoding='utf-8')
        self.commit('bob')
        result = self.ledger()
        self.assertEqual(result['retained_by_author'], {'alice@example.org': 1})
        self.assertEqual(result['canceled_added_by_author'], {'alice@example.org': 1})
        self.assertEqual(result['canceled_deleted_by_author'], {'bob@example.org': 1})

    def test_rename_unicode_and_spaces_keep_authorship(self):
        (self.root / 'old name.py').write_text('one\ntwo\nthree\nfour\n', encoding='utf-8')
        self.commit('alice')
        self.git('mv', 'old name.py', '新 name.py')
        self.commit('mover')
        (self.root / '新 name.py').write_text('one\nthree\nfour\n', encoding='utf-8')
        self.commit('bob')
        result = self.ledger()
        self.assertEqual(result['retained_by_author'], {'alice@example.org': 3})
        self.assertEqual(result['canceled_deleted_by_author'], {'bob@example.org': 1})
        self.assertEqual(result['cumulative_added_by_author'], {'alice@example.org': 4})
        self.assertEqual(result['cumulative_removed_by_author'], {'bob@example.org': 1})
        self.assertEqual(result['cumulative_modified_by_author'], {})

    def test_independent_branch_deletions_cancel_once(self):
        (self.root / 'a.py').write_text('one\ntwo\n', encoding='utf-8')
        self.commit('alice')
        self.git('checkout', '-b', 'side')
        (self.root / 'a.py').write_text('one\n', encoding='utf-8')
        self.commit('side')
        self.git('checkout', 'main')
        (self.root / 'a.py').write_text('one\n', encoding='utf-8')
        self.commit('main')
        self.git('-c', 'user.name=merger', '-c', 'user.email=merger@example.org',
                 'merge', '--no-ff', 'side', '-m', 'merge')
        result = self.ledger()
        self.assertEqual(result['introduced'], 2)
        self.assertEqual(result['cancel'], 1)
        self.assertEqual(result['retained'], 1)
        self.assertEqual(result['canceled_deleted_by_author'], {'main@example.org': 1})
        self.assertEqual(result['cumulative_removed_by_author'], {'main@example.org': 1, 'side@example.org': 1})

    def test_merge_keeping_line_does_not_count_side_branch_deletion(self):
        (self.root / 'a.py').write_text('one\ntwo\n', encoding='utf-8')
        self.commit('alice')
        self.git('checkout', '-b', 'side')
        (self.root / 'a.py').write_text('one\n', encoding='utf-8')
        self.commit('side')
        self.git('checkout', 'main')
        self.commit('main')
        self.git('-c', 'user.name=merger', '-c', 'user.email=merger@example.org',
                 'merge', '-s', 'ours', '--no-ff', 'side', '-m', 'keep main')
        result = self.ledger()
        self.assertEqual(result['retained_by_author'], {'alice@example.org': 2})
        self.assertEqual(result['cancel'], 0)

    def test_merge_does_not_credit_integrator_for_branch_additions(self):
        (self.root / 'base.py').write_text('base\n', encoding='utf-8')
        self.commit('alice')
        self.git('checkout', '-b', 'side')
        (self.root / 'side.py').write_text('side\n', encoding='utf-8')
        self.commit('bob')
        self.git('checkout', 'main')
        (self.root / 'main.py').write_text('main\n', encoding='utf-8')
        self.commit('carol')
        self.git('-c', 'user.name=merger', '-c', 'user.email=merger@example.org',
                 'merge', '--no-ff', 'side', '-m', 'merge')
        result = self.ledger()
        self.assertEqual(result['retained_by_author'],
                         {'alice@example.org': 1, 'bob@example.org': 1, 'carol@example.org': 1})
        self.assertEqual(result['introduced'], 3)
        self.assertEqual(result['cumulative_added_by_author'], {'alice@example.org': 1, 'bob@example.org': 1, 'carol@example.org': 1})

    def test_same_blob_from_independent_branches_has_one_final_owner(self):
        self.commit('root')
        self.git('checkout', '-b', 'side')
        (self.root / 'a.py').write_text('same\n', encoding='utf-8')
        self.commit('bob')
        self.git('checkout', 'main')
        (self.root / 'a.py').write_text('same\n', encoding='utf-8')
        self.commit('alice')
        self.git('-c', 'user.name=merger', '-c', 'user.email=merger@example.org',
                 'merge', '--no-ff', 'side', '-m', 'merge')
        result = self.ledger()
        self.assertEqual(result['retained_by_author'], {'alice@example.org': 1})
        self.assertEqual(result['canceled_added_by_author'], {'bob@example.org': 1})
        self.assertEqual(result['canceled_deleted_by_author'], {'merger@example.org': 1})

    def test_cli_exports_conserved_counts(self):
        (self.root / 'a.py').write_text('one\ntwo\n', encoding='utf-8')
        self.commit('alice')
        (self.root / 'a.py').write_text('one\n', encoding='utf-8')
        self.commit('bob')
        repos = self.base / 'repos.json'
        repos.write_text('["sample"]', encoding='utf-8')
        subprocess.run([sys.executable, str(Path(__file__).with_name('aggregate_local_git.py')),
                        '--sources', str(self.base), '--repos', str(repos),
                        '--output', str(self.base / 'report')], check=True, capture_output=True)
        report = json.loads((self.base / 'report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['metric_version'], 3)
        self.assertEqual(report['contributors'][0]['cumulative_added_lines'], 2)
        self.assertEqual(report['contributors'][1]['cumulative_removed_lines'], 1)
        self.assertEqual(report['repo_summary'][0]['cumulative_removed_lines'], 1)
        self.assertEqual(report['repo_summary'][0]['add'], 1)
        self.assertEqual(report['repo_summary'][0]['delete'], 0)
        self.assertTrue(report['repo_summary'][0]['conservation_verified'])
        self.assertEqual(report['contributors'][0]['added_lines'], 1)
        self.assertEqual(report['contributors'][1]['canceled_deleted_lines'], 1)
        exported = json.loads((self.base / 'report.ndjson').read_text(encoding='utf-8').splitlines()[1])
        self.assertEqual(exported['cumulative_removed_lines'], 1)
        with (self.base / 'report.csv').open(encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]['cumulative_added_lines'], '2')
        self.assertEqual(rows[1]['cumulative_modified_lines'], '0')
        self.assertEqual(rows[1]['cumulative_removed_lines'], '1')

    def test_copy_creates_new_occurrences_without_stealing_original(self):
        (self.root / 'a.py').write_text('one\ntwo\n', encoding='utf-8')
        self.commit('alice')
        (self.root / 'copy.py').write_text('one\ntwo\n', encoding='utf-8')
        self.commit('bob')
        result = self.ledger()
        self.assertEqual(result['retained_by_author'], {'alice@example.org': 2, 'bob@example.org': 2})
        self.assertEqual(result['introduced'], 4)

        self.assertEqual(result['cumulative_added_by_author'], {'alice@example.org': 2, 'bob@example.org': 2})

    def test_conflict_resolution_creates_only_actual_new_lines(self):
        (self.root / 'a.py').write_text('original\n', encoding='utf-8')
        self.commit('alice')
        self.git('checkout', '-b', 'side')
        (self.root / 'a.py').write_text('side\n', encoding='utf-8')
        self.commit('bob')
        self.git('checkout', 'main')
        (self.root / 'a.py').write_text('main\n', encoding='utf-8')
        self.commit('carol')
        merge = subprocess.run(['git', '-c', 'user.name=merger', '-c', 'user.email=merger@example.org',
                                'merge', '--no-commit', 'side'], cwd=self.root, capture_output=True)
        self.assertEqual(merge.returncode, 1)
        (self.root / 'a.py').write_text('resolution\n', encoding='utf-8')
        self.commit('merger')
        result = self.ledger()
        self.assertEqual(result['retained_by_author'], {'merger@example.org': 1})
        self.assertEqual(result['introduced'], 4)
        self.assertEqual(result['cancel'], 3)
        self.assertEqual(result['canceled_added_by_author'],
                         {'alice@example.org': 1, 'bob@example.org': 1, 'carol@example.org': 1})
        self.assertEqual(result['canceled_deleted_by_author'], {'carol@example.org': 1, 'merger@example.org': 2})
        self.assertEqual(result['cumulative_added_by_author'], {'alice@example.org': 1})
        self.assertEqual(result['cumulative_modified_by_author'], {'bob@example.org': 1, 'carol@example.org': 1})

    def test_replacements_preserve_each_authors_historical_work(self):
        (self.root / 'a.py').write_text('original\n', encoding='utf-8')
        self.commit('alice')
        (self.root / 'a.py').write_text('edited\n', encoding='utf-8')
        self.commit('bob')
        (self.root / 'a.py').write_text('final\n', encoding='utf-8')
        self.commit('carol')
        (self.root / 'a.py').unlink()
        self.commit('dave')
        result = self.ledger()
        self.assertEqual(result['cumulative_added_by_author'], {'alice@example.org': 1})
        self.assertEqual(result['cumulative_modified_by_author'], {'bob@example.org': 1, 'carol@example.org': 1})
        self.assertEqual(result['cumulative_removed_by_author'], {'dave@example.org': 1})
        self.assertEqual(result['retained'], 0)

    def test_separate_blocks_are_not_paired_across_unchanged_lines(self):
        (self.root / 'a.py').write_text('remove\nkeep\n', encoding='utf-8')
        self.commit('alice')
        (self.root / 'a.py').write_text('keep\ninsert\n', encoding='utf-8')
        self.commit('bob')
        result = self.ledger()
        self.assertEqual(result['cumulative_added_by_author'], {'alice@example.org': 2, 'bob@example.org': 1})
        self.assertEqual(result['cumulative_removed_by_author'], {'bob@example.org': 1})
        self.assertEqual(sum(result['cumulative_modified_by_author'].values()), 0)

    def test_unequal_replacement_blocks_count_only_the_surplus(self):
        (self.root / 'a.py').write_text('old\n', encoding='utf-8')
        self.commit('alice')
        (self.root / 'a.py').write_text('one\ntwo\nthree\n', encoding='utf-8')
        self.commit('bob')
        (self.root / 'a.py').write_text('new\n', encoding='utf-8')
        self.commit('carol')
        result = self.ledger()
        self.assertEqual(result['cumulative_added_by_author'], {'alice@example.org': 1, 'bob@example.org': 2})
        self.assertEqual(result['cumulative_modified_by_author'], {'bob@example.org': 1, 'carol@example.org': 1})
        self.assertEqual(result['cumulative_removed_by_author'], {'carol@example.org': 2})

    def test_source_scope_and_binary_transitions_are_not_line_replacements(self):
        (self.root / 'a.py').write_text('one\ntwo\n', encoding='utf-8')
        self.commit('alice')
        self.git('mv', 'a.py', 'a.txt')
        self.commit('bob')
        self.git('mv', 'a.txt', 'b.py')
        self.commit('carol')
        (self.root / 'b.py').write_bytes(b'\0binary')
        self.commit('dave')
        (self.root / 'b.py').write_text('text\n', encoding='utf-8')
        self.commit('eve')
        result = self.ledger()
        self.assertEqual(result['cumulative_added_by_author'],
                         {'alice@example.org': 2, 'carol@example.org': 2, 'eve@example.org': 1})
        self.assertEqual(result['cumulative_removed_by_author'], {'bob@example.org': 2, 'dave@example.org': 2})
        self.assertEqual(result['cumulative_modified_by_author'], {})


if __name__ == '__main__':
    unittest.main()
