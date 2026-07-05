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

    Re-processes 'fallback' summaries (Q1 fix: only 'llm' source is final).
    Skips reports where raw_text is NULL (Q2 fix: no placeholder embed).
    """
    async with AsyncSessionLocal() as session:
        stmt = (
            select(NewsReport)
            .where(NewsReport.summary_source != "llm")
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

            # 1. Summaries (returns list of (summary, source, model, tokens) tuples)
            try:
                summary_results = await summarize_batch(raw_texts)
            except Exception as e:
                print(f"[p2] batch {i//BATCH_SIZE} summarize failed: {e}; retry one-by-one")
                summary_results = []
                for t in raw_texts:
                    try:
                        from app.services.summarize import generate_summary
                        summary_results.append(await generate_summary(t))
                    except Exception:
                        summary_results.append(("", "fallback", None, None))
                        errors += 1

            # 2. Write summaries + source + model + tokens FIRST
            embed_inputs = []
            embed_idx = []
            for j, r in enumerate(batch):
                summ, src, model_id, tokens = summary_results[j]
                if summ and summ.strip():
                    r.summary = summ
                    r.summary_source = src
                    if src == "llm":
                        r.summary_model = model_id
                        r.summary_tokens = tokens
                if not r.raw_text or not r.raw_text.strip():
                    continue
                embed_text = summ if (summ and summ.strip()) else r.raw_text[:500]
                embed_inputs.append(embed_text)
                embed_idx.append(j)

            # Commit summaries immediately (save LLM tokens already spent)
            await session.commit()

            # 3. Embeddings
            if not embed_inputs:
                # all rows in this batch were empty raw_text; nothing to embed
                processed += len(batch)
                continue

            try:
                embeddings = await embed_async(embed_inputs)
            except Exception as e:
                print(f"[p2] batch {i//BATCH_SIZE} embed failed: {e}; summaries saved but no embedding")
                errors += len(batch)
                processed += len(batch)
                continue

            for k, j in enumerate(embed_idx):
                batch[j].embedding = embeddings[k]
            await session.commit()

            processed += len(batch)
            print(f"[p2] batch {i//BATCH_SIZE+1}: {len(batch)} done (cum {processed})")

        return {
            "processed": processed,
            "skipped_empty_text": skipped_empty,
            "errors": errors,
        }