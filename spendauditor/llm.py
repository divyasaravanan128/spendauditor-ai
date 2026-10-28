"""The LLM gateway: every model call in the app goes through here.

One place to:
1. **Meter**: each call's tokens are priced (LiteLLM's real price list) and written to `live_charges`
   as FOCUS-shaped rows, tagged with the agent step and the question. The app watches its own AI bill
   with the same metrics it uses on the company bill.
2. **Enforce a budget**: before every call, today's spend is checked against a daily cap. A public demo
   link can't run up an unlimited bill.
3. **Get structured output**: `structured()` forces the model to answer through a tool whose input
   schema is a Pydantic model, so agents exchange validated objects, not free text.
"""
from __future__ import annotations

import contextvars
import json
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from . import config
from .db import connect_rw

question_id: contextvars.ContextVar[str] = contextvars.ContextVar("question_id", default="adhoc")
T = TypeVar("T", bound=BaseModel)

# Used only if a model is missing from the price file (USD per token: input, output)
FALLBACK_PRICES = {"claude-haiku-5-5": (1e-07, 5e-07)}

LIVE_SCHEMA = """CREATE TABLE IF NOT EXISTS live_charges (
    Id TEXT PRIMARY KEY, ChargePeriodStart TEXT, BillingPeriodStart TEXT, ChargeCategory TEXT,
    ProviderName TEXT, ServiceName TEXT, ServiceCategory TEXT, ConsumedQuantity REAL, ConsumedUnit TEXT,
    ListUnitPrice REAL, PricingUnit TEXT, EffectiveCost REAL, BilledCost REAL, x_Model TEXT,
    x_TokenType TEXT, x_Step TEXT, x_QuestionId TEXT)"""


class BudgetExceeded(RuntimeError):
    """Today's AI spend has reached the daily cap."""


class StructuredOutputError(RuntimeError):
    """The model did not return a valid object."""


@lru_cache(maxsize=1)
def _price_table() -> dict:
    try:
        return json.loads(config.PRICES_FILE.read_text())
    except FileNotFoundError:
        return {}


def price_per_token(model: str) -> tuple[float, float]:
    entry = _price_table().get(model)
    if entry and entry.get("input_cost_per_token") is not None:
        return float(entry["input_cost_per_token"]), float(entry["output_cost_per_token"])
    return FALLBACK_PRICES.get(model, FALLBACK_PRICES["claude-haiku-5-5"])


def spent_today() -> float:
    today = datetime.now(timezone.utc).date().isoformat()
    with connect_rw(config.LIVE_DB_PATH) as con:
        con.execute(LIVE_SCHEMA)
        row = con.execute("SELECT COALESCE(SUM(EffectiveCost), 0) FROM live_charges "
                          "WHERE substr(ChargePeriodStart, 1, 10) = ?", (today,)).fetchone()
    return float(row[0])


def _log_usage(model: str, step: str, usage) -> float:
    p_in, p_out = price_per_token(model)
    now = datetime.now(timezone.utc)
    in_tokens = (getattr(usage, "input_tokens", 0) or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0) \
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
    out_tokens = getattr(usage, "output_tokens", 0) or 0
    searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0
    lines = [
        ("Claude API", "Tokens", in_tokens, p_in * 1e6, "1M Tokens", in_tokens * p_in, "input"),
        ("Claude API", "Tokens", out_tokens, p_out * 1e6, "1M Tokens", out_tokens * p_out, "output"),
    ]
    if searches:
        lines.append(("Claude web search", "Searches", searches, config.WEB_SEARCH_PRICE_USD, "Searches",
                      searches * config.WEB_SEARCH_PRICE_USD, None))
    total = 0.0
    with connect_rw(config.LIVE_DB_PATH) as con:
        con.execute(LIVE_SCHEMA)
        for service, unit, qty, unit_price, pricing_unit, cost, token_type in lines:
            if not qty:
                continue
            con.execute("INSERT INTO live_charges VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                uuid.uuid4().hex, now.isoformat(timespec="seconds"), now.date().replace(day=1).isoformat(), "Usage",
                "Anthropic", service, "AI and Machine Learning", qty, unit, unit_price, pricing_unit, cost, cost,
                model, token_type, step, question_id.get()))
            total += cost
    return total


@lru_cache(maxsize=1)
def _client():
    import anthropic

    return anthropic.Anthropic(max_retries=2, timeout=60)


def complete(step: str, messages: list[dict], system: str = "", *, tools: list | None = None,
             tool_choice: dict | None = None, max_tokens: int = 1500, model: str | None = None):
    """One metered, budget-checked call to Claude. Returns the raw Message."""
    model = model or config.MODEL
    if spent_today() >= config.DAILY_BUDGET_USD:
        raise BudgetExceeded(f"Daily AI budget of ${config.DAILY_BUDGET_USD:.2f} reached. "
                             "Raise SPENDAUDITOR_DAILY_BUDGET_USD or try again tomorrow.")
    kwargs = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = tools
    if tool_choice:
        kwargs["tool_choice"] = tool_choice
    msg = _client().messages.create(**kwargs)
    _log_usage(model, step, msg.usage)
    return msg


def text_of(msg) -> str:
    return "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text").strip()


def structured(step: str, prompt: str, schema: type[T], system: str = "", *, model: str | None = None,
               max_tokens: int = 1500) -> T:
    """Ask for an answer that must validate against a Pydantic model (one repair attempt on failure)."""
    tool = {"name": "answer", "description": f"Return the {schema.__name__}.",
            "input_schema": schema.model_json_schema()}
    messages = [{"role": "user", "content": prompt}]
    for attempt in range(2):
        msg = complete(step, messages, system, tools=[tool], tool_choice={"type": "tool", "name": "answer"},
                       model=model, max_tokens=max_tokens)
        block = next((b for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
        if getattr(msg, "stop_reason", None) == "max_tokens" and attempt == 0:
            max_tokens *= 2  # the answer was cut off mid-object: retry once with twice the room
            continue
        try:
            if block is None:
                raise StructuredOutputError("no tool call returned")
            return schema.model_validate(block.input)
        except (ValidationError, StructuredOutputError) as e:
            if attempt == 1:
                raise StructuredOutputError(f"{step}: invalid structured output: {e}") from e
            messages = messages + [{"role": "assistant", "content": msg.content},
                                   {"role": "user", "content": [{"type": "tool_result", "tool_use_id": block.id,
                                                                 "content": f"Invalid: {e}. Call answer again with "
                                                                            "valid fields.", "is_error": True}]
                                    if block else f"Call the answer tool. Error: {e}"}]
    raise AssertionError("unreachable")


def new_question() -> str:
    qid = uuid.uuid4().hex[:12]
    question_id.set(qid)
    return qid


def question_cost(qid: str) -> dict:
    with connect_rw(config.LIVE_DB_PATH) as con:
        con.execute(LIVE_SCHEMA)
        rows = con.execute("SELECT x_Step, SUM(EffectiveCost), SUM(CASE WHEN ConsumedUnit='Tokens' THEN "
                           "ConsumedQuantity ELSE 0 END) FROM live_charges WHERE x_QuestionId = ? GROUP BY x_Step",
                           (qid,)).fetchall()
    steps = {r[0]: {"cost_usd": round(r[1], 6), "tokens": int(r[2])} for r in rows}
    return {"total_usd": round(sum(s["cost_usd"] for s in steps.values()), 6), "by_step": steps}
