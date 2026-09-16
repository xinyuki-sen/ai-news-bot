#!/usr/bin/env python3
"""
AI News Bot — Scrapes RSS feeds for AI news, filters, dedupes, ranks,
posts to Discord, and generates JSON data files for the static frontend.
"""

# ── Imports ──────────────────────────────────────────────────────────────
import feedparser
import sqlite3
import requests
import hashlib
import re
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

# ── Constants ────────────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))
DB_FILE = "seen_posts.db"
USER_AGENT = "Mozilla/5.0 (Chrono-AI/3.0; +https://xinyuki-sen.github.io/chrono/)"
MAX_NEWS_ARTICLES = 100
MAX_TOOL_ENTRIES = 60

# ── News Feeds ───────────────────────────────────────────────────────────
NEWS_FEEDS = [
    # Top Tech & Deep Tech
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://news.ycombinator.com/rss",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
    
    # Frontier Research & Papers
    "https://arxiv.org/rss/cs.AI",
    "https://arxiv.org/rss/cs.CL",   # Computation & Language (LLMs/NLP)
    "https://arxiv.org/rss/cs.LG",   # Machine Learning
    "https://huggingface.co/blog/feed.xml",
    "https://deepmind.google/blog/rss.xml",
    
    # AI Engineering & Industry Analysis
    "https://simonwillison.net/atom/everything/",
    "https://www.latent.space/feed",
    "https://www.reddit.com/r/MachineLearning/.rss",
    "https://www.reddit.com/r/artificial/.rss",
    
    # Video Briefs
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCbfYPyITQ-7l4upoX8nvctg",  # Two Minute Papers
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCZHmQk67mSJgfCCTn7xBfKw",  # 3Blue1Brown
]

# ── Tool Feeds ───────────────────────────────────────────────────────────
TOOL_FEEDS = [
    "https://www.producthunt.com/feed?category=ai",
]

# ── AI Relevance Keywords ────────────────────────────────────────────────
AI_KEYWORDS = [
    "LLM", "large language model", "GPT", "transformer",
    "neural network", "deep learning", "machine learning",
    "diffusion", "CLIP", "vision", "NLP", "AI safety",
    "open source", "model", "training", "inference",
    "embedding", "attention", "fine-tune", "dataset",
    "agent", "claude", "gemini", "deepseek", "reasoning",
]

HIGH_IMPACT_WORDS = [
    "breakthrough", "release", "launch", "new model", "state-of-the-art",
    "open source", "outperforms", "announces", "unveils", "frontier",
]

SOURCE_WEIGHT = {
    "cs.AI updates on arXiv.org": 3,
    "cs.CL updates on arXiv.org": 3,
    "cs.LG updates on arXiv.org": 3,
    "Google DeepMind": 3,
    "Hugging Face Blog": 3,
    "GitHub Trending AI": 3,
    "Hacker News": 2,
    "Ars Technica - All content": 2,
    "TechCrunch": 2,
    "VentureBeat": 2,
    "The Verge": 2,
    "MIT Technology Review": 3,
    "Simon Willison’s Weblog": 2,
    "Latent Space": 2,
}

SOURCE_CATEGORY = {
    "cs.AI updates on arXiv.org": "Research",
    "cs.CL updates on arXiv.org": "Research",
    "cs.LG updates on arXiv.org": "Research",
    "Google DeepMind": "Frontiers",
    "Hugging Face Blog": "Open Source",
    "GitHub Trending AI": "Open Source",
    "Hacker News": "Products",
    "Ars Technica - All content": "LLMs",
    "TechCrunch": "Industry",
    "VentureBeat": "Enterprise AI",
    "The Verge": "Products",
    "MIT Technology Review": "Research",
    "Simon Willison’s Weblog": "Coding",
    "Latent Space": "Engineering",
}

# ── Tool Categories ──────────────────────────────────────────────────────
TOOL_CATEGORIES = {
    "Writing":            ["writing", "copywriting", "content", "blog", "essay", "grammar"],
    "Coding":             ["code", "coding", "developer", "programming", "ide", "debug", "github", "api"],
    "Image":              ["image", "photo", "design", "art", "avatar", "graphic", "midjourney", "dalle"],
    "Video":              ["video", "editing", "animation", "clip", "youtube"],
    "Audio":              ["audio", "voice", "music", "speech", "podcast", "text-to-speech"],
    "Productivity":       ["productivity", "workflow", "automation", "task", "notes", "notion", "calendar"],
    "Research":           ["research", "search", "data", "analysis", "summarize", "paper", "arxiv"],
    "Chatbot/Assistant":  ["chatbot", "assistant", "agent", "companion", "copilot"],
}


