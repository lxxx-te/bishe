"""Daily digest endpoint.

Design (grilling 2026-08):
- structured list, not LLM-generated article (every field traceable to DB)
- selection: rank by source breadth (# distinct media sources) descending,
  cap at DIGEST_LIMIT items
- date parameterized: ?date=YYYY-MM-DD defaults to today; ?days=N selects the
  last N days. publish_time is naive/mixed tz, so day boundaries are accepted
  at day precision (documented limitation).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models import NewsEvent, NewsReport

router = APIRouter()

DIGEST_LIMIT = 10


@router.get("/digest")
async def get_digest(
    date_str: str | None = Query(None, alias="date", description="digest date YYYY-MM-DD, defaults to today"),
    days: int = Query(1, ge=1, le=30, description="look-back window in days"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Build a daily digest: structured list of the day's top events.

    Selection rule: rank by source breadth = COUNT(DISTINCT report.source_site)
    desc (multi-source first), then time desc, capped at DIGEST_LIMIT.
    Summaries reuse event.merged_summary — zero new LLM calls, every field
    traceable to the database.
    """
    # Window: [day_start, day_start + days)
    if date_str:
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return {"date": date_str, "error": "date must be YYYY-MM-DD", "items": []}
    else:
        day = date.today()
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=days)

    breadth = func.count(func.distinct(NewsReport.source_site))
    stmt = (
        select(NewsEvent, breadth.label("source_breadth"))
        .outerjoin(NewsReport, NewsReport.event_id == NewsEvent.id)
        .where(
            NewsEvent.event_publish_time.is_not(None),
            NewsEvent.event_publish_time >= start,
            NewsEvent.event_publish_time < end,
        )
        .group_by(NewsEvent.id)
        .order_by(
            breadth.desc(),
            NewsEvent.event_publish_time.desc(),
        )
        .limit(DIGEST_LIMIT)
    )
    rows = (await session.execute(stmt)).all()
    events = [r[0] for r in rows]
    breadth_map = {r[0].id: r[1] for r in rows}

    event_ids = [ev.id for ev in events]
    reports_map: dict[int, list[NewsReport]] = {eid: [] for eid in event_ids}
    if event_ids:
        reports = (
            (await session.execute(
                select(NewsReport)
                .where(NewsReport.event_id.in_(event_ids))
                .order_by(NewsReport.id)
            ))
            .scalars()
            .all()
        )
        for r in reports:
            if r.event_id in reports_map:
                reports_map[r.event_id].append(r)

    items = []
    for ev in events:
        reps = reports_map.get(ev.id, [])
        first = reps[0] if reps else None
        items.append({
            "event_id": ev.id,
            "title": first.title if first else (ev.merged_summary or "")[:40],
            "summary": ev.merged_summary,
            "category": ev.category,
            "event_publish_time": ev.event_publish_time.isoformat() if ev.event_publish_time else None,
            "source_breadth": breadth_map.get(ev.id, 0),
            "sources": sorted({r.source_site for r in reps}),
            "link": first.original_url if first else None,
            "reports": [
                {
                    "source_site": r.source_site,
                    "title": r.title,
                    "original_url": r.original_url,
                }
                for r in reps
            ],
        })

    return {
        "date": day.isoformat(),
        "days": days,
        "count": len(items),
        "items": items,
    }