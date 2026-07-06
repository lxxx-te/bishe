"""Interactive CLI to label dedup_gold.csv.

For each row, display summary_a and summary_b with cosine_sim, let you press:
  y     -> same event
  n     -> different event
  s     -> skip (leave empty, come back later)
  q     -> save and quit
  b     -> go back one row

Run in terminal:
    python -m scripts.label_dedup_gold
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "dedup_gold.csv"


def load() -> list[dict]:
    with CSV_PATH.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save(rows: list[dict]) -> None:
    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["idx", "report_id_a", "report_id_b", "cosine_sim",
                        "summary_a", "summary_b", "label"],
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"[label] saved to {CSV_PATH}")


def main() -> int:
    rows = load()
    if not rows:
        print(f"[label] {CSV_PATH} empty or missing")
        return 1

    print(f"[label] {len(rows)} pairs to label")
    print(f"[label] keys: y=same, n=diff, s=skip, b=back, q=save+quit")
    print()

    i = 0
    while i < len(rows):
        r = rows[i]
        existing = (r.get("label") or "").strip()
        marker = f" (currently: {existing})" if existing else ""
        print(f"\n--- #{r['idx']} / {len(rows)}  sim={r['cosine_sim']}{marker} ---")
        print(f"A (id={r['report_id_a']}):")
        print(f"  {r['summary_a']}")
        print(f"B (id={r['report_id_b']}):")
        print(f"  {r['summary_b']}")
        print()
        try:
            key = input("y/n/s/b/q > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            save(rows)
            return 0

        if key == "y":
            r["label"] = "y"
            i += 1
        elif key == "n":
            r["label"] = "n"
            i += 1
        elif key == "s":
            i += 1
        elif key == "b":
            i = max(0, i - 1)
        elif key == "q":
            save(rows)
            print(f"[label] labelled {sum(1 for r in rows if r.get('label') in ('y','n'))} / {len(rows)}")
            return 0
        else:
            print("unknown key; y/n/s/b/q")

    save(rows)
    n_labelled = sum(1 for r in rows if r.get("label") in ("y", "n"))
    print(f"\n[label] done: {n_labelled} / {len(rows)} labelled")
    if n_labelled < len(rows):
        print("[label] rerun to label the rest")
    else:
        print("[label] next: python -m scripts.tune_dedup_threshold")
    return 0


if __name__ == "__main__":
    sys.exit(main())