# ═══════════════════════════════════════════════════════════════════════════
# DATABASE — Dedupe memory using SQLite
# ═══════════════════════════════════════════════════════════════════════════

def init_db():
    """Create the seen-posts table if it doesn't exist."""
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS posts "
        "(post_hash TEXT PRIMARY KEY, title TEXT, date_added TEXT)"
    )
    conn.commit()
    conn.close()


def prune_old_posts(days: int = 60):
    """Prune posts older than `days` to keep DB fast and compact."""
    cutoff = (datetime.now(IST) - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("DELETE FROM posts WHERE date_added < ?", (cutoff,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted > 0:
        print(f"  ✓ Database maintenance: pruned {deleted} entries older than {days} days")


def is_seen(title: str) -> bool:
    """Check if we've already processed this title."""
    post_hash = hashlib.md5(title.encode()).hexdigest()
    conn = sqlite3.connect(DB_FILE)
    result = conn.execute(
        "SELECT 1 FROM posts WHERE post_hash = ?", (post_hash,)
    ).fetchone()
    conn.close()
    return result is not None


def mark_seen(title: str):
    """Record a title so we skip it next time."""
    post_hash = hashlib.md5(title.encode()).hexdigest()
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "INSERT OR IGNORE INTO posts VALUES (?, ?, ?)",
        (post_hash, title, datetime.now(IST).isoformat()),
    )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", text).strip()


def clean_latex(text: str) -> str:
    """Sanitize raw LaTeX syntax from arXiv abstracts for clean display."""
    if not text:
        return ""
    text = re.sub(r"\$([^\$]+)\$", r"\1", text)
    text = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\[a-zA-Z]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def atomic_write_json(filepath: str, data):
    """Write JSON file atomically using a temp file to prevent corruption."""
    dirname = os.path.dirname(filepath) or "."
    with tempfile.NamedTemporaryFile("w", dir=dirname, delete=False, encoding="utf-8") as tf:
        json.dump(data, tf, ensure_ascii=False, indent=2)
        temp_name = tf.name
    os.replace(temp_name, filepath)


def is_relevant(title: str, description: str = "") -> bool:
    """Check if title/description contains AI keywords."""
    text = f"{title} {description}".lower()
    return any(kw.lower() in text for kw in AI_KEYWORDS)


def categorize_tool(title: str, description: str = "") -> str:
    """Assign a category to a tool based on keyword matching."""
    text = f"{title} {description}".lower()
    for category, keywords in TOOL_CATEGORIES.items():
        if any(kw in text for kw in keywords):
            return category
    return "Other"


def score_article(article: dict) -> int:
    """Score an article for ranking — higher = more important."""
    text = f"{article['title']} {article.get('description', '')}".lower()
    score = sum(1 for k in AI_KEYWORDS if k.lower() in text)
    score += sum(2 for w in HIGH_IMPACT_WORDS if w.lower() in text)
    score += SOURCE_WEIGHT.get(article["source"], 1)
    return score


# ═══════════════════════════════════════════════════════════════════════════
# NEWS PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

def fetch_feed(feed_url: str) -> list:
    """Download an RSS feed with timeout protection and extract clean articles."""
    try:
        # Use requests with strict timeout to prevent slow hanging feeds
        resp = requests.get(feed_url, headers={"User-Agent": USER_AGENT}, timeout=(5, 10))
        if resp.status_code == 200:
            feed = feedparser.parse(resp.content)
        else:
            feed = feedparser.parse(feed_url, agent=USER_AGENT)

        source_title = feed.feed.get("title", feed_url.split("/")[2] if "/" in feed_url else "Unknown")
        articles = []
        for entry in feed.entries[:20]:
            raw_desc = entry.get("description", "") or entry.get("summary", "")
            clean_desc = clean_latex(strip_html(raw_desc))
            articles.append({
                "title": clean_latex(entry.get("title", "No title")),
                "link": entry.get("link", ""),
                "description": clean_desc,
                "source": source_title,
            })
        return articles
    except Exception as e:
        print(f"  ✗ Error fetching {feed_url}: {e}")
        return []


def fetch_all_news_feeds(urls: list, max_workers: int = 6) -> list:
    """Fetch multiple RSS feeds concurrently using ThreadPoolExecutor."""
    all_articles = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {executor.submit(fetch_feed, url): url for url in urls}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                articles = future.result()
                all_articles.extend(articles)
                domain = url.split("/")[2] if len(url.split("/")) > 2 else url
                print(f"  · {domain:35s} → {len(articles)} entries")
            except Exception as e:
                print(f"  ✗ Concurrent fetch failed for {url}: {e}")
    return all_articles


def filter_articles(articles: list) -> list:
    """Keep only AI-relevant articles."""
    return [a for a in articles if is_relevant(a["title"], a["description"])]


def rank_articles(articles: list) -> list:
    """Sort articles by importance score (descending)."""
    return sorted(articles, key=score_article, reverse=True)


def dedupe_articles(articles: list) -> list:
    """Remove articles we've already seen and mark the new ones."""
    new = []
    for a in articles:
        if not is_seen(a["title"]):
            new.append(a)
            mark_seen(a["title"])
    return new


def post_to_discord(articles: list):
    """Send a formatted digest to the Discord webhook."""
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("  ⚠ DISCORD_WEBHOOK_URL not set — skipping Discord post.")
        return
    if not articles:
        print("  ℹ No new articles to post.")
        return

    today = datetime.now(IST).strftime("%Y-%m-%d")
    lines = [f"⚡ **Chrono AI Intelligence Brief** — {today}\n"]
    for i, a in enumerate(articles[:15], 1):
        lines.append(f"{i}. **{a['title']}**")
        lines.append(f"   {a['source']} — {a.get('link', '')}\n")

    try:
        requests.post(webhook_url, json={"content": "\n".join(lines)}, timeout=10)
        print(f"  ✓ Posted {min(len(articles), 15)} articles to Discord")
    except Exception as e:
        print(f"  ✗ Discord post failed: {e}")


def build_news_json(articles: list):
    """Generate news.json for the frontend atomically."""
    clean = []
    for i, a in enumerate(articles[:MAX_NEWS_ARTICLES], 1):
        cat = SOURCE_CATEGORY.get(
            a["source"], categorize_tool(a["title"], a.get("description", ""))
        )
        summary = strip_html(a.get("description", ""))[:300]
        clean.append({
            "id": i,
            "title": a["title"],
            "url": a["link"],
            "source": a["source"],
            "category": cat,
            "summary": summary or "No summary available.",
            "published_at": datetime.now(IST).isoformat(),
        })

    data = {
        "last_updated": datetime.now(IST).isoformat(),
        "top_pick_ids": [a["id"] for a in clean[:3]],
        "articles": clean,
    }

    atomic_write_json("news.json", data)
    print(f"  ✓ news.json — {len(clean)} articles")


def fetch_hf_trending_models() -> list:
    """Fetch top trending open AI models from Hugging Face Hub open API."""
    models = []
    try:
        url = "https://huggingface.co/api/models?sort=trendingScore&direction=-1&limit=8"
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=8)
        if resp.status_code == 200:
            for item in resp.json():
                model_id = item.get("id", "")
                downloads = item.get("downloads", 0)
                likes = item.get("likes", 0)
                pipeline = item.get("pipeline_tag", "AI Model")
                summary = f"🔥 Trending on Hugging Face · {likes:,} likes · {downloads:,} downloads · Task: {pipeline}"
                models.append({
                    "title": f"HF Model: {model_id}",
                    "link": f"https://huggingface.co/models/{model_id}",
                    "description": summary,
                    "source": "Hugging Face Blog",
                    "category": "Open Source",
                })
            print(f"  ✓ Hugging Face Hub: fetched {len(models)} trending models")
    except Exception as e:
        print(f"  ✗ Hugging Face API request failed: {e}")
    return models


def fetch_github_trending_repos() -> list:
    """Fetch fast-growing, trending AI repositories from GitHub REST API."""
    repos = []
    headers = {"User-Agent": USER_AGENT}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"

    date_cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    url = (
        f"https://api.github.com/search/repositories"
        f"?q=topic:ai+pushed:>{date_cutoff}+stars:>20"
        f"&sort=stars&order=desc&per_page=15"
    )
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("items", []):
                stars = item.get("stargazers_count", 0)
                star_str = f"{stars/1000:.1f}k" if stars >= 1000 else str(stars)
                lang = item.get("language") or "Code"
                desc = strip_html(item.get("description") or "Open-source AI repository.")
                summary = f"⭐ {star_str} stars · {lang} — {desc}"
                repos.append({
                    "title": f"{item.get('full_name', item.get('name'))}",
                    "link": item.get("html_url", ""),
                    "description": summary,
                    "source": "GitHub Trending AI",
                    "category": "Open Source",
                })
            print(f"  ✓ GitHub Trending AI: fetched {len(repos)} repositories")
        else:
            print(f"  ✗ GitHub API error: HTTP {resp.status_code}")
    except Exception as e:
        print(f"  ✗ GitHub API request failed: {e}")
    return repos


# ═══════════════════════════════════════════════════════════════════════════
# TOOLS PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

def fetch_hn_tools() -> list:
    """Fetch AI-related Show HN posts from Hacker News Algolia API."""
    tools = []
    seen_links = set()
    queries = ["AI", "machine learning", "LLM", "GPT", "neural network"]

    for query in queries:
        try:
            url = (
                f"https://hn.algolia.com/api/v1/search_by_date"
                f"?tags=show_hn&query={query}&hitsPerPage=20"
            )
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            for hit in resp.json().get("hits", []):
                title = hit.get("title", "No title")
                link = hit.get("url") or (
                    f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                )
                if link in seen_links:
                    continue
                seen_links.add(link)
                desc = strip_html(hit.get("story_text", "") or "")[:200]
                tools.append({
                    "title": title,
                    "link": link,
                    "category": categorize_tool(title, desc),
                    "description": desc or "Shared on Hacker News.",
                })
        except Exception as e:
            print(f"  ✗ HN query '{query}' failed: {e}")

    return tools


def fetch_tools() -> list:
    """Aggregate tools from all tool feeds + Hacker News."""
    tools = []

    # RSS tool feeds
    for feed_url in TOOL_FEEDS:
        try:
            feed = feedparser.parse(feed_url, agent=USER_AGENT)
            for entry in feed.entries[:MAX_TOOL_ENTRIES]:
                title = entry.get("title", "No title")
                link = entry.get("link", "")
                desc = strip_html(entry.get("description", ""))[:300]
                tools.append({
                    "title": title,
                    "link": link,
                    "category": categorize_tool(title, desc),
                    "description": desc,
                })
        except Exception as e:
            print(f"  ✗ Tool feed error ({feed_url}): {e}")

    # Hacker News tools
    tools.extend(fetch_hn_tools())

    # GitHub Trending AI Repos as developer tools
    for repo in fetch_github_trending_repos():
        tools.append({
            "title": repo["title"],
            "link": repo["link"],
            "category": "Coding",
            "description": repo["description"],
        })

    # Deduplicate by link
    seen = set()
    unique = []
    for t in tools:
        if t["link"] not in seen:
            seen.add(t["link"])
            unique.append(t)
    return unique


def build_tools_json(tools: list):
    """Generate tools.json for the frontend atomically."""
    atomic_write_json("tools.json", tools)
    print(f"  ✓ tools.json — {len(tools)} tools")


# ═══════════════════════════════════════════════════════════════════════════
# PROMPTS LIBRARY
# ═══════════════════════════════════════════════════════════════════════════

PROMPTS = [
    # ── Coding ───────────────────────────────────────────────────────
    {"title": "Senior Code Reviewer",        "category": "Coding",
     "description": "Act as a senior software engineer. Review this code for performance, security, and maintainability. Suggest concrete improvements with code examples:\n\n[Paste Code]"},
    {"title": "Regex Generator",             "category": "Coding",
     "description": "Write a regular expression that matches [Desired Pattern]. Explain how each part of the regex works step-by-step, and provide 3 test examples."},
    {"title": "System Architecture Planner",  "category": "Coding",
     "description": "I need to build a system that does [System Requirements]. Propose a high-level architecture diagram, tech stack, database choices, and potential bottlenecks."},
    {"title": "API Documentation Generator",  "category": "Coding",
     "description": "Write clear, Markdown-formatted API documentation for a REST endpoint that accepts [Input] and returns [Output]. Include curl examples and error codes."},
    {"title": "Debug Detective",             "category": "Coding",
     "description": "I'm getting this error: [Error Message]. Here's the relevant code: [Code]. Walk me through the most likely causes and provide a fix."},
    {"title": "Git Commit Message Writer",   "category": "Coding",
     "description": "Write a clear, conventional commit message for the following code change. Follow the format: type(scope): description. Here's the diff:\n\n[Paste Diff]"},

    # ── Writing ──────────────────────────────────────────────────────
    {"title": "Blog Post Outline",           "category": "Writing",
     "description": "Create a comprehensive, SEO-optimized outline for a blog post about [Topic]. Include H2 and H3 headings, and suggest 5 keywords to target."},
    {"title": "Language Translation & Nuance","category": "Writing",
     "description": "Translate the following text into [Language]. Provide 3 options (formal, neutral, casual) and explain the cultural nuance of each:\n\n[Text]"},
    {"title": "Email Rewriter",              "category": "Writing",
     "description": "Rewrite this email to be more [professional/friendly/concise]. Keep the core message but improve clarity and tone:\n\n[Paste Email]"},

    # ── Marketing ────────────────────────────────────────────────────
    {"title": "Marketing Copywriter",        "category": "Marketing",
     "description": "Write a highly converting landing page hero section with a headline, subheadline, and 3 feature bullet points for: [Product Description]."},
    {"title": "Cold Email Outreach",         "category": "Marketing",
     "description": "Write a concise, engaging cold email to [Target Audience] offering [Service]. Keep it under 150 words with a clear call to action."},
    {"title": "Social Media Thread",         "category": "Marketing",
     "description": "Write a viral Twitter/X thread (8-10 tweets) explaining [Topic] in a way that's educational and shareable. Include a strong hook."},

    # ── Learning ─────────────────────────────────────────────────────
    {"title": "Explain Like I'm 5",          "category": "Learning",
     "description": "Explain the concept of [Concept] to me like I am 5 years old. Use simple analogies, no jargon, and a fun metaphor."},
    {"title": "Socratic Teacher",            "category": "Learning",
     "description": "I want to learn about [Topic]. Don't explain it directly — act as a Socratic teacher. Ask me guiding questions to help me figure it out myself."},
    {"title": "Interview Simulator",         "category": "Learning",
     "description": "Act as a strict technical interviewer for a [Role] position at a FAANG company. Ask one question at a time, wait for my answer, then evaluate it."},
    {"title": "Flashcard Generator",         "category": "Learning",
     "description": "Create 15 spaced-repetition flashcards (Q&A format) covering the key concepts of [Topic]. Order them from foundational to advanced."},

    # ── Research ─────────────────────────────────────────────────────
    {"title": "Data Analysis Guide",         "category": "Research",
     "description": "I have a dataset containing [Data description]. Suggest 5 interesting hypotheses to test and which Python libraries to use for each."},
    {"title": "Paper Summarizer",            "category": "Research",
     "description": "Summarize the following research paper in 3 sections: (1) What problem it solves, (2) How it works, (3) Why it matters. Write for a smart non-expert:\n\n[Paste Abstract]"},

    # ── Design ───────────────────────────────────────────────────────
    {"title": "UX/UI Design Critic",         "category": "Design",
     "description": "Act as a Lead Product Designer. I'll describe a UI flow: [Flow Description]. Critique it for usability, friction points, and accessibility issues."},
    {"title": "Color Palette Generator",     "category": "Design",
     "description": "Generate a cohesive color palette (primary, secondary, accent, background, text) for a [Type of App] with a [Mood] aesthetic. Provide hex codes."},

    # ── Image Generation ─────────────────────────────────────────────
    {"title": "Cinematic Portrait",          "category": "Image Generation",
     "description": "Cinematic portrait photograph of [Subject], shot on 35mm lens, moody lighting, neon cyberpunk city background, depth of field, highly detailed, 8k, photorealistic --ar 16:9"},
    {"title": "Vector Logo",                 "category": "Image Generation",
     "description": "Flat vector logo of a [Subject], minimal, geometric, solid background, dribbble style, corporate identity --no shading --ar 1:1"},
    {"title": "Isometric Illustration",      "category": "Image Generation",
     "description": "Isometric 3D illustration of [Scene], pastel colors, clean lines, minimal detail, white background, modern tech style --ar 4:3"},

    # ── Career ───────────────────────────────────────────────────────
    {"title": "Resume Bullet Rewriter",      "category": "Career",
     "description": "Rewrite these resume bullet points using the XYZ formula (Accomplished X, as measured by Y, by doing Z). Make them impactful and quantified:\n\n[Paste Bullets]"},
    {"title": "LinkedIn Post Creator",       "category": "Career",
     "description": "Write an engaging LinkedIn post about [Topic/Achievement]. Keep it authentic, not salesy. Include a hook, story, and call to engage."},
]


# ═══════════════════════════════════════════════════════════════════════════
# AI MODELS LEADERBOARD DATASET
# ═══════════════════════════════════════════════════════════════════════════

MODELS_LEADERBOARD = [
    {
        "id": "claude-3-7-sonnet",
        "name": "Claude 3.7 Sonnet",
        "provider": "Anthropic",
        "category": "Reasoning",
        "arena_elo": 1382,
        "swe_bench": 70.3,
        "math_score": 96.2,
        "context_window": "200K tokens",
        "context_tokens": 200000,
        "price_input": 3.00,
        "price_output": 15.00,
        "license_type": "Proprietary",
        "is_open": False,
        "multimodal": True,
        "strengths": "Hybrid reasoning (standard + extended thinking), state-of-the-art SWE-bench coding, front-end design, tool orchestration.",
        "link": "https://www.anthropic.com/claude/sonnet",
        "release_date": "2025-02"
    },
    {
        "id": "openai-o3-mini",
        "name": "OpenAI o3-mini",
        "provider": "OpenAI",
        "category": "Reasoning",
        "arena_elo": 1375,
        "swe_bench": 68.5,
        "math_score": 96.8,
        "context_window": "200K tokens",
        "context_tokens": 200000,
        "price_input": 1.10,
        "price_output": 4.40,
        "license_type": "Proprietary",
        "is_open": False,
        "multimodal": False,
        "strengths": "Fast low-latency reasoning, competitive math (AIME), STEM problem solving, low inference price.",
        "link": "https://openai.com/index/openai-o3-mini/",
        "release_date": "2025-01"
    },
    {
        "id": "gemini-2-5-flash",
        "name": "Gemini 2.5 Flash",
        "provider": "Google DeepMind",
        "category": "Fast & Lightweight",
        "arena_elo": 1370,
        "swe_bench": 66.8,
        "math_score": 95.0,
        "context_window": "1M tokens",
        "context_tokens": 1000000,
        "price_input": 0.075,
        "price_output": 0.30,
        "license_type": "Proprietary",
        "is_open": False,
        "multimodal": True,
        "strengths": "Ultra-fast response time, 1M context, exceptional cost efficiency, native multimodal audio/video/image.",
        "link": "https://deepmind.google/technologies/gemini/",
        "release_date": "2025-02"
    },
    {
        "id": "deepseek-r1",
        "name": "DeepSeek R1",
        "provider": "DeepSeek AI",
        "category": "Open Weights",
        "arena_elo": 1364,
        "swe_bench": 65.8,
        "math_score": 90.8,
        "context_window": "128K tokens",
        "context_tokens": 128000,
        "price_input": 0.55,
        "price_output": 2.19,
        "license_type": "MIT (Open Weights)",
        "is_open": True,
        "multimodal": False,
        "strengths": "Open-weights reasoning model, pure RL chain-of-thought, highly affordable self-hosting and API pricing.",
        "link": "https://github.com/deepseek-ai/DeepSeek-R1",
        "release_date": "2025-01"
    },
    {
        "id": "claude-3-5-sonnet",
        "name": "Claude 3.5 Sonnet",
        "provider": "Anthropic",
        "category": "Coding",
        "arena_elo": 1365,
        "swe_bench": 65.0,
        "math_score": 94.0,
        "context_window": "200K tokens",
        "context_tokens": 200000,
        "price_input": 3.00,
        "price_output": 15.00,
        "license_type": "Proprietary",
        "is_open": False,
        "multimodal": True,
        "strengths": "Reliable coding and refactoring, computer use API, nuanced writing, fast execution.",
        "link": "https://www.anthropic.com/claude",
        "release_date": "2024-10"
    },
    {
        "id": "openai-o1",
        "name": "OpenAI o1",
        "provider": "OpenAI",
        "category": "Reasoning",
        "arena_elo": 1378,
        "swe_bench": 67.2,
        "math_score": 96.4,
        "context_window": "200K tokens",
        "context_tokens": 200000,
        "price_input": 15.00,
        "price_output": 60.00,
        "license_type": "Proprietary",
        "is_open": False,
        "multimodal": True,
        "strengths": "Deep scientific reasoning, hard algorithmic math, full image analysis with thinking.",
        "link": "https://openai.com/o1/",
        "release_date": "2024-12"
    },
    {
        "id": "gemini-2-0-pro",
        "name": "Gemini 2.0 Pro Experimental",
        "provider": "Google DeepMind",
        "category": "Reasoning",
        "arena_elo": 1374,
        "swe_bench": 67.5,
        "math_score": 95.5,
        "context_window": "2M tokens",
        "context_tokens": 2000000,
        "price_input": 1.25,
        "price_output": 5.00,
        "license_type": "Proprietary",
        "is_open": False,
        "multimodal": True,
        "strengths": "Massive 2 Million token context window, complex multi-file repo coding, advanced tool use.",
        "link": "https://aistudio.google.com",
        "release_date": "2025-02"
    },
    {
        "id": "gpt-4-5",
        "name": "GPT-4.5 Preview",
        "provider": "OpenAI",
        "category": "Reasoning",
        "arena_elo": 1372,
        "swe_bench": 63.5,
        "math_score": 90.5,
        "context_window": "128K tokens",
        "context_tokens": 128000,
        "price_input": 75.00,
        "price_output": 150.00,
        "license_type": "Proprietary",
        "is_open": False,
        "multimodal": True,
        "strengths": "Massive scale model, unprecedented factual accuracy, nuanced emotional intelligence & writing.",
        "link": "https://openai.com/index/introducing-gpt-4-5/",
        "release_date": "2025-02"
    },
    {
        "id": "gpt-4o",
        "name": "GPT-4o (Omni)",
        "provider": "OpenAI",
        "category": "Vision / Multimodal",
        "arena_elo": 1358,
        "swe_bench": 58.2,
        "math_score": 88.7,
        "context_window": "128K tokens",
        "context_tokens": 128000,
        "price_input": 2.50,
        "price_output": 10.00,
        "license_type": "Proprietary",
        "is_open": False,
        "multimodal": True,
        "strengths": "Real-time multimodal speech and vision, broad general knowledge, daily utility assistant.",
        "link": "https://openai.com/index/hello-gpt-4o/",
        "release_date": "2024-05"
    },
    {
        "id": "deepseek-v3",
        "name": "DeepSeek V3",
        "provider": "DeepSeek AI",
        "category": "Open Weights",
        "arena_elo": 1335,
        "swe_bench": 49.2,
        "math_score": 88.5,
        "context_window": "128K tokens",
        "context_tokens": 128000,
        "price_input": 0.14,
        "price_output": 0.28,
        "license_type": "MIT (Open Weights)",
        "is_open": True,
        "multimodal": False,
        "strengths": "671B Mixture-of-Experts architecture, unbeatable price-to-performance ratio, multi-token prediction.",
        "link": "https://github.com/deepseek-ai/DeepSeek-V3",
        "release_date": "2024-12"
    },
    {
        "id": "qwen-2-5-coder-32b",
        "name": "Qwen 2.5 Coder 32B",
        "provider": "Alibaba Cloud",
        "category": "Coding",
        "arena_elo": 1310,
        "swe_bench": 51.6,
        "math_score": 86.4,
        "context_window": "128K tokens",
        "context_tokens": 128000,
        "price_input": 0.20,
        "price_output": 0.20,
        "license_type": "Apache 2.0 (Open Weights)",
        "is_open": True,
        "multimodal": False,
        "strengths": "World-class open-source coding specialist, excels at code generation, code repair, and repo reasoning.",
        "link": "https://github.com/QwenLM/Qwen2.5-Coder",
        "release_date": "2024-11"
    },
    {
        "id": "llama-3-3-70b",
        "name": "Llama 3.3 70B Instruct",
        "provider": "Meta AI",
        "category": "Open Weights",
        "arena_elo": 1318,
        "swe_bench": 45.8,
        "math_score": 86.9,
        "context_window": "128K tokens",
        "context_tokens": 128000,
        "price_input": 0.20,
        "price_output": 0.60,
        "license_type": "Llama 3.3 Community License",
        "is_open": True,
        "multimodal": False,
        "strengths": "Matches Llama 3.1 405B capabilities in a compact 70B parameter footprint, multilingual support.",
        "link": "https://ai.meta.com/llama/",
        "release_date": "2024-12"
    },
    {
        "id": "qwen-2-5-max",
        "name": "Qwen 2.5 Max",
        "provider": "Alibaba Cloud",
        "category": "Reasoning",
        "arena_elo": 1345,
        "swe_bench": 55.4,
        "math_score": 89.2,
        "context_window": "128K tokens",
        "context_tokens": 128000,
        "price_input": 1.60,
        "price_output": 6.40,
        "license_type": "Proprietary",
        "is_open": False,
        "multimodal": True,
        "strengths": "Flagship MoE model from Alibaba, high multilingual fluency, web search integration, math.",
        "link": "https://chat.qwenlm.ai/",
        "release_date": "2025-01"
    },
    {
        "id": "codestral-2501",
        "name": "Codestral 2501",
        "provider": "Mistral AI",
        "category": "Coding",
        "arena_elo": 1300,
        "swe_bench": 52.0,
        "math_score": 84.5,
        "context_window": "256K tokens",
        "context_tokens": 256000,
        "price_input": 0.30,
        "price_output": 0.90,
        "license_type": "Mistral Commercial / Free Research",
        "is_open": False,
        "multimodal": False,
        "strengths": "Supports 80+ programming languages, 256K context window with Fill-in-the-Middle (FIM) support.",
        "link": "https://mistral.ai/news/codestral-2501/",
        "release_date": "2025-01"
    }
]


def build_prompts_json():
    """Generate prompts.json for the frontend atomically."""
    atomic_write_json("prompts.json", PROMPTS)
    print(f"  ✓ prompts.json — {len(PROMPTS)} prompts")


def build_models_json():
    """Generate models.json for the frontend leaderboard atomically."""
    atomic_write_json("models.json", MODELS_LEADERBOARD)
    print(f"  ✓ models.json — {len(MODELS_LEADERBOARD)} models")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

def main():
    """Run the entire scrape → filter → rank → publish pipeline."""
    print("═" * 60)
    print("  Chrono AI v3.0 — Real-Time AI Intelligence Engine")
    print("═" * 60)

    # 1. Database initialization and cleanup
    init_db()
    prune_old_posts(days=60)

    # 2. Fetch news feeds concurrently
    print("\n📰 Fetching news feeds concurrently...")
    all_articles = fetch_all_news_feeds(NEWS_FEEDS, max_workers=6)

    # 3. Fetch GitHub Trending AI Repositories
    print("\n⭐ Fetching GitHub Trending AI repositories...")
    github_repos = fetch_github_trending_repos()
    all_articles.extend(github_repos)

    # 4. Fetch Hugging Face Trending Models
    print("\n🤗 Fetching Hugging Face Trending Models...")
    hf_models = fetch_hf_trending_models()
    all_articles.extend(hf_models)

    print(f"\n  Total fetched: {len(all_articles)}")

    # 5. Filter & rank
    relevant = filter_articles(all_articles)
    print(f"  AI-relevant:   {len(relevant)}")
    relevant = rank_articles(relevant)

    # 6. Dedupe for Discord
    new_articles = dedupe_articles(relevant)
    new_articles = rank_articles(new_articles)
    print(f"  New (unseen):   {len(new_articles)}")

    # 7. Discord
    print("\n💬 Discord...")
    post_to_discord(new_articles)

    # 8. Build news JSON
    print("\n📄 Generating data files...")
    build_news_json(relevant)

    # 9. Build tools JSON
    print("\n🛠️  Fetching tools...")
    tools = fetch_tools()
    print(f"  Total tools: {len(tools)}")
    build_tools_json(tools)

    # 10. Build prompts & models JSON
    build_prompts_json()
    build_models_json()

    print("\n" + "═" * 60)
    print("  ✅ All done!")
    print("═" * 60)


if __name__ == "__main__":
    main()

