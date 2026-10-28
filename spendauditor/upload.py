"""Bring your own bill: load any FOCUS-format CSV export (AWS, Azure, Google Cloud, Oracle all export FOCUS)
into its own database, so every agent and metric works on it unchanged.

Only a few columns are required. Missing optional columns get safe defaults, and the same tag
allocation rules as the sample data are applied.
"""
from __future__ import annotations

import hashlib
import io

import pandas as pd

from . import config
from .allocation import derive_team, tag_issues
from .db import connect_rw

REQUIRED = ["ChargePeriodStart", "EffectiveCost", "ServiceName", "ProviderName"]
DEFAULTS = {
    "BillingAccountName": None, "ChargeCategory": "Usage", "ChargeFrequency": "Usage-Based", "ChargeDescription": None,
    "ServiceCategory": "Other", "SubAccountName": None, "RegionName": None, "SkuId": None,
    "PricingCategory": "Standard", "ConsumedQuantity": None, "ConsumedUnit": None, "PricingUnit": None,
    "ListUnitPrice": None, "ListCost": None, "BilledCost": None, "CommitmentDiscountId": None, "BillingCurrency": "USD",
    "Tags": "{}", "x_Model": None, "x_TokenType": None,
}


class UploadError(ValueError):
    pass


def load_focus_csv(data: bytes, max_rows: int = 500_000) -> tuple[str, dict]:
    """Validate and load a FOCUS CSV. Returns (database path, summary)."""
    try:
        df = pd.read_csv(io.BytesIO(data), low_memory=False, nrows=max_rows)
    except Exception as e:  # noqa: BLE001
        raise UploadError(f"Could not read the file as CSV: {e}") from e
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise UploadError(f"Not a FOCUS file: missing columns {missing}. Required: {REQUIRED}.")
    for col, default in DEFAULTS.items():
        if col not in df.columns:
            df[col] = default
    df["ChargePeriodStart"] = pd.to_datetime(df["ChargePeriodStart"], errors="coerce", utc=True).dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["ChargePeriodStart"])
    if df.empty:
        raise UploadError("No rows with a valid ChargePeriodStart date.")
    df["BillingPeriodStart"] = df["ChargePeriodStart"].str[:7] + "-01"
    df["BilledCost"] = df["BilledCost"].fillna(df["EffectiveCost"])
    df["ListCost"] = df["ListCost"].fillna(df["EffectiveCost"])
    df["x_Team"] = df["Tags"].map(derive_team)
    issues = df["Tags"].map(tag_issues)
    df["x_TagCompliant"] = issues.map(lambda x: int(not x))
    df["x_TagIssues"] = issues.map(lambda x: "; ".join(x))
    if "Id" not in df.columns:
        df.insert(0, "Id", [f"up-{i:08d}" for i in range(len(df))])
    keep = ["Id", *DEFAULTS, "BillingPeriodStart", "ChargePeriodStart", "EffectiveCost", "ServiceName",
            "ProviderName", "x_Team", "x_TagCompliant", "x_TagIssues"]
    df = df[list(dict.fromkeys(keep))]

    config.UPLOADS.mkdir(parents=True, exist_ok=True)
    path = config.UPLOADS / f"{hashlib.sha256(data).hexdigest()[:16]}.db"
    if path.exists():
        path.unlink()
    with connect_rw(path) as con:
        df.to_sql("charges", con, index=False)
        con.execute("CREATE TABLE budgets (team TEXT, monthly_budget_usd REAL)")
        con.execute("CREATE TABLE events (event_date TEXT, team TEXT, kind TEXT, description TEXT)")
        con.execute("CREATE INDEX idx_up_day ON charges(ChargePeriodStart)")
    summary = {"rows": len(df), "first_day": df.ChargePeriodStart.min(), "last_day": df.ChargePeriodStart.max(),
               "providers": sorted(df.ProviderName.dropna().unique().tolist())[:10],
               "teams_from_tags": int(df.x_Team.nunique()),
               "total_effective_cost": round(float(df.EffectiveCost.sum()), 2)}
    return str(path), summary
