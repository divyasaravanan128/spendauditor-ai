"""Text-to-SQL agent for ad-hoc questions outside the governed metrics.

Safety is layered, so no single layer has to be perfect:
1. The model sees only the whitelisted tables and their columns (plus example values to ground names).
2. Static checks: one statement, must start with SELECT/WITH, no comments, no write keywords.
3. The database connection itself is read-only with an authorizer (see db.py): even if 1 and 2 were
   fooled, a write or a read of a non-whitelisted table is refused by SQLite.
4. Results are capped (LIMIT) and queries time out.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from . import llm
from .db import UnsafeQuery, query

MAX_ROWS = 200
FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|reindex|"
                       r"trigger|load_extension)\b", re.I)

SCHEMA = """Tables (SQLite). Dates are TEXT 'YYYY-MM-DD'. Costs are USD.
charges(Id, BillingAccountName, BillingPeriodStart, ChargePeriodStart, ChargeCategory [Usage|Purchase|Credit],
  ChargeFrequency, ChargeDescription, ProviderName, ServiceName, ServiceCategory, SubAccountName, RegionName, SkuId,
  PricingCategory [Standard|Committed], ConsumedQuantity, ConsumedUnit, PricingUnit, ListUnitPrice, ListCost,
  EffectiveCost, BilledCost, CommitmentDiscountId, BillingCurrency, Tags (JSON text), x_Model, x_TokenType,
  x_Team (owning team from tags, or 'unallocated'), x_TagCompliant (1/0), x_TagIssues)
  -- synthetic company bill, 2025-07-01..2026-09-30, daily rows
budgets(team, monthly_budget_usd)
events(event_date, team, kind, description)  -- business calendar
real_sample(ChargePeriodStart, BillingPeriodStart, ChargeCategory, ProviderName, ServiceName, ServiceCategory,
  SubAccountName, RegionName, PricingCategory, ConsumedQuantity, ConsumedUnit, ListCost, EffectiveCost, BilledCost,
  Tags, x_Team, x_TagCompliant, x_TagIssues)  -- REAL anonymized FinOps Foundation sample, Sep 2024
live_charges(ChargePeriodStart (ISO timestamp), ServiceName, ConsumedQuantity, ConsumedUnit, EffectiveCost,
  x_Model, x_TokenType, x_Step, x_QuestionId)  -- this app's own Claude calls"""


class SqlDraft(BaseModel):
    sql: str = Field(description="One SQLite SELECT statement.")
    explanation: str = Field(description="One sentence on what the query computes.")


class SqlAnswer(BaseModel):
    answer: str
    sql: str
    rows: list[dict]
    error: str | None = None


def check_sql(sql: str) -> str:
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        raise UnsafeQuery("only one statement is allowed")
    if "--" in s or "/*" in s:
        raise UnsafeQuery("comments are not allowed")
    if not re.match(r"^(select|with)\b", s, re.I):
        raise UnsafeQuery("only SELECT queries are allowed")
    if FORBIDDEN.search(s):
        raise UnsafeQuery("write or admin keyword found")
    return f"SELECT * FROM ({s}) LIMIT {MAX_ROWS}"


def _value_hints() -> str:
    teams = [r["x_Team"] for r in query("SELECT DISTINCT x_Team FROM charges")]
    models = [r["x_Model"] for r in query("SELECT DISTINCT x_Model FROM charges WHERE x_Model IS NOT NULL")]
    providers = [r["ProviderName"] for r in query("SELECT DISTINCT ProviderName FROM charges")]
    return f"Known values: x_Team={teams}; x_Model={models}; ProviderName={providers}."


def answer(question: str) -> SqlAnswer:
    prompt = f"{SCHEMA}\n{_value_hints()}\n\nWrite one SQLite query that answers: {question}"
    last_error = None
    for _ in range(2):  # one retry with the database error shown to the model
        p = prompt if not last_error else f"{prompt}\n\nYour previous query failed: {last_error}. Fix it."
        draft = llm.structured("sql_writer", p, SqlDraft, system="You write correct, minimal SQLite.", max_tokens=600)
        try:
            rows = query(check_sql(draft.sql))
            break
        except UnsafeQuery as e:
            return SqlAnswer(answer=f"I won't run that query: {e}.", sql=draft.sql, rows=[], error=str(e))
        except Exception as e:  # noqa: BLE001 - SQL errors go back to the model once
            last_error = str(e)
    else:
        return SqlAnswer(answer="I couldn't write a working query for that.", sql=draft.sql, rows=[], error=last_error)
    shown = rows[:30]
    msg = llm.complete("sql_answer", [{"role": "user", "content":
                       f"Question: {question}\nSQL: {draft.sql}\nRows ({len(rows)} total, first {len(shown)}): "
                       f"{shown}\n\nAnswer in 2-4 sentences using only these rows. Give exact figures."}],
                       max_tokens=500)
    return SqlAnswer(answer=llm.text_of(msg), sql=draft.sql, rows=rows)
