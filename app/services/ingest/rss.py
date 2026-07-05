"""Pull RSS feeds declared in configs/feeds.yaml into a normalized item list.

P1 scope: fetch + normalize only. Summaries / embeddings come in P2.

Hardening vs naive feedparser.parse(url):
- httpx with User-Agent + 15s timeout (feedparser default urllib has none)
- per-feed exception isolation (one dead feed does not kill the batch)
- HTML tag stripping (RSS content:encoded often carries <p><img><a>)
- sync IO wrapped via asyncio.to_thread when called from async context
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import feedparser
import httpx

UA = "Mozilla/5.0 (compatible; NewsAggregatorBot/1.0; +bishe-research)"
FEED_TIMEOUT = 15.0

_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class FeedItem:
    source_site: str
    title: str
    raw_text: str
    original_url: str
    publish_time: datetime | None


def parse_published(entry: dict[str, Any]) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        tp = entry.get(key)
        if tp:
            try:
                from time import struct_time
                if isinstance(tp, struct_time):
                    return datetime(*tp[:6])
            except Exception:
                pass
    for key in ("published", "updated"):
        val = entry.get(key)
        if val:
            try:
                from email.utils import parsedate_to_datetime
                return parsedate_to_datetime(val)
            except Exception:
                continue
    return None


def _strip_html(text: str) -> str:
    # Remove tags, collapse whitespace (entity-decode keeps core text; good enough for P1).
    if not text:
        return ""
    no_tags = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", no_tags).strip()


def extract_text(entry: dict[str, Any]) -> str:
    # Prefer full content, fall back to summary/description. HTML-stripped.
    for key in ("content", "content:encoded", "summary", "description"):
        val = entry.get(key)
        if not val:
            continue
        if isinstance(val, list):
            parts = [v.get("value", "") for v in val if isinstance(v, dict)]
            text = " ".join(p for p in parts if p)
            if text.strip():
                return _strip_html(text)
        if isinstance(val, str) and val.strip():
            return _strip_html(val)
    return ""


def _fetch_one(feed: dict) -> list[FeedItem]:
    """Blocking fetch + parse for one feed. Returns [] on any error."""
    name = feed.get("name") or feed.get("url") or "unknown"
    url = feed.get("url")
    if not url:
        return []
    try:
        with httpx.Client(
            timeout=FEED_TIMEOUT,
            headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            body = resp.content  # bytes; feedparser handles both bytes and str
    except Exception as e:
        print(f"[ingest.rss] {name}: fetch failed ({type(e).__name__}: {e})")
        return []

    parsed = feedparser.parse(body)
    if not parsed.entries:
        print(f"[ingest.rss] {name}: 0 entries (non-RSS body? HTTP {resp.status_code}, {len(body)}B)")
        return []

    items: list[FeedItem] = []
    for entry in parsed.entries:
        link = entry.get("link") or ""
        title = entry.get("title") or ""
        text = extract_text(entry)
        if not link or not title:
            continue
        items.append(
            FeedItem(
                source_site=name,
                title=title.strip(),
                raw_text=text,
                original_url=link.strip(),
                publish_time=parse_published(entry),
            )
        )
    print(f"[ingest.rss] {name}: {len(items)} items")
    return items


async def fetch_rss(feeds: list[dict]) -> list[FeedItem]:
    """Async wrapper. Each blocking _fetch_one runs in a worker thread, so a
    slow/dead feed does not stall the event loop. Feeds run sequentially so
    we can skim logs; parallelize with asyncio.gather later if needed."""
    items: list[FeedItem] = []
    for feed in feeds:
        # asyncio.to_thread does not export to a thread that polls; runnable
        result = await asyncio.to_thread(_fetch_one, feed)
        items.extend(result)
    return items