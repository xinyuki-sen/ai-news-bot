#!/usr/bin/env python3
"""
AI News Bot - Scrapes RSS feeds for AI news, filters, dedupes, posts to Discord
"""

import feedparser
import sqlite3
import requests
import hashlib
from datetime import datetime, timezone, timedelta
import os
import json

IST = timezone(timedelta(hours=5, minutes=30))

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
    "https://feeds.arstechnica.com/arstechnica/index",  # ArsTechnica tech news
    "https://news.ycombinator.com/rss",                   # HackerNews
    "https://arxiv.org/rss/cs.AI",                       # ArXiv AI papers
    "https://www.reddit.com/r/MachineLearning/.rss",     # Reddit ML
    "https://www.reddit.com/r/artificial/.rss",          # Reddit AI
    # YouTube - Two Minute Papers
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCbfYPyITQ-7l4upoX8nvctg",
    # YouTube - Yannic Kilcher
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCZHmQk67mSJgfCCTn7xBfKw",
]

def fetch_feed(feed_url):
    """
    Download an RSS feed and extract articles
    Think: Open a newspaper and read the headline list
    """
    try:
        # Reddit blocks default bot user-agents, so we pretend to be a browser
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
# STEP 4.5: RANK - Score each article so we know what's most important
# ============================================================================
# Some sources are more reliable/significant than others
SOURCE_WEIGHT = {
    "cs.AI updates on arXiv.org": 3,   # Research papers - high value for students
    "Hacker News": 2,
    "Ars Technica - All content": 2,
}

# High-impact words score extra points (bigger news = more of these)
HIGH_IMPACT_WORDS = [
    "breakthrough", "release", "launch", "new model", "state-of-the-art",
    "open source", "outperforms", "announces", "unveils"
]

def score_article(article):
    """
    Give each article a score - higher score = more important
    Think: A teacher grading how newsworthy each article is
    """
    text = (article["title"] + " " + article.get("description", "")).lower()
    score = 0

    # +1 point for each keyword match (more AI-relevant terms = more relevant)
    score += sum(1 for k in KEYWORDS if k.lower() in text)

    # Extra points for high-impact words (signals big news)
    score += sum(2 for w in HIGH_IMPACT_WORDS if w.lower() in text)

    # Extra points based on source reliability/significance
    score += SOURCE_WEIGHT.get(article["source"], 1)

    return score

