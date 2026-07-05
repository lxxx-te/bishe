"""Orchestrator: load feeds config, pull RSS + NewsAPI, persist."""
from __future__ import annotations

import asyncio
from pathlib import Path

import yaml

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.ingest.newsapi import fetch_newsapi
from app.services.ingest.persist import persist_items
from app.services.ingest.rss import FeedItem, fetch_rss


def load_feeds_config() -> list[dict]:
    cfg = Path(settings.root) / "configs" / "feeds.yaml"
    with cfg.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("feeds", []) or []


def load_newsapi_config() -> dict:
    cfg = Path(settings.root) / "configs" / "feeds.yaml"
    with cfg.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("newsapi", {}) or {}


async def run_ingest() -> dict:
    feeds = load_feeds_config()
    na_cfg = load_newsapi_config()

    items: list[FeedItem] = []
    items.extend(await fetch_rss(feeds))
    if na_cfg.get("enabled", True):
        # NewsAPI call is sync; wrap so it does not stall loop only if it stalls
        result = await asyncio.to_thread(
            fetch_newsapi,
            settings.newsapi_api_key,
            na_cfg.get("country", "us"),
            na_cfg.get("page_size", 50),
        )
        items.extend(result)

    async with AsyncSessionLocal() as session:
        stats = await persist_items(session, items)
    print(f"[ingest] done: {stats}")
    return {"total_fetched": len(items), **stats}