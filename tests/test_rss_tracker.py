import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from feedparser import FeedParserDict

from rss_tracker import RSSFeedTracker


class RSSFeedTrackerTests(unittest.TestCase):
    def make_tracker(self, seen_items=None):
        tracker = RSSFeedTracker.__new__(RSSFeedTracker)
        tracker.seen_items = seen_items or {}
        tracker.wallabag = Mock()
        tracker.fetch_feed = Mock(return_value=[])
        tracker.save_seen_items = Mock(return_value=True)
        return tracker

    def test_normalize_item_url_resolves_relative_url_and_removes_fragment(self):
        tracker = self.make_tracker()

        normalized = tracker.normalize_item_url(
            'https://example.com/feed.xml',
            '/article/#feed-fragment',
        )

        self.assertEqual('https://example.com/article/', normalized)

    def test_fragment_variants_have_the_same_hash(self):
        tracker = self.make_tracker()
        feed_url = 'https://example.com/feed.xml'

        with_fragment = tracker.get_item_hash(feed_url, 'https://example.com/article/#atom')
        without_fragment = tracker.get_item_hash(feed_url, 'https://example.com/article/')

        self.assertEqual(with_fragment, without_fragment)

    def test_normalize_seen_items_rekeys_and_collapses_fragment_variants(self):
        tracker = self.make_tracker()
        feed_url = 'https://example.com/feed.xml'
        old_url = 'https://example.com/article/#atom'
        new_url = 'https://example.com/article/'
        old_hash = hashlib.sha256(f'{feed_url}:{old_url}'.encode()).hexdigest()
        new_hash = hashlib.sha256(f'{feed_url}:{new_url}'.encode()).hexdigest()
        seen_items = {
            feed_url: {
                old_hash: {'url': old_url, 'title': 'Old title', 'seen_at': '2026-08-01T00:00:00'},
                new_hash: {'url': new_url, 'title': 'New title', 'seen_at': '2026-08-02T00:00:00'},
            }
        }

        normalized, migration_count, collision_count = tracker.normalize_seen_items(seen_items)

        self.assertEqual(1, len(normalized[feed_url]))
        self.assertEqual(new_url, normalized[feed_url][new_hash]['url'])
        self.assertEqual('New title', normalized[feed_url][new_hash]['title'])
        self.assertEqual(1, migration_count)
        self.assertEqual(1, collision_count)

    def test_process_feed_does_not_repost_fragment_variant(self):
        tracker = self.make_tracker()
        feed_url = 'https://example.com/feed.xml'
        canonical_url = 'https://example.com/article/'
        item_hash = tracker.get_item_hash(feed_url, canonical_url)
        tracker.seen_items = {
            feed_url: {
                item_hash: {'url': canonical_url, 'title': 'Article', 'seen_at': '2026-08-01T00:00:00'}
            }
        }
        tracker.fetch_feed.return_value = [
            FeedParserDict(title='Article', link=f'{canonical_url}#atom')
        ]

        tracker.process_feed({'name': 'Example', 'url': feed_url})

        tracker.wallabag.create_entry.assert_not_called()
        tracker.save_seen_items.assert_not_called()

    def test_process_feed_records_and_saves_only_after_success(self):
        tracker = self.make_tracker()
        feed_url = 'https://example.com/feed.xml'
        tracker.fetch_feed.return_value = [
            FeedParserDict(title='Article', link='https://example.com/article/#atom')
        ]
        tracker.wallabag.create_entry.return_value = {'id': 42}

        tracker.process_feed({'name': 'Example', 'url': feed_url})

        expected_url = 'https://example.com/article/'
        expected_hash = tracker.get_item_hash(feed_url, expected_url)
        tracker.wallabag.create_entry.assert_called_once_with(
            expected_url,
            title='Article',
            tags=None,
            published_at=None,
        )
        self.assertEqual(expected_url, tracker.seen_items[feed_url][expected_hash]['url'])
        tracker.save_seen_items.assert_called_once_with()

    def test_process_feed_does_not_record_failed_post(self):
        tracker = self.make_tracker()
        feed_url = 'https://example.com/feed.xml'
        tracker.fetch_feed.return_value = [
            FeedParserDict(title='Article', link='https://example.com/article/')
        ]
        tracker.wallabag.create_entry.return_value = None

        tracker.process_feed({'name': 'Example', 'url': feed_url})

        self.assertNotIn(feed_url, tracker.seen_items)
        tracker.save_seen_items.assert_not_called()

    def test_save_seen_items_replaces_file_without_leaving_temporary_files(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            tracker = RSSFeedTracker.__new__(RSSFeedTracker)
            tracker.seen_file = Path(temporary_directory) / 'seen_items.json'
            tracker.seen_file.write_text('{}')
            tracker.seen_file.chmod(0o640)
            tracker.seen_items = {'feed': {'hash': {'url': 'https://example.com/article/'}}}

            self.assertTrue(tracker.save_seen_items())

            with tracker.seen_file.open() as f:
                self.assertEqual(tracker.seen_items, json.load(f))
            self.assertEqual(0o640, os.stat(tracker.seen_file).st_mode & 0o777)
            self.assertEqual(['seen_items.json'], sorted(path.name for path in Path(temporary_directory).iterdir()))


if __name__ == '__main__':
    unittest.main()
