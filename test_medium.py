#!/usr/bin/env python3
"""
Quick test to verify Medium URL detection and Freedium prefixing.
"""
import json
import feedparser
import requests

def is_medium_url(url):
    """Check if a URL is hosted on Medium."""
    if not url:
        return False
    return 'medium.com' in url.lower()

def test_medium_feed():
    """Test fetching a Medium feed and showing URL transformation."""
    feed_url = "https://steve-yegge.medium.com/feed"
    
    print(f"🔍 Fetching feed: {feed_url}\n")
    
    try:
        response = requests.get(feed_url, timeout=10)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        
        if not feed.entries:
            print("❌ No entries found in feed")
            return
        
        print(f"✅ Found {len(feed.entries)} entries\n")
        print("=" * 80)
        
        # Show first 3 items
        for i, item in enumerate(feed.entries[:3], 1):
            title = item.get('title', 'No title')
            original_url = item.get('link', '')
            
            print(f"\n📄 Item {i}: {title}")
            print(f"   Original URL: {original_url}")
            
            if is_medium_url(original_url):
                freedium_url = f"https://freedium-mirror.cfd/{original_url}"
                print(f"   🔧 Medium detected!")
                print(f"   ✨ Freedium URL: {freedium_url}")
            else:
                print(f"   ℹ️  Not a Medium URL, no transformation needed")
        
        print("\n" + "=" * 80)
        print("✅ Test complete!")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == '__main__':
    test_medium_feed()
