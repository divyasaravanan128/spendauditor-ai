"""The investigation workflow for "why did our spend change?" (LangGraph).

    plan -> measure -> (policy evidence || web evidence) -> checks -> hypothesize -> skeptic
                                                                        ^              |
                                                                        +-- revise ----+ (at most once)
                                                                                       v
                                                                                chart -> write

Why a fixed workflow and not a free-roaming agent: a cost investigation always needs the same evidence
(how big, where, when, usage-or-price, what else happened), so the steps are fixed and the model's
freedom is inside each step. Cost and latency stay predictable and a failure is easy to locate.
Measurement is deterministic code over MCP; models are used to plan, explain and critique.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from . import charts, config, llm, mcp_client, policy_rag, web_agent
from .skeptic import Driver, Hypothesis, SkepticReview, review, run_checks


# ---------------------------------------------------------------- structured objects
class InvestigationPlan(BaseModel):
    start: str = Field(description="First day of the period being explained (YYYY-MM-DD).")
    end: str = Field(description="Last day of that period (YYYY-MM-DD).")
    baseline_start: str | None = Field(default=None, description="Only if the user names a comparison period.")
    baseline_end: str | None = None
    team: str | None = Field(default=None, description="Only if the question names a team.")
    provider: str | None = None
    service: str | None = None
    model: str | None = None


class Report(BaseModel):
    headline: str = Field(description="One sentence: what changed, by how much, and the main reason.")
    primary_driver: Driver = Field(description="The single most important driver. Use 'trend' (not 'usage') when "
                                               "volume growth continues the growth of earlier months "
                                               "(checks.trend.consistent_with_existing_trend is true); 'usage' only "
                                               "for a new or unusual rise in volume.")
    primary_segment: str = Field(description="Where the main driver sits: a team, service or model name.")
    proven: list[str] = Field(description="2-4 statements the data shows directly, each with a number, one sentence each. "
                                          "End each with an evidence tag: [data], [policy], [calendar] or [outside].")
    plausible: list[str] = Field(default_factory=list, description="0-3 likely but unproven points, one sentence each.")
    ruled_out: list[str] = Field(default_factory=list, description="0-3 explanations the skeptic rejected, one sentence each.")
    actions: list[str] = Field(description="2-3 concrete next steps (e.g. renew a commitment, tag a resource, "
                                           "check a policy approval).")


class State(TypedDict, total=False):
    question: str
    plan: dict
    m: dict  # measurements
    policy: list
    web: dict
    checks: dict
    hypothesis: dict
    review: dict
    revisions: int
    charts: list
    report: dict
    error: str


# ---------------------------------------------------------------- nodes
def plan_node(state: State) -> State:
    cat = mcp_client.run(lambda s: mcp_client.read_catalogue(s))
    rng, vals = cat["data_range"], cat["known_values"]
    prompt = (f"Question: {state['question']}\n\nBilling data covers {rng['first_day']} to {rng['last_day']}; "
              f"'this month' / 'last month' mean the latest full month in the data ({rng['last_day'][:7]}).\n"
              f"Known teams: {vals['team']}\nProviders: {vals['provider']}\nModels: {vals['model']}\n"
              f"Services: {vals['service']}\n\nWhich period should be explained, and which filters does the "
              "question itself name? Use whole calendar months when the question names a month. Leave filters empty "
              "unless the question names them. Leave the baseline empty unless the user names one.")
    p = llm.structured("planner", prompt, InvestigationPlan, max_tokens=400)
    first, last = date.fromisoformat(rng["first_day"]), date.fromisoformat(rng["last_day"])

    def clamp(d: str | None) -> str | None:
        if not d:
            return None
        x = date.fromisoformat(d[:10])
        return min(max(x, first), last).isoformat()

    plan = p.model_dump()
    for k in ("start", "end", "baseline_start", "baseline_end"):
        plan[k] = clamp(plan[k])
    if plan["start"] > plan["end"]:
        plan["start"], plan["end"] = plan["end"], plan["start"]
    if bool(plan["baseline_start"]) != bool(plan["baseline_end"]):
        plan["baseline_start"] = plan["baseline_end"] = None
    if plan.get("team") and plan["team"] not in vals["team"]:
        plan["team"] = None  # never filter on a name that doesn't exist
    return {"plan": plan, "revisions": 0}


async def _measure(session, plan: dict) -> dict:
    s, e = plan["start"], plan["end"]
    bs, be = plan.get("baseline_start"), plan.get("baseline_end")
    base_filters = {k: plan[k] for k in ("team", "provider", "service", "model") if plan.get(k)}
    call = lambda name, **a: mcp_client.call(session, name, a)  # noqa: E731

    by_team = await call("cost_breakdown", dimension="team", start=s, end=e, baseline_start=bs, baseline_end=be,
                         filters=base_filters)
    bs, be = by_team["baseline_period"]
    focus_team = plan.get("team") or (by_team["segments"][0]["segment"] if by_team["segments"] else None)
    f = {**base_filters, **({"team": focus_team} if focus_team else {})}

    m = {"plan": plan, "focus": {"team": focus_team, "filters": f}, "by_team": by_team}
    m["by_service"] = await call("cost_breakdown", dimension="service", start=s, end=e, baseline_start=bs,
                                 baseline_end=be, filters=f, top_n=6)
    m["by_model"] = await call("cost_breakdown", dimension="model", start=s, end=e, baseline_start=bs,
                               baseline_end=be, filters=f, top_n=6)
    m["by_charge_category"] = await call("cost_breakdown", dimension="charge_category", start=s, end=e,
                                         baseline_start=bs, baseline_end=be, filters=f, top_n=5)
    m["split"] = await call("usage_price_split", start=s, end=e, baseline_start=bs, baseline_end=be, filters=f)
    m["split_overall"] = await call("usage_price_split", start=s, end=e, baseline_start=bs, baseline_end=be,
                                    filters=base_filters)
    # Look up to 14 days past the period (when data exists) so a short burst that ends is not mistaken for
    # a lasting level shift.
    last_day = (await mcp_client.read_catalogue(session))["data_range"]["last_day"]
    shape_end = min((date.fromisoformat(e) + timedelta(days=14)).isoformat(), last_day)
    m["change"] = await call("find_change_start", start=s, end=shape_end, filters=f)
    look_back = (date.fromisoformat(bs) - timedelta(days=14)).isoformat()
    m["events"] = await call("list_events", start=look_back, end=e, team=focus_team)
    m["purchases"] = await call("charge_items", start=bs, end=e, charge_category="Purchase", filters=f, limit=5)
    m["credits"] = await call("charge_items", start=bs, end=e, charge_category="Credit", filters=f, limit=5)
    six_months_back = (date.fromisoformat(s).replace(day=1) - timedelta(days=150)).replace(day=1).isoformat()
    m["monthly"] = await call("cost_trend", start=six_months_back, end=e, grain="month", filters=f)
    m["daily"] = await call("cost_trend", start=look_back, end=e, grain="day", filters=f)
    if focus_team == "unallocated" or (by_team["segments"] and any(
            x["segment"] == "unallocated" and abs(x["share_of_total_change_pct"] or 0) > 20 for x in by_team["segments"])):
        m["untagged"] = await call("untagged_spend", start=s, end=e)
    return m


def measure_node(state: State) -> State:
    return {"m": mcp_client.run(_measure, state["plan"])}


def policy_node(state: State) -> State:
    m = state["m"]
    largest = m["split"].get("largest_effect") or ""
    services = ", ".join(x["segment"] for x in m["by_service"]["segments"][:2])
    q = f"{m['focus']['team']} {largest.replace('_', ' ')} {services} " + {
        "rate": "commitment savings plan discount expiry", "credits": "credits end date",
        "mix": "model usage policy larger model approval", "one_off_purchases": "one-time purchase prepayment",
        "volume": "budget approval new recurring spend retries"}.get(largest, "")
    if m["focus"]["team"] == "unallocated":
        q += " untagged spend tagging policy GPU"
    return {"policy": policy_rag.index().search(q, k=3)}


def web_node(state: State) -> State:
    m = state["m"]
    largest = m["split"].get("largest_effect")
    providers = [x["segment"] for x in m["by_model"]["segments"][:1] if x["segment"] != "(none)"]
    if not config.WEB_SEARCH_ENABLED or largest not in ("rate",) or not providers:
        return {"web": {"searched": False, "reason": "not needed or disabled"}}
    plan = state["plan"]
    found = web_agent.search(f"Did the list price of {providers[0]} change around {plan['start']}?")
    return {"web": found.model_dump()}


def checks_node(state: State) -> State:
    return {"checks": run_checks(state["m"])}


def _compact(m: dict) -> dict:
    """What the explainer needs, without huge lists (keeps tokens and cost down)."""
    def seg(b, n=5):
        return {k: b[k] for k in ("baseline_period", "current_period", "baseline_total", "current_total", "change",
                                  "change_pct")} | {"segments": b["segments"][:n]}
    split = m["split"]
    return {"focus": m["focus"], "by_team": seg(m["by_team"]), "by_service": seg(m["by_service"], 4),
            "by_model": seg(m["by_model"], 4), "by_charge_category": seg(m["by_charge_category"], 4),
            "split": {k: split[k] for k in ("total_change", "effects", "effects_share_pct", "largest_effect")}
            | {"by_unit": [{k: u[k] for k in ("unit", "quantity_change_pct", "volume_effect", "mix_effect",
                                               "rate_effect", "top_items")} for u in split["by_unit"][:3]]},
            "overall_split": {k: m["split_overall"][k] for k in ("total_change", "effects", "largest_effect")},
            "change_timing": {k: v for k, v in m["change"].items() if k != "flagged_days"},
            "events": m["events"]["events"], "purchases": m["purchases"]["items"][:3],
            "credits": m["credits"]["items"][:3], "monthly_trend": m["monthly"]["series"]}


def hypothesize_node(state: State) -> State:
    m = state["m"]
    feedback = ""
    if state.get("review") and state["review"]["decision"] == "revise":
        feedback = ("\n\nA skeptical reviewer rejected parts of your previous explanation:\n"
                    f"{json.dumps(state['review'])}\nFix the explanation using the checks.")
    policy = "\n".join(f"- {p['source']}: {p['text'][:600]}" for p in state.get("policy", []))
    web = state.get("web", {})
    prompt = (f"Question: {state['question']}\n\nMeasurements (from governed metrics):\n"
              f"{json.dumps(_compact(m), default=str)[:12000]}\n\nComputed checks:\n"
              f"{json.dumps(state['checks'], default=str)[:6000]}\n\nRelevant policies and contracts:\n{policy}\n\n"
              f"Outside world: {web.get('answer', 'not searched')}\n\n"
              "Propose the causes of the change as claims. Use the usage/price split to choose each claim's driver. "
              "Only cite an event as a cause if it came before the change started." + feedback)
    h = llm.structured("hypothesize", prompt, Hypothesis,
                       system="You are a FinOps analyst explaining a cost change. Numbers must come from the "
                              "measurements. Prefer fewer, well-supported claims.", max_tokens=2000)
    return {"hypothesis": h.model_dump()}


def skeptic_node(state: State) -> State:
    r: SkepticReview = review(state["question"], Hypothesis(**state["hypothesis"]), state["checks"])
    return {"review": r.model_dump(), "revisions": state.get("revisions", 0) + (1 if r.decision == "revise" else 0)}


def chart_node(state: State) -> State:
    m, plan = state["m"], state["plan"]
    f = m["focus"]["filters"]
    title = "Daily spend" + (f" — {', '.join(f'{k}: {v}' for k, v in f.items())}" if f else "")
    return {"charts": [
        charts.daily_spend(m["daily"]["series"], title, m["change"].get("first_flagged_day"), m["events"]["events"],
                           [plan["start"], plan["end"]]),
        charts.effects_bar(m["split"]["effects"]),
    ]}


def write_node(state: State) -> State:
    prompt = (f"Question: {state['question']}\n\nClaims:\n{json.dumps(state['hypothesis'])}\n\nSkeptic review:\n"
              f"{json.dumps(state['review'])}\n\nKey numbers:\n{json.dumps(_compact(state['m']), default=str)[:8000]}\n\n"
              f"Policy excerpts used: {[p['source'] for p in state.get('policy', [])]}\n\n"
              f"Shape of the change (computed): {state['m']['change'].get('pattern')}; trend check: "
              f"{state['checks'].get('trend', 'not applicable')}.\n\n"
              "Write the final report. Supported claims go in 'proven' only if the data shows them directly; "
              "weakened claims go in 'plausible'; rejected claims go in 'ruled_out' with the reason. "
              "Use the user's language; amounts in USD with thousands separators.")
    r = llm.structured("writer", prompt, Report, model=config.WRITER_MODEL,
                       system="You write short, precise FinOps findings for busy engineers and finance leads.",
                       max_tokens=2500)
    return {"report": r.model_dump()}


# ---------------------------------------------------------------- graph
def build_graph():
    g = StateGraph(State)
    for name, fn in [("plan", plan_node), ("measure", measure_node), ("policy", policy_node), ("web", web_node),
                     ("checks", checks_node), ("hypothesize", hypothesize_node), ("skeptic", skeptic_node),
                     ("chart", chart_node), ("write", write_node)]:
        g.add_node(name, fn)
    g.add_edge(START, "plan")
    g.add_edge("plan", "measure")
    g.add_edge("measure", "policy")  # fan-out: evidence gathering runs in parallel
    g.add_edge("measure", "web")
    g.add_edge(["policy", "web"], "checks")  # fan-in: wait for both
    g.add_edge("checks", "hypothesize")
    g.add_edge("hypothesize", "skeptic")
    g.add_conditional_edges("skeptic", lambda s: "hypothesize" if (s["review"]["decision"] == "revise"
                                                                   and s.get("revisions", 0) <= 1) else "chart",
                            {"hypothesize": "hypothesize", "chart": "chart"})
    g.add_edge("chart", "write")
    g.add_edge("write", END)
    return g.compile()


_GRAPH = None


def investigate(question: str, on_step=None) -> State:
    """Run the full investigation. on_step(node_name) is called as each step finishes (for the UI)."""
    global _GRAPH
    _GRAPH = _GRAPH or build_graph()
    state: State = {"question": question}
    for update in _GRAPH.stream(state, stream_mode="updates"):
        for node, delta in update.items():
            state.update(delta or {})
            if on_step:
                on_step(node, state)
    return state
