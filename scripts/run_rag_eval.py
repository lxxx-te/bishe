"""Run RAG evaluation against human-labeled gold set.

Reads data/rag_gold.csv, runs /api/rag/ask logic for each question,
computes:
  - set recall@5
  - refusal accuracy
  - citation validity rate
  - faithfulness (RAGAS with DeepSeek)

Outputs:
  - data/rag_eval_results.json   (per-row details)
  - data/rag_eval_summary.json   (aggregate metrics)

Usage:
  .venv/bin/python -m scripts.run_rag_eval
"""
from __future__ import annotations

import os
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import asyncio
import csv
import json
import math
import re
from pathlib import Path

from datasets import Dataset
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.rag.answer import _build_context, fetch_events_with_reports, rag_answer_stream

GOLD_PATH = Path(settings.root) / "data" / "rag_gold.csv"
RESULTS_PATH = Path(settings.root) / "data" / "rag_eval_results.json"
SUMMARY_PATH = Path(settings.root) / "data" / "rag_eval_summary.json"

_CITATION_RE = re.compile(r"\[事件#(\d+)\]")
_EVENT_SPLIT_RE = re.compile(r"(?==== 事件 #\d+ ===)")


def _parse_sse_events(events: list[str]) -> dict:
    """Parse SSE event strings into {meta, tokens: [], done}."""
    result: dict = {"meta": {}, "tokens": [], "done": {}}
    current_type = None
    current_data = []

    def _flush():
        nonlocal current_type, current_data
        if current_type is None:
            return
        data = "\n".join(current_data)
        if current_type == "meta":
            result["meta"] = json.loads(data)
        elif current_type == "token":
            result["tokens"].append(data)
        elif current_type == "done":
            result["done"] = json.loads(data)
        current_type = None
        current_data = []

    for evt in events:
        for line in evt.split("\n"):
            if line.startswith("event: "):
                _flush()
                current_type = line.replace("event: ", "")
            elif line.startswith("data: "):
                current_data.append(line.replace("data: ", "", 1))
        _flush()
    return result


def _compute_recall(retrieved: list[int], gold: list[int]) -> float:
    """set recall@5 = min(|Top-5 ∩ gold|, 5) / min(|gold|, 5)."""
    if not gold:
        return 1.0 if not retrieved else 0.0
    inter = set(retrieved[:5]) & set(gold)
    numerator = min(len(inter), 5)
    denominator = min(len(gold), 5)
    return numerator / denominator if denominator else 1.0


def _split_context_blocks(context: str) -> list[str]:
    """Split built context into per-event chunks for RAGAS."""
    blocks = [b.strip() for b in _EVENT_SPLIT_RE.split(context) if b.strip()]
    return blocks if blocks else [context]


def _run_ragas_faithfulness(eval_rows: list[dict]) -> list[float | None]:
    """Run RAGAS faithfulness on all rows. Returns scores in same order."""
    if not settings.deepseek_api_key:
        return [None] * len(eval_rows)

    # Filter rows that have answers (skip pure errors)
    valid_indices = []
    questions, answers, contexts_list = [], [], []
    for i, row in enumerate(eval_rows):
        if row.get("answer"):
            valid_indices.append(i)
            questions.append(row["question"])
            answers.append(row["answer"])
            contexts_list.append(_split_context_blocks(row.get("context", "")))

    if not valid_indices:
        return [None] * len(eval_rows)

    try:
        from ragas import evaluate
        from ragas.metrics import faithfulness

        llm = ChatOpenAI(
            model=settings.deepseek_model,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            temperature=0.1,
            max_tokens=512,
        )
        ds = Dataset.from_dict({
            "question": questions,
            "answer": answers,
            "contexts": contexts_list,
        })
        result = evaluate(ds, metrics=[faithfulness], llm=llm)
        scores = list(result["faithfulness"])
    except Exception as e:
        print(f"[run_rag_eval] RAGAS faithfulness failed: {e}; falling back to None")
        scores = [None] * len(valid_indices)

    # Map back to original order
    out = [None] * len(eval_rows)
    for idx, score in zip(valid_indices, scores):
        out[idx] = score
    return out


