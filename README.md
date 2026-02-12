# RSS Feed Tracker for Wallabag

This service automatically fetches RSS feeds every 30 minutes and posts new items to Wallabag.

## Features

- Fetches RSS feeds on a 30-minute schedule
- Tracks seen items to avoid duplicates
- When adding a new feed, fetches the last 10 items by default
- Stores feeds in `feeds.json` for easy management
- Automatically posts new items to Wallabag via API

## Configuration

The service is configured via `docker-compose.yml` with the following environment variables:

- `WALLABAG_URL` - Wallabag instance URL
- `WALLABAG_CLIENT_ID` - OAuth2 client ID
- `WALLABAG_CLIENT_SECRET` - OAuth2 client secret
- `WALLABAG_USERNAME` - Wallabag username
- `WALLABAG_PASSWORD` - Wallabag password
- `INTERVAL_MINUTES` - Check interval (default: 30)
- `DEFAULT_FETCH_COUNT` - Items to fetch for new feeds (default: 10)

## Adding RSS Feeds

Edit `feeds.json` to add new RSS feeds:

```json
{
  "feeds": [
    {
      "name": "Feed Name",
      "url": "https://example.com/feed.xml",
      "tags": ["tag1", "tag2"],
      "max_items": 10
    }
  ]
}
```

Fields:
- `name` - Display name for the feed
- `url` - RSS feed URL (required)
- `tags` - Array of tags to apply to items from this feed (optional)
- `max_items` - Number of items to fetch when adding a new feed (optional, defaults to 10)

## How It Works

1. The service runs continuously, checking feeds every 30 minutes
2. For each feed, it fetches the RSS feed and parses entries
3. It checks each item against `seen_items.json` to avoid duplicates
4. New items are posted to Wallabag via the API
5. Seen items are tracked in `seen_items.json`

## Files

- `feeds.json` - RSS feed configuration (read-only mount)
- `seen_items.json` - Tracks which items have been processed (read-write)

## Logs

View logs:
```bash
cd ~/docker/rss-wallabag
docker-compose logs -f
```

## Restarting

After modifying `feeds.json`, restart the container:
```bash
cd ~/docker/rss-wallabag
docker-compose restart
```

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
