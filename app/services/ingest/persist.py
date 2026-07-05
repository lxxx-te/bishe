"""Persist fetched FeedItems into news_report with URL-level dedup.

Uses a single bulk INSERT ... ON CONFLICT DO NOTHING: handles in-batch dups
and DB-existing dups in one round-trip, atomic on conflict.
"""
from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NewsReport
from app.services.ingest.rss import FeedItem


async def persist_items(session: AsyncSession, items: list[FeedItem]) -> dict:
    if not items:
        return {"inserted": 0, "skipped": 0}

    # In-batch URL dedup (keep first occurrence) + filter empty payloads.
    seen_urls: set[str] = set()
    rows: list[dict] = []
    for it in items:
        if not it.original_url or it.original_url in seen_urls:
            continue
        seen_urls.add(it.original_url)
        rows.append(
            {
                "source_site": it.source_site,
                "title": it.title,
                "raw_text": it.raw_text or None,
                "original_url": it.original_url,
                "publish_time": it.publish_time,
            }
        )

    if not rows:
        return {"inserted": 0, "skipped": len(items)}

    stmt = pg_insert(NewsReport).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["original_url"])
    # SQLAlchemy 1.4 async-style: returning() then count inserted rows
    stmt = stmt.returning(NewsReport.id)
    res = await session.execute(stmt)
    inserted = len(res.fetchall())
    await session.commit()
    return {"inserted": inserted, "skipped": len(items) - inserted}