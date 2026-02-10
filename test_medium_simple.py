#!/usr/bin/env python3
"""
Simple test to verify Medium URL detection logic.
"""

def is_medium_url(url):
    """Check if a URL is hosted on Medium."""
    if not url:
        return False
    return 'medium.com' in url.lower()

# Test cases
test_urls = [
    "https://steve-yegge.medium.com/welcome-to-gas-town-4f25ee16dd04",
    "https://medium.com/@someuser/some-article-123",
    "https://towardsdatascience.com/some-article",  # Medium custom domain
    "https://jonsimpson.ca/some-article",  # Not Medium
    "https://simonwillison.net/2024/something/",  # Not Medium
]

print("🧪 Testing Medium URL Detection\n")
print("=" * 80)

for url in test_urls:
    is_medium = is_medium_url(url)
    symbol = "✅" if is_medium else "❌"
    
    print(f"\n{symbol} URL: {url}")
    print(f"   Medium detected: {is_medium}")
    
    if is_medium:
        freedium_url = f"https://freedium-mirror.cfd/{url}"
        print(f"   Transformed to: {freedium_url}")

print("\n" + "=" * 80)
print("\n💡 Note: Medium custom domains (like towardsdatascience.com) won't be")
print("   detected by this simple check. Could extend if needed.")
