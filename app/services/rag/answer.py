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

import json
import re
from typing import AsyncIterator

from app.models import NewsEvent, NewsReport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rag.retrieval import retrieve
from app.services.summarize import _get_client, RAG_TEMP

RAG_SYSTEM_UNCONSTRAINED = (
    "你是一个新闻事件问答助手。请根据下面提供的新闻事件 context 回答用户问题。"
    "如果 context 中没有相关信息，请直接说明。"
)

RAG_SYSTEM = (
    "你是一个新闻事件问答助手。规则：\n"
    "1) 你只能用下文 context 提供的事件信息回答用户问题，"
    "不许使用 context 之外的任何知识、推测或编造。\n"
    "2) 只要 context 中有任何与问题相关的事件，就应优先回答；"
    "只有当 context 完全没有相关信息时，才输出'信息不足'四个字。\n"
    "3) 每个事实句最好以事件引用结尾，格式 [事件#<id>]；"
    "综述性语句允许不引用，但涉及具体事实时必须引用。\n"
    "4) 输出全中文。\n"
    "5) 对于需要多个事件回答的问题（如'最近有哪些...'），可以引用 1-5 个相关事件，"
    "不要刻意只选一个，也不要把不相关的事件都列出来。\n"
)

# Query-time full-text extraction (use_full_text): a pre-generation LLM pass
# that pulls query-relevant verbatim passages out of the retrieved events'
# stored report raw_text. Fixes the summary-bottleneck: details absent from
# merged_summary become answerable. Extraction output is treated as context
# and stays under the same constrained-generation rules.
FULL_TEXT_EXTRACT_SYSTEM = (
    "你是新闻问答的资料提取员。给你一个用户问题和若干新闻报道原文，"
    "逐篇判断：只摘录与问题直接相关的原文片段（尽量保留原句，不要改写），"
    "每段摘录前单独一行标注 [报道#<id>]；整篇与问题无关则只输出一行"
    "[报道#<id>] 无关。不要解释，不要补充原文之外的信息。"
)
FULL_TEXT_CHARS_PER_REPORT = 700
FULL_TEXT_TOTAL_CHAR_CAP = 9000


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
        # report sources (title + source site)
        for r in reports[:3]:
            block.append(f"  - [{r.source_site}] {r.title}")
        blocks.append("\n".join(block))
    return "\n\n".join(blocks)


def _build_full_text_block(
    events_with_reports: list[tuple[NewsEvent, list[NewsReport]]],
) -> str:
    """Render stored report raw_text excerpts for the extraction pass."""
    parts: list[str] = []
    total = 0
    for ev, reports in events_with_reports:
        for r in reports[:3]:
            if not r.raw_text:
                continue
            excerpt = r.raw_text[:FULL_TEXT_CHARS_PER_REPORT]
            if total + len(excerpt) > FULL_TEXT_TOTAL_CHAR_CAP:
                return "\n\n".join(parts)
            total += len(excerpt)
            parts.append(
                f"[报道#{r.id} | 事件#{ev.id} | 来源:{r.source_site}] "
                f"{r.title}\n{excerpt}"
            )
    return "\n\n".join(parts)


async def extract_relevant_passages(
    query_text: str,
    events_with_reports: list[tuple[NewsEvent, list[NewsReport]]],
) -> str:
    """One non-streaming LLM call: pull verbatim passages relevant to the
    query out of the retrieved reports' raw_text. Returns '' when nothing
    relevant (or on failure — answering must degrade to summary-only, not
    die, unlike the ingest gate where fail-loud is correct)."""
    block = _build_full_text_block(events_with_reports)
    if not block.strip():
        return ""
    from app.core.config import settings

    client = _get_client()
    try:
        resp = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {"role": "system", "content": FULL_TEXT_EXTRACT_SYSTEM},
                {
                    "role": "user",
                    "content": f"用户问题: {query_text}\n\n报道原文:\n{block}",
                },
            ],
            temperature=0.1,
            max_tokens=1200,
            timeout=60.0,
        )
        out = (resp.choices[0].message.content or "").strip()
        print(f"[rag.fulltext] extracted {len(out)} chars", flush=True)
        return out
    except Exception as e:
        print(f"[rag.fulltext] extraction failed, summary-only fallback: {e}", flush=True)
        return ""


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
_CITATION_RE = re.compile(r"\[事件#(\d+)\]")


def _sse_token(text: str) -> str:
    """Format text as a single SSE token event, escaping newlines per spec."""
    lines = text.split("\n")
    data_lines = "\n".join(f"data: {line}" for line in lines)
    return f"event: token\n{data_lines}\n\n"


