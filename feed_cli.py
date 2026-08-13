#!/usr/bin/env python3
"""Manage RSS feeds stored in the tracker SQLite database."""

import argparse
import json
import os
import sqlite3
import sys

from storage import Storage

DEFAULT_DATABASE_FILE = os.getenv("DATABASE_FILE", "/data/rss-wallabag/rss_tracker.db")


def build_parser():
    parser = argparse.ArgumentParser(description="Manage RSS Wallabag feeds")
    parser.add_argument("--database", default=DEFAULT_DATABASE_FILE)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List configured feeds")
    list_parser.add_argument("--json", action="store_true", dest="as_json")
    list_parser.add_argument("--all", action="store_true", help="Include disabled feeds")

    add_parser = subparsers.add_parser("add", help="Add a feed")
    add_parser.add_argument("url")
    add_parser.add_argument("--name", required=True)
    add_parser.add_argument("--tag", action="append", default=[])
    add_parser.add_argument("--max-items", type=int, default=10)

    update_parser = subparsers.add_parser("update", help="Update a feed")
    update_parser.add_argument("url")
    update_parser.add_argument("--name")
    update_parser.add_argument("--tag", action="append", default=None)
    update_parser.add_argument("--max-items", type=int)

    for command in ("enable", "disable"):
        command_parser = subparsers.add_parser(command, help=f"{command.title()} a feed")
        command_parser.add_argument("url")

    subparsers.add_parser("stats", help="Show database counts")

    export_parser = subparsers.add_parser("export-json", help="Export rollback-compatible JSON")
    export_parser.add_argument("--feeds-file", required=True)
    export_parser.add_argument("--seen-file", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        with Storage(args.database) as storage:
            if args.command == "list":
                feeds = storage.list_feeds(enabled_only=not args.all)
                if args.as_json:
                    print(json.dumps(feeds, indent=2))
                else:
                    for feed in feeds:
                        state = "enabled" if feed["enabled"] else "disabled"
                        print(f"{feed['name']}\t{feed['url']}\t{state}\tmax={feed['max_items']}")
            elif args.command == "add":
                storage.add_feed(args.name, args.url, args.tag, args.max_items)
                print(f"Added feed: {args.name}")
            elif args.command == "update":
                if not storage.update_feed(args.url, args.name, args.tag, args.max_items):
                    print("No matching feed or no changes requested", file=sys.stderr)
                    return 1
                print(f"Updated feed: {args.url}")
            elif args.command in ("enable", "disable"):
                enabled = args.command == "enable"
                if not storage.set_feed_enabled(args.url, enabled):
                    print("No matching feed", file=sys.stderr)
                    return 1
                print(f"{args.command.title()}d feed: {args.url}")
            elif args.command == "stats":
                print(
                    json.dumps(
                        {
                            "database": str(storage.database_file),
                            "feeds": storage.count_feeds(),
                            "enabled_feeds": storage.count_feeds(enabled_only=True),
                            "seen_items": storage.count_seen(),
                        },
                        indent=2,
                    )
                )
            elif args.command == "export-json":
                storage.export_legacy_json(args.feeds_file, args.seen_file)
                print("Exported feeds and seen items")
    except (sqlite3.Error, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