def rank_articles(articles):
    """Sort articles from most to least important"""
    return sorted(articles, key=score_article, reverse=True)

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
    # Build a JSON array of articles for the frontend JS to use
    # Only send title/link/source (not raw description - Reddit's HTML/quotes broke the page before)
    clean_articles = [{"title": a["title"], "link": a["link"], "source": a["source"]} for a in articles[:40]]
    articles_json = json.dumps(clean_articles)
    sources = sorted(set(a['source'] for a in articles[:40]))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AI News Digest</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Segoe UI', -apple-system, sans-serif;
    background: #0d1117;
    color: #c9d1d9;
    display: flex;
    min-height: 100vh;
  }}
  /* SIDEBAR */
  .sidebar {{
    width: 70px;
    background: #161b22;
    border-right: 1px solid #30363d;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 20px 0;
    gap: 24px;
    position: sticky;
    top: 0;
    height: 100vh;
  }}
  .sidebar .logo {{ font-size: 1.6em; margin-bottom: 10px; }}
  .sidebar .icon {{ font-size: 1.2em; opacity: 0.5; cursor: default; }}
  /* MAIN */
  .main {{ flex: 1; padding: 30px 40px; max-width: 900px; }}
  .topbar {{ display: flex; gap: 10px; margin-bottom: 24px; }}
  .search {{
    flex: 1;
    background: #21262d;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 10px 14px;
    color: #c9d1d9;
    font-size: 0.95em;
  }}
  h1 {{
    font-size: 1.8em;
    background: linear-gradient(90deg, #58a6ff, #a371f7);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 4px;
  }}
  .date {{ color: #8b949e; font-size: 0.85em; margin-bottom: 20px; }}
  .tabs {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 24px; }}
  .tab {{
    padding: 6px 14px;
    border-radius: 20px;
    background: #21262d;
    border: 1px solid #30363d;
    color: #8b949e;
    font-size: 0.8em;
    cursor: pointer;
  }}
  .tab.active {{ background: #58a6ff; color: #0d1117; border-color: #58a6ff; }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 14px;
  }}
  .card {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 16px;
    cursor: pointer;
    transition: transform 0.15s, border-color 0.15s;
    height: 140px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    overflow: hidden;
  }}
  .card:hover {{ transform: translateY(-3px); border-color: #58a6ff; }}
  .card .title {{
    color: #e6edf3;
    font-weight: 600;
    font-size: 0.95em;
    line-height: 1.4;
    display: -webkit-box;
    -webkit-line-clamp: 4;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }}
  .badge {{
    display: inline-block; margin-top: 10px; padding: 3px 10px;
    border-radius: 20px; background: #21262d; color: #8b949e; font-size: 0.72em;
    width: fit-content;
  }}
  /* DETAIL PANEL - stays visible while page scrolls */
  .panel {{
    width: 320px;
    background: #161b22;
    border-left: 1px solid #30363d;
    padding: 30px 24px;
    position: sticky;
    top: 0;
    height: 100vh;
    overflow-y: auto;
    align-self: flex-start;
  }}
  .panel h3 {{ color: #e6edf3; font-size: 1.1em; margin-bottom: 12px; line-height: 1.4; }}
  .panel .src {{ color: #8b949e; font-size: 0.85em; margin-bottom: 20px; }}
  .panel a.open {{
    display: block; text-align: center; background: #58a6ff; color: #0d1117;
    padding: 10px; border-radius: 8px; text-decoration: none; font-weight: 600;
  }}
  .panel .empty {{ color: #6e7681; font-size: 0.9em; }}
  .empty-state {{ text-align: center; color: #8b949e; padding: 60px 0; }}
  .top-picks {{ margin-bottom: 28px; }}
  .top-picks h2 {{ font-size: 1.1em; color: #e6edf3; margin-bottom: 12px; }}
  .top-card {{
    background: linear-gradient(135deg, #1c2333, #161b22);
    border: 1px solid #58a6ff;
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 10px;
    cursor: pointer;
  }}
  .top-card:hover {{ border-color: #a371f7; }}
  .top-card .rank {{ color: #58a6ff; font-weight: 700; font-size: 0.8em; margin-right: 6px; }}
  .top-card .title {{ color: #e6edf3; font-weight: 600; display: inline; }}
</style>
</head>
<body>
  <div class="sidebar">
    <div class="logo">🤖</div>
    <a href="index.html" class="icon" style="text-decoration:none;">🏠</a>
    <a href="tools.html" class="icon" style="text-decoration:none;">🛠️</a>
    <div class="icon">🔖</div>
    <div class="icon">⚙️</div>
  </div>

  <div class="main">
    <h1>AI News Digest</h1>
    <div class="date">Last updated: {datetime.now(IST).strftime('%Y-%m-%d %H:%M')} IST · Refreshes hourly</div>
    <div id="topPicks"></div>
    <div class="topbar">
      <input class="search" id="search" placeholder="Search title or source...">
    </div>
    <div class="tabs" id="tabs"></div>
    <div class="grid" id="grid"></div>
  </div>

  <div class="panel" id="panel">
    <p class="empty">Click an article to preview it here.</p>
  </div>

<script>
const articles = {articles_json};
const sources = {json.dumps(sources)};
let activeSource = "All";

function renderTopPicks() {{
  const topEl = document.getElementById('topPicks');
  if (articles.length === 0) {{ topEl.innerHTML = ''; return; }}
  const top3 = articles.slice(0, 3);
  topEl.innerHTML = `
    <div class="top-picks">
      <h2>🔥 Top Picks</h2>
      ${{top3.map((a, i) =>
        `<div class="top-card" onclick="showDetail(${{i}})">
           <span class="rank">#${{i+1}}</span><span class="title">${{escapeHtml(a.title)}}</span>
           <div class="badge">${{escapeHtml(a.source)}}</div>
         </div>`
      ).join('')}}
    </div>
  `;
}}

function renderTabs() {{
  const tabsEl = document.getElementById('tabs');
  const all = ["All", ...sources];
  tabsEl.innerHTML = all.map(s =>
    `<div class="tab ${{s === activeSource ? 'active' : ''}}" onclick="setSource('${{s.replace(/'/g, "\\\\'")}}')">${{s}}</div>`
  ).join('');
}}

function setSource(s) {{
  activeSource = s;
  renderTabs();
  renderGrid();
}}

function escapeHtml(text) {{
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}}

function renderGrid() {{
  const query = document.getElementById('search').value.toLowerCase();
  const gridEl = document.getElementById('grid');
  const filtered = articles
    .map((a, i) => ({{ ...a, _idx: i }}))
    .filter(a => {{
      const matchesSource = activeSource === "All" || a.source === activeSource;
      const matchesQuery = a.title.toLowerCase().includes(query) || a.source.toLowerCase().includes(query);
      return matchesSource && matchesQuery;
    }});
  if (filtered.length === 0) {{
    gridEl.innerHTML = '<div class="empty-state">No articles match.</div>';
    return;
  }}
  gridEl.innerHTML = filtered.map(a =>
    `<div class="card" onclick="showDetail(${{a._idx}})">
       <div class="title">${{escapeHtml(a.title)}}</div>
       <div class="badge">${{escapeHtml(a.source)}}</div>
     </div>`
  ).join('');
}}

function showDetail(idx) {{
  const a = articles[idx];
  document.getElementById('panel').innerHTML = `
    <h3>${{escapeHtml(a.title)}}</h3>
    <div class="src">Source: ${{escapeHtml(a.source)}}</div>
    <a class="open" href="${{a.link}}" target="_blank">Open Article →</a>
  `;
}}

document.getElementById('search').addEventListener('input', renderGrid);
renderTopPicks();
renderTabs();
renderGrid();
</script>
</body></html>"""

    with open("index.html", "w") as f:
        f.write(html)
    print("✓ Webpage generated (index.html)")

TOOL_FEEDS = [
    "https://www.producthunt.com/topics/artificial-intelligence.rss",
]

TOOL_CATEGORIES = {
    "Writing": ["writing", "copywriting", "content", "blog", "essay", "grammar"],
    "Coding": ["code", "coding", "developer", "programming", "IDE", "debug"],
    "Image": ["image", "photo", "design", "art", "avatar", "graphic"],
    "Video": ["video", "editing", "animation", "clip"],
    "Audio": ["audio", "voice", "music", "speech", "podcast"],
    "Productivity": ["productivity", "workflow", "automation", "task", "notes"],
    "Research": ["research", "search", "data", "analysis", "summarize"],
    "Chatbot/Assistant": ["chatbot", "assistant", "agent", "companion"],
}

def categorize_tool(title, description=""):
    """
    Figure out which 'bucket' a tool belongs in based on its name/description
    Think: Sorting laundry into piles by type
    """
    text = (title + " " + description).lower()
    for category, words in TOOL_CATEGORIES.items():
        if any(w in text for w in words):
            return category
    return "Other"

def fetch_tools():
    """Get latest AI tools from Product Hunt and sort into categories"""
    tools = []
    for feed_url in TOOL_FEEDS:
        try:
            feed = feedparser.parse(feed_url, agent="Mozilla/5.0 (AI-News-Bot/1.0)")
            for entry in feed.entries[:30]:
                title = entry.get("title", "No title")
                link = entry.get("link", "")
                description = entry.get("description", "")
                tools.append({
                    "title": title,
                    "link": link,
                    "category": categorize_tool(title, description)
                })
        except Exception as e:
            print(f"Error fetching tools feed: {e}")
    return tools

def build_tools_page(tools):
    """Create a tools.html page, grouped by category"""
    grouped = {}
    for tool in tools:
        grouped.setdefault(tool["category"], []).append(tool)

    tools_json = json.dumps(tools)
    categories = sorted(grouped.keys())

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AI Tools Directory</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', -apple-system, sans-serif; background: #0d1117; color: #c9d1d9; padding: 30px 40px; max-width: 1000px; margin: 0 auto; }}
  h1 {{ font-size: 1.8em; background: linear-gradient(90deg, #58a6ff, #a371f7); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 6px; }}
  .date {{ color: #8b949e; font-size: 0.85em; margin-bottom: 20px; }}
  nav {{ margin-bottom: 10px; }}
  nav a {{ color: #58a6ff; text-decoration: none; font-size: 0.9em; }}
  .tabs {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 20px 0; }}
  .tab {{ padding: 6px 14px; border-radius: 20px; background: #21262d; border: 1px solid #30363d; color: #8b949e; font-size: 0.8em; cursor: pointer; }}
  .tab.active {{ background: #58a6ff; color: #0d1117; border-color: #58a6ff; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 16px; height: 100px; display: flex; flex-direction: column; justify-content: space-between; overflow: hidden; }}
  .card a {{ color: #e6edf3; text-decoration: none; font-weight: 600; font-size: 0.95em; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }}
  .card a:hover {{ color: #58a6ff; }}
  .badge {{ padding: 3px 10px; border-radius: 20px; background: #21262d; color: #8b949e; font-size: 0.72em; width: fit-content; }}
  .empty-state {{ text-align: center; color: #8b949e; padding: 60px 0; }}
</style>
</head>
<body>
  <nav><a href="index.html">← Back to News</a></nav>
  <h1>🛠️ AI Tools Directory</h1>
  <div class="date">Last updated: {datetime.now(IST).strftime('%Y-%m-%d %H:%M')} IST</div>
  <div class="tabs" id="tabs"></div>
  <div class="grid" id="grid"></div>

<script>
const tools = {tools_json};
const categories = {json.dumps(categories)};
let activeCat = "All";

function renderTabs() {{
  const all = ["All", ...categories];
  document.getElementById('tabs').innerHTML = all.map(c =>
    `<div class="tab ${{c === activeCat ? 'active' : ''}}" onclick="setCat('${{c}}')">${{c}}</div>`
  ).join('');
}}

function setCat(c) {{ activeCat = c; renderTabs(); renderGrid(); }}

function renderGrid() {{
  const filtered = activeCat === "All" ? tools : tools.filter(t => t.category === activeCat);
  const gridEl = document.getElementById('grid');
  if (filtered.length === 0) {{
    gridEl.innerHTML = '<div class="empty-state">No tools in this category yet.</div>';
    return;
  }}
  gridEl.innerHTML = filtered.map(t =>
    `<div class="card">
       <a href="${{t.link}}" target="_blank">${{t.title}}</a>
       <div class="badge">${{t.category}}</div>
     </div>`
  ).join('');
}}

renderTabs();
renderGrid();
</script>
</body></html>"""

    with open("tools.html", "w") as f:
        f.write(html)
    print("✓ Tools page generated (tools.html)")

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
    
    # Rank by importance (most relevant first)
    relevant = rank_articles(relevant)
    new_articles = rank_articles(new_articles)
    
    # Post to Discord
    post_to_discord(new_articles)
    
    # Build webpage
    build_webpage(relevant)
    
    # Build tools directory page
    print("Fetching AI tools...")
    tools = fetch_tools()
    print(f"Found {len(tools)} tools")
    build_tools_page(tools)
    
    print("Done!")

if __name__ == "__main__":
    main()
