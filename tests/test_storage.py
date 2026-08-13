import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from storage import MIGRATION_KEY, Storage, get_item_hash


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_file = self.root / "tracker.db"
        self.storage = Storage(self.database_file)

    def tearDown(self):
        self.storage.close()
        self.temporary_directory.cleanup()

    def write_legacy_files(self):
        feeds_file = self.root / "feeds.json"
        seen_file = self.root / "seen_items.json"
        feed_url = "https://example.com/feed.xml"
        fragment_url = "https://example.com/article/#atom"
        canonical_url = "https://example.com/article/"
        fragment_hash = hashlib.sha256(f"{feed_url}:{fragment_url}".encode()).hexdigest()
        canonical_hash = hashlib.sha256(f"{feed_url}:{canonical_url}".encode()).hexdigest()
        feeds_file.write_text(
            json.dumps(
                {
                    "feeds": [
                        {
                            "name": "Example",
                            "url": feed_url,
                            "tags": ["technology"],
                            "max_items": 12,
                        },
                        {
                            "name": "Empty",
                            "url": "https://empty.example/feed.xml",
                        },
                    ]
                }
            )
        )
        seen_file.write_text(
            json.dumps(
                {
                    feed_url: {
                        fragment_hash: {
                            "url": fragment_url,
                            "title": "Old title",
                            "seen_at": "2026-08-01T00:00:00",
                        },
                        canonical_hash: {
                            "url": canonical_url,
                            "title": "New title",
                            "seen_at": "2026-08-02T00:00:00",
                        },
                    }
                }
            )
        )
        return feeds_file, seen_file, feed_url, canonical_url

    def test_database_uses_wal_and_foreign_keys(self):
        journal_mode = self.storage.connection.execute("PRAGMA journal_mode").fetchone()[0]
        foreign_keys = self.storage.connection.execute("PRAGMA foreign_keys").fetchone()[0]

        self.assertEqual("wal", journal_mode)
        self.assertEqual(1, foreign_keys)

    def test_second_connection_can_manage_feeds(self):
        with Storage(self.database_file) as manager:
            manager.add_feed("Example", "https://example.com/feed.xml")

        self.assertEqual(1, self.storage.count_feeds())
        self.assertEqual("Example", self.storage.list_feeds()[0]["name"])

    def test_migration_imports_json_and_collapses_equivalent_items(self):
        feeds_file, seen_file, feed_url, canonical_url = self.write_legacy_files()

        report = self.storage.migrate_legacy_json(feeds_file, seen_file)

        self.assertTrue(report.performed)
        self.assertEqual(2, report.feed_count)
        self.assertEqual(1, report.seen_count)
        self.assertEqual(2, report.legacy_seen_records)
        self.assertEqual(1, report.collapsed_records)
        feed = self.storage.get_feed(feed_url)
        self.assertEqual(["technology"], feed["tags"])
        self.assertEqual(12, feed["max_items"])
        self.assertTrue(
            self.storage.has_seen(feed["id"], get_item_hash(feed_url, canonical_url))
        )
        metadata = self.storage.connection.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (MIGRATION_KEY,),
        ).fetchone()
        self.assertIsNotNone(metadata)

    def test_migration_is_idempotent(self):
        feeds_file, seen_file, _feed_url, _canonical_url = self.write_legacy_files()
        self.storage.migrate_legacy_json(feeds_file, seen_file)

        second_report = self.storage.migrate_legacy_json(feeds_file, seen_file)

        self.assertFalse(second_report.performed)
        self.assertEqual(2, second_report.feed_count)
        self.assertEqual(1, second_report.seen_count)

    def test_feed_management_preserves_seen_items_when_disabled(self):
        feed_id = self.storage.add_feed(
            "Example",
            "https://example.com/feed.xml",
            ["one"],
            10,
        )
        item_hash = get_item_hash(
            "https://example.com/feed.xml",
            "https://example.com/article/",
        )
        self.storage.record_seen(
            feed_id,
            item_hash,
            "https://example.com/article/",
            "Article",
        )

        self.storage.set_feed_enabled("https://example.com/feed.xml", False)
        self.storage.update_feed(
            "https://example.com/feed.xml",
            name="Renamed",
            tags=["two"],
            max_items=20,
        )

        self.assertEqual([], self.storage.list_feeds())
        feed = self.storage.list_feeds(enabled_only=False)[0]
        self.assertEqual("Renamed", feed["name"])
        self.assertEqual(["two"], feed["tags"])
        self.assertEqual(20, feed["max_items"])
        self.assertEqual(1, self.storage.count_seen(feed_id))

    def test_export_json_is_rollback_compatible(self):
        feeds_file, seen_file, feed_url, _canonical_url = self.write_legacy_files()
        self.storage.migrate_legacy_json(feeds_file, seen_file)
        exported_feeds = self.root / "exported-feeds.json"
        exported_seen = self.root / "exported-seen.json"

        self.storage.export_legacy_json(exported_feeds, exported_seen)

        feed_payload = json.loads(exported_feeds.read_text())
        seen_payload = json.loads(exported_seen.read_text())
        self.assertEqual(2, len(feed_payload["feeds"]))
        self.assertEqual(1, len(seen_payload[feed_url]))


if __name__ == "__main__":
    unittest.main()
