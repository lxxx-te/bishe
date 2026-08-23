"""Interactive CLI to label RAG evaluation gold event IDs.

Reads data/rag_gold_template.csv, prompts for gold_event_ids per row,
writes data/rag_gold.csv.

For specific questions, automatically shows the source event summary and
report titles so the human labeler can verify the gold event.

For broad questions, suggests candidate events by category/keyword matching.
The human labeler must still review and confirm; suggestions are not ground truth.

Commands:
  <id1,id2,...>  label gold event IDs (comma separated, no spaces)
  .              skip this row
  q              save progress and quit
"""
from __future__ import annotations

import asyncio
import csv
import re
from pathlib import Path

from sqlalchemy import select, or_

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models import NewsEvent, NewsReport

TEMPLATE_PATH = Path(settings.root) / "data" / "rag_gold_template.csv"
OUTPUT_PATH = Path(settings.root) / "data" / "rag_gold.csv"

# Prefer existing output as the source of truth so that partial labels are preserved.
SOURCE_PATH = OUTPUT_PATH

_CATEGORIES = ["政治", "经济", "文化", "社会", "科技", "国际", "体育", "其他"]


def _parse_input(raw: str) -> list[int] | None:
    """Parse comma-separated ints. Empty -> []. None -> skip."""
    raw = raw.strip()
    if raw in (".", ""):
        return None  # skip
    if raw.lower() == "q":
        return "QUIT"
    if not raw:
        return []
    try:
        return [int(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError:
        return None


async def _load_event_details(event_ids: set[int]) -> dict[int, dict]:
    """Fetch event summaries and report titles for display."""
    if not event_ids:
        return {}
    details: dict[int, dict] = {}
    async with AsyncSessionLocal() as session:
        events = (
            (await session.execute(select(NewsEvent).where(NewsEvent.id.in_(event_ids))))
            .scalars()
            .all()
        )
        for ev in events:
            reports = (
                (await session.execute(
                    select(NewsReport).where(NewsReport.event_id == ev.id).limit(5)
                ))
                .scalars()
                .all()
            )
            details[ev.id] = {
                "summary": ev.merged_summary or "(无摘要)",
                "category": ev.category or "(无)",
                "time": ev.event_publish_time.isoformat() if ev.event_publish_time else "(无)",
                "reports": [f"[{r.source_site}] {r.title}" for r in reports],
            }
    return details


async def _find_candidate_events(questions: list[str]) -> dict[str, list[dict]]:
    """Suggest candidate events for each broad question.

    Strategy:
      1. If question contains a known category name, include events of that category.
      2. Include events whose summary contains any prominent noun (>1 char) from question.
      3. Deduplicate and limit to top 10 by source_count.

    Returns plain dicts so the in-memory results can be used safely after the
    async DB session is closed.
    """
    result: dict[str, list[dict]] = {}
    async with AsyncSessionLocal() as session:
        for q in questions:
            conditions = []
            for cat in _CATEGORIES:
                if cat in q:
                    conditions.append(NewsEvent.category == cat)
            cleaned = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", " ", q)
            tokens = {t for t in cleaned.split() if len(t) >= 2 and t not in _CATEGORIES}
            for tok in tokens:
                conditions.append(NewsEvent.merged_summary.ilike(f"%{tok}%"))

            if not conditions:
                result[q] = []
                continue

            events = (
                (await session.execute(
                    select(NewsEvent)
                    .where(or_(*conditions))
                    .order_by(NewsEvent.source_count.desc(), NewsEvent.event_publish_time.desc())
                    .limit(10)
                ))
                .scalars()
                .all()
            )
            result[q] = [
                {
                    "id": ev.id,
                    "category": ev.category,
                    "summary": ev.merged_summary or "(无摘要)",
                }
                for ev in events
            ]
    return result


def _show_event(ev_id: int, details: dict[int, dict]):
    d = details.get(ev_id)
    if not d:
        print(f"  [事件 {ev_id} 详情未找到]")
        return
    print(f"\n  --- 事件 #{ev_id} ---")
    print(f"  摘要: {d['summary'][:200]}")
    print(f"  类别: {d['category']} | 时间: {d['time']}")
    if d["reports"]:
        print("  报道:")
        for r in d["reports"][:3]:
            print(f"    · {r}")


async def _preload(rows: list[dict]) -> tuple[dict[int, dict], dict[str, list[NewsEvent]]]:
    """Pre-fetch all data needed for the interactive loop."""
    source_ids = {
        int(row["source_event_id"])
        for row in rows
        if row.get("source_event_id", "").strip()
    }
    broad_questions = [row["question"] for row in rows if row.get("question_type") == "broad"]

    event_details = await _load_event_details(source_ids)
    broad_candidates = await _find_candidate_events(broad_questions)
    return event_details, broad_candidates


def main():
    # Use the existing output as the source of truth when it exists, so that
    # partial labels are preserved even if the template has been regenerated.
    if SOURCE_PATH.exists():
        with SOURCE_PATH.open("r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        print(f"[label_rag_gold] Loaded {len(rows)} rows from existing output {SOURCE_PATH}")
    elif TEMPLATE_PATH.exists():
        with TEMPLATE_PATH.open("r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        print(f"[label_rag_gold] Loaded {len(rows)} rows from template {TEMPLATE_PATH}")
    else:
        print(f"[label_rag_gold] Template not found: {TEMPLATE_PATH}")
        print("Run: .venv/bin/python -m scripts.build_rag_gold")
        return

    # Pre-fetch data with a single event loop
    print("[label_rag_gold] Pre-loading event details and broad candidates...")
    event_details, broad_candidates = asyncio.run(_preload(rows))
    print(f"[label_rag_gold] Loaded details for {len(event_details)} events")

    labeled = 0
    skipped = 0
    already_done = 0
    total = len(rows)

    def _progress():
        done = sum(
            1
            for r in rows
            if r.get("gold_event_ids", "").strip() != "" or r.get("question_type") == "refusal"
        )
        return done, total

    for idx, row in enumerate(rows, 1):
        row_id = row["id"]
        is_labeled = (
            row.get("gold_event_ids", "").strip() != "" or row.get("question_type") == "refusal"
        )
        if is_labeled:
            already_done += 1
            continue

        done_so_far, _ = _progress()
        remaining = total - done_so_far
        print("\n" + "=" * 60)
        print(f"[{idx}/{total}] id={row_id}  type={row['question_type']}  (剩余 {remaining} 条)")
        print(f"Q: {row['question']}")

        if row["source_event_id"]:
            ev_id = int(row["source_event_id"])
            print(f"source_event_id: {ev_id}")
            _show_event(ev_id, event_details)

        if row["question_type"] == "broad":
            candidates = broad_candidates.get(row["question"], [])
            if candidates:
                print("\n  [候选事件，请人工核对]")
                for ev in candidates:
                    print(f"  ID={ev['id']} | 类别={ev['category']} | 摘要={ev['summary'][:80]}...")
            else:
                print("\n  [未找到候选事件]")

        if row["notes"]:
            print(f"\nnotes: {row['notes']}")

        while True:
            prompt = "gold_event_ids (逗号分隔，空=拒答，.=跳过，q=保存退出): "
            user_input = input(prompt).strip()
            parsed = _parse_input(user_input)
            if parsed is None:
                print("  -> skipped")
                skipped += 1
                break
            if parsed == "QUIT":
                _save(rows)
                print(f"[label_rag_gold] Saved progress to {OUTPUT_PATH}")
                return
            # Valid list
            row["gold_event_ids"] = ",".join(str(x) for x in parsed)
            labeled += 1
            print(f"  -> labeled {row['gold_event_ids']}")
            break

    _save(rows)
    done_final, _ = _progress()
    print(f"\n[label_rag_gold] Done. Labeled {labeled} new rows, skipped {skipped}.")
    print(f"[label_rag_gold] Total labeled: {done_final}/{total}. Saved to {OUTPUT_PATH}")


def _save(all_rows: list[dict]):
    """Write all rows (including existing labels) to OUTPUT_PATH."""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["id", "question", "question_type", "gold_event_ids", "source_event_id", "notes"]
    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)


if __name__ == "__main__":
    main()
