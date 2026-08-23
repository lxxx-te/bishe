"""P1 ingest smoke test: feeds config loads + URL-dedup persists.

Uses a synthetic feed item (not real RSS) to avoid depending on network.
Run with: pytest -v tests/test_ingest.py
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models import NewsReport
from app.services.ingest.persist import persist_items
from app.services.ingest.rss import FeedItem


@pytest_asyncio.fixture
async def clean_news_report():
    """Delete all reports created during the test (by source_site marker)."""
    yield "news_aggregator_test_src"
    async with AsyncSessionLocal() as s:
        await s.execute(
            NewsReport.__table__.delete().where(
                NewsReport.source_site == "news_aggregator_test_src"
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_persist_dedup_in_batch(clean_news_report):
    """Inserting the same URL twice in one batch only inserts one row."""
    items = [
        FeedItem(
            source_site="news_aggregator_test_src",
            title="test A",
            raw_text="这是第一条完整报道的正文。",
            original_url="https://example.com/test-p1-a",
            publish_time=None,
        ),
        FeedItem(
            source_site="news_aggregator_test_src",
            title="test A (dup)",
            raw_text="这是同一条完整报道的另一份拷贝。",
            original_url="https://example.com/test-p1-a",  # same URL
            publish_time=None,
        ),
        FeedItem(
            source_site="news_aggregator_test_src",
            title="test B",
            raw_text="这是第二条完整报道的正文。",
            original_url="https://example.com/test-p1-b",
            publish_time=None,
        ),
    ]
    async with AsyncSessionLocal() as s:
        stats = await persist_items(s, items)
    assert stats["inserted"] == 2, stats
    assert stats["skipped"] == 1, stats


@pytest.mark.asyncio
async def test_persist_dedup_against_db(clean_news_report):
    """Inserting an URL that already exists in DB is skipped without raising."""
    items = [
        FeedItem(
            source_site="news_aggregator_test_src",
            title="db dup",
            raw_text="db dup body",
            original_url="https://example.com/test-p1-c",
            publish_time=None,
        ),
    ]
    async with AsyncSessionLocal() as s:
        await persist_items(s, items)
    # second insert of same URL
    async with AsyncSessionLocal() as s:
        stats = await persist_items(s, items)
    assert stats["inserted"] == 0, stats
    assert stats["skipped"] == 1, stats


@pytest.mark.asyncio
async def test_persist_null_raw_text_rejected(clean_news_report):
    """Q10 admission gate supersedes Q2: empty-content items are NOT reports,
    rejected at persist time (残句/空壳不产生幻影事件)."""
    items = [
        FeedItem(
            source_site="news_aggregator_test_src",
            title="no body",
            raw_text="",  # empty -> incomplete -> rejected
            original_url="https://example.com/test-p1-null-text",
            publish_time=None,
        ),
    ]
    async with AsyncSessionLocal() as s:
        stats = await persist_items(s, items)
    assert stats["rejected"] == 1, stats
    assert stats["inserted"] == 0, stats