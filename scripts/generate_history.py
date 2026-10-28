"""Generate 15 months of synthetic company billing history in FOCUS 1.3 shape, with planted events.

Why synthetic: no public dataset has multi-month bills with AI token costs (see README).
Why planted events: because we cause every change on purpose, we know the true answer to every
"why did it change?" question. data/answer_key.json records them, and the evals score the agents
against it.

What is borrowed from reality:
- Account names, regions and the tag style come from the FinOps Foundation's real FOCUS sample.
- AI model prices come from the LiteLLM price file (real list prices).
- Cloud unit prices are typical public on-demand prices, rounded (illustrative, not quoted).

Output: data/raw/company_charges.csv, data/answer_key.json, data/raw/budgets.csv,
data/raw/events.csv, and the budget table inside data/policies/budget_policy.md.

Run:  python scripts/generate_history.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spendauditor.config import ANSWER_KEY, POLICIES, PRICES_FILE, RAW  # noqa: E402

START, END = date(2025, 7, 1), date(2026, 9, 30)
rng = np.random.default_rng(42)  # fixed seed: the same data every run

# ---------------------------------------------------------------- teams (accounts from the real sample)
TEAMS = {
    "payments": {"account": "Orion Zenith", "app": "PayCore", "env": "prod"},
    "search": {"account": "Atlas Orion", "app": "FindIt", "env": "prod"},
    "data-platform": {"account": "Orion Odyssey", "app": "Lakehouse", "env": "prod"},
    "support-ai": {"account": "Orion Pioneer", "app": "HelpBot", "env": "prod"},
    "marketing": {"account": "Odyssey Horizon", "app": "CampaignGen", "env": "prod"},
    "ml-research": {"account": "Pioneer Nimbus", "app": "Experiments", "env": "dev"},
}
BILLING_ACCOUNT = "Nimbus Holdings"


# ---------------------------------------------------------------- prices
def model_prices() -> dict[str, tuple[float, float]]:
    """(input, output) USD per 1M tokens, from the real LiteLLM price file."""
    raw = json.loads(PRICES_FILE.read_text())
    out = {}
    for m in ["claude-haiku-4-5", "claude-sonnet-4-5", "gpt-5-mini"]:
        out[m] = (raw[m]["input_cost_per_token"] * 1e6, raw[m]["output_cost_per_token"] * 1e6)
    return out


@dataclass
class Line:
    """One recurring daily usage line: a team using one service/SKU/model."""

    team: str
    provider: str
    service: str
    category: str
    unit: str
    pricing_unit: str
    price: float  # list price per pricing unit
    qty: float  # base consumed quantity per day (in `unit`)
    region: str = "us-east-1"
    sku: str = ""
    model: str | None = None
    token_type: str | None = None
    growth: float = 0.0  # monthly growth rate
    weekday_only: bool = False  # AI traffic drops at weekends
    noise: float = 0.08
    tag_style: str = "clean"  # clean | env_alias | spaced_key | partly_missing
    extra: dict = field(default_factory=dict)

    @property
    def units_per_pricing_unit(self) -> float:
        return 1_000_000 if self.pricing_unit == "1M Tokens" else 1.0


def build_lines(mp: dict) -> list[Line]:
    L = []
    gb_day = 0.023 / 30  # S3 standard, per GB-day
    # payments: steady EC2 + RDS (EC2 covered by a Savings Plan until 30 Apr 2026)
    L += [
        Line("payments", "AWS", "Amazon Elastic Compute Cloud", "Compute", "Hours", "Hours", 0.192, 960, sku="m5.xlarge"),
        Line("payments", "AWS", "Amazon Relational Database Service", "Databases", "Hours", "Hours", 0.35, 96, sku="db.r6g.large"),
        Line("payments", "AWS", "AWS Data Transfer", "Networking", "GB", "GB", 0.09, 900, sku="DataTransfer-Out"),
    ]
    # search: EC2 + OpenAI tokens (launch on 1 Aug 2026, price cut the same day)
    L += [
        Line("search", "AWS", "Amazon Elastic Compute Cloud", "Compute", "Hours", "Hours", 0.192, 480, sku="m5.xlarge"),
        Line("search", "OpenAI", "OpenAI API", "AI and Machine Learning", "Tokens", "1M Tokens", mp["gpt-5-mini"][0],
             900e6, model="gpt-5-mini", token_type="input", weekday_only=True),
        Line("search", "OpenAI", "OpenAI API", "AI and Machine Learning", "Tokens", "1M Tokens", mp["gpt-5-mini"][1],
             120e6, model="gpt-5-mini", token_type="output", weekday_only=True),
    ]
    # data-platform: growing S3, BigQuery on Google Cloud (startup credits until 31 Aug 2026), messy env tag
    L += [
        Line("data-platform", "AWS", "Amazon Simple Storage Service", "Storage", "GB-Day", "GB-Day", gb_day, 2.4e6,
             sku="S3-Standard", growth=0.04, noise=0.02, tag_style="env_alias"),
        Line("data-platform", "Google Cloud", "BigQuery", "Analytics", "TB", "TB", 6.25, 60, region="us-central1",
             sku="Analysis-OnDemand", tag_style="env_alias"),
        Line("data-platform", "AWS", "Amazon Elastic Compute Cloud", "Compute", "Hours", "Hours", 0.384, 240,
             sku="m5.2xlarge", tag_style="env_alias"),
    ]
    # support-ai: chatbot on Claude (switches Haiku -> Sonnet on 3 Mar 2026)
    L += [
        Line("support-ai", "Anthropic", "Claude API", "AI and Machine Learning", "Tokens", "1M Tokens",
             mp["claude-haiku-4-5"][0], 220e6, model="claude-haiku-4-5", token_type="input", weekday_only=True),
        Line("support-ai", "Anthropic", "Claude API", "AI and Machine Learning", "Tokens", "1M Tokens",
             mp["claude-haiku-4-5"][1], 30e6, model="claude-haiku-4-5", token_type="output", weekday_only=True),
        Line("support-ai", "AWS", "Amazon Elastic Compute Cloud", "Compute", "Hours", "Hours", 0.192, 144, sku="m5.xlarge"),
    ]
    # marketing: content generation on Claude Haiku, storage with sloppy tags
    L += [
        Line("marketing", "Anthropic", "Claude API", "AI and Machine Learning", "Tokens", "1M Tokens",
             mp["claude-haiku-4-5"][0], 60e6, model="claude-haiku-4-5", token_type="input", weekday_only=True,
             tag_style="spaced_key"),
        Line("marketing", "Anthropic", "Claude API", "AI and Machine Learning", "Tokens", "1M Tokens",
             mp["claude-haiku-4-5"][1], 25e6, model="claude-haiku-4-5", token_type="output", weekday_only=True,
             tag_style="spaced_key"),
        Line("marketing", "Microsoft", "Storage Accounts", "Storage", "GB-Day", "GB-Day", 0.02 / 30, 4.5e5,
             region="eastus", sku="Hot-LRS"),
        # a legacy storage account whose tags never had `team` or `environment`: small, steady unallocated spend
        Line("marketing", "Microsoft", "Storage Accounts", "Storage", "GB-Day", "GB-Day", 0.02 / 30, 1.5e5,
             region="eastus", sku="Hot-LRS-legacy", noise=0.02, tag_style="partly_missing"),
    ]
    # ml-research: small Sonnet experiments + a GPU box
    L += [
        Line("ml-research", "Anthropic", "Claude API", "AI and Machine Learning", "Tokens", "1M Tokens",
             mp["claude-sonnet-4-5"][0], 15e6, model="claude-sonnet-4-5", token_type="input", noise=0.3),
        Line("ml-research", "Anthropic", "Claude API", "AI and Machine Learning", "Tokens", "1M Tokens",
             mp["claude-sonnet-4-5"][1], 3e6, model="claude-sonnet-4-5", token_type="output", noise=0.3),
        Line("ml-research", "AWS", "Amazon Elastic Compute Cloud", "Compute", "Hours", "Hours", 1.212, 24,
             sku="g5.2xlarge", noise=0.2),
    ]
    return L


def tags_for(line: Line, day_rng: np.random.Generator) -> str:
    t = TEAMS[line.team]
    if line.tag_style == "env_alias":  # data-platform uses `env`, not `environment`
        d = {"team": line.team, "env": t["env"], "application": t["app"]}
    elif line.tag_style == "spaced_key" and day_rng.random() < 0.3:  # a key with a stray leading space
        d = {" team": line.team, "environment": t["env"], "application": t["app"]}
    elif line.tag_style == "partly_missing":
        d = {"application": t["app"]}
    else:
        d = {"team": line.team, "environment": t["env"], "application": t["app"]}
    return json.dumps(d)


# ---------------------------------------------------------------- planted events
SP_END = date(2026, 4, 30)  # Savings Plan SP-PAY-2025 covers payments EC2 until this date
SWITCH = date(2026, 3, 3)  # support-ai: Haiku -> Sonnet
LAUNCH = date(2026, 8, 1)  # search launch (+45% tokens) and OpenAI price cut (-20%)
GPU_START, GPU_END = date(2026, 7, 8), date(2026, 7, 29)  # untagged GPU cluster left running
SUPPORT_PURCHASE = date(2026, 6, 15)  # annual AWS Business Support prepayment
CREDITS_END = date(2026, 8, 31)  # Google Cloud startup credits
STORM_START, STORM_END = date(2026, 9, 10), date(2026, 9, 14)  # marketing retry storm


def row(d: date, line: Line, qty: float, unit_price: float, tags: str, **over) -> dict:
    list_cost = qty / line.units_per_pricing_unit * unit_price
    r = {
        "BillingAccountName": BILLING_ACCOUNT,
        "BillingPeriodStart": d.replace(day=1).isoformat(),
        "ChargePeriodStart": d.isoformat(),
        "ChargeCategory": "Usage",
        "ChargeFrequency": "Usage-Based",
        "ChargeDescription": f"{line.service} {line.sku or line.model} {line.token_type or ''}".strip(),
        "ProviderName": line.provider,
        "ServiceName": line.service,
        "ServiceCategory": line.category,
        "SubAccountName": TEAMS[line.team]["account"],
        "RegionName": line.region,
        "SkuId": line.sku or line.model,
        "PricingCategory": "Standard",
        "ConsumedQuantity": round(qty, 4),
        "ConsumedUnit": line.unit,
        "PricingUnit": line.pricing_unit,
        "ListUnitPrice": unit_price,
        "ListCost": round(list_cost, 6),
        "EffectiveCost": round(list_cost, 6),
        "BilledCost": round(list_cost, 6),
        "CommitmentDiscountId": None,
        "BillingCurrency": "USD",
        "Tags": tags,
        "x_Model": line.model,
        "x_TokenType": line.token_type,
    }
    r.update(over)
    return r


def generate() -> tuple[pd.DataFrame, list[dict]]:
    mp = model_prices()
    lines = build_lines(mp)
    days = pd.date_range(START, END, freq="D").date
    rows: list[dict] = []
    for d in days:
        months = (d.year - START.year) * 12 + d.month - START.month + (d.day - 1) / 30
        weekend = d.weekday() >= 5
        for ln in lines:
            qty = ln.qty * (1 + ln.growth) ** months
            if ln.weekday_only and weekend:
                qty *= 0.55
            qty *= rng.lognormal(0, ln.noise)
            price = ln.price
            over = {}

            # E1 model switch: support-ai moves its chatbot from Haiku to Sonnet (same token volumes)
            if ln.team == "support-ai" and ln.model == "claude-haiku-4-5" and d >= SWITCH:
                ln_s = Line(**{**ln.__dict__, "model": "claude-sonnet-4-5"})
                price = mp["claude-sonnet-4-5"][0 if ln.token_type == "input" else 1]
                rows.append(row(d, ln_s, qty, price, tags_for(ln, rng)))
                continue
            # E5 search launch + price cut on the same day
            if ln.team == "search" and ln.provider == "OpenAI" and d >= LAUNCH:
                qty *= 1.45
                price *= 0.80
            # E7 retry storm: a batch job bug resends prompts for five days
            if ln.team == "marketing" and ln.model and STORM_START <= d <= STORM_END:
                qty *= 3.0 if ln.token_type == "output" else 2.2
            # E2 Savings Plan on payments EC2 until 30 Apr 2026: 28% discount on effective cost
            if ln.team == "payments" and ln.sku == "m5.xlarge" and d <= SP_END:
                lc = qty * price
                over = {"PricingCategory": "Committed", "CommitmentDiscountId": "SP-PAY-2025",
                        "EffectiveCost": round(lc * 0.72, 6), "BilledCost": round(lc * 0.72, 6)}
            rows.append(row(d, ln, qty, price, tags_for(ln, rng), **over))

        # E4 untagged GPU cluster in the ml-research account (8 x g5.2xlarge, 24h/day, no tags)
        if GPU_START <= d <= GPU_END:
            gpu = Line("ml-research", "AWS", "Amazon Elastic Compute Cloud", "Compute", "Hours", "Hours", 1.212, 0,
                       sku="g5.2xlarge")
            rows.append(row(d, gpu, 192 * rng.lognormal(0, 0.02), 1.212, "{}"))

        # E6 Google Cloud startup credits: monthly credit until 31 Aug 2026
        if d.day == 28 and d <= CREDITS_END:
            amt = -2_400.0
            rows.append({**row(d, lines[7], 0, 0, json.dumps({"team": "data-platform", "env": "prod",
                                                                  "application": "Lakehouse"})),
                         "ChargeCategory": "Credit", "ChargeFrequency": "Recurring",
                         "ChargeDescription": "Google Cloud for Startups credit", "ConsumedQuantity": None,
                         "ConsumedUnit": None, "ListCost": 0.0, "EffectiveCost": amt, "BilledCost": amt,
                         "SkuId": "Promotional-Credit"})

        # E3 annual AWS Business Support prepayment (one-off purchase, shared cost)
        if d == SUPPORT_PURCHASE:
            sup = Line("payments", "AWS", "AWS Support (Business)", "Management and Governance", "Units", "Units",
                       48_000, 0, sku="Support-Business-Annual")
            r = row(d, sup, 1, 48_000, json.dumps({"team": "shared", "environment": "prod",
                                                    "application": "Platform"}))
            r.update({"ChargeCategory": "Purchase", "ChargeFrequency": "One-Time",
                      "ChargeDescription": "AWS Business Support, 12-month prepayment"})
            rows.append(r)

    df = pd.DataFrame(rows)
    df.insert(0, "Id", [f"chg-{i:07d}" for i in range(len(df))])
    return df, answer_key()


def answer_key() -> list[dict]:
    """Ground truth for every planted event. `driver` uses the same vocabulary as the skeptic."""
    return [
        {"id": "E1", "date": SWITCH.isoformat(), "team": "support-ai", "provider": "Anthropic",
         "driver": "mix", "locus": "claude-sonnet-4-5",
         "truth": "support-ai switched its chatbot from claude-haiku-4-5 to claude-sonnet-4-5 on 2026-03-03; "
                  "token volumes did not change, cost per token tripled",
         "logged_in_calendar": True},
        {"id": "E2", "date": "2026-05-01", "team": "payments", "provider": "AWS", "driver": "price",
         "locus": "Amazon Elastic Compute Cloud",
         "truth": "Savings Plan SP-PAY-2025 expired on 2026-04-30; the same EC2 usage now pays on-demand rates "
                  "(+39% effective cost)", "logged_in_calendar": False},
        {"id": "E3", "date": SUPPORT_PURCHASE.isoformat(), "team": "shared", "provider": "AWS",
         "driver": "one_off", "locus": "AWS Support (Business)",
         "truth": "annual AWS Business Support prepayment of $48,000, a one-time purchase, not an anomaly",
         "logged_in_calendar": True},
        {"id": "E4", "date": GPU_START.isoformat(), "team": "unallocated", "provider": "AWS", "driver": "usage",
         "locus": "Amazon Elastic Compute Cloud",
         "truth": "8 untagged g5.2xlarge GPU instances ran 24h/day from 2026-07-08 to 2026-07-29 in the "
                  "ml-research account (Pioneer Nimbus) with no tags", "logged_in_calendar": False},
        {"id": "E5", "date": LAUNCH.isoformat(), "team": "search", "provider": "OpenAI", "driver": "usage",
         "locus": "gpt-5-mini",
         "truth": "search launched a feature on 2026-08-01 (+45% tokens) while the provider cut gpt-5-mini "
                  "prices 20% the same day; net spend rose ~16%, so usage growth is masked by the price cut",
         "logged_in_calendar": True},
        {"id": "E6", "date": "2026-09-01", "team": "data-platform", "provider": "Google Cloud",
         "driver": "credit", "locus": "BigQuery",
         "truth": "Google Cloud startup credits ($2,400/month) ended after 2026-08-31; usage is unchanged",
         "logged_in_calendar": False},
        {"id": "E7", "date": STORM_START.isoformat(), "team": "marketing", "provider": "Anthropic",
         "driver": "usage", "locus": "claude-haiku-4-5",
         "truth": "a batch-job retry bug resent prompts from 2026-09-10 to 2026-09-14 (output tokens x3, "
                  "input x2.2); the campaign launch on 2026-09-15 came AFTER the spike and is a decoy",
         "logged_in_calendar": False, "decoy": "autumn campaign launch on 2026-09-15"},
        {"id": "T1", "date": START.isoformat(), "team": "data-platform", "provider": "AWS", "driver": "trend",
         "locus": "Amazon Simple Storage Service",
         "truth": "S3 storage grows ~4% every month: a steady trend, not an anomaly", "logged_in_calendar": False},
    ]


# ---------------------------------------------------------------- calendar (deliberately incomplete, one decoy)
EVENTS = [
    ("2026-03-02", "support-ai", "change", "HelpBot release 4.2: answer quality upgrade (model change to Sonnet approved by team lead)"),
    ("2026-03-10", "support-ai", "business", "Support ticket volume up ~5% after new product line"),
    ("2026-06-15", "shared", "contract", "AWS Business Support renewed for 12 months (annual prepayment)"),
    ("2026-08-01", "search", "launch", "FindIt 'Ask' feature launched to all users"),
    ("2026-08-01", "search", "vendor", "Provider notice: gpt-5-mini list price reduced (synthetic event for this dataset)"),
    ("2026-09-15", "marketing", "launch", "Autumn campaign launched"),
]


def budgets(df: pd.DataFrame) -> pd.DataFrame:
    """Monthly budget per team: 115% of the team's median month in Jul-Dec 2025, rounded."""
    from spendauditor.allocation import derive_team

    d = df.copy()
    d["x_Team"] = d["Tags"].map(derive_team)
    d["month"] = d["BillingPeriodStart"]
    base = d[d.month < "2026-01-01"].groupby(["x_Team", "month"]).EffectiveCost.sum().groupby("x_Team").median()
    out = (base * 1.15).round(-2).clip(lower=500).reset_index()
    out.columns = ["team", "monthly_budget_usd"]
    out = out[~out.team.isin(["unallocated", "shared"])]
    return out


