"""Build a template CSV for RAG evaluation gold set.

Generates candidate questions from existing events. Leaves gold_event_ids empty
so humans can label them without circular reasoning (do not use system output
as ground truth).

Output: data/rag_gold_template.csv
Columns: id, question, question_type, gold_event_ids, source_event_id, notes

Question types:
- specific: gold = 1 event (generated from a single event summary)
- broad:    gold = N events (human should fill gold_event_ids)
- refusal:  gold = [] (questions outside the corpus)

Usage:
  .venv/bin/python -m scripts.build_rag_gold
  .venv/bin/python -m scripts.build_rag_gold --no-llm   # skip LLM, use heuristic questions
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import random
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models import NewsEvent
from app.services.summarize import _call_deepseek, _get_client

OUTPUT_PATH = Path(settings.root) / "data" / "rag_gold_template.csv"

_SPECIFIC_QUESTION_SYSTEM = (
    "你是一个新闻问答评测集构造助手。请根据给定的事件摘要，"
    "生成 1 个自然语言问题。要求：\n"
    "1) 问题必须能被该事件摘要回答；\n"
    "2) 问题要自然，像真实用户会问的；\n"
    "3) 只输出问题本身，不要解释、不要编号、不要引号。"
)

_BROAD_QUESTION_EXAMPLES = [
    "最近一周有哪些重要政治新闻？",
    "最近关于国际会议的报道有哪些？",
    "最近发生了哪些社会热点事件？",
    "近期有哪些经济政策发布？",
    "最近关于科技领域的新闻有哪些？",
    "最近有哪些文化活动报道？",
    "最近体育方面有什么重要新闻？",
    "近期有哪些法律法规出台？",
    "最近关于外交活动的报道有哪些？",
    "最近有哪些涉及民生的事件？",
    "近期有哪些关于环境保护的报道？",
    "最近有哪些关于教育改革的新闻？",
    "近期关于医疗健康领域有什么报道？",
    "最近有哪些关于交通运输的事件？",
    "近期有哪些关于食品安全的新闻？",
    "最近有哪些关于安全生产的事件？",
    "近期有哪些关于乡村振兴的报道？",
    "最近有哪些关于科技创新的新闻？",
    "近期有哪些关于文旅活动的事件？",
    "最近有哪些关于志愿服务的新闻？",
]

_REFUSAL_QUESTION_EXAMPLES = [
    "明天的彩票开奖号码是多少？",
    "2020 年美国总统大选结果如何？",
    "如何制作蛋糕？",
    "北京大学计算机系某教授的联系方式是什么？",
    "某明星的私人生活细节有哪些？",
    "请预测下个月股市走势",
    "太阳系外宜居星球有哪些？",
    "如何学习 Python 编程？",
    "某品牌手机的内部参数是什么？",
    "今天北京天气怎么样？",
]


async def _generate_question_for_event(client, event: NewsEvent) -> str | None:
    """Use LLM to generate one natural question from an event summary."""
    if not event.merged_summary:
        return None
    try:
        text, _, _ = await _call_deepseek(
            client,
            _SPECIFIC_QUESTION_SYSTEM,
            f"事件摘要：{event.merged_summary[:200]}\n\n请生成一个问题：",
            temperature=0.3,
            max_tokens=100,
        )
        return text.strip().rstrip("?？").strip()
    except Exception as e:
        print(f"[build_rag_gold] LLM failed for event {event.id}: {e}")
        return None


async def main(no_llm: bool = False):
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with AsyncSessionLocal() as session:
        events = (
            (await session.execute(
                select(NewsEvent).where(NewsEvent.merged_summary.is_not(None))
            ))
            .scalars()
            .all()
        )

    if not events:
        print("[build_rag_gold] No events found. Run P1-P4 first.")
        return

    # Pick diverse events for 25 specific questions.
    multi_source = [e for e in events if e.source_count and e.source_count >= 2]
    single_source = [e for e in events if e.source_count and e.source_count == 1]

    selected = (
        random.sample(multi_source, min(20, len(multi_source))) if multi_source else []
    )
    remaining = 25 - len(selected)
    selected += (
        random.sample(single_source, min(remaining, len(single_source))) if single_source else []
    )
    # If still short, fill with any events
    if len(selected) < 25:
        pool = [e for e in events if e not in selected]
        selected += random.sample(pool, min(25 - len(selected), len(pool)))

    client = None if no_llm else (_get_client() if settings.deepseek_api_key else None)

    rows: list[dict] = []

    # Specific questions (25 from event summaries)
    n_specific = 25
    for idx, event in enumerate(selected[:n_specific]):
        if idx % 5 == 0:
            print(f"[build_rag_gold] generating specific question {idx+1}/{n_specific}...")
        if client:
            q = await _generate_question_for_event(client, event)
        else:
            q = None
        if not q:
            # Fallback heuristic question
            summary_head = (event.merged_summary or "该事件")[:20]
            q = f"关于{summary_head}...的事件详情？"
        rows.append({
            "id": idx + 1,
            "question": q,
            "question_type": "specific",
            "gold_event_ids": "",
            "source_event_id": event.id,
            "notes": "请人工确认 gold_event_ids，可填 1 个或多个事件 ID",
        })

    # Broad questions (5 placeholders, human fills gold)
    n_broad = 5
    for i, q in enumerate(_BROAD_QUESTION_EXAMPLES[:n_broad]):
        rows.append({
            "id": len(rows) + 1,
            "question": q,
            "question_type": "broad",
            "gold_event_ids": "",
            "source_event_id": "",
            "notes": "请人工标出所有相关事件 ID，用逗号分隔",
        })

    # Refusal questions (5)
    n_refusal = 5
    for i, q in enumerate(_REFUSAL_QUESTION_EXAMPLES[:n_refusal]):
        rows.append({
            "id": len(rows) + 1,
            "question": q,
            "question_type": "refusal",
            "gold_event_ids": "",
            "source_event_id": "",
            "notes": "gold_event_ids 留空，表示应拒答",
        })

    # Shuffle so specific/broad/refusal are mixed
    random.shuffle(rows)
    for i, row in enumerate(rows):
        row["id"] = i + 1

    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "question", "question_type", "gold_event_ids", "source_event_id", "notes"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"[build_rag_gold] Wrote {len(rows)} candidate questions to {OUTPUT_PATH}")
    print(f"[build_rag_gold] Composition: {n_specific} specific + {n_broad} broad + {n_refusal} refusal")
    print("[build_rag_gold] Next step: run scripts/label_rag_gold.py to label gold_event_ids")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-llm", action="store_true", help="skip LLM, use heuristic questions")
    args = parser.parse_args()
    asyncio.run(main(no_llm=args.no_llm))
