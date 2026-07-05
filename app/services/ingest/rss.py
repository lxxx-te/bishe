"""Pull RSS feeds declared in configs/feeds.yaml into a normalized item list.

P1 scope: fetch + normalize only. Summaries / embeddings come in P2.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import feedparser


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


def extract_text(entry: dict[str, Any]) -> str:
    # Prefer full content, fall back to summary/description.
    for key in ("content", "content:encoded", "summary", "description"):
        val = entry.get(key)
        if not val:
            continue
        if isinstance(val, list):
            # feedparser content list: [{"value": "...", "type": "..."}]
            parts = [v.get("value", "") for v in val if isinstance(v, dict)]
            text = " ".join(p for p in parts if p)
            if text.strip():
                return text
        if isinstance(val, str) and val.strip():
            return val
    return ""


def fetch_rss(feeds: list[dict]) -> list[FeedItem]:
    items: list[FeedItem] = []
    for feed in feeds:
        name = feed.get("name") or feed.get("url") or "unknown"
        url = feed.get("url")
        if not url:
            continue
        parsed = feedparser.parse(url)
        n_ok = 0
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
                    raw_text=text.strip(),
                    original_url=link.strip(),
                    publish_time=parse_published(entry),
                )
            )
            n_ok += 1
        print(f"[ingest.rss] {name}: {n_ok} items")
    return items