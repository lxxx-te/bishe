"""P4-1: 5W1H fact-slot extraction + category derivation (single LLM call per report).

Per Q1(a) decision: LLM is allowed to return "N/A" for slots not mentioned in
the article (instead of fabricating). P4 merge treats "N/A" as null/skip.

Per Q2(a) decision: category is derived in the same LLM call from keywords +
5W1H, mapped to a fixed vocabulary {政治,经济,文化,社会,科技,国际,体育,其他}.

Per Q3(a) decision: relative time words ("今日","昨日") are resolved against
the report's publish_time at day precision (no strict tz handling).

Per Q5(a) decision: slots are persisted as a single INSERT batch with N/A,
no per-row cascade needed downstream.
"""
from __future__ import annotations

import asyncio
import re

from app.services.summarize import _get_client, _call_deepseek

# Fixed category vocabulary (P5 feed filtering uses these as interest tags)
CATEGORIES = {"政治", "经济", "文化", "社会", "科技", "国际", "体育", "其他"}

_SYS = (
    "你是一个事实槽位抽取器。任务：从下面给出的新闻摘要中抽取 5W1H 六个事实槽位 + 事件类别。"
    "严格规则：\n"
    "1) 每个槽位只能填摘要中明确陈述的事实。如果摘要没提到该槽位，必须填 'N/A'。"
    "   绝不允许编造、推测、补全。N/A 是合法值，不是错误。\n"
    "2) 'when' 槽位：若摘要里出现'今日''昨日''本月X日''上周X'等相对时间词，"
    "请按下述 publish_time 反算成绝对日期 YYYY-MM-DD。\n"
    "3) 槽位值不超过 60 字，过长请提炼核心。\n"
    "4) 输出严格 JSON 格式，不要解释、不要前缀后缀。字段：who, what, when, where, why, howmany, category。\n"
    "5) category 从这 8 类选一个：政治/经济/文化/社会/科技/国际/体育/其他。"
)

_USER_TMPL = (
    "publish_time: {pub}\n"
    "摘要：\n{summary}\n\n"
    "输出 JSON（字段 who/what/when/where/why/howmany/category）："
)

_RELATIVE_DAYS = {
    "今日": 0, "今天": 0, "昨日": -1, "昨天": -1, "前日": -2, "前天": -2,
}


def _resolve_when(value: str, publish_time) -> str:
    """Resolve relative day words in 'when' slot against publish_time.
    Returns absolute YYYY-MM-DD string, or original value if unresolvable.
    """
    if not value or value.strip() == "N/A":
        return value
    v = value.strip()
    for word, delta in _RELATIVE_DAYS.items():
        if word in v:
            if publish_time:
                try:
                    from datetime import timedelta
                    new_date = publish_time + timedelta(days=delta)
                    rest = v.replace(word, "").strip(" ，,的日")
                    return new_date.strftime("%Y-%m-%d") + (f" {rest}" if rest else "")
                except Exception:
                    return v
            return v
    # Try YYYY-MM-DD/月-日 patterns: keep as-is
    return v


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
        import json
        return json.loads(t)
    except Exception:
        pass
    # Fallback: regex extract { ... } blob
    m = re.search(r"\{[^{}]*\}", t, re.S)
    if not m:
        return None
    try:
        import json
        return json.loads(m.group(0))
    except Exception:
        return None


async def extract_facts(
    summary: str | None, publish_time, keywords: list[str] | None = None
) -> dict | None:
    """Extract 5W1H + category from one report's summary.

    Returns dict {who, what, when, where, why, howmany, category} or None
    on hard failure. 'when' is resolved to absolute date.
    """
    if not summary or not summary.strip():
        return None
    from app.core.config import settings

    if not settings.deepseek_api_key:
        # Fallback: empty slots all N/A, category "其他"
        return {
            "who": "N/A", "what": "N/A", "when": "N/A",
            "where": "N/A", "why": "N/A", "howmany": "N/A",
            "category": "其他",
        }

    client = _get_client()
    pub_str = publish_time.strftime("%Y-%m-%d") if publish_time else "未知"
    # Include keywords hint for better category (Q2 derivation)
    kw_hint = f" (keywords 暗示: {'/'.join(keywords[:3])})" if keywords else ""
    user = _USER_TMPL.format(pub=pub_str + kw_hint, summary=summary[:1500])

    try:
        text, _, _ = await _call_deepseek(
            client, _SYS, user, temperature=0.1, max_tokens=400
        )
    except Exception as e:
        print(f"[facts] deepseek failed: {e}")
        return None

    parsed = _parse_json_response(text)
    if not parsed:
        print(f"[facts] json parse failed: {text[:200]}")
        return None

    slots = {}
    for k in ("who", "what", "when", "where", "why", "howmany", "category"):
        v = (parsed.get(k) or "N/A").strip()
        if v == "":
            v = "N/A"
        slots[k] = v

    # Resolve when (relative -> absolute)
    slots["when"] = _resolve_when(slots["when"], publish_time)

    # Normalize category
    cat = slots["category"]
    if cat not in CATEGORIES:
        # Map common variants
        cat_map = {"财经": "经济", "教育": "社会", "健康": "社会", "娱乐": "文化",
                   "体育": "体育", "国际": "国际", "国内": "政治", "时政": "政治"}
        slots["category"] = cat_map.get(cat, "其他")
    return slots


async def extract_facts_batch(
    summaries: list[str | None],
    publish_times: list,
    keywords_list: list[list[str] | None] | None = None,
) -> list[dict | None]:
    """Batch wrapper with concurrency limit."""
    sem = asyncio.Semaphore(8)
    kws = keywords_list or [None] * len(summaries)

    async def _one(s, pt, kw):
        async with sem:
            return await extract_facts(s, pt, kw)

    return await asyncio.gather(
        *[_one(s, pt, kw) for s, pt, kw in zip(summaries, publish_times, kws)]
    )