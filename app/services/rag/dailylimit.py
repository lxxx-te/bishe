"""Daily RAG query limit (Q10 cost control: 200/day).

Simple file-based counter (no Redis in dev). File: data/rag_daily_counter.json
key format: 'YYYY-MM-DD' -> int count.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app.core.config import settings

COUNTER_PATH = Path(settings.root) / "data" / "rag_daily_counter.json"


def _load() -> dict:
    if not COUNTER_PATH.exists():
        return {}
    try:
        return json.loads(COUNTER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    COUNTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    COUNTER_PATH.write_text(json.dumps(data), encoding="utf-8")


def get_today_count() -> int:
    key = date.today().isoformat()
    data = _load()
    # Reset stale keys (keep only today)
    today_data = {key: data.get(key, 0)}
    _save(today_data)
    return today_data[key]


def today_limit_reached() -> bool:
    return get_today_count() >= settings.deepseek_rag_daily_limit


def increment_today(n: int = 1) -> int:
    key = date.today().isoformat()
    data = _load()
    data[key] = data.get(key, 0) + n
    _save(data)
    return data[key]