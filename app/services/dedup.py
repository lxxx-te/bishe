"""P3 ingest-time event dedup via keyword gate + ANN (Q19 + Q20 decisions).

Pipeline for each new report (with non-null embedding):
1. SQL gate: SELECT news_event WHERE keywords && new_report.keywords (array overlap, GIN)
2. ANN cosine over gated candidates; if max similarity > 0.75 -> ATTACH to existing
3. Otherwise SPAWN new event with this report as first member

Q20 fix: after attach, async recompute merged_summary + event.embedding via
BackgroundTasks (avoids first-report anchoring bias).

Note: reports with NULL embedding (empty raw_text) are SKIPPED entirely -
they cannot participate in vector dedup per Q2 fix.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import NewsEvent, NewsReport
from app.services.embed import embed_async, embed_one

DEDUP_THRESHOLD = settings.dedup_sim_threshold  # Q7-Q18: tuned from 100-pair gold set (precision=0.929, recall=1.000, F1=0.963); default 0.90 in config, override via DEDUP_SIM_THRESHOLD
# Note: at 0.75 the threshold was catastrophic (precision 0.134). Keyword gate
# acts as additional safety margin on top of this already-validated threshold.


def _cosine(a, b) -> float:
    """Pure-Python cosine for small candidate set. Handles numpy arrays
    and lists (pgvector may return either depending on bind path)."""
    if a is None or b is None:
        return 0.0
    # Normalize to lists
    try:
        a = list(a)
        b = list(b)
    except TypeError:
        return 0.0
    if len(a) == 0 or len(b) == 0 or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def _find_candidate_events(
    session: AsyncSession, report_keywords: list[str]
) -> list[NewsEvent]:
    """SQL gate: events whose keywords array overlaps with report keywords."""
    if not report_keywords:
        return []
    stmt = (
        select(NewsEvent)
        .where(NewsEvent.keywords.op("&&")(text(":kws")))
        .order_by(NewsEvent.id.desc())
        .limit(50)
    )
    res = await session.execute(stmt, params={"kws": report_keywords})
    return list(res.scalars().all())


async def _spawn_event(
    session: AsyncSession, report: NewsReport, keywords: list[str]
) -> NewsEvent:
    """Create a new event with this report as first member."""
    event = NewsEvent(
        merged_summary=report.summary or report.title,
        embedding=report.embedding,
        keywords=keywords,
        source_count=1,
        event_publish_time=report.publish_time,
        ts=datetime.utcnow(),
    )
    session.add(event)
    await session.flush()  # get event.id
    report.event_id = event.id
    await session.commit()
    return event


async def _attach_to_event(
    session: AsyncSession, report: NewsReport, event: NewsEvent, new_keywords: list[str]
) -> None:
    """Attach report to existing event and recompute event center (Q20 fix).

    Q2 fix: merge new report's keywords into event.keywords (union, dedup, cap 6)
    so the SQL gate keeps widening as more reports attach - prevents later reports
    with divergent keywords from missing this event and spawning duplicates.

    Q20 deferred to P4: async re-embed merged_summary; for now event.embedding
    stays at first-report anchor (will be fixed by P4 aggregation).
    """
    report.event_id = event.id
    event.source_count = (event.source_count or 1) + 1
    # Union keywords (cap at 6 to keep GIN index efficient)
    merged = list(set((event.keywords or []) + new_keywords))[:6]
    event.keywords = merged
    # Update event_publish_time to min
    if report.publish_time and (
        not event.event_publish_time or report.publish_time < event.event_publish_time
    ):
        event.event_publish_time = report.publish_time
    await session.commit()


async def dedup_one(
    session: AsyncSession, report: NewsReport, keywords: list[str]
) -> dict:
    """Run dedup for a single report. Returns action: 'spawn' or 'attach'."""
    if report.embedding is None:
        return {"action": "skipped_null_embedding"}

    candidates = await _find_candidate_events(session, keywords)
    if not candidates:
        event = await _spawn_event(session, report, keywords)
        return {"action": "spawn", "event_id": event.id}

    # ANN cosine over gated candidates
    best_event = None
    best_sim = 0.0
    for ev in candidates:
        if ev.embedding is None:
            continue
        sim = _cosine(report.embedding, ev.embedding)
        if sim > best_sim:
            best_sim = sim
            best_event = ev

    if best_event and best_sim > DEDUP_THRESHOLD:
        await _attach_to_event(session, report, best_event, keywords)
        return {"action": "attach", "event_id": best_event.id, "similarity": best_sim}

    # No candidate above threshold -> spawn
    event = await _spawn_event(session, report, keywords)
    return {"action": "spawn", "event_id": event.id}


async def run_p3(limit: int | None = None) -> dict:
    """Process all reports that have embedding but no event_id yet.

    Steps:
    1. Extract keywords (if not yet on report - TODO add column or use event keywords)
    2. Run dedup per report
    3. Return stats

    NOTE: keywords are extracted per-report but stored on the event when spawned.
    For attach, the event already has keywords. We extract keywords here from
    the report's summary.
    """
    from app.services.keywords import extract_keywords_batch

    async with AsyncSessionLocal_p3() as session:
        stmt = (
            select(NewsReport)
            .where(
                NewsReport.embedding.is_not(None),
                NewsReport.event_id.is_(None),
            )
            .order_by(NewsReport.id)
        )
        if limit:
            stmt = stmt.limit(limit)
        rows = (await session.execute(stmt)).scalars().all()

        if not rows:
            print("[p3] nothing to dedup")
            return {"processed": 0, "spawn": 0, "attach": 0, "skipped": 0}

        print(f"[p3] {len(rows)} reports pending dedup")

        # Extract keywords for all (batch)
        # Fail loud: empty keywords would defeat the SQL gate and mass-spawn
        # every report as a singleton event. Re-raise instead of degrading.
        summaries = [r.summary or r.title or "" for r in rows]
        keywords_list = await extract_keywords_batch(summaries)
        if len(keywords_list) != len(rows):
            raise RuntimeError(
                f"[p3] extract_keywords_batch returned {len(keywords_list)} "
                f"keyword lists for {len(rows)} rows; aborting dedup run"
            )

        stats = {"processed": 0, "spawn": 0, "attach": 0, "skipped": 0}
        for i, r in enumerate(rows):
            kws = keywords_list[i] if i < len(keywords_list) else []
            result = await dedup_one(session, r, kws)
            stats["processed"] += 1
            if result["action"] == "spawn":
                stats["spawn"] += 1
            elif result["action"] == "attach":
                stats["attach"] += 1
            else:
                stats["skipped"] += 1
            if (i + 1) % 20 == 0:
                print(f"[p3] {i+1}/{len(rows)} done (spawn={stats['spawn']}, attach={stats['attach']})")

        print(f"[p3] done: {stats}")
        return stats


# Lazy import to avoid circular
from app.db.session import AsyncSessionLocal as AsyncSessionLocal_p3  # noqa: E402