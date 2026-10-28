"""The skeptic: tries to break an explanation before the user sees it.

Two layers:
1. `run_checks()` is deterministic code. It turns the measurements into facts that settle the classic
   false alarms in cost analysis:
     - usage or price?   (volume vs mix vs rate shares)
     - one-off or trend? (purchases, spike shape, growth in earlier months)
     - credits?          (did a credit end or shrink?)
     - timing            (did the proposed cause come BEFORE the change started?)
     - concentration     (does the named segment explain most of the change?)
2. `review()` is an LLM judge that reads the hypothesis against those facts and returns a verdict per
   claim. It cannot overrule the arithmetic; it decides whether the words match it.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from . import llm

Driver = Literal["usage", "price", "mix", "one_off", "credit", "trend", "untagged", "unknown"]


def _pct(part, whole):
    return round(part / whole * 100, 1) if whole else None


def run_checks(m: dict) -> dict:
    """Deterministic facts from the measurements (see investigate.measure for the keys)."""
    split = m["split"]
    eff, total = split["effects"], split["total_change"] or 0.0
    shares = {k: _pct(v, total) for k, v in eff.items()}
    checks = {}

    # 1. usage or price
    checks["usage_or_price"] = {
        "effects_usd": eff, "effects_share_pct": shares, "largest_effect": split["largest_effect"],
        "rule": "Call it usage growth only if volume is the largest effect. Rate = same items, new unit price "
                "(e.g. a discount ended). Mix = shift to pricier items (e.g. a bigger model).",
    }
    # 2. one-off
    shape = m["change"].get("pattern")
    purchases = [i for i in m.get("purchases", {}).get("items", [])]
    checks["one_off"] = {
        "one_off_share_pct": shares.get("one_off_purchases"), "spike_shape": shape,
        "purchase_lines": purchases[:3],
        "rule": "A one-time purchase (prepayment, support plan) is expected spend, not an anomaly or usage growth.",
    }
    # 3. credits
    credits = m.get("credits", {}).get("items", [])
    checks["credits"] = {
        "credit_effect_usd": eff.get("credits"), "credit_share_pct": shares.get("credits"),
        "credit_lines_recent": credits[:3],
        "rule": "A positive credit effect means a credit shrank or ended: the bill rose with no change in usage.",
    }
    # 4. timing: events relative to when the change started
    start = m["change"].get("first_flagged_day")
    timed = []
    for ev in m.get("events", {}).get("events", []):
        rel = "unknown"
        if start:
            gap = (date.fromisoformat(ev["event_date"]) - date.fromisoformat(start)).days
            rel = "before_or_same_day" if gap <= 1 else "after"
        timed.append({**ev, "relative_to_change_start": rel})
    checks["timing"] = {
        "change_first_flagged_day": start, "shape": shape, "events": timed,
        "rule": "A cause must come before (or on) the day the change started. An event after the start cannot be "
                "the cause. Flagged days may lag the true start by a few days for gradual or weekend starts.",
    }
    # 5. concentration
    teams = m["by_team"]
    checks["concentration"] = {
        "focus": m["focus"], "total_change_usd": teams["change"],
        "focus_team_share_of_total_change_pct": next(
            (s["share_of_total_change_pct"] for s in teams["segments"] if s["segment"] == m["focus"].get("team")), None),
        "top_teams": [{k: s[k] for k in ("segment", "change", "share_of_total_change_pct")} for s in teams["segments"][:4]],
        "rule": "A segment named as THE cause must explain most (>50%) of the change; otherwise call it a contributor.",
    }
    # 6. trend: is this just the usual monthly growth?
    series = [p["value"] for p in m.get("monthly", {}).get("series", [])]
    if len(series) >= 5:
        prior = [(b - a) / a * 100 for a, b in zip(series[-5:-2], series[-4:-1]) if a]
        latest = (series[-1] - series[-2]) / series[-2] * 100 if series[-2] else None
        avg_prior = round(sum(prior) / len(prior), 1) if prior else None
        consistent = (latest is not None and avg_prior is not None and avg_prior > 1
                      and abs(latest - avg_prior) <= max(2.0, 0.6 * abs(avg_prior)))
        checks["trend"] = {"monthly_growth_prior_3_months_avg_pct": avg_prior,
                           "latest_month_change_pct": round(latest, 1) if latest is not None else None,
                           "consistent_with_existing_trend": consistent,
                           "rule": "If the change matches growth seen in earlier months, it is a trend, not an anomaly."}
    # 7. tags
    if m.get("untagged"):
        u = m["untagged"]
        checks["untagged"] = {"unallocated_cost": u["unallocated_cost"], "unallocated_share_pct": u["unallocated_share_pct"],
                              "top_unallocated": u["unallocated_by_account_service"][:3]}
    return checks


class Claim(BaseModel):
    claim: str = Field(description="One causal statement, with the amount and the date.")
    driver: Driver
    segment: str = Field(description="Where: team, service or model, e.g. 'support-ai / claude-sonnet-4-5'.")
    amount_usd: float | None = Field(default=None, description="How much of the change this claim explains.")
    cause_date: str | None = Field(default=None, description="When the cause happened (YYYY-MM-DD), if known.")
    evidence: list[str] = Field(description="Which measurements or documents support it, e.g. 'usage_price_split: "
                                            "mix +$18.7k', 'calendar 2026-03-02', 'commitments register'.")


class Hypothesis(BaseModel):
    claims: list[Claim] = Field(description="1-4 claims, most important first.")


class ClaimVerdict(BaseModel):
    claim_index: int
    verdict: Literal["supported", "weakened", "rejected"]
    reason: str = Field(description="Which check decides it, with the number.")


class SkepticReview(BaseModel):
    verdicts: list[ClaimVerdict]
    missing: list[str] = Field(default_factory=list, description="Important effects the claims ignore (e.g. credits "
                                                                   "ending explains 42% of the change).")
    decision: Literal["pass", "revise"] = Field(description="'revise' if any main claim is rejected or something "
                                                            "important is missing; otherwise 'pass'.")


SKEPTIC_SYSTEM = """You are a skeptical FinOps reviewer. Your job is to catch wrong explanations of cost changes.
Judge each claim ONLY against the computed checks. Reject a claim when:
- it calls a change "usage growth" but volume is not the largest effect (rate or mix dominate);
- it treats a one-off purchase as an anomaly or as usage;
- it names an event that happened AFTER the change started as the cause;
- it ignores credits ending when the credit effect is a large share;
- it names a segment as the main cause but that segment explains well under half of the change;
- it calls a steady trend an anomaly.
Mark 'weakened' when the claim is directionally right but overstates certainty or amount.
Be brief and quote the numbers."""


def review(question: str, hypothesis: Hypothesis, checks: dict) -> SkepticReview:
    import json

    prompt = (f"Question: {question}\n\nComputed checks (trust these numbers):\n{json.dumps(checks, default=str)[:9000]}"
              f"\n\nClaims to review:\n" + "\n".join(f"[{i}] ({c.driver}, {c.segment}, {c.amount_usd}, cause date "
                                                    f"{c.cause_date}) {c.claim}" for i, c in enumerate(hypothesis.claims)))
    return llm.structured("skeptic", prompt, SkepticReview, system=SKEPTIC_SYSTEM, max_tokens=900)
