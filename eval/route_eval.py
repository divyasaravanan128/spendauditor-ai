"""Routing eval: a frozen set of 20 questions with the correct route.

When a route is wrong, fix the router, never the test set.
Run:  python eval/route_eval.py --repeat 3
Reports accuracy, per-route recall, misroutes with the router's own reason, and unstable questions
(different routes across repeats). Costs about $0.02 for 3 repeats with Haiku.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spendauditor import router  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()
    cases = json.loads((ROOT / "eval" / "route_questions.json").read_text())
    results = defaultdict(list)
    for _ in range(args.repeat):
        for c in cases:
            d = router.route(c["q"], data_range="2025-07-01 to 2026-09-30")
            results[c["q"]].append((d.route, d.reason))
    total = correct = 0
    recall = defaultdict(lambda: [0, 0])
    misroutes, unstable = [], []
    for c in cases:
        routes = [r for r, _ in results[c["q"]]]
        for r, reason in results[c["q"]]:
            total += 1
            correct += r == c["route"]
            recall[c["route"]][0] += r == c["route"]
            recall[c["route"]][1] += 1
            if r != c["route"]:
                misroutes.append({"q": c["q"], "expected": c["route"], "got": r, "reason": reason})
        if len(set(routes)) > 1:
            unstable.append({"q": c["q"], "routes": dict(Counter(routes))})
    print(f"\nAccuracy: {correct}/{total} = {correct / total:.0%}")
    for route, (ok, n) in sorted(recall.items()):
        print(f"  {route:<12} recall {ok}/{n}")
    print(f"\nMisroutes ({len(misroutes)}):")
    for m in misroutes:
        print(f"  [{m['expected']} -> {m['got']}] {m['q']}  ({m['reason']})")
    print(f"\nUnstable questions: {len(unstable)}")
    for u in unstable:
        print(f"  {u['q']}: {u['routes']}")
    out = ROOT / "eval" / "results"
    out.mkdir(exist_ok=True)
    (out / f"route_{datetime.now():%Y%m%d_%H%M}.json").write_text(json.dumps(
        {"accuracy": correct / total, "misroutes": misroutes, "unstable": unstable}, indent=2))


if __name__ == "__main__":
    main()
