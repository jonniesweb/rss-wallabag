# RSS Feed Tracker for Wallabag

This service automatically fetches RSS feeds every 30 minutes and posts new items to Wallabag.

## Features

- Fetches RSS feeds on a 30-minute schedule
- Stores feeds and seen articles transactionally in SQLite
- Normalizes article URLs before deduplication
- When adding a new feed, fetches the last 10 items by default
- Includes a safe CLI for adding, updating, enabling, and disabling feeds
- Automatically posts new items to Wallabag via API

## Configuration

The service is configured via `docker-compose.yml` with the following environment variables:

- `WALLABAG_URL` - Wallabag instance URL
- `WALLABAG_CLIENT_ID` - OAuth2 client ID
- `WALLABAG_CLIENT_SECRET` - OAuth2 client secret
- `WALLABAG_USERNAME` - Wallabag username
- `WALLABAG_PASSWORD` - Wallabag password
- `DATABASE_FILE` - SQLite database path (default: `/app/data/rss_tracker.db`)
- `LEGACY_FEEDS_FILE` - One-time feed migration source
- `LEGACY_SEEN_FILE` - One-time seen-item migration source
- `INTERVAL_MINUTES` - Check interval (default: 30)
- `DEFAULT_FETCH_COUNT` - Items to fetch for new feeds (default: 10)

## Adding RSS Feeds

Use `feed_cli.py` to manage feeds:

```bash
python feed_cli.py --database data/rss_tracker.db add \
  https://example.com/feed.xml --name "Feed Name" --tag tag1 --max-items 10
python feed_cli.py --database data/rss_tracker.db list
python feed_cli.py --database data/rss_tracker.db disable https://example.com/feed.xml
```

The CLI also supports `update`, `enable`, `stats`, and `export-json`. SQLite uses
WAL mode and a busy timeout so the tracker and management CLI can safely access
the database concurrently.

### OpenClaw

Mount the entire tracker data directory so SQLite's database, WAL, and shared
memory files stay together, and mount `feed_cli.py` plus `storage.py` read-only.
OpenClaw can then manage feeds without restarting the scraper:

```bash
python3 /data/rss-wallabag-tools/feed_cli.py \
  --database /data/rss-wallabag/rss_tracker.db list --json
```

## Migrating from JSON

On first startup, the tracker imports `feeds.json` and `seen_items.json` in one
transaction. It canonicalizes item URLs, collapses equivalent records, and
records a migration marker so later changes to the legacy files are ignored.
The original files are retained for rollback.

Run only the migration without contacting Wallabag:

```bash
python rss_tracker.py --migrate-only
```

## How It Works

1. The service runs continuously, checking feeds every 30 minutes
2. For each enabled SQLite feed, it fetches and parses entries
3. It resolves relative URLs and removes URL fragments before checking for duplicates
4. It checks each item against the SQLite seen-items table
5. New items are posted to Wallabag via the API
6. Successfully posted items are committed immediately to SQLite

## Files

- `data/rss_tracker.db` - Feeds, seen items, and migration metadata
- `feeds.json` and `data/seen_items.json` - Retained one-time migration sources

## Logs

View logs:
```bash
cd ~/docker/rss-wallabag
docker-compose logs -f
```

## Restarting

Feed-management changes are picked up on the next 30-minute cycle; no restart
is required.

## Updating Code

After pulling code changes (e.g., `git pull`), **always rebuild** the image:
```bash
cd ~/docker/rss-wallabag
docker-compose up --build -d
```

⚠️ Just using `docker-compose restart` or `up -d` won't pick up code changes — you must use `--build` to rebuild the image.

## Status

Check container status:
```bash
cd ~/docker/rss-wallabag
docker-compose ps
```
