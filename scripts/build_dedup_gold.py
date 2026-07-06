"""Build 100-pair dedup gold tuning set via nearest-neighbor sampling (Q18 decision).

Pipeline (per Q18 decision):
1. Pick all reports with non-null embedding (excludes empty-raw_text rows)
2. Randomly sample up to N=200 reports
3. For each, compute cosine Top-3 nearest neighbors (excluding itself)
4. Form (a, b) pairs, dedupe (a,b) === (b,a)
5. Randomly select 100 pairs
6. Write to data/dedup_gold.csv with columns: idx, summary_a, summary_b, label
   (label is empty - YOU fill it with 'y' or 'n' for same_event)

Then you run:
    python -m scripts.tune_dedup_threshold   # reads scored CSV, scans threshold

Usage:
    python -m scripts.build_dedup_gold
    # edit data/dedup_gold.csv -> fill label column
    python -m scripts.tune_dedup_threshold
"""
from __future__ import annotations

import csv
import math
import os
import random
from pathlib import Path

import psycopg

DB_DSN = "postgresql://news:changeme@localhost:5432/news_aggregator"
OUT_CSV = Path(__file__).resolve().parents[1] / "data" / "dedup_gold.csv"
SEED = 42
N_SAMPLE = 200  # 抽样种子报道数
TOP_K = 3       # 每篇取 Top-3 近邻
N_PAIRS = 100   # 最终标注集大小


def fetch_reports() -> list[dict]:
    conn = psycopg.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, summary FROM news_report "
        "WHERE embedding IS NOT NULL AND summary IS NOT NULL AND summary_source='llm'"
        " ORDER BY id"
    )
    rows = [{"id": r[0], "summary": r[1]} for r in cur.fetchall()]
    conn.close()
    return rows


def _parse_vec(emb) -> list[float] | None:
    """pgvector via psycopg may return string '[0.1,0.2,...]' or ndarray/list."""
    if emb is None:
        return None
    if isinstance(emb, (list, tuple)):
        return [float(x) for x in emb]
    s = str(emb).strip()
    if s.startswith("["):
        s = s[1:]
    if s.endswith("]"):
        s = s[:-1]
    parts = [p.strip() for p in s.split(",") if p.strip()]
    try:
        return [float(p) for p in parts]
    except ValueError:
        return None


def fetch_embeddings(ids: list[int]) -> dict[int, list[float]]:
    conn = psycopg.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, embedding FROM news_report WHERE id = ANY(%s)",
        (ids,),
    )
    out = {}
    for rid, emb in cur.fetchall():
        v = _parse_vec(emb)
        if v is not None:
            out[rid] = v
    conn.close()
    return out


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def build_pairs(reports: list[dict], embeddings: dict[int, list[float]]) -> list[tuple]:
    """For each sampled report, find Top-K相似的 and form pairs."""
    rng = random.Random(SEED)
    sample = rng.sample(reports, min(N_SAMPLE, len(reports)))
    sample_ids = [r["id"] for r in sample]
    sample_embs = {r["id"]: embeddings[r["id"]] for r in sample if r["id"] in embeddings}

    # Pool of all candidates to compare against (整个库里所有有 emb 的)
    all_ids = list(embeddings.keys())

    pairs_set: set[tuple[int, int]] = set()
    pairs_list: list[tuple[int, int, float, str, str]] = []

    for r in sample:
        rid = r["id"]
        if rid not in sample_embs:
            continue
        a_emb = sample_embs[rid]
        # Compute cosine vs all candidates (excluding self)
        sims = []
        for oid in all_ids:
            if oid == rid or oid not in embeddings:
                continue
            sim = cosine(a_emb, embeddings[oid])
            sims.append((oid, sim))
        # Top-K
        sims.sort(key=lambda x: -x[1])
        for oid, sim in sims[:TOP_K]:
            pair_key = (min(rid, oid), max(rid, oid))
            if pair_key in pairs_set:
                continue
            pairs_set.add(pair_key)
            # Look up summaries
            sa = next((x["summary"] for x in reports if x["id"] == pair_key[0]), "")
            sb = next((x["summary"] for x in reports if x["id"] == pair_key[1]), "")
            pairs_list.append((pair_key[0], pair_key[1], round(sim, 4), sa, sb))

    return pairs_list


def main() -> int:
    print(f"[gold] out csv: {OUT_CSV}")
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    reports = fetch_reports()
    print(f"[gold] {len(reports)} LLM-summaries available")

    if len(reports) < 10:
        print("[gold] ERROR: <10 reports, can't build meaningful pairs")
        return 1

    all_ids = [r["id"] for r in reports]
    embeddings = fetch_embeddings(all_ids)
    print(f"[gold] {len(embeddings)} embeddings loaded")

    pairs = build_pairs(reports, embeddings)
    print(f"[gold] {len(pairs)} candidate pairs (近邻采样)")

    if len(pairs) < N_PAIRS:
        print(f"[gold] WARNING: only {len(pairs)} pairs available, taking all")

    # Randomly select N_PAIRS
    rng = random.Random(SEED)
    sampled = rng.sample(pairs, min(N_PAIRS, len(pairs)))

    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["idx", "report_id_a", "report_id_b", "cosine_sim", "summary_a", "summary_b", "label"])
        for i, (ra, rb, sim, sa, sb) in enumerate(sampled, start=1):
            # Truncate summaries for human labelling readability (full text not needed)
            sa_short = sa[:200]
            sb_short = sb[:200]
            w.writerow([i, ra, rb, sim, sa_short, sb_short, ""])  # label empty for you

    print(f"[gold] wrote {len(sampled)} pairs to {OUT_CSV}")
    print()
    print("[gold] NEXT STEPS:")
    print("  1. Open data/dedup_gold.csv in Excel/Numbers or any editor")
    print("  2. For each row, read summary_a and summary_b")
    print("  3. Fill label column with 'y' if SAME EVENT, 'n' if different")
    print("  4. Save the CSV")
    print("  5. Run: python -m scripts.tune_dedup_threshold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())