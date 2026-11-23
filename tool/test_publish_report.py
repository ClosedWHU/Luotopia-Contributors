"""Publishing metadata and destructive-retention boundaries."""

from datetime import datetime, timezone
import unittest

from publish_report import obsolete_releases, obsolete_runs, release_metadata


def release(identifier, date='2026-11-23T00:00:00Z', **changes):
    return dict(id=identifier, tag_name=f'report-{identifier}', published_at=date,
                draft=False, prerelease=False, **changes)


def run(identifier, status='completed'):
    return dict(id=identifier, created_at='2026-11-23T00:00:00Z', status=status)


class PublishingTests(unittest.TestCase):
    def test_summary_uses_local_time_and_counts_removed_lines_once(self):
        report = {
            'human_contributors': [{}, {}], 'bot_contributors': [{}],
            'repo_summary': [
                {'add': 1200, 'introduced_lines': 1500, 'commits': 10,
                 'cumulative_added_lines': 1300, 'cumulative_modified_lines': 200, 'cumulative_removed_lines': 100},
                {'add': 300, 'introduced_lines': 400, 'commits': 2,
                 'cumulative_added_lines': 350, 'cumulative_modified_lines': 50, 'cumulative_removed_lines': 20},
            ],
        }
        tag, title, notes = release_metadata(
            report, datetime(2026, 11, 22, 22, 17, 30, tzinfo=timezone.utc), '123')
        self.assertEqual(tag, 'report-20261123-061730-123')
        self.assertEqual(title, '2026-11-23 06:17:30 UTC+8 | 2 humans + 1 bots | '
                         '+1,650 ~250 -120 SLOC | 1,500 retained')
        self.assertIn('| Introduced lines no longer retained | 400 |', notes)
        self.assertIn('| Contributors (human + automation) | 3 |', notes)

    def test_release_cleanup_orders_all_pages_and_preserves_unrelated_assets(self):
        rows = [release(1), release(11), release(10), release(9), release(8),
                release(7), release(6), release(5), release(4), release(3), release(2),
                dict(release(12), tag_name='manual-backup'),
                dict(release(13), draft=True), dict(release(14), prerelease=True)]
        self.assertEqual(obsolete_releases(rows), [release(1)])

    def test_exactly_ten_releases_does_not_delete_any(self):
        self.assertEqual(obsolete_releases([
            release(10), release(9), release(8), release(7), release(6),
            release(5), release(4), release(3), release(2), release(1),
        ]), [])

    def test_runs_include_failures_but_never_delete_running_or_current_work(self):
        rows = [run(10), run(9), run(8), run(7), run(6), run(5), run(4), run(3),
                run(2), run(1), run(0, 'in_progress'), run(-1), run(-2)]
        self.assertEqual(obsolete_runs(rows, -2), [run(-1)])


if __name__ == '__main__':
    unittest.main()
