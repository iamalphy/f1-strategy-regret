#!/usr/bin/env python3
"""
Credibility gate.

The design says no ranking is published unless the replay explains the real
finishing order. This script enforces that: it reads the validation outputs and
exits non-zero if the model is not good enough to stand behind.

Thresholds are deliberately modest for a single-driver replay - the point is to
catch a broken model, not to claim predictive power.
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROC = ROOT / "data" / "processed"

MIN_RACES = 16          # 2024 has 24 rounds; wet races are legitimately skipped
MIN_SPEARMAN = 0.70     # rank agreement with the actual finishing order
MIN_WITHIN_2 = 0.55     # share of drivers placed within two positions
MAX_RMSE = 2.5          # seconds; a pace model above this is not usable


def main() -> int:
    summary_path = PROC / "validation.json"
    if not summary_path.exists():
        raise SystemExit("validation.json missing - run pipeline/model.py first")
    s = json.loads(summary_path.read_text(encoding="utf-8"))

    checks = [
        ("races modelled", s["races_modelled"], MIN_RACES, ">="),
        ("mean Spearman", s["mean_spearman"], MIN_SPEARMAN, ">="),
        ("mean within 2 positions", s["mean_within_2_positions"], MIN_WITHIN_2, ">="),
        ("mean RMSE (s)", s["mean_rmse_s"], MAX_RMSE, "<="),
    ]

    ok = True
    print("=== credibility gate ===")
    for label, value, threshold, op in checks:
        passed = value >= threshold if op == ">=" else value <= threshold
        ok &= bool(passed)
        print(f"  {'PASS' if passed else 'FAIL'}  {label:26s} {value:.3f} "
              f"(threshold {op} {threshold})")

    if not math.isfinite(s["total_regret_points"]):
        ok = False
        print("  FAIL  total regret is not finite")

    skipped = s.get("skipped", [])
    print(f"\n  skipped: {len(skipped)} races")
    for row in skipped:
        print(f"    R{row.get('round', '?'):>2} {row.get('location', '?'):<20} {row['reason']}")

    if not ok:
        print("\nModel is not credible enough to publish a ranking.")
        return 1
    print("\nModel passes the credibility gate; results may be published.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