def write_budget_policy(b: pd.DataFrame) -> None:
    path = POLICIES / "budget_policy.md"
    text = path.read_text()
    table = "| Team | Monthly budget (USD) |\n|---|---|\n" + "\n".join(
        f"| {r.team} | {r.monthly_budget_usd:,.0f} |" for r in b.itertuples())
    start, end = "<!-- BUDGETS:START -->", "<!-- BUDGETS:END -->"
    head, rest = text.split(start)
    _, tail = rest.split(end)
    path.write_text(f"{head}{start}\n{table}\n{end}{tail}")


def main() -> None:
    df, key = generate()
    RAW.mkdir(parents=True, exist_ok=True)
    df.to_csv(RAW / "company_charges.csv", index=False)
    ANSWER_KEY.write_text(json.dumps(key, indent=2))
    pd.DataFrame(EVENTS, columns=["event_date", "team", "kind", "description"]).to_csv(RAW / "events.csv", index=False)
    b = budgets(df)
    b.to_csv(RAW / "budgets.csv", index=False)
    write_budget_policy(b)
    print(f"{len(df):,} charge rows, {df.ChargePeriodStart.min()} to {df.ChargePeriodStart.max()}, "
          f"total effective cost ${df.EffectiveCost.sum():,.0f}")
    print(b.to_string(index=False))


if __name__ == "__main__":
    main()
