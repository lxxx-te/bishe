"""GET /api/reports - list persisted news reports for browser inspection."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models import NewsReport

router = APIRouter()


@router.get("/reports")
async def list_reports(
    source: str | None = Query(None, description="filter by source_site substring"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List persisted news reports, newest first. Optional source substring filter."""
    stmt = select(NewsReport).order_by(NewsReport.id.desc()).limit(limit).offset(offset)
    if source:
        stmt = stmt.where(NewsReport.source_site.ilike(f"%{source}%"))

    rows = (await session.execute(stmt)).scalars().all()

    # Total count (filtered if source given) for pagination metadata
    cnt_stmt = select(func.count(NewsReport.id))
    if source:
        cnt_stmt = cnt_stmt.where(NewsReport.source_site.ilike(f"%{source}%"))
    total = (await session.execute(cnt_stmt)).scalar_one()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "count": len(rows),
        "items": [
            {
                "id": r.id,
                "source_site": r.source_site,
                "title": r.title,
                "raw_text_len": len(r.raw_text) if r.raw_text else 0,
                "raw_text_preview": (r.raw_text[:200] + "...") if r.raw_text and len(r.raw_text) > 200 else r.raw_text,
                "original_url": r.original_url,
                "publish_time": r.publish_time.isoformat() if r.publish_time else None,
                "has_summary": bool(r.summary),
            }
            for r in rows
        ],
    }


@router.get("/reports/{report_id}")
async def get_report(report_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Fetch one report by id with full raw_text."""
    row = (await session.execute(select(NewsReport).where(NewsReport.id == report_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"report {report_id} not found")
    return {
        "id": row.id,
        "source_site": row.source_site,
        "title": row.title,
        "raw_text": row.raw_text,
        "original_url": row.original_url,
        "publish_time": row.publish_time.isoformat() if row.publish_time else None,
        "summary": row.summary,
        "has_embedding": row.embedding is not None,
        "event_id": row.event_id,
    }


@router.get("/reports/stats/summary")
async def reports_stats(session: AsyncSession = Depends(get_session)) -> dict:
    """Aggregate counts: per source, total, empty raw_text counts."""
    src_stmt = (
        select(NewsReport.source_site, func.count(NewsReport.id))
        .group_by(NewsReport.source_site)
        .order_by(func.count(NewsReport.id).desc())
    )
    src_rows = (await session.execute(src_stmt)).all()

    total = (await session.execute(select(func.count(NewsReport.id)))).scalar_one()
    no_text = (
        await session.execute(
            select(func.count(NewsReport.id)).where(
                (NewsReport.raw_text.is_(None)) | (NewsReport.raw_text == "")
            )
        )
    ).scalar_one()
    no_summary = (
        await session.execute(
            select(func.count(NewsReport.id)).where(NewsReport.summary.is_(None))
        )
    ).scalar_one()

    return {
        "total": total,
        "no_raw_text": no_text,
        "no_summary_yet": no_summary,
        "by_source": [{"source_site": s, "count": c} for s, c in src_rows],
    }