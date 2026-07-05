"""Persist fetched FeedItems into news_report with URL-level dedup."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NewsReport
from app.services.ingest.rss import FeedItem


async def persist_items(session: AsyncSession, items: list[FeedItem]) -> dict:
    if not items:
        return {"inserted": 0, "skipped": 0}

    urls = [it.original_url for it in items]
    existing_q = select(NewsReport.original_url).where(
        NewsReport.original_url.in_(urls)
    )
    existing = {row[0] for row in (await session.execute(existing_q)).all()}

    to_insert = []
    for it in items:
        if it.original_url in existing:
            continue
        to_insert.append(
            {
                "source_site": it.source_site,
                "title": it.title,
                "raw_text": it.raw_text,
                "original_url": it.original_url,
                "publish_time": it.publish_time,
            }
        )

    if not to_insert:
        return {"inserted": 0, "skipped": len(items)}

    # Use psycopg-style executemany via ORM add_all for portability.
    session.add_all([NewsReport(**row) for row in to_insert])
    await session.commit()
    return {"inserted": len(to_insert), "skipped": len(items) - len(to_insert)}


async def upsert_by_url(session: AsyncSession, item: FeedItem) -> NewsReport:
    """Insert-or-return existing report by URL."""
    stmt = pg_insert(NewsReport).values(
        source_site=item.source_site,
        title=item.title,
        raw_text=item.raw_text,
        original_url=item.original_url,
        publish_time=item.publish_time,
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["original_url"]).returning(NewsReport)
    res = await session.execute(stmt)
    row = res.scalar_one_or_none()
    if row is None:
        sel = select(NewsReport).where(NewsReport.original_url == item.original_url)
        row = (await session.execute(sel)).scalar_one()
    await session.commit()
    return row