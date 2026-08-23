"""P5-1: event retrieval pipeline (three stages).

Stage 1 (Q5 fix): SQL time pre-filter by event_publish_time (HNSW index on
  embedding helps but the time gate is a plain btree index on event_publish_time)
Stage 2: pgvector ANN Top-20 via <=> cosine distance (uses HNSW index)
Stage 3: bge-reranker cross-encoder rerank Top-5

The retrieval result is a list of NewsEvent objects (eager-load reports
for the answer generation prompt).
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NewsEvent
from app.services.embed import embed_one

# Local bge-reranker singleton (heavy model loaded once)
_reranker_singleton: Any = None


def _get_reranker():
    global _reranker_singleton
    if _reranker_singleton is None:
        from sentence_transformers import CrossEncoder
        print("[rag.rerank] loading BAAI/bge-reranker-base (CPU)...")
        _reranker_singleton = CrossEncoder("BAAI/bge-reranker-base", max_length=512)
        print("[rag.rerank] ready")
    return _reranker_singleton


def _parse_time_window(query: str) -> tuple[datetime, datetime] | None:
    """Parse Chinese time expressions in the query.

    Returns (start, end) datetime range or None if no time expression.
    Recognizes: 最近N天, 近N天, 过去N天, N天内, 本周, 今天/今日, 昨天/昨日,
                上周N, 本月, etc. Default days_back on no match = 3.
    """
    import re
    q = query

    if not q:
        return None

    # 最近N天 / 近N天 / 过去N天 / N天内
    m = re.search(r"(最近|近|过去)(\d+)天|(\d+)天[内以]", q)
    if m:
        days = int(m.group(2) or m.group(3))
        end = datetime.now()
        start = end - timedelta(days=days)
        return (start, end)

    # 本周
    if "本周" in q or "这周" in q:
        end = datetime.now()
        start = end - timedelta(days=7)
        return (start, end)

    # 最近一周
    if "最近一周" in q or "近一周" in q:
        end = datetime.now()
        start = end - timedelta(days=7)
        return (start, end)

    # 今天 / 今日
    if "今天" in q or "今日" in q:
        end = datetime.now()
        start = datetime(end.year, end.month, end.day)
        return (start, end)

    # 昨天 / 昨日
    if "昨天" in q or "昨日" in q:
        end = datetime.now()
        start = end - timedelta(days=1)
        return (start, end)

    # 上周
    if "上周" in q:
        end = datetime.now()
        start = end - timedelta(days=14)
        return (start, end)

    # 本月
    if "本月" in q:
        end = datetime.now()
        start = end - timedelta(days=30)
        return (start, end)

    # No time word - return None so stage1 skips time filter entirely
    # (covers historical data spanning multi-year; user who doesn't specify
    # time wants semantic relevance regardless of when)
    return None


async def stage1_time_filter(
    session: AsyncSession,
    time_window: tuple[datetime, datetime] | None,
    interest_tags: list[str] | None,
    limit: int = 200,
) -> list[int]:
    """Stage 1: SQL pre-filter by event_publish_time (and optional interest category).
    Returns list of event IDs (processed eagerly, capped at `limit`)
    """
    if time_window is None:
        # No time word: no time filter (covers all events regardless of date)
        # This handles demos with mixed historical+recent data.
        stmt = select(NewsEvent.id).order_by(
            NewsEvent.source_count.desc(), NewsEvent.event_publish_time.desc()
        ).limit(limit)
        if interest_tags:
            stmt = stmt.where(NewsEvent.category.in_(interest_tags))
        rows = (await session.execute(stmt)).all()
        return [r[0] for r in rows]

    start, end = time_window
    stmt = (
        select(NewsEvent.id)
        .where(
            NewsEvent.event_publish_time.is_not(None),
            NewsEvent.event_publish_time >= start,
            NewsEvent.event_publish_time <= end,
        )
        .order_by(NewsEvent.event_publish_time.desc())
        .limit(limit)
    )
    if interest_tags:
        stmt = stmt.where(NewsEvent.category.in_(interest_tags))

    rows = (await session.execute(stmt)).all()
    return [r[0] for r in rows]


async def stage2_ann_recall(
    session: AsyncSession,
    query_text: str,
    candidate_ids: list[int],
    top_k: int = 20,
) -> list[tuple[int, float]]:
    """Stage 2: pgvector ANN cosine Top-K over candidates (filtered by id list).

    Returns list of (event_id, cosine_distance). Lower distance = more similar.
    cosine distance = 1 - cosine_similarity.
    """
    if not candidate_ids:
        return []

    q_emb = await embed_one(query_text)
    if all(v == 0.0 for v in q_emb):
        return []  # all zeros = empty query

    # pgvector cosine distance operator: <=>
    from sqlalchemy import text as sql_text
    # Embedding passed as string repr because asyncpg path needs string cast
    q_emb_str = "[" + ",".join(f"{v:.6f}" for v in q_emb) + "]"

    stmt = sql_text("""
        SELECT id, embedding <=> :q AS distance
        FROM news_event
        WHERE id = ANY(:ids) AND embedding IS NOT NULL
        ORDER BY embedding <=> :q
        LIMIT :k
    """)
    rows = (await session.execute(stmt, {"q": q_emb_str, "ids": candidate_ids, "k": top_k})).all()
    return [(r[0], float(r[1])) for r in rows]


async def stage3_rerank(
    session: AsyncSession,
    query_text: str,
    event_ids_with_dist: list[tuple[int, float]],
    top_k: int = 5,
) -> list[tuple[int, float]]:
    """Stage 3: bge-reranker cross-encoder rerank.
    Returns list of (event_id, reranker_score), sorted by score desc.
    """
    if not event_ids_with_dist:
        return []

    ids = [eid for eid, _ in event_ids_with_dist]
    stmt = select(NewsEvent.id, NewsEvent.merged_summary).where(NewsEvent.id.in_(ids))
    rows = (await session.execute(stmt)).all()
    id_to_summary = {r[0]: (r[1] or "") for r in rows}

    pairs = [(query_text, id_to_summary[eid]) for eid in ids if id_to_summary.get(eid)]
    if not pairs:
        return []

    # Encode in worker thread (sentence-transformers is sync)
    def _rerank():
        model = _get_reranker()
        scores = model.predict(pairs, show_progress_bar=False)
        return scores.tolist() if hasattr(scores, "tolist") else list(scores)

    try:
        scores = await asyncio.to_thread(_rerank)
    except Exception as e:
        print(f"[rag.rerank] failed: {e}; using ANN order")
        scores = [1.0 - d for _, d in event_ids_with_dist]

    # Combine + sort
    scored = list(zip(ids, scores))
    scored.sort(key=lambda x: -x[1])
    return scored[:top_k]


async def retrieve(
    session: AsyncSession,
    query_text: str,
    interest_tags: list[str] | None = None,
    top_recall: int = 20,
    top_final: int = 5,
    use_time_filter: bool = True,
    use_reranker: bool = True,
) -> list[int]:
    """Full pipeline: time -> ANN -> rerank -> event ids.

    Feature flags for ablation studies:
      use_time_filter=False: skip SQL time pre-filter (semantic-only)
      use_reranker=False:    return ANN order without cross-encoder rerank
    """
    window = _parse_time_window(query_text) if use_time_filter else None
    candidate_ids = await stage1_time_filter(session, window, interest_tags)
    if not candidate_ids:
        return []
    ann_results = await stage2_ann_recall(session, query_text, candidate_ids, top_k=top_recall)
    if not use_reranker:
        return [eid for eid, _ in ann_results[:top_final]]
    rerank_results = await stage3_rerank(session, query_text, ann_results, top_k=top_final)
    return [eid for eid, _ in rerank_results]