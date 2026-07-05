"""P2 orchestrator: summarize + embed all news_report rows that lack them.

Iterates reports WHERE summary IS NULL OR embedding IS NULL in small batches.
Idempotent - safe to re-run after partial failures (resumes from NULL markers).
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models import NewsReport
from app.services.embed import embed_async
from app.services.summarize import summarize_batch

BATCH_SIZE = 16  # DeepSeek RPM-friendly; bge batch easily handles 64+


async def run_p2(limit: int | None = None) -> dict:
    """Process all reports that still need summary/embedding.

    Args:
        limit: max reports to process this run (None = all pending)

    Returns:
        {"processed": N, "skipped_empty_text": K, "errors": M}
    """
    async with AsyncSessionLocal() as session:
        stmt = (
            select(NewsReport)
            .where((NewsReport.summary.is_(None)) | (NewsReport.embedding.is_(None)))
            .order_by(NewsReport.id)
        )
        if limit:
            stmt = stmt.limit(limit)
        rows = (await session.execute(stmt)).scalars().all()

        if not rows:
            print("[p2] nothing to do")
            return {"processed": 0, "skipped_empty_text": 0, "errors": 0}

        print(f"[p2] {len(rows)} reports pending summary+embed")

        skipped_empty = 0
        processed = 0
        errors = 0

        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            raw_texts = []
            non_empty_idx = []
            for j, r in enumerate(batch):
                if not r.raw_text or not r.raw_text.strip():
                    skipped_empty += 1
                    raw_texts.append("")
                else:
                    raw_texts.append(r.raw_text)
                    non_empty_idx.append(j)

            # 1. Summaries (only for non-empty)
            try:
                full_summaries = await summarize_batch(raw_texts)
            except Exception as e:
                print(f"[p2] batch {i//BATCH_SIZE} summarize failed: {e}; retry one-by-one")
                full_summaries = []
                for t in raw_texts:
                    try:
                        from app.services.summarize import generate_summary
                        full_summaries.append(await generate_summary(t))
                    except Exception:
                        full_summaries.append("")
                        errors += 1

            # 2. Write summaries FIRST (so embed failure does not lose summaries)
            summaries_for_embed = []
            for j, r in enumerate(batch):
                summ = full_summaries[j]
                if summ and summ.strip():
                    r.summary = summ
                    summaries_for_embed.append(summ)
                elif r.raw_text:
                    summaries_for_embed.append(r.raw_text[:500])
                else:
                    summaries_for_embed.append("（空内容）")

            try:
                embeddings = await embed_async(summaries_for_embed)
            except Exception as e:
                print(f"[p2] batch {i//BATCH_SIZE} embed failed: {e}; saving summaries only")
                # Still save summaries even if embed failed.
                await session.commit()
                errors += len(batch)
                processed += sum(1 for s in full_summaries if s)
                continue

            # 3. Write embeddings
            for j, r in enumerate(batch):
                r.embedding = embeddings[j]
                processed += 1

            await session.commit()
            print(f"[p2] batch {i//BATCH_SIZE+1}: {len(batch)} done (cum {processed})")

        return {
            "processed": processed,
            "skipped_empty_text": skipped_empty,
            "errors": errors,
        }