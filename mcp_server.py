"""SpendAuditor cost-metrics MCP server.

Exposes the governed cost metrics over the Model Context Protocol, using all three MCP primitives:
- Tools:     the metrics (read-only)
- Resource:  spend://catalogue, what can be asked (measures, dimensions, known teams/models, date range)
- Prompt:    investigate_cost_change, the investigation recipe an analyst would follow

The app's agents connect to it as an MCP client over stdio. Any MCP host can use it too.
Claude Desktop config (claude_desktop_config.json):

    "mcpServers": {
      "spendauditor": {
        "command": "/ABSOLUTE/PATH/spendauditor-ai/.venv/bin/python",
        "args": ["/ABSOLUTE/PATH/spendauditor-ai/mcp_server.py"]
      }
    }
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from spendauditor import metrics as M  # noqa: E402

server = MCPServer(
    name="spendauditor-metrics",
    log_level="ERROR",
    instructions=(
        "Governed cloud and AI cost metrics over a FOCUS-format bill. All costs are USD effective cost "
        "(after discounts and credits) unless a measure says otherwise. Dates are YYYY-MM-DD. "
        "Read the spend://catalogue resource for valid teams, models and the data range."
    ),
)
def governed(fn):
    """Turn a rule violation (unknown metric, bad date) into a clear tool error the model can read and fix."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except M.MetricError as e:
            raise ToolError(str(e)) from e

    return wrapper


READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
FILTERS_DOC = ("Optional filters, e.g. {\"team\": \"payments\", \"provider\": \"AWS\"}. "
               "Keys: team, provider, service, service_category, account, model, token_type, charge_category, "
               "pricing_category, region, sku.")


@server.tool(annotations=READ_ONLY, description="""Cost over time. grain: day | week | month. measure: effective_cost | billed_cost | list_cost | tokens.
    """ + FILTERS_DOC)
@governed
def cost_trend(start: str, end: str, grain: str = "month", measure: str = "effective_cost",
               filters: dict | None = None) -> dict:
    return M.cost_trend(start, end, measure, grain, filters)


@server.tool(annotations=READ_ONLY, description="""Which segments drove a change: compares [start, end] with a baseline period (default: the previous
    period of the same length, or the previous month for whole months). dimension: team | provider |
    service | service_category | account | model | token_type | charge_category | pricing_category |
    region | sku. """ + FILTERS_DOC)
@governed
def cost_breakdown(dimension: str, start: str, end: str, baseline_start: str | None = None,
                   baseline_end: str | None = None, measure: str = "effective_cost",
                   filters: dict | None = None, top_n: int = 10) -> dict:
    return M.cost_breakdown(dimension, start, end, baseline_start, baseline_end, measure, filters, top_n)


@server.tool(annotations=READ_ONLY, description="""Splits a change in effective cost into volume (used more), mix (shifted to pricier items, e.g. a
    bigger model), rate (same items, new unit price, e.g. a discount ended), credits and one-off purchases.
    Use it before claiming a cause: it tells you whether the change is usage, price or neither.
    """ + FILTERS_DOC)
@governed
def usage_price_split(start: str, end: str, baseline_start: str | None = None, baseline_end: str | None = None,
                      filters: dict | None = None) -> dict:
    return M.usage_price_split(start, end, baseline_start, baseline_end, filters)


@server.tool(annotations=READ_ONLY, description="""When did spend move, and was it a one-day spike, a short burst or a lasting level shift?
    Compares each day with comparable recent days (weekday vs weekend). """ + FILTERS_DOC)
@governed
def find_change_start(start: str, end: str, measure: str = "effective_cost", filters: dict | None = None) -> dict:
    return M.find_change_start(start, end, measure, filters)


@server.tool(annotations=READ_ONLY, description="""Spend with no usable team tag (unallocated), by account and service, plus tag-policy breaches.""")
@governed
def untagged_spend(start: str, end: str) -> dict:
    return M.untagged_spend(start, end)


@server.tool(annotations=READ_ONLY, description="""Each team's effective cost against its monthly budget. month: YYYY-MM.""")
@governed
def budget_vs_actual(month: str) -> dict:
    return M.budget_vs_actual(month)


@server.tool(annotations=READ_ONLY, description="""Individual bill lines of one category: Purchase (prepayments, one-offs), Credit, Adjustment, Tax,
    or the largest Usage lines. """ + FILTERS_DOC)
@governed
def charge_items(start: str, end: str, charge_category: str, filters: dict | None = None, limit: int = 10) -> dict:
    return M.charge_items(start, end, charge_category, filters, limit)


@server.tool(annotations=READ_ONLY, description="""Business calendar entries (launches, contract renewals, vendor notices) in a date range.
    The calendar is incomplete: absence of an event proves nothing.""")
@governed
def list_events(start: str, end: str, team: str | None = None) -> dict:
    return M.list_events(start, end, team)


@server.resource("spend://catalogue", name="catalogue", mime_type="application/json",
                 description="Valid measures, dimensions, grains, known teams/providers/models and the data range.")
def catalogue() -> str:
    return json.dumps(M.catalogue(), indent=2)


@server.prompt(name="investigate_cost_change",
               description="The investigation recipe for 'why did our spend change?' questions.")
def investigate_cost_change(question: str) -> str:
    return f"""Investigate this cost question like a careful FinOps analyst: {question}

1. Confirm the change: compare the period with its baseline (cost_breakdown by team). Is it real and how big?
2. Find where it is concentrated: break down by team, then service or model, inside the team that moved most.
3. Find when it started and its shape (find_change_start): one-day spike, short burst or level shift?
4. Split the change (usage_price_split): volume, mix, rate, credits, one-off purchases. Never call a
   price or mix change "more usage", and never call a one-off purchase an anomaly.
5. Look for evidence: the business calendar (list_events), one-off lines (charge_items), tagging (untagged_spend).
6. Only claim a cause if it came BEFORE the change and explains most of it. Say what is proven by the
   data and what is only plausible."""


if __name__ == "__main__":
    server.run("stdio")
