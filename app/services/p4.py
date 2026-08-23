"""P4 orchestrator: per-report category derivation + event re-embed.

Pipeline (Q4/Q5 decision: batch re-embed, NOT concurrent per-attach):
1. For each report with a summary: derive category (LLM) -> news_report.category
2. For each event: category = majority vote over member reports' categories
3. For each event with source_count >= 2 (Q20: avoid single-anchor bias):
   a. LLM-merge all report summaries -> new merged_summary
   b. BGE re-encode new merged_summary -> event.embedding

The 5W1H fact-slot extraction + 4-grade conflict merge were removed
(grilling 2026-08): real output showed false conflicts (punctuation-level
differences flagged red) and verbose slot values that damaged the
"verifiable design" story. Category is kept — the event stream filter and
the digest depend on it.

Single-pass sequential, no race conditions (Q4 fix).
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy import select, update

from app.db.session import AsyncSessionLocal
from app.models import NewsEvent, NewsReport
from app.services.embed import embed_async
from app.services.fact_extract import extract_category_batch
from app.services.summarize import _get_client, _call_deepseek

_MERGE_SYS = (
    "你是事件摘要合并器。任务：把下面给定的多篇报道摘要合并成一个事件级摘要。"
    "规则：1) 只能用提供的摘要里的事实，不引入外部信息。"
    "2) 输出 150 字以内。3) 优先保留各家共识事实 + 关键差异点。"
    "4) 只输出摘要本身。"
)


async def _extract_and_persist_categories(session, limit_events: int | None) -> int:
    """Derive category for reports that lack one, persist to news_report.category."""
    stmt = (
        select(NewsReport)
        .where(
            NewsReport.summary.is_not(None),
            NewsReport.category.is_(None),
        )
        .order_by(NewsReport.id)
    )
    if limit_events:
        pass  # caller-level control not needed for category pass
    rows = (await session.execute(stmt)).scalars().all()

    if not rows:
        print("[p4] categories: nothing to extract")
        return 0

    print(f"[p4] categories: extracting for {len(rows)} reports")
    summaries = [r.summary for r in rows]
    # Fetch keywords from their event (report keywords are not stored per-report)
    ev_kw_map: dict[int, list[str]] = {}
    for r in rows:
        if r.event_id and r.event_id not in ev_kw_map:
            ev = (await session.execute(
                select(NewsEvent).where(NewsEvent.id == r.event_id)
            )).scalar_one_or_none()
            ev_kw_map[r.event_id] = ev.keywords if ev else []
        ev_kw_map.setdefault(r.event_id or 0, [])

    keywords_list = [ev_kw_map.get(r.event_id or 0, []) for r in rows]

    cats = await extract_category_batch(summaries, keywords_list)

    n_cat = 0
    for r, cat in zip(rows, cats):
        if not cat:
            print(f"[p4] categories: skip report #{r.id} (extraction failed)")
            continue
        await session.execute(
            update(NewsReport).where(NewsReport.id == r.id).values(category=cat)
        )
        n_cat += 1

    await session.commit()
    print(f"[p4] categories: persisted {n_cat} categories")
    return n_cat


async def _majority_category_per_event(session) -> dict:
    """For each event, category = majority vote over member reports' categories."""
    stmt = select(NewsEvent).order_by(NewsEvent.id)
    events = (await session.execute(stmt)).scalars().all()

    stats = {"events": 0, "updated": 0}

    for ev in events:
        rstmt = select(NewsReport).where(NewsReport.event_id == ev.id).order_by(NewsReport.id)
        reports = (await session.execute(rstmt)).scalars().all()
        if not reports:
            continue

        cats_seen = [r.category for r in reports if r.category]
        if not cats_seen:
            continue

        ranking = Counter(cats_seen).most_common()
        top, _ = ranking[0]
        if top != ev.category:
            ev.category = top
            stats["updated"] += 1
        stats["events"] += 1

    await session.commit()
    return stats


async def _reembed_multi_source_events(session) -> dict:
    """Q20 fix: for events with source_count >= 2, LLM-merge summaries ->
    new merged_summary -> re-encode -> event.embedding. Single-pass sequential,
    no concurrent race (Q4 fix)."""
    stmt = (
        select(NewsEvent)
        .where(NewsEvent.source_count > 1)
        .order_by(NewsEvent.id)
    )
    events = (await session.execute(stmt)).scalars().all()

    if not events:
        print("[p4] reembed: no multi-source events")
        return {"reembed": 0, "failed": 0}

    print(f"[p4] reembed: {len(events)} multi-source events")
    stats = {"reembed": 0, "failed": 0}

    for ev in events:
        rstmt = (select(NewsReport.summary)
                 .where(NewsReport.event_id == ev.id, NewsReport.summary.is_not(None))
                 .order_by(NewsReport.id))
        summaries = [r[0] for r in (await session.execute(rstmt)).all() if r[0]]
        if not summaries:
            stats["failed"] += 1
            continue

        # Concatenate summaries (cap at 2000 chars to keep prompt bounded)
        joined = " / ".join(summaries)[:2000]
        user = f"以下是同一事件的 {len(summaries)} 篇报道摘要:\n\n{joined}\n\n合并摘要:"

        try:
            client = _get_client()
            new_summ, _, _ = await _call_deepseek(
                client, _MERGE_SYS, user, temperature=0.2, max_tokens=300
            )
        except Exception as e:
            print(f"[p4] reembed: event #{ev.id} LLM merge failed: {e}")
            stats["failed"] += 1
            continue

        if not new_summ or not new_summ.strip():
            stats["failed"] += 1
            continue

        # Re-encode
        try:
            vecs = await embed_async([new_summ])
            ev.merged_summary = new_summ.strip()
            ev.embedding = vecs[0]
            stats["reembed"] += 1
        except Exception as e:
            print(f"[p4] reembed: event #{ev.id} BGE encode failed: {e}")
            stats["failed"] += 1

        # Commit per event (idempotent if re-run)
        await session.commit()

    print(f"[p4] reembed: done ({stats})")
    return stats


async def run_p4(limit_events: int | None = None) -> dict:
    """P4 orchestrator: categories -> majority vote -> re-embed"""
    async with AsyncSessionLocal() as session:
        # Phase 1: derive + persist per-report categories
        n_cat = await _extract_and_persist_categories(session, limit_events)

        # Phase 2: event-level category majority vote
        vote_stats = await _majority_category_per_event(session)

        # Phase 3: re-embed multi-source events
        reembed_stats = await _reembed_multi_source_events(session)

    return {
        "extracted_reports": n_cat,
        **vote_stats,
        "reembed": reembed_stats.get("reembed", 0),
        "reembed_failed": reembed_stats.get("failed", 0),
    }
