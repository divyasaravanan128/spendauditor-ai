"""Investigation eval: does the analyst find the TRUE cause of each planted event?

Because the history is synthetic with planted causes (data/answer_key.json), every scenario has a known
answer. Each scenario is scored on:
- driver:  the report's primary_driver is one of the accepted drivers (usage / price / mix / one_off / credit / trend)
- segment: the report's main segment names the right team, service or model
- decoy:   a decoy event that happened after the change is not stated as a proven cause
- skeptic_caught: the skeptic sent a draft back at least once (how often it intervenes)
Run:  python eval/investigation_eval.py            (about $0.10-0.30 with Haiku, web search off)
      python eval/investigation_eval.py --only E1 E7
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spendauditor import llm  # noqa: E402
from spendauditor.investigate import investigate  # noqa: E402


def score(case: dict, state: dict) -> dict:
    r = state.get("report") or {}
    seg_text = f"{r.get('primary_segment', '')} {r.get('headline', '')}".lower()
    s = {
        "id": case["id"],
        "driver_ok": r.get("primary_driver") in case["expected_driver"],
        "segment_ok": any(x.lower() in seg_text for x in case["expected_segment_any"]),
        "skeptic_revised": state.get("revisions", 0) > 0,
        "got_driver": r.get("primary_driver"), "got_segment": r.get("primary_segment"),
        "headline": r.get("headline"),
    }
    if case.get("decoy_must_not_be_proven"):
        s["decoy_ok"] = not any(case["decoy_must_not_be_proven"] in p.lower() and "not" not in p.lower()
                                for p in r.get("proven", []))
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    cases = json.loads((ROOT / "eval" / "scenarios.json").read_text())
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    rows = []
    for c in cases:
        qid = llm.new_question()
        t0 = time.time()
        try:
            state = investigate(c["question"])
            s = score(c, state)
        except Exception as e:  # noqa: BLE001
            s = {"id": c["id"], "driver_ok": False, "segment_ok": False, "error": str(e)}
        s["seconds"] = round(time.time() - t0, 1)
        s["cost_usd"] = llm.question_cost(qid)["total_usd"]
        rows.append(s)
        print(f"{s['id']}: driver {'✓' if s['driver_ok'] else '✗'} ({s.get('got_driver')}), "
              f"segment {'✓' if s['segment_ok'] else '✗'} ({s.get('got_segment')}), "
              f"decoy {s.get('decoy_ok', '-')}, revised {s.get('skeptic_revised')}, ${s['cost_usd']:.4f}, {s['seconds']}s")
    n = len(rows)
    summary = {
        "scenarios": n,
        "driver_accuracy": sum(r["driver_ok"] for r in rows) / n,
        "segment_accuracy": sum(r["segment_ok"] for r in rows) / n,
        "both_correct": sum(r["driver_ok"] and r["segment_ok"] for r in rows) / n,
        "skeptic_revision_rate": sum(bool(r.get("skeptic_revised")) for r in rows) / n,
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
    }
    print("\n" + json.dumps(summary, indent=2))
    out = ROOT / "eval" / "results"
    out.mkdir(exist_ok=True)
    (out / f"investigation_{datetime.now():%Y%m%d_%H%M}.json").write_text(json.dumps({"summary": summary, "rows": rows},
                                                                                     indent=2, default=str))


if __name__ == "__main__":
    main()
