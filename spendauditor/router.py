"""Router: decides which agent answers, and rewrites follow-ups so they stand alone."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from . import llm

Route = Literal["investigate", "metric", "sql", "policy", "web", "direct"]

ROUTES_DOC = """Routes:
- investigate: WHY did spend change / jump / drop / spike? What caused it? Is this an anomaly? (multi-step investigation)
- metric: a cost NUMBER or comparison answerable with governed metrics: spend by team/service/model/month,
  trends, budget vs actual, untagged spend, top cost drivers. No "why".
- sql: a data question the governed metrics don't cover: specific bill lines, row counts, regions, SKUs,
  unit prices, the real FinOps Foundation sample (real_sample), this app's own AI calls (live_charges).
- policy: what our policies / contracts / commitments / definitions SAY (tagging rules, budgets and approval
  thresholds, AI model rules, savings plans, credits, FOCUS column meanings). Also "does X break policy?"
  when the question is about the rule itself.
- web: facts about the OUTSIDE world: provider price changes, model releases or deprecations, public pricing.
- direct: greetings, thanks, questions about this app itself, or anything off-topic (answer briefly)."""


class RouteDecision(BaseModel):
    route: Route
    standalone_question: str = Field(description="The question rewritten to make sense without the chat history. "
                                                 "Resolve 'it', 'that team', 'last month'. Do not add facts the "
                                                 "user did not say.")
    reason: str = Field(description="One short sentence: why this route.")


def route(question: str, history: list[dict] | None = None, data_range: str = "") -> RouteDecision:
    convo = ""
    if history:
        convo = "Recent conversation:\n" + "\n".join(f"{m['role']}: {m['content'][:400]}" for m in history[-6:]) + "\n\n"
    prompt = (f"{ROUTES_DOC}\n\nBilling data covers {data_range}. 'Last month' means the latest full month in the "
              f"data.\n\n{convo}New question: {question}\n\nPick exactly one route.")
    return llm.structured("router", prompt, RouteDecision,
                          system="You route questions for a cloud and AI cost analyst app. Be precise.",
                          max_tokens=400)
