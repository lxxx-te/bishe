"""Ablation row #6: query-time full-text extraction (use_full_text).

Protocol mirrors run_ablation.py: same first-10 retrieval-question sample,
same RAGAS faithfulness scoring. Two arms over the FINAL pipeline
(time filter + reranker + constrained generation):

  A. use_full_text=False   context = merged summaries only
  B. use_full_text=True    context = summaries + extracted raw-text passages

recall@5 is recorded to show it is invariant by construction (the flag only
changes context building, not retrieval).

Outputs:
  - data/fulltext_ablation_results.json  (per-question answers + metrics)
  - data/fulltext_ablation_table.csv     (2-row table for paper appendix)

Usage:
  .venv/bin/python -m scripts.run_fulltext_ablation
"""
from __future__ import annotations

import os
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import asyncio
import csv
import json
from pathlib import Path

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.rag.answer import (
    _build_context,
    extract_relevant_passages,
    fetch_events_with_reports,
    rag_answer_stream,
)
from scripts.run_ablation import (
    GOLD_PATH,
    _compute_recall,
    _parse_sse_events,
    _run_ragas_faithfulness,
)

OUT_PATH = Path(settings.root) / "data" / "fulltext_ablation_results.json"
TABLE_PATH = Path(settings.root) / "data" / "fulltext_ablation_table.csv"

FULLTEXT_HEADER = "\n\n=== 相关报道原文摘录（同样受规则约束，只能用这些内容） ===\n"


def _load_sample(n: int = 10) -> list[dict]:
    with GOLD_PATH.open("r", encoding="utf-8") as f:
        import csv
        rows = list(csv.DictReader(f))
    labeled = []
    for r in rows:
        gold_raw = r.get("gold_event_ids", "").strip()
        gold = [int(x) for x in gold_raw.split(",") if x.strip()] if gold_raw else []
        if gold:
            labeled.append({"question": r["question"], "gold_event_ids": gold})
    return labeled[:n]


async def _eval_arm(session, rows: list[dict], use_full_text: bool) -> dict:
    recalls = []
    eval_rows = []
    refusals = 0
    for i, row in enumerate(rows):
        events = []
        async for evt in rag_answer_stream(
            session,
            row["question"],
            use_time_filter=True,
            use_reranker=True,
            use_constrained_generation=True,
            use_full_text=use_full_text,
        ):
            events.append(evt)
        parsed = _parse_sse_events(events)
        answer_text = "".join(parsed.get("tokens", []))
        refusal = parsed.get("done", {}).get("refusal", False)
        refusals += int(refusal)

        retrieved = []
        for evt in events:
            if evt.startswith("event: meta\ndata: "):
                retrieved = json.loads(evt.replace("event: meta\ndata: ", "")).get("event_ids", [])
                break
        recalls.append(_compute_recall(retrieved, row["gold_event_ids"]))

        # Rebuild the ACTUAL grounding context for honest RAGAS scoring.
        events_with_reports = await fetch_events_with_reports(session, retrieved)
        context = _build_context(events_with_reports)
        if use_full_text and settings.deepseek_api_key:
            passages = await extract_relevant_passages(row["question"], events_with_reports)
            if passages:
                context += FULLTEXT_HEADER + passages

        eval_rows.append({
            "question": row["question"],
            "answer": answer_text,
            "context": context,
        })
        print(f"  [arm fulltext={use_full_text}] {i+1}/{len(rows)} done "
              f"(answer {len(answer_text)} chars, refusal={refusal})", flush=True)

    faith_scores = _run_ragas_faithfulness(eval_rows)
    faiths = [s for s in faith_scores if s is not None]
    return {
        "arm": "B_full_text" if use_full_text else "A_summary_only",
        "avg_recall@5": sum(recalls) / len(recalls) if recalls else 0.0,
        "avg_answer_chars": sum(len(r["answer"]) for r in eval_rows) / len(eval_rows),
        "refusals": refusals,
        "avg_faithfulness": sum(faiths) / len(faiths) if faiths else None,
        "rows": [
            {"question": r["question"], "answer": r["answer"], "faithfulness": s}
            for r, s in zip(eval_rows, faith_scores)
        ],
    }


async def main():
    rows = _load_sample()
    print(f"[run_fulltext_ablation] sample = first {len(rows)} retrieval questions", flush=True)

    results = []
    async with AsyncSessionLocal() as session:
        for arm in (False, True):
            print(f"[run_fulltext_ablation] Running arm use_full_text={arm} ...", flush=True)
            res = await _eval_arm(session, rows, arm)
            results.append(res)
            print(f"  -> recall@5={res['avg_recall@5']:.3f} "
                  f"faithfulness={res['avg_faithfulness']} "
                  f"avg_chars={res['avg_answer_chars']:.0f} refusals={res['refusals']}", flush=True)

    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    with TABLE_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["配置", "use_full_text", "recall@5", "faithfulness", "平均答案长度", "拒答数"],
        )
        writer.writeheader()
        for r in results:
            writer.writerow({
                "配置": r["arm"],
                "use_full_text": r["arm"] == "B_full_text",
                "recall@5": f"{r['avg_recall@5']:.3f}",
                "faithfulness": f"{r['avg_faithfulness']:.3f}" if r["avg_faithfulness"] is not None else "",
                "平均答案长度": f"{r['avg_answer_chars']:.0f}",
                "拒答数": r["refusals"],
            })
    print(f"[run_fulltext_ablation] saved {OUT_PATH} and {TABLE_PATH}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
