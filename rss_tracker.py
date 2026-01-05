#!/usr/bin/env python3
"""
RSS Feed Tracker that fetches RSS feeds and posts new items to Wallabag.
Runs every 30 minutes.
"""

import os
import json
import time
import logging
import hashlib
import requests
from datetime import datetime
from pathlib import Path
import feedparser

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration from environment variables
WALLABAG_URL = os.getenv('WALLABAG_URL', 'http://wallabag')
WALLABAG_CLIENT_ID = os.getenv('WALLABAG_CLIENT_ID', '')
WALLABAG_CLIENT_SECRET = os.getenv('WALLABAG_CLIENT_SECRET', '')
WALLABAG_USERNAME = os.getenv('WALLABAG_USERNAME', '')
WALLABAG_PASSWORD = os.getenv('WALLABAG_PASSWORD', '')
FEEDS_FILE = os.getenv('FEEDS_FILE', '/app/feeds.json')
SEEN_FILE = os.getenv('SEEN_FILE', '/app/seen_items.json')
INTERVAL_MINUTES = int(os.getenv('INTERVAL_MINUTES', '30'))
DEFAULT_FETCH_COUNT = int(os.getenv('DEFAULT_FETCH_COUNT', '10'))


class WallabagClient:
    """Client for interacting with Wallabag API."""
    
    def __init__(self):
        self.url = WALLABAG_URL
        self.client_id = WALLABAG_CLIENT_ID
        self.client_secret = WALLABAG_CLIENT_SECRET
        self.username = WALLABAG_USERNAME
        self.password = WALLABAG_PASSWORD
        self.access_token = None
        self.token_expires_at = 0
    
    def get_token(self):
        """Get OAuth2 access token from Wallabag."""
        # Check if we have a valid token
        if self.access_token and time.time() < self.token_expires_at:
            return self.access_token
        
        token_url = f"{self.url}/oauth/v2/token"
        
        data = {
            'grant_type': 'password',
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'username': self.username,
            'password': self.password
        }
        
        try:
            response = requests.post(token_url, data=data, timeout=10)
            response.raise_for_status()
            token_data = response.json()
            self.access_token = token_data.get('access_token')
            expires_in = token_data.get('expires_in', 3600)
            self.token_expires_at = time.time() + expires_in - 60  # Refresh 1 min early
            logger.info("Successfully obtained Wallabag access token")
            return self.access_token
        except Exception as e:
            logger.error(f"Failed to get Wallabag token: {e}")
            return None
    
    def create_entry(self, url, title=None, tags=None):
        """Create a new entry in Wallabag."""
        if not self.get_token():
            logger.error("Cannot create entry: no access token")
            return None
        
        entries_url = f"{self.url}/api/entries.json"
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
        
        params = {
            'url': url,
        }
        
        if title:
            params['title'] = title
        
        if tags:
            if isinstance(tags, list):
                tags = ','.join(tags)
            params['tags'] = tags
        
        try:
            response = requests.post(entries_url, headers=headers, json=params, timeout=10)
            response.raise_for_status()
            result = response.json()
            logger.info(f"Created Wallabag entry: {title or url}")
            return result
        except Exception as e:
            logger.error(f"Failed to create Wallabag entry: {e}")
            if 'response' in locals():
                logger.error(f"Response: {response.text}")
            return None


