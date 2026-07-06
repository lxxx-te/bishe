"""P3 keyword extraction for keyword-gate dedup (Q19 decision).

Each report needs 3 keywords extracted from its summary via LLM-constrained
extraction. Stored on news_report temporarily, then propagated to news_event
when an event is spawned. Used by dedup.py SQL gate `WHERE keywords && new`.
"""
from __future__ import annotations

import asyncio
import json
import re

from app.services.summarize import _get_client, _call_deepseek

_KW_SYS = (
    "你是一个关键词抽取器。任务：从下面给出的新闻摘要中抽取出 3 个最能代表事件主题的关键词。"
    "规则：1) 只输出 3 个关键词，逗号分隔，不要编号、不要引号、不要解释。"
    "2) 关键词应为名词或名词短语（如'火灾''新能源车''两会'），不要动词、不要形容词。"
    "3) 长度 2-8 字。4) 优先选具体实体（人名/地名/事件名）而非泛词（如'事情''情况'）。"
)

_KW_USER_TMPL = "摘要：\n{summary}\n\n3 个关键词："

_KW_RE = re.compile(r"[，,、]")


async def extract_keywords(summary: str | None) -> list[str]:
    """Extract 3 keywords from a summary. Returns [] on no summary / error."""
    if not summary or not summary.strip():
        return []
    from app.core.config import settings

    if not settings.deepseek_api_key:
        # Fallback: split on punctuation, take first 3 non-empty chunks >=2 chars
        chunks = _KW_RE.split(summary)
        return [c.strip() for c in chunks if len(c.strip()) >= 2][:3]

    client = _get_client()
    user = _KW_USER_TMPL.format(summary=summary[:1000])
    try:
        text, _, _ = await _call_deepseek(
            client, _KW_SYS, user, temperature=0.1, max_tokens=60
        )
    except Exception as e:
        print(f"[keywords] deepseek failed: {e}; fallback to split")
        chunks = _KW_RE.split(summary)
        return [c.strip() for c in chunks if len(c.strip()) >= 2][:3]

    # Parse comma-separated keywords
    kws = [k.strip() for k in re.split(r"[，,、\s]+", text) if k.strip()]
    # Filter to 3, length 2-8
    kws = [k for k in kws if 2 <= len(k) <= 8][:3]
    return kws


async def extract_keywords_batch(summaries: list[str | None]) -> list[list[str]]:
    """Batch wrapper with concurrency limit."""
    sem = asyncio.Semaphore(8)

    async def _one(s: str | None) -> list[str]:
        async with sem:
            return await extract_keywords(s)

    return await asyncio.gather(*[_one(s) for s in summaries], return_exceptions=False)