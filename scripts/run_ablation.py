"""Run 5-config ablation study on the RAG pipeline.

Configs:
  ① dense Top-5           (no time filter, no reranker, no constrained generation)
  ② ① + time filter      (time filter on)
  ③ ② + reranker         (time filter + reranker)
  ④ ③ + constrained gen  (time filter + reranker + constrained prompt)
  ⑤ final = ④

Outputs:
  - data/rag_ablation_results.json   (metrics per config)
  - data/rag_ablation_table.csv      (5-row table for paper)

Usage:
  .venv/bin/python -m scripts.run_ablation
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
from app.services.rag.retrieval import retrieve

GOLD_PATH = Path(settings.root) / "data" / "rag_gold.csv"
RESULTS_PATH = Path(settings.root) / "data" / "rag_ablation_results.json"
TABLE_PATH = Path(settings.root) / "data" / "rag_ablation_table.csv"

_EVENT_SPLIT_RE = re.compile(r"(?==== 事件 #\d+ ===)")


def _load_gold() -> list[dict]:
    with GOLD_PATH.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    labeled = []
    for r in rows:
        gold_raw = r.get("gold_event_ids", "").strip()
        gold = [int(x) for x in gold_raw.split(",") if x.strip()] if gold_raw else []
        labeled.append({"question": r["question"], "gold_event_ids": gold})
    return labeled


def _compute_recall(retrieved: list[int], gold: list[int]) -> float:
    if not gold:
        return 1.0 if not retrieved else 0.0
    inter = set(retrieved[:5]) & set(gold)
    return min(len(inter), 5) / min(len(gold), 5)


def _parse_sse_events(events: list[str]) -> dict:
    """Parse SSE event strings into {tokens: [], done: {}}."""
    result: dict = {"tokens": [], "done": {}}
    current_type = None
    current_data = []

    def _flush():
        nonlocal current_type, current_data
        if current_type is None:
            return
        data = "\n".join(current_data)
        if current_type == "token":
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


def _split_context_blocks(context: str) -> list[str]:
    blocks = [b.strip() for b in _EVENT_SPLIT_RE.split(context) if b.strip()]
    return blocks if blocks else [context]


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


def _run_ragas_faithfulness(eval_rows: list[dict]) -> list[float | None]:
    """Run RAGAS faithfulness on all rows. Returns scores in same order."""
    if not settings.deepseek_api_key:
        return [None] * len(eval_rows)

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
        print(f"[run_ablation] RAGAS faithfulness failed: {e}; falling back to None")
        scores = [None] * len(valid_indices)

    out = [None] * len(eval_rows)
    for idx, score in zip(valid_indices, scores):
        out[idx] = _safe_float(score)
    return out


async def _eval_config(session, rows: list[dict], config: dict, config_name: str) -> dict:
    recalls = []
    for row in rows:
        event_ids = await retrieve(
            session,
            row["question"],
            use_time_filter=config["use_time_filter"],
            use_reranker=config["use_reranker"],
        )
        recalls.append(_compute_recall(event_ids, row["gold_event_ids"]))

    # For faithfulness / refusal we need actual answers; run a small sample
    # to keep token cost bounded. Use first 10 non-refusal rows.
    sample_rows = [r for r in rows if r["gold_event_ids"]][:10]
    eval_rows = []
    refusal_corrects = []
    for row in sample_rows:
        events = []
        async for evt in rag_answer_stream(
            session,
            row["question"],
            use_time_filter=config["use_time_filter"],
            use_reranker=config["use_reranker"],
            use_constrained_generation=config["use_constrained_generation"],
        ):
            events.append(evt)

        parsed = _parse_sse_events(events)
        answer_text = "".join(parsed.get("tokens", []))
        refusal = parsed.get("done", {}).get("refusal", False)
        expected_refusal = len(row["gold_event_ids"]) == 0
        refusal_corrects.append(refusal == expected_refusal)

        # Reconstruct context for RAGAS
        retrieved = []
        for evt in events:
            if evt.startswith("event: meta\ndata: "):
                retrieved = json.loads(evt.replace("event: meta\ndata: ", "")).get("event_ids", [])
                break
        events_with_reports = await fetch_events_with_reports(session, retrieved)
        context = _build_context(events_with_reports)

        eval_rows.append({
            "question": row["question"],
            "answer": answer_text,
            "context": context,
        })

    faithfulness_scores = _run_ragas_faithfulness(eval_rows)
    faithfulnesses = [s for s in faithfulness_scores if s is not None]

    return {
        "config": config_name,
        "use_time_filter": config["use_time_filter"],
        "use_reranker": config["use_reranker"],
        "use_constrained_generation": config["use_constrained_generation"],
        "avg_recall@5": sum(recalls) / len(recalls) if recalls else 0.0,
        "avg_faithfulness": sum(faithfulnesses) / len(faithfulnesses) if faithfulnesses else None,
        "refusal_accuracy": sum(refusal_corrects) / len(refusal_corrects) if refusal_corrects else None,
    }


async def main():
    if not GOLD_PATH.exists():
        print(f"[run_ablation] Gold set not found: {GOLD_PATH}")
        print("Run: .venv/bin/python -m scripts.label_rag_gold")
        return

    rows = _load_gold()
    if not rows:
        print("[run_ablation] No labeled rows found.")
        return

    configs = [
        ("① dense Top-5", {"use_time_filter": False, "use_reranker": False, "use_constrained_generation": False}),
        ("② ① + time filter", {"use_time_filter": True, "use_reranker": False, "use_constrained_generation": False}),
        ("③ ② + reranker", {"use_time_filter": True, "use_reranker": True, "use_constrained_generation": False}),
        ("④ ③ + constrained gen", {"use_time_filter": True, "use_reranker": True, "use_constrained_generation": True}),
        ("⑤ final", {"use_time_filter": True, "use_reranker": True, "use_constrained_generation": True}),
    ]

    results = []
    async with AsyncSessionLocal() as session:
        for name, config in configs:
            print(f"[run_ablation] Running {name} ...")
            res = await _eval_config(session, rows, config, name)
            results.append(res)
            print(f"  recall@5={res['avg_recall@5']:.3f} faithfulness={res['avg_faithfulness']}")

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    with TABLE_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["配置", "time_filter", "reranker", "constrained_gen", "recall@5", "faithfulness"],
        )
        writer.writeheader()
        for r in results:
            writer.writerow({
                "配置": r["config"],
                "time_filter": r["use_time_filter"],
                "reranker": r["use_reranker"],
                "constrained_gen": r["use_constrained_generation"],
                "recall@5": f"{r['avg_recall@5']:.3f}",
                "faithfulness": f"{r['avg_faithfulness']:.3f}" if r["avg_faithfulness"] is not None else "",
            })

    print(f"\n[run_ablation] Results saved to {RESULTS_PATH}")
    print(f"[run_ablation] Table saved to {TABLE_PATH}")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
