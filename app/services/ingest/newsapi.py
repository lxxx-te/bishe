"""NewsAPI.org client. Returns the same FeedItem shape as rss.py.

Free tier limits: country param restricted to a handful; we default to 'us'
for demo (China not allowed on free tier) but keep 'cn' configurable.
"""
from __future__ import annotations

from datetime import datetime

import httpx

from app.services.ingest.rss import FeedItem


def fetch_newsapi(api_key: str, country: str = "us", page_size: int = 50) -> list[FeedItem]:
    if not api_key:
        print("[ingest.newsapi] no API key, skipping")
        return []
    url = "https://newsapi.org/v2/top-headlines"
    params = {"country": country, "pageSize": page_size, "apiKey": api_key}
    try:
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[ingest.newsapi] request failed: {e}")
        return []

    if data.get("status") != "ok":
        print(f"[ingest.newsapi] status not ok: {data.get('message')}")
        return []

    items: list[FeedItem] = []
    for art in data.get("articles", []):
        link = art.get("url") or ""
        title = art.get("title") or ""
        if not link or not title:
            continue
        pub = art.get("publishedAt")
        pub_dt = None
        if pub:
            try:
                pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            except Exception:
                pub_dt = None
        items.append(
            FeedItem(
                source_site=f"NewsAPI:{art.get('source', {}).get('name', 'unknown')}",
                title=title.strip(),
                raw_text=(art.get("content") or art.get("description") or "").strip(),
                original_url=link.strip(),
                publish_time=pub_dt,
            )
        )
    print(f"[ingest.newsapi] {len(items)} items (country={country})")
    return items