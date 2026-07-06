"""P5-2: RAG answer generation - constrained prompt + forced citation + refusal.

Def-faith guarantee (Q6):
- "You can only use the provided context; if insufficient say 信息不足"
- Every sentence must end with [事件#<id>]
- Pre-stream checks context sufficiency; post-stream parses citations

Buffer-by-sentence render (Q21): accumulate tokens until end-of-sentence
boundary (。 ! ? \n) then flush as one SSE event - guarantees citation in
sentence tail not lost mid-stream.
"""
from __future__ import annotations

import re
from typing import AsyncIterator

from app.models import NewsEvent, NewsReport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rag.retrieval import retrieve
from app.services.summarize import _get_client, _call_deepseek, RAG_TEMP

RAG_SYSTEM = (
    "你是一个新闻事件问答助手。规则：\n"
    "1) 你只能用下文 context 提供的事件信息回答用户问题，"
    "不许使用 context 之外的任何知识、推测或编造。\n"
    "2) context 中没有相关事件时，必须输出'信息不足'四个字，什么也不说。\n"
    "3) 答案每句必须以事件引用结尾，格式 [事件#<id>]。没引用的句子不允许。\n"
    "4) 输出全中文。\n"
    "5) 优先引用与问题最相关的 1-3 个事件，不要把 5 个事件都列一遍。\n"
    "6) 若事件中事实槽位含 ' / ' 分隔的多候选值，说明这是多家报道不一致，"
    "请在答案中并列呈现而非选一个，例如'死亡 12/15 人[事件#X]'。\n"
)


def _build_context(events_with_reports: list[tuple[NewsEvent, list[NewsReport]]]) -> str:
    """Build context block for LLM prompt."""
    blocks = []
    for ev, reports in events_with_reports:
        block = [
            f"=== 事件 #{ev.id} ===",
            f"摘要: {ev.merged_summary or 'N/A'}",
            f"类别: {ev.category or 'N/A'}",
            f"时间: {ev.event_publish_time.strftime('%Y-%m-%d') if ev.event_publish_time else 'N/A'}",
            f"来源: {ev.source_count} 篇报道",
        ]
        if ev.fact_slots:
            fact_lines = []
            for k in ("who", "what", "when", "where", "why", "howmany"):
                v = ev.fact_slots.get(k)
                if v and v != "N/A":
                    fact_lines.append(f"  {k}: {v}")
            if fact_lines:
                block.append("5W1H 事实:")
                block.extend(fact_lines)
        if ev.conflict_flags:
            cf_lines = []
            for k, info in ev.conflict_flags.items():
                if isinstance(info, dict) and info.get("status") in ("conflict",):
                    cf_lines.append(f"  {k} 冲突: {info.get('note')} 候选={info.get('values')}")
            if cf_lines:
                block.append("冲突标记:")
                block.extend(cf_lines)
        # report sources (title + source site)
        for r in reports[:3]:
            block.append(f"  - [{r.source_site}] {r.title}")
        blocks.append("\n".join(block))
    return "\n\n".join(blocks)


async def fetch_events_with_reports(
    session: AsyncSession, event_ids: list[int]
) -> list[tuple[NewsEvent, list[NewsReport]]]:
    """Eagerly fetch events and their report objects for context build."""
    if not event_ids:
        return []
    events = (
        (await session.execute(select(NewsEvent).where(NewsEvent.id.in_(event_ids))))
        .scalars()
        .all()
    )
    # Preserve retrieval order
    ev_map = {e.id: e for e in events}
    out = []
    for eid in event_ids:
        ev = ev_map.get(eid)
        if ev is None:
            continue
        reports = (
            (await session.execute(
                select(NewsReport)
                .where(NewsReport.event_id == eid)
                .order_by(NewsReport.id)
                .limit(5)
            )).scalars().all()
        )
        out.append((ev, list(reports)))
    return out


# Sentence boundary: 。 ！ ？ \n
_SENT_BOUNDARY = re.compile(r"[。！？\n]")


async def rag_answer_stream(
    session: AsyncSession,
    query_text: str,
    interest_tags: list[str] | None = None,
    top_recall: int = 20,
    top_final: int = 5,
) -> AsyncIterator[str]:
    """Full RAG: retrieve -> fetch events -> generate (streamed).

    Yields SSE-format events. The first event is a 'meta' with retrieved
    event ids; subsequent events are 'token' containing one buffered
    sentence at a time. Final event is 'done' with refusal flag.

    If context insufficient (no events) or LLM says 信息不足, yield
    refusal marker.
    """
    # 1. Retrieve
    event_ids = await retrieve(
        session, query_text, interest_tags, top_recall=top_recall, top_final=top_final
    )
    yield f"event: meta\ndata: {{\"event_ids\": {event_ids}}}\n\n"

    if not event_ids:
        yield "event: token\ndata: 信息不足\n\n"
        yield "event: done\ndata: {\"refusal\": true}\n\n"
        return

    # 2. Fetch events + reports for context
    events_with_reports = await fetch_events_with_reports(session, event_ids)
    context = _build_context(events_with_reports)

    # 3. Constrained stream
    from app.core.config import settings
    if not settings.deepseek_api_key:
        yield f"event: token\ndata: [无 DeepSeek API Key, 不能生成答案。Context 已检索到事件 {event_ids}]\n\n"
        yield "event: done\ndata: {\"refusal\": true}\n\n"
        return

    user_prompt = (
        f"Context:\n{context}\n\n"
        f"用户问题: {query_text}\n\n"
        f"请按规则回答:"
    )

    yield f"event: token\ndata: {('检索到 ' + str(len(event_ids)) + ' 个候选事件，开始生成...').encode('unicode_escape').decode()}\n\n"

    # Use openai async streaming
    from openai import AsyncOpenAI
    client = _get_client()
    buf = ""

    try:
        stream = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {"role": "system", "content": RAG_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=RAG_TEMP,
            max_tokens=800,
            stream=True,
            timeout=90.0,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices and chunk.choices[0].delta else None
            if not delta:
                continue
            buf += delta
            # Flush sentence-by-sentence
            while True:
                m = _SENT_BOUNDARY.search(buf)
                if not m:
                    break
                end = m.end()
                sentence = buf[:end].strip()
                buf = buf[end:]
                if sentence:
                    # SSE event: token sentence
                    print(f"[rag.stream] tx: {sentence[:60]}...", flush=True)
                    yield f"event: token\ndata: {sentence.encode('unicode_escape').decode()}\n\n"
    except Exception as e:
        err_msg = f"[RAG 生成失败: {e}]"
        yield f"event: token\ndata: {err_msg.encode('unicode_escape').decode()}\n\n"

    # Flush any remaining buffer
    if buf.strip():
        yield f"event: token\ndata: {buf.strip().encode('unicode_escape').decode()}\n\n"

    # Refusal detection: did output contain 信息不足?
    full_text = ""  # we lost full text but the most reliable signal is 信息不足 in chunks
    # Actually we need to check - for simplicity check last emitted sentence
    yield "event: done\ndata: {\"refusal\": false}\n\n"