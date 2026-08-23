"""P4-1: event category derivation (single LLM call per report).

Per Q1(a)+Q2(a) decisions: LLM derives the category from the report summary +
keywords, mapped to a fixed vocabulary {政治,经济,文化,社会,科技,国际,体育,其他}.
The 5W1H fact-slot extraction and 4-grade conflict grading were removed
(grilling 2026-08: real output was dominated by false conflicts and verbose
slot values; category is kept because the event stream filter depends on it).
"""
from __future__ import annotations

import asyncio
import json
import re

from app.services.summarize import _get_client, _call_deepseek

# Fixed category vocabulary (P5 feed filtering uses these as interest tags)
CATEGORIES = {"政治", "经济", "文化", "社会", "科技", "国际", "体育", "其他"}

_SYS = (
    "你是一个新闻分类器。任务：从下面给出的新闻摘要中判断事件类别。"
    "严格规则：\n"
    "1) 只能从这 8 类中选一个：政治/经济/文化/社会/科技/国际/体育/其他。\n"
    "2) 类别由摘要内容决定，不要被标题党带偏。\n"
    "3) 输出严格 JSON 格式：{\"category\": \"某类\"}。不要解释、不要前缀后缀。"
)

_USER_TMPL = (
    "摘要：\n{summary}\n\n"
    "输出 JSON（字段 category）："
)


def _parse_json_response(text: str) -> dict | None:
    """Parse LLM JSON response. Tolerant: strip code fences, trailing commas."""
    if not text:
        return None
    t = text.strip()
    # Strip ```json ... ``` fences
    if t.startswith("```"):
        lines = t.split("\n")
        t = "\n".join(l for l in lines if not l.startswith("```"))
    # Try strict json first
    try:
        return json.loads(t)
    except Exception:
        pass
    # Fallback: regex extract { ... } blob
    m = re.search(r"\{[^{}]*\}", t, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


async def extract_category(
    summary: str | None, keywords: list[str] | None = None
) -> str | None:
    """Derive one event category from a report's summary.

    Returns one of CATEGORIES, or None on hard failure.
    """
    if not summary or not summary.strip():
        return None
    from app.core.config import settings

    if not settings.deepseek_api_key:
        return "其他"

    client = _get_client()
    # Include keywords hint for better category (Q1(a) derivation)
    kw_hint = f" (keywords 暗示: {'/'.join(keywords[:3])})" if keywords else ""
    user = _USER_TMPL.format(summary=summary[:1500] + kw_hint)

    try:
        text, _, _ = await _call_deepseek(
            client, _SYS, user, temperature=0.1, max_tokens=100
        )
    except Exception as e:
        print(f"[facts] deepseek failed: {e}")
        return None

    parsed = _parse_json_response(text)
    if not parsed:
        print(f"[facts] json parse failed: {text[:200]}")
        return None

    cat = (parsed.get("category") or "其他").strip()
    if cat in CATEGORIES:
        return cat
    # Map common variants
    cat_map = {"财经": "经济", "教育": "社会", "健康": "社会", "娱乐": "文化",
               "体育": "体育", "国际": "国际", "国内": "政治", "时政": "政治"}
    return cat_map.get(cat, "其他")


async def extract_category_batch(
    summaries: list[str | None],
    keywords_list: list[list[str] | None] | None = None,
) -> list[str | None]:
    """Batch wrapper with concurrency limit."""
    sem = asyncio.Semaphore(8)
    kws = keywords_list or [None] * len(summaries)

    async def _one(s, kw):
        async with sem:
            return await extract_category(s, kw)

    return await asyncio.gather(
        *[_one(s, kw) for s, kw in zip(summaries, kws)]
    )
