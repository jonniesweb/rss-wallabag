import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import feed_cli


class FeedCliTests(unittest.TestCase):
    def test_add_list_disable_and_stats(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_file = str(Path(temporary_directory) / "tracker.db")

            self.assertEqual(
                0,
                feed_cli.main(
                    [
                        "--database",
                        database_file,
                        "add",
                        "https://example.com/feed.xml",
                        "--name",
                        "Example",
                        "--tag",
                        "technology",
                    ]
                ),
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    0,
                    feed_cli.main(["--database", database_file, "list", "--json"]),
                )
            feeds = json.loads(output.getvalue())
            self.assertEqual("Example", feeds[0]["name"])
            self.assertEqual(["technology"], feeds[0]["tags"])

            self.assertEqual(
                0,
                feed_cli.main(
                    [
                        "--database",
                        database_file,
                        "disable",
                        "https://example.com/feed.xml",
                    ]
                ),
            )
            output = io.StringIO()
            with redirect_stdout(output):
                feed_cli.main(["--database", database_file, "stats"])
            stats = json.loads(output.getvalue())
            self.assertEqual(1, stats["feeds"])
            self.assertEqual(0, stats["enabled_feeds"])


if __name__ == "__main__":
    unittest.main()
