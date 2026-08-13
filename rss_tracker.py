#!/usr/bin/env python3
"""Fetch RSS feeds and post new items to Wallabag."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast

import feedparser
import requests

from storage import Feed, Storage, get_item_hash, normalize_item_url

if TYPE_CHECKING:
    from types import FrameType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

WALLABAG_URL = os.getenv("WALLABAG_URL", "http://wallabag")
WALLABAG_CLIENT_ID = os.getenv("WALLABAG_CLIENT_ID", "")
WALLABAG_CLIENT_SECRET = os.getenv("WALLABAG_CLIENT_SECRET", "")
WALLABAG_USERNAME = os.getenv("WALLABAG_USERNAME", "")
WALLABAG_PASSWORD = os.getenv("WALLABAG_PASSWORD", "")
DATABASE_FILE = os.getenv("DATABASE_FILE", "/app/data/rss_tracker.db")
LEGACY_FEEDS_FILE = os.getenv("LEGACY_FEEDS_FILE", "/app/legacy/feeds.json")
LEGACY_SEEN_FILE = os.getenv("LEGACY_SEEN_FILE", "/app/data/seen_items.json")
INTERVAL_MINUTES = int(os.getenv("INTERVAL_MINUTES", "30"))
DEFAULT_FETCH_COUNT = int(os.getenv("DEFAULT_FETCH_COUNT", "10"))

FeedEntry = Mapping[str, object]


class Wallabag(Protocol):
    """The Wallabag operation used by the feed tracker."""

    def create_entry(
        self,
        url: str,
        title: str | None = None,
        tags: str | list[str] | None = None,
        published_at: str | None = None,
    ) -> dict[str, object] | None: ...


class WallabagClient:
    """Client for interacting with the Wallabag API."""

    def __init__(self) -> None:
        self.url = WALLABAG_URL
        self.client_id = WALLABAG_CLIENT_ID
        self.client_secret = WALLABAG_CLIENT_SECRET
        self.username = WALLABAG_USERNAME
        self.password = WALLABAG_PASSWORD
        self.access_token: str | None = None
        self.token_expires_at = 0.0

    def get_token(self) -> str | None:
        if self.access_token and time.time() < self.token_expires_at:
            return self.access_token

        token_url = f"{self.url}/oauth/v2/token"
        data = {
            "grant_type": "password",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "username": self.username,
            "password": self.password,
        }

        try:
            response = requests.post(token_url, data=data, timeout=10)
            response.raise_for_status()
            token_data = response.json()
            access_token = token_data.get("access_token")
            expires_in = token_data.get("expires_in", 3600)
            if not isinstance(access_token, str) or not isinstance(
                expires_in, int | float
            ):
                raise ValueError("Wallabag returned an invalid token response")
            self.access_token = access_token
            self.token_expires_at = time.time() + expires_in - 60
            logger.info("Successfully obtained Wallabag access token")
            return self.access_token
        except (requests.RequestException, ValueError) as error:
            logger.error("Failed to get Wallabag token: %s", error)
            return None

    def create_entry(
        self,
        url: str,
        title: str | None = None,
        tags: str | list[str] | None = None,
        published_at: str | None = None,
    ) -> dict[str, object] | None:
        if not self.get_token():
            logger.error("Cannot create entry: no access token")
            return None

        entries_url = f"{self.url}/api/entries.json"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        params = {"url": url}
        if title:
            params["title"] = title
        if tags:
            params["tags"] = ",".join(tags) if isinstance(tags, list) else tags
        if published_at:
            params["published_at"] = published_at

        response = None
        try:
            response = requests.post(
                entries_url, headers=headers, json=params, timeout=10
            )
            response.raise_for_status()
            result = cast("dict[str, object]", response.json())

            if published_at and result.get("published_at") != published_at:
                entry_id = result.get("id")
                if entry_id:
                    time.sleep(3)
                    update_url = f"{self.url}/api/entries/{entry_id}.json"
                    try:
                        update_response = requests.patch(
                            update_url,
                            headers=headers,
                            json={"published_at": published_at},
                            timeout=10,
                        )
                        update_response.raise_for_status()
                        result = cast("dict[str, object]", update_response.json())
                    except (requests.RequestException, ValueError) as error:
                        logger.debug("Failed to update published_at: %s", error)

            logger.info("Created Wallabag entry: %s", title or url)
            return result
        except (requests.RequestException, ValueError) as error:
            logger.error("Failed to create Wallabag entry: %s", error)
            if response is not None:
                logger.error("Response: %s", response.text)
            return None


class RSSFeedTracker:
    """Track RSS feeds in SQLite and post unseen items to Wallabag."""

    def __init__(
        self,
        storage: Storage,
        wallabag: Wallabag | None = None,
        install_signal_handlers: bool = True,
    ) -> None:
        self.storage = storage
        self.wallabag = wallabag or WallabagClient()
        self.shutdown_requested = False
        if install_signal_handlers:
            self._setup_signal_handlers()

    def _setup_signal_handlers(self) -> None:
        def signal_handler(signum: int, _frame: FrameType | None) -> None:
            signal_names = {
                signal.SIGTERM: "SIGTERM",
                signal.SIGINT: "SIGINT",
            }
            signal_name = signal_names.get(signum, f"signal {signum}")
            logger.info("Received %s, initiating graceful shutdown...", signal_name)
            self.shutdown_requested = True

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

    def load_feeds(self) -> list[Feed]:
        return self.storage.list_feeds(enabled_only=True)

    @staticmethod
    def normalize_item_url(feed_url: str, item_url: str) -> str:
        return normalize_item_url(feed_url, item_url)

    @staticmethod
    def get_item_hash(feed_url: str, item_url: str) -> str:
        return get_item_hash(feed_url, item_url)

    @staticmethod
    def get_item_published_date(item: FeedEntry) -> str | None:
        for parsed_field in ("published_parsed", "updated_parsed"):
            parsed_value = getattr(item, parsed_field, None)
            if parsed_value:
                try:
                    date = datetime(*parsed_value[:6], tzinfo=UTC)
                    return date.strftime("%Y-%m-%dT%H:%M:%S+0000")
                except (ValueError, OSError, TypeError) as error:
                    logger.debug("Error converting %s: %s", parsed_field, error)
        return None

    @staticmethod
    def is_medium_url(url: str) -> bool:
        return bool(url and "medium.com" in url.lower())

    def fetch_feed(
        self, feed_url: str, max_items: int | None = None
    ) -> list[FeedEntry]:
        try:
            logger.info("Fetching feed: %s", feed_url)
            response = requests.get(feed_url, timeout=5)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            if feed.bozo:
                logger.warning(
                    "Feed parsing warning for %s: %s", feed_url, feed.bozo_exception
                )
            if not feed.entries:
                logger.warning("No entries found in feed: %s", feed_url)
                return []
            items = feed.entries[:max_items] if max_items else feed.entries
            logger.info("Found %d items in feed: %s", len(items), feed_url)
            return cast("list[FeedEntry]", items)
        except requests.exceptions.Timeout:
            logger.error("Timeout fetching feed %s (5 seconds)", feed_url)
            return []
        except requests.exceptions.RequestException as error:
            logger.error("Error fetching feed %s: %s", feed_url, error)
            return []
        except (ValueError, TypeError, AttributeError) as error:
            logger.error("Error parsing feed %s: %s", feed_url, error)
            return []

    def process_feed(self, feed_config: Feed) -> None:
        feed_id = feed_config["id"]
        feed_url = feed_config["url"]
        feed_name = feed_config.get("name", feed_url)
        max_items = feed_config.get("max_items", DEFAULT_FETCH_COUNT)

        if self.storage.count_seen(feed_id) == 0:
            logger.info(
                "New feed detected: %s. Fetching last %d items.", feed_name, max_items
            )
        else:
            logger.info("Fetching last %d items from %s.", max_items, feed_name)

        items = self.fetch_feed(feed_url, max_items=max_items)
        new_count = 0
        for item in items:
            item_url = self.normalize_item_url(feed_url, item.get("link", ""))
            if not item_url:
                continue

            item_hash = self.get_item_hash(feed_url, item_url)
            if self.storage.has_seen(feed_id, item_hash):
                continue

            item_tags = []
            if getattr(item, "tags", None):
                item_tags = [
                    tag.get("term", "") for tag in item.tags if tag.get("term")
                ]
            elif getattr(item, "category", None):
                item_tags = [item.category]

            item_title = item.get("title", "")
            published_date = self.get_item_published_date(item)
            tags_string = ",".join(item_tags) if item_tags else None
            actual_url = item_url
            if self.is_medium_url(item_url):
                actual_url = f"https://freedium-mirror.cfd/{item_url}"
                logger.info("Using Freedium mirror for Medium post: %s", item_title)

            result = self.wallabag.create_entry(
                actual_url,
                title=item_title,
                tags=tags_string,
                published_at=published_date,
            )
            if result:
                self.storage.record_seen(feed_id, item_hash, item_url, item_title)
                new_count += 1
                logger.info("Posted new item to Wallabag: %s", item_title)
            else:
                logger.error("Failed to post item to Wallabag: %s", item_url)

        if new_count:
            logger.info("Processed %d new items from %s", new_count, feed_name)

    def run(self, once: bool = False, clip: bool = False) -> None:
        mode = " (one-off run)" if once else " (clip mode)" if clip else ""
        logger.info("Starting RSS feed tracker%s", mode)
        try:
            while not self.shutdown_requested:
                feeds = self.load_feeds()
                if not feeds:
                    logger.warning("No feeds configured. Add feeds with feed_cli.py")
                else:
                    logger.info("Processing %d feeds", len(feeds))
                    for feed_config in feeds:
                        if self.shutdown_requested:
                            break
                        try:
                            self.process_feed(feed_config)
                        except Exception:
                            logger.exception(
                                "Error processing feed %s",
                                feed_config.get("url", "unknown"),
                            )

                if once or self.shutdown_requested:
                    break

                logger.info("Sleeping for %d minutes...", INTERVAL_MINUTES)
                slept = 0
                sleep_seconds = INTERVAL_MINUTES * 60
                while slept < sleep_seconds and not self.shutdown_requested:
                    time.sleep(min(1, sleep_seconds - slept))
                    slept += 1
                if clip:
                    logger.info("Clip mode: exiting after sleep")
                    break
        finally:
            logger.info("RSS feed tracker stopped")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RSS Feed Tracker for Wallabag")
    parser.add_argument(
        "--once", action="store_true", help="Process feeds once and exit"
    )
    parser.add_argument(
        "--clip", action="store_true", help="Process once, sleep, then exit"
    )
    parser.add_argument(
        "--migrate-only", action="store_true", help="Migrate JSON and exit"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with Storage(DATABASE_FILE, DEFAULT_FETCH_COUNT) as storage:
        report = storage.migrate_legacy_json(LEGACY_FEEDS_FILE, LEGACY_SEEN_FILE)
        if report.performed:
            logger.info(
                "Migrated %d feeds and %d seen items to SQLite; collapsed %d equivalent records",
                report.feed_count,
                report.seen_count,
                report.collapsed_records,
            )

        if args.migrate_only:
            print(json.dumps(report.to_dict(), sort_keys=True))
            return 0

        required_variables = [
            "WALLABAG_CLIENT_ID",
            "WALLABAG_CLIENT_SECRET",
            "WALLABAG_USERNAME",
            "WALLABAG_PASSWORD",
        ]
        missing = [name for name in required_variables if not os.getenv(name)]
        if missing:
            logger.error(
                "Missing required environment variables: %s", ", ".join(missing)
            )
            return 1

        logger.info("Wallabag URL: %s", WALLABAG_URL)
        logger.info("Database file: %s", DATABASE_FILE)
        logger.info("Check interval: %d minutes", INTERVAL_MINUTES)
        tracker = RSSFeedTracker(storage)
        tracker.run(once=args.once, clip=args.clip)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
