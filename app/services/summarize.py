"""DeepSeek LLM client: adaptive summary with constrained prompt + N-gram check.

Adaptive strategy (Q decision):
- raw_text >= 300 chars: 压到 ~100 字事实摘要
- raw_text < 300 chars: 只剥离模板话术 + 编辑署名，保留核心事实 (~150 字上限)

Constrained prompt enforces:
- Only facts from the article; no external knowledge
- No consecutive >=15 char fragments copied verbatim from source (N-gram post-check)

N-gram check rejects summaries containing 15-char runs from source;
rejected summaries are regenerated once, then fallback to truncated original.

Temperature split (Q3 decision):
- summarize: temp=0.3 (slight freedom to rephrase, avoid plagiarism)
- rag_call:  temp=0.1 (faithful to context, minimize hallucination)

Source provenance (Q1 fix): each summary records whether it came from
'llm' (real DeepSeek call) or 'fallback' (local boilerplate strip when
no API key). P2 only re-processes rows where summary_source != 'llm'.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings

# ---- temperatures per task ----
SUMMARY_TEMP = 0.3   # extract facts, mild rephrase freedom
RAG_TEMP = 0.1       # faithful to context, minimize hallucination

# Constrained summary prompt - shared backbone
_SYS = (
    "你是一个事实摘要器。任务：阅读下面给出的新闻原文，用你自己的话提炼核心事实。"
    "规则：1) 只说出原文已陈述的事实，不引入任何外部知识或推测。"
    "2) 不要连续复现原文任何 >=10 个字的片段，必须用自己的表述重新组织。"
    "3) 输出 100 字以内（长稿）或 150 字以内（短稿），只包含核心事实，不含署名/编辑/版式话术。"
    "4) 只输出摘要本身，不要解释、不要前缀如'摘要：'，不要引号。"
)

_LONG_USER_TMPL = (
    "原文（{n}字，请压缩到 100 字以内的事实摘要）：\n\n{text}\n\n摘要："
)
_SHORT_USER_TMPL = (
    "原文（{n}字，较短。请剥离模板话术和编辑署名、保留所有核心事实，输出 <=150 字）：\n\n{text}\n\n摘要："
)

_SHORT_THRESHOLD = 300

_BOILERPLATE_RE = re.compile(
    r"((?:中新网|中新社|新华社|人民网)[^ ]{0,15}?\d{1,2}月\d{1,2}日电\s*(?:题[：:]\s*)?"
    r"|新华社[^ ]+电\s*|题[：:]\s*"
    r"|编辑[：:][^。\n]*|策划[：:][^。\n]*|统筹[：:][^。\n]*"
    r"|主笔[：:][^。\n]*|制作[：:][^。\n]*|来源[：:][^。\n]*|责编[：:][^。\n]*)"
)


def _build_prompt(raw_text: str) -> tuple[str, dict[str, Any]]:
    n = len(raw_text)
    if n >= _SHORT_THRESHOLD:
        return _LONG_USER_TMPL.format(text=raw_text[:8000], n=n), {"mode": "long", "n": n}
    return _SHORT_USER_TMPL.format(text=raw_text[:8000], n=n), {"mode": "short", "n": n}


def _strip_boilerplate(text: str) -> str:
    text = _BOILERPLATE_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:150]


def _detect_plagiarism(summary: str, raw_text: str, n: int = 15) -> bool:
    if not summary or not raw_text or len(raw_text) < n:
        return False
    raw_norm = re.sub(r"\s+", "", raw_text)
    summ_norm = re.sub(r"\s+", "", summary)
    if len(raw_norm) < n:
        return False
    ngrams = {raw_norm[i : i + n] for i in range(len(raw_norm) - n + 1)}
    for i in range(len(summ_norm) - n + 1):
        if summ_norm[i : i + n] in ngrams:
            return True
    return False


_client_singleton: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = AsyncOpenAI(
            api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url
        )
    return _client_singleton


async def _call_deepseek(
    client: AsyncOpenAI, system: str, user: str, temperature: float = SUMMARY_TEMP,
    max_tokens: int = 400,
) -> tuple[str, str, int]:
    """Call DeepSeek; return (text, model_version, total_tokens)."""
    resp = await client.chat.completions.create(
        model=settings.deepseek_model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=60.0,
    )
    text = (resp.choices[0].message.content or "").strip()
    model_id = resp.model or settings.deepseek_model
    usage = resp.usage
    tokens = (usage.total_tokens if usage else 0) or 0
    return text, model_id, tokens


async def generate_summary(raw_text: str | None) -> tuple[str, str, str | None, int | None]:
    """Generate an adaptive, plagiarism-checked summary.

    Returns (summary_text, source, model_version, tokens). For fallback
    rows, model_version=None and tokens=None.

    - None/empty/<50-char raw_text -> ("", "fallback", None, None)  # Q5: skip ultra-short
    - First LLM attempt fails plagiarism -> 1 retry with stricter prompt
    - Retry still plagiarized or LLM call errors -> local boilerplate strip fallback
    """
    if not raw_text or not raw_text.strip():
        return "", "fallback", None, None

    # Q5 fix: ultra-short raw_text (<50 chars, basically a headline) has no
    # content to summarize. Skip LLM, go straight to boilerplate strip.
    ULTRA_SHORT = 50
    if len(raw_text.strip()) < ULTRA_SHORT:
        return _strip_boilerplate(raw_text), "fallback", None, None

    if not settings.deepseek_api_key:
        return _strip_boilerplate(raw_text) if len(raw_text) < _SHORT_THRESHOLD else raw_text[:150], "fallback", None, None

    client = _get_client()
    user_prompt, meta = _build_prompt(raw_text)

    try:
        summary, model_id, tokens = await _call_deepseek(client, _SYS, user_prompt, temperature=SUMMARY_TEMP)
    except Exception as e:
        print(f"[summary] deepseek failed: {e}; fallback to local strip")
        return _strip_boilerplate(raw_text) if meta["mode"] == "short" else raw_text[:150], "fallback", None, None

    if _detect_plagiarism(summary, raw_text, n=20):  # Q4 fix: 15 -> 20
        retry_sys = (
            _SYS
            + "\n\n注意：你上一次的输出包含与原文连续 >=20 字的相同片段，"
            "这违反规则。请重新组织语言，确保不与原文任何 20 字连续片段相同。"
        )
        try:
            summary, model_id, tokens = await _call_deepseek(client, retry_sys, user_prompt, temperature=SUMMARY_TEMP)
        except Exception as e:
            print(f"[summary] deepseek retry failed: {e}")

        if _detect_plagiarism(summary, raw_text, n=20):
            print(f"[summary] plagiarism persists after retry (mode={meta['mode']}, n={meta['n']}); fallback")
            return _strip_boilerplate(raw_text) if meta["mode"] == "short" else raw_text[:150], "fallback", None, None

    return summary, "llm", model_id, tokens


async def summarize_batch(raw_texts: list[str | None]) -> list[tuple[str, str, str | None, int | None]]:
    """Summarize a batch concurrently. Returns list of (summary, source, model, tokens)."""
    sem = asyncio.Semaphore(8)

    async def _one(t: str | None) -> tuple[str, str, str | None, int | None]:
        async with sem:
            return await generate_summary(t)

    return await asyncio.gather(*[_one(t) for t in raw_texts], return_exceptions=False)


async def rag_call(system: str, user: str, max_tokens: int = 800) -> tuple[str, str, int]:
    """RAG answer generation call. temperature=0.1 faithful to context.
    Returns (text, model_version, total_tokens)."""
    client = _get_client()
    return await _call_deepseek(
        client, system, user, temperature=RAG_TEMP, max_tokens=max_tokens
    )