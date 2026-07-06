"""P4-2: event-level fact-slot merge + 4-grade conflict classification.

Per Q1(a): N/A values are SKIPPED in merge (do not pollute).
Per Q12 + Q6(a) decision: time-word window is 3 days (not 24h). Values within
a 3-day window (when slots) are marked 'uncertain' (grey, not red). Beyond 3
days or non-time slots with divergence -> 'conflict' (red).

4 grades (per CONTEXT.md Conflict Grade):
  consistent: all non-N/A values identical
  merged:     semantically mergeable (subset/superset)
  uncertain:  when-slot divergence within 3-day window
  conflict:   significant divergence -> red, keep all candidates
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

WINDOW_DAYS = 3  # Q6(a): 3-day window for time-slot uncertainty

_SLOT_KEYS = ["who", "what", "when", "where", "why", "howmany"]


def _is_na(v: str) -> bool:
    return not v or v.strip().lower() in ("n/a", "na", "null", "")


def _normalize(v: str) -> str:
    return re.sub(r"\s+", "", v).lower().strip()


def _try_parse_date(s: str):
    """Try to parse YYYY-MM-DD or similar. Returns datetime or None."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y年%m月%d日", "%Y/%m/%d", "%m月%d日"):
        try:
            d = datetime.strptime(s, fmt)
            # If year missing (e.g. %m月%d日), assume 2026 (current year)
            if fmt == "%m月%d日":
                d = d.replace(year=2026)
            return d
        except ValueError:
            continue
    # Try first YYYY-MM-DD substring
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def _grade_when(values: list[str]) -> tuple[str, list[str]]:
    """Grade 'when' slot separately due to time-window rule.
    Returns (status, output_values)."""
    non_na = [v for v in values if not _is_na(v)]
    if not non_na:
        return "consistent", []  # all N/A, treat as consistent-empty
    if len(non_na) == 1:
        return "consistent", non_na

    # Try parse all as dates
    dates: list[datetime | None] = []
    for v in non_na:
        d = _try_parse_date(v)
        dates.append(d)

    # If all parse and within 3 days -> uncertain (Q6 window)
    parsed = [d for d in dates if d is not None]
    if len(parsed) == len(non_na):
        mn, mx = min(parsed), max(parsed)
        if (mx - mn).days <= WINDOW_DAYS:
            # Uncertain (grey); pick earliest as candidate but keep all
            return "uncertain", non_na
        else:
            return "conflict", non_na

    # Mixed date/string OR all string: check normalize equality
    norm_set = {_normalize(v) for v in non_na}
    if len(norm_set) == 1:
        return "consistent", [non_na[0]]
    # Try subset/superset (merged): e.g. "7月4日" and "7月4日上午"
    if all("7月" in v or "202" in v for v in non_na):
        # Time-like but unparseable; check coarse-grain match on YYYY-MM-DD
        coarse = []
        for v in non_na:
            m = re.search(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}月\d{1,2}日", v)
            coarse.append(m.group(0) if m else v)
        if len({_normalize(c) for c in coarse}) == 1:
            return "merged", [coarse[0]]
    return "conflict", non_na


def _grade_generic(values: list[str]) -> tuple[str, list[str]]:
    """Grade non-when slot.
    Returns (status, output_values)."""
    non_na = [v for v in values if not _is_na(v)]
    if not non_na:
        return "consistent", []
    if len(non_na) == 1:
        return "consistent", non_na

    norm_set = {_normalize(v) for v in non_na}
    if len(norm_set) == 1:
        return "consistent", [non_na[0]]

    # Try subset/superset (merged): one contains another
    # If one normalized value is substring of all others, accept coarse one
    sorted_vals = sorted(non_na, key=len)
    smallest = _normalize(sorted_vals[0])
    if all(smallest and smallest in _normalize(v) for v in non_na):
        return "merged", [sorted_vals[0]]

    # All same length but small diff (e.g. 12 vs 15 numbers) -> conflict
    return "conflict", non_na


def merge_event_facts(reports_facts: list[dict]) -> tuple[dict, dict]:
    """Merge per-report fact dicts into event-level fact_slots + conflict_flags.

    Args:
        reports_facts: list of dicts each with keys who/what/when/where/why/howmany
                       (may include category, ignored here)

    Returns:
        (fact_slots, conflict_flags):
            fact_slots: dict {slot_key: chosen_value or "N/A"}
            conflict_flags: dict {slot_key: {status, values, note}}
    """
    fact_slots: dict[str, str] = {}
    conflict_flags: dict[str, dict] = {}

    for slot in _SLOT_KEYS:
        values = []
        for rf in reports_facts:
            if rf is None:
                continue
            v = rf.get(slot, "N/A")
            values.append(v)

        if slot == "when":
            status, out_vals = _grade_when(values)
        else:
            status, out_vals = _grade_generic(values)

        # Q2(a) fix: P5 RAG reads event.fact_slots only (doesn't separately
        # query conflict_flags). To expose conflict candidates to RAG prompt,
        # store merged candidate string in fact_slots (e.g. "12 / 15 / 12").
        # consistent / merged -> single chosen value
        # uncertain / conflict -> "v1 / v2 / ..." (P5 LLM sees all candidates
        #   and the P7 frontend renders in red/grey accordingly)
        if status in ("consistent", "merged"):
            fact_slots[slot] = out_vals[0] if out_vals else "N/A"
        else:  # uncertain / conflict
            fact_slots[slot] = " / ".join(out_vals) if out_vals else "N/A"
            conflict_flags[slot] = {
                "status": status,
                "values": out_vals,
                "note": "不确定事件时间/数值" if status == "uncertain" else "多家报道不一致",
            }

    return fact_slots, conflict_flags