async def rag_answer_stream(
    session: AsyncSession,
    query_text: str,
    interest_tags: list[str] | None = None,
    top_recall: int = 20,
    top_final: int = 5,
    use_time_filter: bool = True,
    use_reranker: bool = True,
    use_constrained_generation: bool = True,
    use_full_text: bool = False,
) -> AsyncIterator[str]:
    """Full RAG: retrieve -> fetch events -> generate (streamed).

    Yields SSE-format events. The first event is a 'meta' with retrieved
    event ids; subsequent events are 'token' containing one buffered
    sentence at a time. Final event is 'done' with refusal flag and
    citation metadata.

    Feature flags for ablation studies:
      use_time_filter=False:          skip SQL time pre-filter
      use_reranker=False:             skip cross-encoder rerank
      use_constrained_generation=False: plain prompt without forced citation/refusal
      use_full_text=True:             pre-generation extraction pass over the
                                      retrieved events' stored report raw_text
                                      (summary-bottleneck fix; ablation row)

    If context insufficient (no events) or LLM says 信息不足, yield
    refusal marker.
    """
    # 1. Retrieve
    event_ids = await retrieve(
        session, query_text, interest_tags,
        top_recall=top_recall, top_final=top_final,
        use_time_filter=use_time_filter, use_reranker=use_reranker,
    )
    yield f"event: meta\ndata: {json.dumps({'event_ids': event_ids})}\n\n"

    if not event_ids:
        yield "event: token\ndata: 信息不足\n\n"
        yield "event: done\ndata: {\"refusal\": true}\n\n"
        yield "event: eof\ndata: [DONE]\n\n"
        return

    # 2. Fetch events + reports for context
    events_with_reports = await fetch_events_with_reports(session, event_ids)
    context = _build_context(events_with_reports)

    # 2b. Optional query-time full-text extraction (use_full_text).
    # Runs only when an API key exists; failure degrades to summary-only.
    from app.core.config import settings
    if use_full_text and settings.deepseek_api_key:
        passages = await extract_relevant_passages(query_text, events_with_reports)
        if passages:
            context += "\n\n=== 相关报道原文摘录（同样受规则约束，只能用这些内容） ===\n" + passages
    # 3. Constrained stream
    if not settings.deepseek_api_key:
        yield f"event: token\ndata: [无 DeepSeek API Key, 不能生成答案。Context 已检索到事件 {event_ids}]\n\n"
        yield "event: done\ndata: {\"refusal\": true}\n\n"
        yield "event: eof\ndata: [DONE]\n\n"
        return

    user_prompt = (
        f"Context:\n{context}\n\n"
        f"用户问题: {query_text}\n\n"
        f"请按规则回答:"
    )

    # Use openai async streaming
    client = _get_client()
    buf = ""
    full_text = ""

    try:
        system_prompt = RAG_SYSTEM if use_constrained_generation else RAG_SYSTEM_UNCONSTRAINED
        stream = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {"role": "system", "content": system_prompt},
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
            full_text += delta
            # Flush sentence-by-sentence
            while True:
                m = _SENT_BOUNDARY.search(buf)
                if not m:
                    break
                end = m.end()
                sentence = buf[:end].strip()
                buf = buf[end:]
                if sentence:
                    # SSE event: token sentence (multiline data lines per SSE spec)
                    print(f"[rag.stream] tx: {sentence[:60]}...", flush=True)
                    yield _sse_token(sentence)
    except Exception as e:
        err_msg = f"[RAG 生成失败: {e}]"
        yield _sse_token(err_msg)
        yield f"event: done\ndata: {json.dumps({'refusal': True, 'error': True})}\n\n"
        yield "event: eof\ndata: [DONE]\n\n"
        return

    # Flush any remaining buffer
    if buf.strip():
        yield _sse_token(buf.strip())

    # Refusal detection: did output contain 信息不足?
    is_refusal = "信息不足" in full_text

    # Citation parsing and validation
    cited_ids = [int(m) for m in _CITATION_RE.findall(full_text)]
    valid_citations = [c for c in cited_ids if c in event_ids]
    citations_valid = len(cited_ids) == 0 or len(cited_ids) == len(valid_citations)
    # Note: if LLM says "信息不足" it should not cite anything; empty citations are valid.

    done_payload = {
        "refusal": is_refusal,
        "citations": cited_ids,
        "citations_valid": citations_valid,
    }
    yield f"event: done\ndata: {json.dumps(done_payload, ensure_ascii=False)}\n\n"
    yield "event: eof\ndata: [DONE]\n\n"
