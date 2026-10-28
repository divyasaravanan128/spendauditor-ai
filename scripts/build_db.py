"""Load all data into one SQLite file: data/spendauditor.db.

Tables
- charges       synthetic company bill (FOCUS 1.3 shape + x_ columns), with allocation columns added here
- budgets       monthly budget per team (also written into the budget policy)
- events        the business calendar: launches, contract renewals, vendor notices (deliberately incomplete)
- real_sample   the FinOps Foundation's real anonymized FOCUS sample (10,000 rows, Sep 2024)
- live_charges  this app's own AI calls, written by the LLM gateway while you use it (starts empty)

Run:  python scripts/build_db.py   (after fetch_data.py and generate_history.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spendauditor.allocation import derive_team, tag_issues  # noqa: E402
from spendauditor.config import DB_PATH, RAW  # noqa: E402
from spendauditor.db import connect_rw  # noqa: E402

LIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_charges (
    Id TEXT PRIMARY KEY,
    ChargePeriodStart TEXT,        -- ISO timestamp of the call
    BillingPeriodStart TEXT,
    ChargeCategory TEXT,
    ProviderName TEXT,
    ServiceName TEXT,
    ServiceCategory TEXT,
    ConsumedQuantity REAL,
    ConsumedUnit TEXT,
    ListUnitPrice REAL,
    PricingUnit TEXT,
    EffectiveCost REAL,
    BilledCost REAL,
    x_Model TEXT,
    x_TokenType TEXT,
    x_Step TEXT,                    -- which agent step made the call (router, skeptic, writer ...)
    x_QuestionId TEXT               -- groups all calls for one user question
)"""


def add_allocation(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["x_Team"] = df["Tags"].map(derive_team)
    issues = df["Tags"].map(tag_issues)
    df["x_TagCompliant"] = issues.map(lambda x: int(not x))
    df["x_TagIssues"] = issues.map(lambda x: "; ".join(x))
    return df


def load_real_sample() -> pd.DataFrame:
    keep = ["ChargePeriodStart", "BillingPeriodStart", "ChargeCategory", "ProviderName", "ServiceName",
            "ServiceCategory", "SubAccountName", "RegionName", "PricingCategory", "ConsumedQuantity",
            "ConsumedUnit", "ListCost", "EffectiveCost", "BilledCost", "Tags"]
    df = pd.read_csv(RAW / "focus_sample_10000.csv", usecols=keep, low_memory=False)
    return add_allocation(df)


def main() -> None:
    charges = add_allocation(pd.read_csv(RAW / "company_charges.csv"))
    budgets = pd.read_csv(RAW / "budgets.csv")
    events = pd.read_csv(RAW / "events.csv")
    real = load_real_sample()

    if DB_PATH.exists():
        DB_PATH.unlink()
    with connect_rw() as con:
        charges.to_sql("charges", con, index=False)
        budgets.to_sql("budgets", con, index=False)
        events.to_sql("events", con, index=False)
        real.to_sql("real_sample", con, index=False)
        con.execute(LIVE_SCHEMA)
        for col in ["ChargePeriodStart", "x_Team", "ServiceName", "x_Model", "ProviderName"]:
            con.execute(f"CREATE INDEX idx_charges_{col} ON charges({col})")
    print(f"built {DB_PATH} : charges={len(charges):,} budgets={len(budgets)} events={len(events)} "
          f"real_sample={len(real):,}")


if __name__ == "__main__":
    main()
