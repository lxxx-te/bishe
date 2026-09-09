"""RSS 2.0 output feed.

Design (grilling 2026-08):
- RSS 2.0 only (no Atom) — Q14 decision.
- One <item> per event. item.link = first report's original_url (readable
  landing page); item.title = first report title; item.description =
  merged_summary + source list + keyword tags (all traceable to DB, zero new
  LLM calls).
- Params: days (look-back window, default 7), category (optional filter).
- Served as text/xml; sorted by event_publish_time desc.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from email.utils import format_datetime
from html import escape
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models import NewsEvent, NewsReport

router = APIRouter()


def _esc(s: str | None) -> str:
    return escape(s or "", quote=True)


FEED_FALLBACK_LIMIT = 20


async def _iter_feed(
    days: int,
    category: str | None,
    session: AsyncSession,
    request: Request,
) -> AsyncIterator[str]:
    start = datetime.now() - timedelta(days=days)
    base = str(request.base_url).rstrip("/")
    self_url = str(request.url)

    def _select(window_start: datetime | None):
        stmt = select(NewsEvent).where(NewsEvent.event_publish_time.is_not(None))
        if window_start is not None:
            stmt = stmt.where(NewsEvent.event_publish_time >= window_start)
        if category:
            stmt = stmt.where(NewsEvent.category == category)
        return stmt.order_by(NewsEvent.event_publish_time.desc())

    events = (await session.execute(_select(start))).scalars().all()
    fallback_note = ""
    if not events:
        # Snapshot data older than the wall-clock window (e.g. frozen demo DB).
        # Degrade to latest events overall instead of serving an empty channel.
        events = (
            (await session.execute(_select(None).limit(FEED_FALLBACK_LIMIT)))
            .scalars()
            .all()
        )
        if events:
            fallback_note = (
                f"<!-- window [{start.date()}, now] empty; fell back to latest "
                f"{len(events)} events (snapshot data predates window) -->"
            )

    yield f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
<title>多源新闻事件聚合 — 事件流</title>
<link>{_esc(base + "/")}</link>
<description>按事件聚合的多源新闻摘要输出（向量去重聚合 + RAG 检索系统）</description>
<language>zh-cn</language>
<atom:link href="{_esc(self_url)}" rel="self" type="application/rss+xml"/>
{fallback_note}
"""

    event_ids = [ev.id for ev in events]
    reports_map: dict[int, list[NewsReport]] = {}
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
            reports_map.setdefault(r.event_id, []).append(r)

    for ev in events:
        reps = reports_map.get(ev.id, [])
        first = reps[0] if reps else None
        sources = ", ".join(sorted({r.source_site for r in reps}))
        breadth = len({r.source_site for r in reps})
        kws = " ".join(f"#{_esc(k)}" for k in (ev.keywords or [])[:5])
        desc = _esc(ev.merged_summary or "")
        if sources:
            desc += f"<br/><br/>来源：{_esc(sources)}（共 {breadth} 源）"
        if kws:
            desc += f"<br/>标签：{kws}"
        pub = format_datetime(ev.event_publish_time) if ev.event_publish_time else ""
        cat = _esc(ev.category or "")
        yield f"""<item>
<title>{_esc(first.title if first else (ev.merged_summary or '')[:40])}</title>
<link>{_esc(first.original_url if first else '')}</link>
<description>{desc}</description>
<guid isPermaLink="false">event-{ev.id}</guid>
<category>{cat}</category>
<pubDate>{_esc(pub)}</pubDate>
</item>
"""
    yield "</channel>\n</rss>\n"


@router.get("/feed/events.rss", response_class=Response)
async def events_rss(
    request: Request,
    days: int = Query(7, ge=1, le=90, description="look-back window in days"),
    category: str | None = Query(None, description="optional category filter"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    body = "".join([chunk async for chunk in _iter_feed(days, category, session, request)])
    return Response(
        content=body,
        media_type="application/rss+xml; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )