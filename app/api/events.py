"""Event stream endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models import NewsEvent, NewsReport

router = APIRouter()


@router.get("/events")
async def list_events(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    category: str | None = Query(None, description="filter by event category"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List aggregated news events for the event stream page.

    Returns event cards with merged summary, category, source breadth
    (= COUNT(DISTINCT report.source_site)) and up to 5 source reports.
    Ranked by source breadth desc, then time desc.
    """
    breadth = func.count(func.distinct(NewsReport.source_site))
    stmt = (
        select(NewsEvent, breadth.label("source_breadth"))
        .outerjoin(NewsReport, NewsReport.event_id == NewsEvent.id)
        .group_by(NewsEvent.id)
        .order_by(
            breadth.desc(),
            NewsEvent.event_publish_time.desc(),
        )
    )
    if category:
        stmt = stmt.where(NewsEvent.category == category)

    total_stmt = select(NewsEvent.id)
    if category:
        total_stmt = total_stmt.where(NewsEvent.category == category)

    total = len((await session.execute(total_stmt)).scalars().all())

    rows = (await session.execute(stmt.limit(limit).offset(offset))).all()
    events = [r[0] for r in rows]

    event_ids = [r.id for r in events]
    breadth_map = {r[0].id: r[1] for r in rows}
    reports_map: dict[int, list[dict]] = {eid: [] for eid in event_ids}
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
                reports_map[r.event_id].append({
                    "id": r.id,
                    "source_site": r.source_site,
                    "title": r.title,
                    "original_url": r.original_url,
                })

    items = []
    for ev in events:
        items.append({
            "id": ev.id,
            "merged_summary": ev.merged_summary,
            "category": ev.category,
            "event_publish_time": ev.event_publish_time.isoformat() if ev.event_publish_time else None,
            "source_breadth": breadth_map.get(ev.id, 0),
            "keywords": ev.keywords or [],
            "reports": reports_map.get(ev.id, [])[:5],
        })

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "count": len(items),
        "items": items,
    }


@router.get("/events/{event_id}")
async def get_event(
    event_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Fetch a single event with its reports for citation detail popup."""
    ev = (await session.execute(select(NewsEvent).where(NewsEvent.id == event_id))).scalar_one_or_none()
    if ev is None:
        raise HTTPException(status_code=404, detail=f"event {event_id} not found")

    reports = (
        (await session.execute(
            select(NewsReport).where(NewsReport.event_id == event_id).order_by(NewsReport.id)
        ))
        .scalars()
        .all()
    )

    return {
        "id": ev.id,
        "merged_summary": ev.merged_summary,
        "category": ev.category,
        "event_publish_time": ev.event_publish_time.isoformat() if ev.event_publish_time else None,
        "source_breadth": len({r.source_site for r in reports}),
        "keywords": ev.keywords or [],
        "reports": [
            {
                "id": r.id,
                "source_site": r.source_site,
                "title": r.title,
                "original_url": r.original_url,
            }
            for r in reports
        ],
    }