class RSSFeedTracker:
    """Tracks RSS feeds and posts new items to Wallabag."""
    
    def __init__(self):
        self.wallabag = WallabagClient()
        self.feeds_file = Path(FEEDS_FILE)
        self.seen_file = Path(SEEN_FILE)
        self.seen_items = self.load_seen_items()
    
    def load_feeds(self):
        """Load RSS feeds from feeds.json."""
        try:
            if not self.feeds_file.exists():
                logger.warning(f"Feeds file not found: {self.feeds_file}")
                return []
            
            with open(self.feeds_file, 'r') as f:
                feeds_data = json.load(f)
                return feeds_data.get('feeds', [])
        except Exception as e:
            logger.error(f"Error loading feeds: {e}")
            return []
    
    def load_seen_items(self):
        """Load seen items from seen_items.json."""
        try:
            if not self.seen_file.exists():
                return {}
            
            with open(self.seen_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Error loading seen items: {e}")
            return {}
    
    def save_seen_items(self):
        """Save seen items to seen_items.json."""
        try:
            with open(self.seen_file, 'w') as f:
                json.dump(self.seen_items, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving seen items: {e}")
    
    def get_item_hash(self, feed_url, item_url):
        """Generate a unique hash for an RSS item."""
        return hashlib.sha256(f"{feed_url}:{item_url}".encode()).hexdigest()
    
    def fetch_feed(self, feed_url, max_items=None):
        """Fetch and parse an RSS feed."""
        try:
            logger.info(f"Fetching feed: {feed_url}")
            feed = feedparser.parse(feed_url)
            
            if feed.bozo:
                logger.warning(f"Feed parsing warning for {feed_url}: {feed.bozo_exception}")
            
            if not feed.entries:
                logger.warning(f"No entries found in feed: {feed_url}")
                return []
            
            items = feed.entries[:max_items] if max_items else feed.entries
            logger.info(f"Found {len(items)} items in feed: {feed_url}")
            return items
        except Exception as e:
            logger.error(f"Error fetching feed {feed_url}: {e}")
            return []
    
    def process_feed(self, feed_config):
        """Process a single RSS feed."""
        feed_url = feed_config.get('url')
        feed_name = feed_config.get('name', feed_url)
        tags = feed_config.get('tags', [])
        max_items = feed_config.get('max_items', DEFAULT_FETCH_COUNT)
        
        if not feed_url:
            logger.error(f"Feed config missing URL: {feed_config}")
            return
        
        # Check if this is a new feed (not in seen_items)
        feed_key = feed_url
        is_new_feed = feed_key not in self.seen_items
        
        if is_new_feed:
            logger.info(f"New feed detected: {feed_name}. Fetching last {max_items} items.")
            items = self.fetch_feed(feed_url, max_items=max_items)
        else:
            items = self.fetch_feed(feed_url)
        
        new_count = 0
        for item in items:
            item_url = item.get('link', '')
            if not item_url:
                continue
            
            item_hash = self.get_item_hash(feed_url, item_url)
            
            # Check if we've seen this item before
            if item_hash in self.seen_items.get(feed_key, {}):
                continue
            
            # Mark as seen
            if feed_key not in self.seen_items:
                self.seen_items[feed_key] = {}
            
            self.seen_items[feed_key][item_hash] = {
                'url': item_url,
                'title': item.get('title', ''),
                'seen_at': datetime.now().isoformat()
            }
            
            # Post to Wallabag
            item_title = item.get('title', '')
            result = self.wallabag.create_entry(item_url, title=item_title, tags=tags)
            
            if result:
                new_count += 1
                logger.info(f"Posted new item to Wallabag: {item_title}")
            else:
                logger.error(f"Failed to post item to Wallabag: {item_url}")
        
        if new_count > 0:
            logger.info(f"Processed {new_count} new items from {feed_name}")
            self.save_seen_items()
    
    def run(self):
        """Run the RSS feed tracker."""
        logger.info("Starting RSS feed tracker")
        
        while True:
            try:
                feeds = self.load_feeds()
                
                if not feeds:
                    logger.warning("No feeds configured. Add feeds to feeds.json")
                else:
                    logger.info(f"Processing {len(feeds)} feeds")
                    for feed_config in feeds:
                        try:
                            self.process_feed(feed_config)
                        except Exception as e:
                            logger.error(f"Error processing feed {feed_config.get('url', 'unknown')}: {e}", exc_info=True)
                
                logger.info(f"Sleeping for {INTERVAL_MINUTES} minutes...")
                time.sleep(INTERVAL_MINUTES * 60)
            
            except KeyboardInterrupt:
                logger.info("Shutting down...")
                break
            except Exception as e:
                logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
                logger.info(f"Sleeping for {INTERVAL_MINUTES} minutes before retry...")
                time.sleep(INTERVAL_MINUTES * 60)


def main():
    """Main entry point."""
    # Validate required configuration
    required_vars = ['WALLABAG_CLIENT_ID', 'WALLABAG_CLIENT_SECRET', 
                     'WALLABAG_USERNAME', 'WALLABAG_PASSWORD']
    missing = [var for var in required_vars if not os.getenv(var)]
    
    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        exit(1)
    
    logger.info(f"Wallabag URL: {WALLABAG_URL}")
    logger.info(f"Feeds file: {FEEDS_FILE}")
    logger.info(f"Seen items file: {SEEN_FILE}")
    logger.info(f"Check interval: {INTERVAL_MINUTES} minutes")
    
    tracker = RSSFeedTracker()
    tracker.run()


if __name__ == '__main__':
    main()