async def _eval_one(session, row: dict) -> dict:
    question = row["question"]
    gold_raw = row.get("gold_event_ids", "").strip()
    gold = [int(x) for x in gold_raw.split(",") if x.strip()] if gold_raw else []

    events = []
    async for evt in rag_answer_stream(session, question):
        events.append(evt)

    parsed = _parse_sse_events(events)
    meta = parsed.get("meta", {})
    tokens = parsed.get("tokens", [])
    done = parsed.get("done", {})

    retrieved = meta.get("event_ids", [])
    answer_text = "".join(tokens)
    refusal = done.get("refusal", False)
    citations_valid = done.get("citations_valid", True)

    # Reconstruct context for RAGAS faithfulness
    events_with_reports = await fetch_events_with_reports(session, retrieved)
    context = _build_context(events_with_reports)

    recall = _compute_recall(retrieved, gold)
    expected_refusal = len(gold) == 0
    refusal_correct = (refusal == expected_refusal)

    return {
        "question": question,
        "question_type": row.get("question_type", ""),
        "gold_event_ids": gold,
        "retrieved_event_ids": retrieved,
        "answer": answer_text,
        "context": context,
        "refusal": refusal,
        "refusal_correct": refusal_correct,
        "citations_valid": citations_valid,
        "recall@5": recall,
        "faithfulness": None,  # filled later by RAGAS
    }


async def main():
    if not GOLD_PATH.exists():
        print(f"[run_rag_eval] Gold set not found: {GOLD_PATH}")
        print("Run: .venv/bin/python -m scripts.label_rag_gold")
        return

    with GOLD_PATH.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Filter rows that have been labeled
    labeled_rows = [r for r in rows if r.get("gold_event_ids", "").strip() or r.get("question_type") == "refusal"]
    if not labeled_rows:
        print("[run_rag_eval] No labeled rows found. Please label the gold set first.")
        return

    results = []
    async with AsyncSessionLocal() as session:
        for i, row in enumerate(labeled_rows):
            print(f"[run_rag_eval] {i+1}/{len(labeled_rows)}: {row['question'][:40]}...")
            try:
                res = await _eval_one(session, row)
                results.append(res)
            except Exception as e:
                print(f"[run_rag_eval] failed for row {row.get('id')}: {e}")
                results.append({
                    "question": row["question"],
                    "error": str(e),
                })

    # Run RAGAS faithfulness in batch
    print("[run_rag_eval] Running RAGAS faithfulness...")
    faithfulness_scores = _run_ragas_faithfulness(results)
    for res, score in zip(results, faithfulness_scores):
        if "faithfulness" in res:
            res["faithfulness"] = score

    # Sanitize RAGAS scores before aggregating (NaN -> None)
    def _safe_float(v):
        if v is None:
            return None
        try:
            f = float(v)
            if math.isnan(f) or math.isinf(f):
                return None
            return f
        except Exception:
            return None

    # Aggregate metrics
    recalls = [r["recall@5"] for r in results if "recall@5" in r]
    refusal_corrects = [r["refusal_correct"] for r in results if "refusal_correct" in r]
    citation_valids = [r["citations_valid"] for r in results if "citations_valid" in r]
    faithfulnesses = [_safe_float(r.get("faithfulness")) for r in results]
    faithfulnesses = [v for v in faithfulnesses if v is not None]

    summary = {
        "n": len(results),
        "avg_recall@5": sum(recalls) / len(recalls) if recalls else 0.0,
        "refusal_accuracy": sum(refusal_corrects) / len(refusal_corrects) if refusal_corrects else 0.0,
        "citation_valid_rate": sum(citation_valids) / len(citation_valids) if citation_valids else 0.0,
        "avg_faithfulness": _safe_float(sum(faithfulnesses) / len(faithfulnesses)) if faithfulnesses else None,
    }

    # Sanitize results JSON (RAGAS may return numpy types / NaN)
    clean_results = []
    for r in results:
        clean_r = {}
        for k, v in r.items():
            clean_r[k] = _safe_float(v) if k == "faithfulness" else v
        clean_results.append(clean_r)

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(clean_results, ensure_ascii=False, indent=2), encoding="utf-8")
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n[run_rag_eval] Summary:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"[run_rag_eval] Detailed results saved to {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
