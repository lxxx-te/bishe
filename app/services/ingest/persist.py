"""Persist fetched FeedItems into news_report with URL-level dedup.

Uses a single bulk INSERT ... ON CONFLICT DO NOTHING: handles in-batch dups
and DB-existing dups in one round-trip, atomic on conflict.

Admission gate (Q10): a Report must have complete source text. Truncated
feed teasers (cut mid-sentence) are NOT news articles -- they poison keyword
extraction (byline-only rows once merged two unrelated stories via reporter
names), embeddings, and spawn phantom events. Completeness is structural,
not length-based: short-but-complete briefs pass; headline fragments fail.
"""
from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NewsReport
from app.services.ingest.rss import FeedItem

# Sentence-terminal punctuation that marks finished prose.
_TERMINAL_PUNCT = set("。！？…")
# Closing quotes/brackets stripped before the terminal check, so articles
# ending with a quoted sentence (...。") still pass.
_CLOSING_WRAPPERS = set("」』\"”')）】〉»")


def has_complete_text(text: str | None) -> bool:
    """True iff text ends on a finished sentence (after stripping trailing
    closing quotes/brackets). Structural completeness check, length-agnostic."""
    if not text:
        return False
    t = text.rstrip()
    while t and t[-1] in _CLOSING_WRAPPERS:
        t = t[:-1].rstrip()
    return bool(t) and t[-1] in _TERMINAL_PUNCT


async def persist_items(session: AsyncSession, items: list[FeedItem]) -> dict:
    if not items:
        return {"inserted": 0, "skipped": 0}

    # In-batch URL dedup (keep first occurrence) + filter empty payloads.
    seen_urls: set[str] = set()
    rows: list[dict] = []
    rejected = 0
    for it in items:
        if not it.original_url or it.original_url in seen_urls:
            continue
        seen_urls.add(it.original_url)
        if not has_complete_text(it.raw_text):
            rejected += 1
            print(f"[ingest.persist] rejected incomplete text: {it.title[:40]}")
            continue
        rows.append(
            {
                "source_site": it.source_site,
                "title": it.title,
                "raw_text": it.raw_text,
                "original_url": it.original_url,
                "publish_time": it.publish_time,
            }
        )

    if not rows:
        return {"inserted": 0, "skipped": len(items), "rejected": rejected}

    stmt = pg_insert(NewsReport).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["original_url"])
    # SQLAlchemy 1.4 async-style: returning() then count inserted rows
    stmt = stmt.returning(NewsReport.id)
    res = await session.execute(stmt)
    inserted = len(res.fetchall())
    await session.commit()
    return {"inserted": inserted, "skipped": len(items) - inserted, "rejected": rejected}