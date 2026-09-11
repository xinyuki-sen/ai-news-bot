#!/usr/bin/env python3
"""
AI News Bot - Scrapes RSS feeds for AI news, filters, dedupes, posts to Discord
"""

import feedparser
import sqlite3
import requests
import hashlib
from datetime import datetime
import os
import json

# ============================================================================
# STEP 1: SETUP - Create database file to remember what we've already seen
# ============================================================================
DB_FILE = "seen_posts.db"

def init_db():
    """Create database table if it doesn't exist - think of this as a notebook to track what we've seen"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS posts
                 (post_hash TEXT PRIMARY KEY, title TEXT, date_added TEXT)''')
    conn.commit()
    conn.close()

def is_seen(title):
    """Check if we've already posted this before - prevents posting the same news twice"""
    post_hash = hashlib.md5(title.encode()).hexdigest()  # Convert title to a short code
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT * FROM posts WHERE post_hash = ?', (post_hash,))
    result = c.fetchone()
    conn.close()
    return result is not None

def mark_seen(title):
    """Write this post to our notebook so we don't post it again tomorrow"""
    post_hash = hashlib.md5(title.encode()).hexdigest()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO posts VALUES (?, ?, ?)',
              (post_hash, title, datetime.now().isoformat()))
    conn.commit()
    conn.close()

# ============================================================================
# STEP 2: KEYWORDS - Words that make something "AI-relevant" for students
# ============================================================================
KEYWORDS = [
    "LLM", "large language model", "GPT", "transformer",
    "neural network", "deep learning", "machine learning",
    "diffusion", "CLIP", "vision", "NLP", "AI safety",
    "open source", "model", "training", "inference",
    "embedding", "attention", "fine-tune", "dataset"
]

def is_relevant(title, description=""):
    """
    Check if title/description contains AI keywords
    Think: Does this article talk about AI stuff students should know?
    """
    text = (title + " " + description).lower()
    return any(keyword.lower() in text for keyword in KEYWORDS)

# ============================================================================
# STEP 3: FETCH RSS FEEDS - Get latest articles from each source
# ============================================================================
FEEDS = [
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://news.ycombinator.com/rss",
    "https://arxiv.org/rss/cs.AI",
    "https://www.reddit.com/r/MachineLearning/.rss",
    "https://www.reddit.com/r/artificial/.rss",
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCbfYPyITQ-7l4upoX8nvctg",
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCZHmQk67mSJgfCCTn7xBfKw",
]

def fetch_feed(feed_url):
    """
    Download an RSS feed and extract articles
    Think: Open a newspaper and read the headline list
    """
    try:
        feed = feedparser.parse(feed_url, agent="Mozilla/5.0 (AI-News-Bot/1.0)")
        articles = []
        for entry in feed.entries[:20]:  # Only grab the 20 most recent
            title = entry.get("title", "No title")
            link = entry.get("link", "")
            description = entry.get("description", "")
            articles.append({
                "title": title,
                "link": link,
                "description": description,
                "source": feed.feed.get("title", "Unknown")
            })
        return articles
    except Exception as e:
        print(f"Error fetching {feed_url}: {e}")
        return []

# ============================================================================
# STEP 4: FILTER - Keep only relevant articles
# ============================================================================
def filter_articles(articles):
    """
    Go through all articles and keep only the AI-related ones
    Think: Flipping through a newspaper and tearing out only the tech pages
    """
    relevant = []
    for article in articles:
        if is_relevant(article["title"], article["description"]):
            relevant.append(article)
    return relevant

# ============================================================================
# STEP 5: DEDUPE - Remove duplicates (same article from multiple sources)
# ============================================================================
def dedupe_articles(articles):
    """
    Remove articles we've already posted before
    Think: If I posted "GPT-5 released" yesterday, don't post it again today
    """
    new_articles = []
    for article in articles:
        if not is_seen(article["title"]):
            new_articles.append(article)
            mark_seen(article["title"])
    return new_articles

# ============================================================================
# STEP 6: POST TO DISCORD - Send formatted message to webhook
# ============================================================================
def post_to_discord(articles):
    """
    Send articles to Discord channel via webhook
    Think: Pinging your study group chat with curated news
    """
    discord_webhook = os.getenv("DISCORD_WEBHOOK_URL")
    
    if not discord_webhook:
        print("ERROR: No DISCORD_WEBHOOK_URL found. Set it as environment variable.")
        return
    
    if not articles:
        print("No new relevant articles today.")
        return
    
    # Create a formatted message
    message = f"🤖 **AI News Update** - {datetime.now().strftime('%Y-%m-%d')}\n\n"
    
    for i, article in enumerate(articles[:15], 1):  # Max 15 per day
        message += f"{i}. **{article['title']}**\n"
        message += f"   Source: {article['source']}\n"
        if article['link']:
            message += f"   Link: {article['link']}\n"
        message += "\n"
    
    # Send to Discord
    try:
        requests.post(discord_webhook, json={"content": message})
        print(f"✓ Posted {len(articles)} articles to Discord")
    except Exception as e:
        print(f"Error posting to Discord: {e}")

# ============================================================================
# STEP 6.5: BUILD WEB PAGE - Save results as an HTML page
# ============================================================================
def build_webpage(articles):
    """
    Create an index.html file showing today's articles
    Think: Turning our Discord message into a simple website
    """
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AI News Digest</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 700px; margin: 40px auto; padding: 0 20px; background: #0d1117; color: #c9d1d9; }}
  h1 {{ color: #58a6ff; }}
  .date {{ color: #8b949e; margin-bottom: 30px; }}
  .article {{ border-bottom: 1px solid #30363d; padding: 15px 0; }}
  .article a {{ color: #58a6ff; text-decoration: none; font-weight: 600; }}
  .article a:hover {{ text-decoration: underline; }}
  .source {{ color: #8b949e; font-size: 0.85em; }}
</style>
</head>
<body>
<h1>🤖 AI News Digest</h1>
<p class="date">Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}</p>
"""
    if not articles:
        html += "<p>No new articles today. Check back tomorrow.</p>"
    else:
        for article in articles[:15]:
            html += f"""<div class="article">
  <a href="{article['link']}" target="_blank">{article['title']}</a>
  <div class="source">Source: {article['source']}</div>
</div>
"""
    html += "</body></html>"

    with open("index.html", "w") as f:
        f.write(html)
    print("✓ Webpage generated (index.html)")

# ============================================================================
# STEP 7: MAIN - Run everything in order
# ============================================================================
def main():
    """Run the entire pipeline"""
    print("Starting AI News Bot...")
    
    # Initialize database
    init_db()
    
    # Fetch all feeds
    all_articles = []
    for feed_url in FEEDS:
        print(f"Fetching {feed_url}...")
        articles = fetch_feed(feed_url)
        all_articles.extend(articles)
    
    print(f"Fetched {len(all_articles)} total articles")
    
    # Filter for relevance
    relevant = filter_articles(all_articles)
    print(f"Found {len(relevant)} relevant articles")
    
    # Remove duplicates
    new_articles = dedupe_articles(relevant)
    print(f"Found {len(new_articles)} new articles")
    
    # Post to Discord
    post_to_discord(new_articles)
    
    # Build webpage (uses ALL relevant articles seen today, not just new ones,
    # so the page always shows a full recent list, not just what's new)
    build_webpage(relevant)
    
    print("Done!")

if __name__ == "__main__":
    main()
