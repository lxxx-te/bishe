"""P4 orchestrator: per-event fact extraction + merge + re-embed event center.

Pipeline (Q4/Q5 decision: batch re-embed, NOT concurrent per-attach):
1. For each report with non-null raw_text: extract 5W1H + category (LLM)
2. Persist to news_report_fact (one row per report × 6 slots)
3. Set news_report.summary_source / category on news_event for first report
4. For each event:
   a. Gather all reports' fact slots
   b. merge_event_facts() -> fact_slots + conflict_flags
   c. Write news_event.fact_slots + conflict_flags + category
5. For each event with source_count >= 2 (Q20: avoid single-anchor bias):
   a. LLM-merge all report summaries -> new merged_summary
   b. BGE re-encode new merged_summary -> event.embedding

Single-pass sequential, no race conditions (Q4 fix).
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import AsyncSessionLocal
from app.models import NewsEvent, NewsReport, NewsReportFact
from app.services.embed import embed_async
from app.services.fact_extract import extract_facts_batch
from app.services.fact_merge import merge_event_facts
from app.services.summarize import _get_client, _call_deepseek, generate_summary

_MERGE_SYS = (
    "你是事件摘要合并器。任务：把下面给定的多篇报道摘要合并成一个事件级摘要。"
    "规则：1) 只能用提供的摘要里的事实，不引入外部信息。"
    "2) 输出 150 字以内。3) 优先保留各家共识事实 + 关键差异点。"
    "4) 只输出摘要本身。"
)


async def _extract_and_persist_facts(session, limit_events: int | None) -> int:
    """Extract 5W1H for all reports lacking facts, persist to news_report_fact."""
    # Reports with non-null raw_text and summary_source='llm' but no fact rows yet
    # (avoid re-extracting facts already in DB)
    stmt = (
        select(NewsReport)
        .where(
            NewsReport.raw_text.is_not(None),
            NewsReport.summary.is_not(None),
            ~NewsReport.id.in_(
                select(NewsReportFact.report_id).distinct()
            ),
        )
        .order_by(NewsReport.id)
    )
    if limit_events:
        # We limit by reports, not events; let caller handle if needed
        pass
    rows = (await session.execute(stmt)).scalars().all()

    if not rows:
        print("[p4] facts: nothing to extract")
        return 0

    print(f"[p4] facts: extracting for {len(rows)} reports")
    summaries = [r.summary for r in rows]
    publish_times = [r.publish_time for r in rows]
    # Fetch keywords from their event (first report keywords)
    ev_kw_map: dict[int, list[str]] = {}
    for r in rows:
        if r.event_id and r.event_id not in ev_kw_map:
            ev = (await session.execute(
                select(NewsEvent).where(NewsEvent.id == r.event_id)
            )).scalar_one_or_none()
            ev_kw_map[r.event_id] = ev.keywords if ev else []
        ev_kw_map.setdefault(r.event_id or 0, [])

    keywords_list = [ev_kw_map.get(r.event_id or 0, []) for r in rows]

    facts_list = await extract_facts_batch(summaries, publish_times, keywords_list)

    # Persist rows (one per report × 6 slots)
    fact_rows = []
    cat_updates: list[tuple[int, str]] = []  # (report_id, category) for Q1(a) fix
    for r, facts in zip(rows, facts_list):
        if facts is None:
            print(f"[p4] facts: skip report #{r.id} (extraction failed)")
            continue
        for slot_key in ("who", "what", "when", "where", "why", "howmany"):
            fact_rows.append({
                "report_id": r.id,
                "slot_key": slot_key,
                "slot_value": facts.get(slot_key, "N/A"),
            })
        # Q1(a) fix: use LLM-derived category (not heuristic), persist to news_report.category
        cat_updates.append((r.id, facts.get("category", "其他")))

    if fact_rows:
        await session.execute(pg_insert(NewsReportFact).values(fact_rows))
        # Q1(a): write category directly onto each report row
        for rid, cat in cat_updates:
            await session.execute(
                update(NewsReport).where(NewsReport.id == rid).values(category=cat)
            )
        await session.commit()
        print(f"[p4] facts: persisted {len(fact_rows)} fact rows "
              f"({len(fact_rows)//6} reports × 6 slots, {len(cat_updates)} category")

    return len(rows)


async def _merge_and_persist_per_event(session) -> dict:
    """For each event, merge its reports' facts into event-level slots."""
    stmt = select(NewsEvent).order_by(NewsEvent.id)
    events = (await session.execute(stmt)).scalars().all()

    stats = {"events": 0, "merged": 0, "conflicts": 0, "single_source": 0}

    for ev in events:
        # Fetch all reports under this event (now with non-null category per Q1 fix)
        rstmt = select(NewsReport).where(NewsReport.event_id == ev.id).order_by(NewsReport.id)
        reports = (await session.execute(rstmt)).scalars().all()
        if not reports:
            continue

        # Fetch each report's fact rows
        fact_rows_by_report: dict[int, dict] = {}
        cats_seen: list[str] = []
        for r in reports:
            fstmt = select(NewsReportFact).where(NewsReportFact.report_id == r.id)
            fr = (await session.execute(fstmt)).scalars().all()
            if not fr:
                continue
            d = {f.slot_key: f.slot_value for f in fr}
            fact_rows_by_report[r.id] = d
            # Q1(a) fix: collect LLM-derived category from each report
            if r.category:
                cats_seen.append(r.category)

        facts_list = list(fact_rows_by_report.values())
        if not facts_list:
            stats["events"] += 1
            continue

        fact_slots, conflict_flags = merge_event_facts(facts_list)
        ev.fact_slots = fact_slots
        ev.conflict_flags = conflict_flags

        # Q1(a) fix: event.category via MAJORITY VOTE over reports' LLM categories
        # (replaces earlier hard-coded keyword heuristic).
        if cats_seen:
            from collections import Counter
            ranking = Counter(cats_seen).most_common()
            # Pick most common; tie-break: first by source_count order
            top, _ = ranking[0]
            ev.category = top
        else:
            ev.category = ev.category or "其他"

        stats["events"] += 1
        if ev.source_count and ev.source_count > 1:
            stats["merged"] += 1
            if conflict_flags:
                stats["conflicts"] += 1
        else:
            stats["single_source"] += 1

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
    """P4 orchestrator: extract facts -> merge per-event -> re-embed"""
    async with AsyncSessionLocal() as session:
        # Phase 1: extract + persist per-report facts
        n_extracted = await _extract_and_persist_facts(session, limit_events)

        # Phase 2: merge per-event + persist event-level fact_slots/conflict_flags
        merge_stats = await _merge_and_persist_per_event(session)

        # Phase 3: re-embed multi-source events
        reembed_stats = await _reembed_multi_source_events(session)

    return {
        "extracted_reports": n_extracted,
        **merge_stats,
        "reembed": reembed_stats.get("reembed", 0),
        "reembed_failed": reembed_stats.get("failed", 0),
    }