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
    Writes news.json for the enhanced frontend to consume.
    The index.html is a static template in the repo — the bot
    just updates the data file, not the HTML.
    """
    SOURCE_CATEGORY = {
        "cs.AI updates on arXiv.org": "Research",
        "Hacker News": "Products",
        "Ars Technica - All content": "LLMs",
    }

    clean_articles = []
    for i, a in enumerate(articles[:40], 1):
        cat = SOURCE_CATEGORY.get(a["source"], categorize_tool(a["title"], a.get("description", "")))
        summary = a.get("description", "")
        if summary:
            # Strip basic HTML tags from RSS descriptions
            import re
            summary = re.sub(r'<[^>]+>', '', summary).strip()[:300]
        clean_articles.append({
            "id": i,
            "title": a["title"],
            "url": a["link"],
            "source": a["source"],
            "category": cat,
            "summary": summary or "No summary available.",
            "published_at": datetime.now(IST).isoformat(),
        })

    top_pick_ids = [a["id"] for a in clean_articles[:3]]

    data = {
        "last_updated": datetime.now(IST).isoformat(),
        "top_pick_ids": top_pick_ids,
        "articles": clean_articles,
    }

    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("✓ news.json generated")





TOOL_FEEDS = [
    "https://www.producthunt.com/feed?category=ai",
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

def fetch_hn_tools():
    tools = []
    queries = ["AI", "machine learning", "LLM", "GPT"]
    try:
        for q in queries:
            url = f"https://hn.algolia.com/api/v1/search_by_date?tags=show_hn&query={q}&hitsPerPage=20"
            response = requests.get(url, timeout=10)
            data = response.json()
            for hit in data.get("hits", []):
                title = hit.get("title", "No title")
                link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                if not any(t["link"] == link for t in tools): # avoid duplicates
                    tools.append({"title": title, "link": link, "category": categorize_tool(title), "description": hit.get("story_text", "")[:200] if hit.get("story_text") else "Built and shared on Hacker News."})
    except Exception as e:
        print(f"Error fetching HN tools: {e}")
    return tools

def fetch_tools():
    """Get latest AI tools from Product Hunt and sort into categories"""
    tools = []
    for feed_url in TOOL_FEEDS:
        try:
            feed = feedparser.parse(feed_url, agent="Mozilla/5.0 (AI-News-Bot/1.0)")
            for entry in feed.entries[:50]: # Increased to 50
                title = entry.get("title", "No title")
                link = entry.get("link", "")
                description = entry.get("description", "")
                
                # Clean description
                import re
                clean_desc = re.sub(r'<[^>]+>', '', description).strip()[:300]
                
                tools.append({
                    "title": title,
                    "link": link,
                    "category": categorize_tool(title, clean_desc),
                    "description": clean_desc
                })
        except Exception as e:
            print(f"Error fetching tools feed: {e}")
    
    tools.extend(fetch_hn_tools())
    
    # Deduplicate tools by link
    seen = set()
    unique_tools = []
    for t in tools:
        if t["link"] not in seen:
            seen.add(t["link"])
            unique_tools.append(t)
            
    return unique_tools
    
def build_tools_page(tools):
    """
    Writes tools.json for the enhanced frontend to consume.
    """
    with open("tools.json", "w", encoding="utf-8") as f:
        json.dump(tools, f, ensure_ascii=False, indent=2)
    print("✓ tools.json generated")

def build_prompts_page():
    """
    Generates a curated list of high-quality AI prompts into prompts.json
    """
    prompts = [
        {
            "title": "Senior Code Reviewer",
            "category": "Coding",
            "description": "Act as a senior software engineer. Review this code for performance, security, and maintainability. Suggest concrete improvements: [Paste Code]"
        },
        {
            "title": "Explain Like I'm 5",
            "category": "Learning",
            "description": "Explain the concept of [Concept] to me like I am 5 years old. Use simple analogies and avoid jargon."
        },
        {
            "title": "Marketing Copywriter",
            "category": "Marketing",
            "description": "Write a highly converting, punchy landing page hero section and 3 feature bullet points for a product that does [Product Description]."
        },
        {
            "title": "Interview Simulator",
            "category": "Career",
            "description": "Act as a strict technical interviewer for a [Role] position. Ask me one question at a time and wait for my response before continuing. Evaluate my answers."
        },
        {
            "title": "Midjourney Cinematic Portrait",
            "category": "Image Generation",
            "description": "Cinematic portrait photograph of [Subject], shot on 35mm lens, moody lighting, neon cyberpunk city background, depth of field, highly detailed, 8k, photorealistic --ar 16:9"
        },
        {
            "title": "Regex Generator",
            "category": "Coding",
            "description": "Write a regular expression that matches [Desired Pattern]. Explain how each part of the regex works step-by-step."
        },
        {
            "title": "Cold Email Outreach",
            "category": "Marketing",
            "description": "Write a concise, engaging cold email to a [Target Audience] offering [Your Service]. Keep it under 150 words and include a clear call to action."
        },
        {
            "title": "Language Translation & Nuance",
            "category": "Writing",
            "description": "Translate the following text into [Language]. Provide 3 options ranging from formal to casual, and explain the cultural nuance of each choice: [Text]"
        },
        {
            "title": "UX/UI Design Critic",
            "category": "Design",
            "description": "Act as a Lead Product Designer. I will describe a UI flow for my app: [Flow Description]. Critique it for usability, friction points, and accessibility."
        },
        {
            "title": "System Architecture Planner",
            "category": "Coding",
            "description": "I need to build a system that does [System Requirements]. Propose a high-level system architecture, including tech stack, database choices, and potential bottlenecks to watch out for."
        },
        {
            "title": "Blog Post Outline",
            "category": "Writing",
            "description": "Create a comprehensive, SEO-optimized outline for a blog post about [Topic]. Include H2 and H3 headings, and suggest keywords to target."
        },
        {
            "title": "Data Analysis Guide",
            "category": "Research",
            "description": "I have a dataset containing [Data description]. Give me 5 interesting hypotheses I could test with this data, and suggest which Python libraries I should use."
        },
        {
            "title": "Socratic Teacher",
            "category": "Learning",
            "description": "I want to learn about [Topic]. Do not explain it to me directly. Instead, act as a Socratic teacher and ask me guiding questions to help me figure it out myself."
        },
        {
            "title": "API Documentation Generator",
            "category": "Coding",
            "description": "Write clear, Markdown-formatted API documentation for a REST endpoint that accepts [Input] and returns [Output]. Include curl examples and error codes."
        },
        {
            "title": "Midjourney Vector Logo",
            "category": "Image Generation",
            "description": "Flat vector logo of a [Subject], minimal, geometric, solid background, dribbble style, corporate identity --no shading --ar 1:1"
        }
    ]
    with open("prompts.json", "w", encoding="utf-8") as f:
        json.dump(prompts, f, ensure_ascii=False, indent=2)
    print("✓ prompts.json generated")

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
    
    # Build prompts page
    build_prompts_page()
    
    print("Done!")

if __name__ == "__main__":
    main()
