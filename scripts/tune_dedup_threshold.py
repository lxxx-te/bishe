"""Scan dedup threshold 0.50-0.95 and compute P/R/F1 against human-labelled gold set.

Reads data/dedup_gold.csv (with label column filled by you).
For each threshold t in [0.50, 0.55, 0.60, ..., 0.95]:
  - predict same_event = (cosine_sim >= t)
  - precision = TP / (TP + FP)
  - recall = TP / (TP + FN)
  - F1 = harmonic_mean
Print table + recommend threshold where precision >= 0.9 (Q7 decision:
non-symmetric cost: false-merge worse than false-split).

Usage:
    python -m scripts.tune_dedup_threshold
"""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

IN_CSV = Path(__file__).resolve().parents[1] / "data" / "dedup_gold.csv"
THRESHOLDS = [round(0.50 + 0.05 * i, 2) for i in range(10)]  # 0.50, 0.55, ..., 0.95


def load_gold() -> list[tuple[float, bool]]:
    """Returns list of (cosine_sim, true_same_event)."""
    rows = []
    with IN_CSV.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            label = (row.get("label") or "").strip().lower()
            if label not in ("y", "n"):
                continue  # skip un-labelled rows
            sim = float(row["cosine_sim"])
            truth = label == "y"
            rows.append((sim, truth))
    return rows


def prf(preds: list[bool], truths: list[bool]) -> tuple[float, float, float]:
    tp = sum(1 for p, t in zip(preds, truths) if p and t)
    fp = sum(1 for p, t in zip(preds, truths) if p and not t)
    fn = sum(1 for p, t in zip(preds, truths) if not p and t)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def main() -> int:
    if not IN_CSV.exists():
        print(f"[tune] {IN_CSV} not found; run scripts.build_dedup_gold first")
        return 1

    rows = load_gold()
    if not rows:
        print("[tune] no labelled rows found in CSV")
        print("[tune] open the file and fill 'label' column with 'y' or 'n' per row")
        return 1

    n_pos = sum(1 for _, t in rows if t)
    n_neg = len(rows) - n_pos
    print(f"[tune] {len(rows)} labelled pairs (pos={n_pos}, neg={n_neg})")
    print()

    print(f"{'threshold':>10} {'precision':>10} {'recall':>10} {'f1':>10}")
    print("-" * 44)
    results = []
    for t in THRESHOLDS:
        preds = [sim >= t for sim, _ in rows]
        truths = [tr for _, tr in rows]
        p, r, f1 = prf(preds, truths)
        results.append((t, p, r, f1))
        marker = ""
        if p >= 0.9:
            marker = "  <- precision OK (Q7 target)"
        print(f"{t:>10.2f} {p:>10.3f} {r:>10.3f} {f1:>10.3f}{marker}")

    # Recommend threshold: highest precision >= 0.9 (Q7 非对称成本 → 选高 precision 点)
    candidates_q7 = [(t, p, r, f1) for t, p, r, f1 in results if p >= 0.9]
    print()
    if candidates_q7:
        # Among precision >= 0.9, take the one with highest recall (most balanced)
        best = max(candidates_q7, key=lambda x: x[2])
        print(f"[tune] Q7 recommendation: threshold = {best[0]:.2f}")
        print(f"         precision = {best[1]:.3f}, recall = {best[2]:.3f}, F1 = {best[3]:.3f}")
        print(f"         (highest recall among precision >= 0.9)")
    else:
        # No threshold hits 0.9 precision; default to highest precision
        best = max(results, key=lambda x: x[1])
        print(f"[tune] WARNING: no threshold hits precision >= 0.9")
        print(f"         highest precision: threshold = {best[0]:.2f}, precision = {best[1]:.3f}")
        print(f"         consider: (a) more labels, (b) re-tune keyword extractor,")
        print(f"         (c) lower target precision to 0.85, (d) accept current as optimistic")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())