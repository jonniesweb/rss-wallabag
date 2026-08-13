"""SQLite persistence and legacy JSON migration for the RSS tracker."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast
from urllib.parse import urldefrag, urljoin

if TYPE_CHECKING:
    from types import TracebackType

SCHEMA_VERSION = 1
MIGRATION_KEY = "legacy_json_migration_v1"


StrPath = str | os.PathLike[str]


class Feed(TypedDict):
    """A feed record exposed to the tracker and management CLI."""

    id: int
    name: str
    url: str
    tags: list[str]
    max_items: int
    enabled: bool
    position: int


class SeenRecord(TypedDict):
    url: str
    title: str
    seen_at: str


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def normalize_item_url(feed_url: str, item_url: str) -> str:
    """Resolve an item URL and remove fragments that do not reach the server."""
    if not item_url:
        return item_url

    absolute_url = urljoin(feed_url, item_url)
    normalized_url, _fragment = urldefrag(absolute_url)
    return normalized_url


def get_item_hash(feed_url: str, item_url: str) -> str:
    """Return the stable identity used for an item within a feed."""
    normalized_url = normalize_item_url(feed_url, item_url)
    return hashlib.sha256(f"{feed_url}:{normalized_url}".encode()).hexdigest()


@dataclass(frozen=True)
class MigrationReport:
    performed: bool
    feed_count: int
    seen_count: int
    legacy_seen_records: int = 0
    collapsed_records: int = 0

    def to_dict(self) -> dict[str, bool | int]:
        return cast("dict[str, bool | int]", asdict(self))


class Storage:
    """Own the tracker database and its short, committed operations."""

    def __init__(self, database_file: StrPath, default_fetch_count: int = 10) -> None:
        self.database_file = Path(database_file)
        self.default_fetch_count = default_fetch_count
        self.database_file.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_file, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self._initialize_schema()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Storage:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _initialize_schema(self) -> None:
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS feeds (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    max_items INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    position INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS seen_items (
                    id INTEGER PRIMARY KEY,
                    feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
                    item_hash TEXT NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    seen_at TEXT NOT NULL,
                    UNIQUE (feed_id, item_hash)
                );

                CREATE INDEX IF NOT EXISTS idx_seen_items_feed_seen_at
                    ON seen_items(feed_id, seen_at);

                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            self.connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _next_position(self) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS next_position FROM feeds"
        ).fetchone()
        return int(row["next_position"])

    @staticmethod
    def _decode_feed(row: sqlite3.Row) -> Feed:
        return {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "url": str(row["url"]),
            "tags": cast("list[str]", json.loads(row["tags_json"])),
            "max_items": int(row["max_items"]),
            "enabled": bool(row["enabled"]),
            "position": int(row["position"]),
        }

    def list_feeds(self, enabled_only: bool = True) -> list[Feed]:
        where_clause = "WHERE enabled = 1" if enabled_only else ""
        rows = self.connection.execute(
            f"""
            SELECT id, name, url, tags_json, max_items, enabled, position
            FROM feeds
            {where_clause}
            ORDER BY position, id
            """
        ).fetchall()
        return [self._decode_feed(row) for row in rows]

    def get_feed(self, url: str) -> Feed | None:
        row = self.connection.execute(
            """
            SELECT id, name, url, tags_json, max_items, enabled, position
            FROM feeds
            WHERE url = ?
            """,
            (url,),
        ).fetchone()
        return self._decode_feed(row) if row else None

    def add_feed(
        self,
        name: str,
        url: str,
        tags: list[str] | None = None,
        max_items: int | None = None,
    ) -> int:
        now = utc_now()
        tags = tags or []
        max_items = max_items if max_items is not None else self.default_fetch_count
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO feeds (
                    name, url, tags_json, max_items, enabled, position, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    name,
                    url,
                    json.dumps(tags),
                    max_items,
                    self._next_position(),
                    now,
                    now,
                ),
            )
        if cursor.lastrowid is None:
            raise sqlite3.DatabaseError("Feed insert did not return an ID")
        return cursor.lastrowid

    def update_feed(
        self,
        url: str,
        name: str | None = None,
        tags: list[str] | None = None,
        max_items: int | None = None,
    ) -> bool:
        updates: list[str] = []
        values: list[object] = []
        if name is not None:
            updates.append("name = ?")
            values.append(name)
        if tags is not None:
            updates.append("tags_json = ?")
            values.append(json.dumps(tags))
        if max_items is not None:
            updates.append("max_items = ?")
            values.append(max_items)
        if not updates:
            return False

        updates.append("updated_at = ?")
        values.append(utc_now())
        values.append(url)
        with self.connection:
            cursor = self.connection.execute(
                f"UPDATE feeds SET {', '.join(updates)} WHERE url = ?",
                values,
            )
        return cursor.rowcount == 1

    def set_feed_enabled(self, url: str, enabled: bool) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE feeds SET enabled = ?, updated_at = ? WHERE url = ?",
                (int(enabled), utc_now(), url),
            )
        return cursor.rowcount == 1

    def count_feeds(self, enabled_only: bool = False) -> int:
        where_clause = "WHERE enabled = 1" if enabled_only else ""
        row = self.connection.execute(
            f"SELECT COUNT(*) AS count FROM feeds {where_clause}"
        ).fetchone()
        return int(row["count"])

    def count_seen(self, feed_id: int | None = None) -> int:
        if feed_id is None:
            row = self.connection.execute(
                "SELECT COUNT(*) AS count FROM seen_items"
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT COUNT(*) AS count FROM seen_items WHERE feed_id = ?",
                (feed_id,),
            ).fetchone()
        return int(row["count"])

    def has_seen(self, feed_id: int, item_hash: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM seen_items WHERE feed_id = ? AND item_hash = ?",
            (feed_id, item_hash),
        ).fetchone()
        return row is not None

    def record_seen(
        self,
        feed_id: int,
        item_hash: str,
        url: str,
        title: str,
        seen_at: str | None = None,
    ) -> bool:
        seen_at = seen_at or utc_now()
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO seen_items (
                    feed_id, item_hash, url, title, seen_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (feed_id, item_hash, url, title, seen_at),
            )
        return cursor.rowcount == 1

    def _get_metadata(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (key,),
        ).fetchone()
        return str(row["value"]) if row else None

    def migrate_legacy_json(
        self, feeds_file: StrPath, seen_file: StrPath
    ) -> MigrationReport:
        """Import the legacy JSON files once, preserving canonical identities."""
        if self._get_metadata(MIGRATION_KEY) is not None:
            return MigrationReport(False, self.count_feeds(), self.count_seen())

        feeds_path = Path(feeds_file)
        seen_path = Path(seen_file)
        legacy_feeds: list[dict[str, object]] = []
        legacy_seen: dict[str, dict[str, dict[str, object]]] = {}

        if feeds_path.exists():
            with feeds_path.open() as file_handle:
                feeds_payload = cast("dict[str, object]", json.load(file_handle))
            raw_feeds = feeds_payload.get("feeds", [])
            if not isinstance(raw_feeds, list):
                raise ValueError("Legacy feeds JSON must contain a feeds list")
            if not all(isinstance(feed, dict) for feed in raw_feeds):
                raise ValueError("Every legacy feed must be an object")
            legacy_feeds = cast("list[dict[str, object]]", raw_feeds)

        if seen_path.exists():
            with seen_path.open() as file_handle:
                raw_seen = json.load(file_handle)
            if not isinstance(raw_seen, dict):
                raise ValueError("Legacy seen-items JSON must contain an object")
            legacy_seen = cast("dict[str, dict[str, dict[str, object]]]", raw_seen)

        normalized_seen: dict[tuple[str, str], SeenRecord] = {}
        legacy_seen_records = 0
        for feed_url, items in legacy_seen.items():
            if not isinstance(items, dict):
                raise TypeError(f"Seen items for {feed_url} must be an object")
            for record in items.values():
                if not isinstance(record, dict):
                    raise ValueError(f"Seen item for {feed_url} must be an object")
                record_url = record.get("url")
                if not isinstance(record_url, str) or not record_url:
                    raise ValueError(f"Seen item for {feed_url} is missing its URL")
                record_title = record.get("title", "")
                record_seen_at = record.get("seen_at")
                if not isinstance(record_title, str):
                    raise ValueError(f"Seen item for {feed_url} has an invalid title")
                if record_seen_at is not None and not isinstance(record_seen_at, str):
                    raise ValueError(
                        f"Seen item for {feed_url} has an invalid timestamp"
                    )
                legacy_seen_records += 1
                normalized_url = normalize_item_url(feed_url, record_url)
                item_hash = get_item_hash(feed_url, normalized_url)
                normalized_record: SeenRecord = {
                    "url": normalized_url,
                    "title": record_title,
                    "seen_at": record_seen_at or utc_now(),
                }
                key = (feed_url, item_hash)
                existing = normalized_seen.get(key)
                if (
                    existing is None
                    or normalized_record["seen_at"] > existing["seen_at"]
                ):
                    normalized_seen[key] = normalized_record

        now = utc_now()
        with self.connection:
            next_position = self._next_position()
            configured_urls = set()
            for offset, feed in enumerate(legacy_feeds):
                feed_url = feed.get("url")
                if not isinstance(feed_url, str) or not feed_url:
                    raise ValueError("Legacy feed is missing its URL")
                feed_name = feed.get("name") or feed_url
                feed_tags = feed.get("tags") or []
                feed_max_items = feed.get("max_items", self.default_fetch_count)
                if not isinstance(feed_name, str):
                    raise ValueError(f"Legacy feed {feed_url} has an invalid name")
                if not isinstance(feed_tags, list) or not all(
                    isinstance(tag, str) for tag in feed_tags
                ):
                    raise ValueError(f"Legacy feed {feed_url} has invalid tags")
                if not isinstance(feed_max_items, int):
                    raise ValueError(f"Legacy feed {feed_url} has invalid max_items")
                configured_urls.add(feed_url)
                self.connection.execute(
                    """
                    INSERT INTO feeds (
                        name, url, tags_json, max_items, enabled, position, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(url) DO UPDATE SET
                        name = excluded.name,
                        tags_json = excluded.tags_json,
                        max_items = excluded.max_items,
                        updated_at = excluded.updated_at
                    """,
                    (
                        feed_name,
                        feed_url,
                        json.dumps(feed_tags),
                        feed_max_items,
                        next_position + offset,
                        now,
                        now,
                    ),
                )

            missing_urls = sorted(set(legacy_seen) - configured_urls)
            for offset, feed_url in enumerate(missing_urls, start=len(legacy_feeds)):
                self.connection.execute(
                    """
                    INSERT INTO feeds (
                        name, url, tags_json, max_items, enabled, position, created_at, updated_at
                    ) VALUES (?, ?, '[]', ?, 1, ?, ?, ?)
                    ON CONFLICT(url) DO NOTHING
                    """,
                    (
                        feed_url,
                        feed_url,
                        self.default_fetch_count,
                        next_position + offset,
                        now,
                        now,
                    ),
                )

            feed_ids = {
                row["url"]: row["id"]
                for row in self.connection.execute("SELECT id, url FROM feeds")
            }
            for (feed_url, item_hash), record in normalized_seen.items():
                self.connection.execute(
                    """
                    INSERT INTO seen_items (
                        feed_id, item_hash, url, title, seen_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(feed_id, item_hash) DO UPDATE SET
                        url = excluded.url,
                        title = excluded.title,
                        seen_at = excluded.seen_at
                    WHERE excluded.seen_at > seen_items.seen_at
                    """,
                    (
                        feed_ids[feed_url],
                        item_hash,
                        record["url"],
                        record["title"],
                        record["seen_at"],
                    ),
                )

            report = MigrationReport(
                True,
                self.count_feeds(),
                self.count_seen(),
                legacy_seen_records,
                legacy_seen_records - len(normalized_seen),
            )
            self.connection.execute(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                (MIGRATION_KEY, json.dumps(report.to_dict(), sort_keys=True)),
            )

        return report

    def export_legacy_json(self, feeds_file: StrPath, seen_file: StrPath) -> None:
        """Export a rollback-compatible snapshot of the SQLite data."""
        feeds: list[dict[str, object]] = []
        for feed in self.list_feeds(enabled_only=False):
            feed_payload = {
                "name": feed["name"],
                "url": feed["url"],
                "tags": feed["tags"],
                "max_items": feed["max_items"],
            }
            if feed["enabled"]:
                feeds.append(feed_payload)

        seen_items: dict[str, dict[str, SeenRecord]] = {}
        rows = self.connection.execute(
            """
            SELECT feeds.url AS feed_url, seen_items.item_hash, seen_items.url,
                   seen_items.title, seen_items.seen_at
            FROM seen_items
            JOIN feeds ON feeds.id = seen_items.feed_id
            ORDER BY feeds.position, seen_items.seen_at
            """
        )
        for row in rows:
            seen_items.setdefault(row["feed_url"], {})[row["item_hash"]] = {
                "url": row["url"],
                "title": row["title"],
                "seen_at": row["seen_at"],
            }

        _atomic_write_json(Path(feeds_file), {"feeds": feeds})
        _atomic_write_json(Path(seen_file), seen_items)


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file_handle:
            temporary_path = Path(file_handle.name)
            json.dump(payload, file_handle, indent=2)
            file_handle.flush()
            os.fsync(file_handle.fileno())

        file_mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
        os.chmod(temporary_path, file_mode)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
