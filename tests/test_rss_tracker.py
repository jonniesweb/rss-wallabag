import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from feedparser import FeedParserDict

from rss_tracker import RSSFeedTracker
from storage import Feed, Storage


class RSSFeedTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_file = Path(self.temporary_directory.name) / "tracker.db"
        self.storage = Storage(database_file)
        self.feed_id = self.storage.add_feed(
            "Example",
            "https://example.com/feed.xml",
            max_items=10,
        )
        self.wallabag = Mock()
        self.tracker = RSSFeedTracker(
            self.storage,
            wallabag=self.wallabag,
            install_signal_handlers=False,
        )
        self.tracker.fetch_feed = Mock(return_value=[])

    def tearDown(self) -> None:
        self.storage.close()
        self.temporary_directory.cleanup()

    def feed(self) -> Feed:
        return self.storage.list_feeds()[0]

    def test_normalize_item_url_resolves_relative_url_and_removes_fragment(
        self,
    ) -> None:
        normalized = self.tracker.normalize_item_url(
            "https://example.com/feed.xml",
            "/article/#feed-fragment",
        )

        self.assertEqual("https://example.com/article/", normalized)

    def test_fragment_variants_have_the_same_hash(self) -> None:
        feed_url = "https://example.com/feed.xml"

        with_fragment = self.tracker.get_item_hash(
            feed_url,
            "https://example.com/article/#atom",
        )
        without_fragment = self.tracker.get_item_hash(
            feed_url,
            "https://example.com/article/",
        )

        self.assertEqual(with_fragment, without_fragment)

    def test_process_feed_does_not_repost_fragment_variant(self) -> None:
        feed_url = "https://example.com/feed.xml"
        canonical_url = "https://example.com/article/"
        item_hash = self.tracker.get_item_hash(feed_url, canonical_url)
        self.storage.record_seen(self.feed_id, item_hash, canonical_url, "Article")
        self.tracker.fetch_feed.return_value = [
            FeedParserDict(title="Article", link=f"{canonical_url}#atom")
        ]

        self.tracker.process_feed(self.feed())

        self.wallabag.create_entry.assert_not_called()
        self.assertEqual(1, self.storage.count_seen())

    def test_process_feed_records_only_after_success(self) -> None:
        self.tracker.fetch_feed.return_value = [
            FeedParserDict(
                title="Article",
                link="https://example.com/article/#atom",
            )
        ]
        self.wallabag.create_entry.return_value = {"id": 42}

        self.tracker.process_feed(self.feed())

        expected_url = "https://example.com/article/"
        expected_hash = self.tracker.get_item_hash(self.feed()["url"], expected_url)
        self.wallabag.create_entry.assert_called_once_with(
            expected_url,
            title="Article",
            tags=None,
            published_at=None,
        )
        self.assertTrue(self.storage.has_seen(self.feed_id, expected_hash))
        self.assertEqual(1, self.storage.count_seen())

    def test_process_feed_does_not_record_failed_post(self) -> None:
        self.tracker.fetch_feed.return_value = [
            FeedParserDict(
                title="Article",
                link="https://example.com/article/",
            )
        ]
        self.wallabag.create_entry.return_value = None

        self.tracker.process_feed(self.feed())

        self.assertEqual(0, self.storage.count_seen())

    def test_disabled_feed_is_not_loaded(self) -> None:
        self.storage.set_feed_enabled("https://example.com/feed.xml", False)

        self.assertEqual([], self.tracker.load_feeds())


if __name__ == "__main__":
    unittest.main()
