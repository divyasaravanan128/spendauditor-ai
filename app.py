"""SpendAuditor AI: Streamlit app.  Run:  streamlit run app.py"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from spendauditor import config, db, llm, metrics
from spendauditor import charts as C

st.set_page_config(page_title="SpendAuditor AI", page_icon="🔎", layout="wide")

EXAMPLES = [
    "Why did our AI spend jump in March 2026?",
    "Why did payments' cloud bill go up in May 2026?",
    "What caused the spike in June 2026?",
    "Why did unallocated spend rise in July 2026?",
    "Why did marketing's AI spend spike in mid-September 2026?",
    "Which teams were over budget in September 2026?",
    "What does our policy say about switching to a bigger model?",
    "How many Claude calls has this app made today, and what did they cost?",
]
STEP_LABELS = {"plan": "Planned the investigation", "measure": "Measured the change (MCP metrics)",
               "policy": "Searched policies and contracts", "web": "Checked outside sources",
               "checks": "Ran the skeptic's checks", "hypothesize": "Drafted an explanation",
               "skeptic": "Skeptic reviewed the explanation", "chart": "Built the charts", "write": "Wrote the report"}


# ---------------------------------------------------------------- data source (sample or uploaded bill)
def active_db() -> Path:
    return Path(st.session_state.get("db_path", config.DATA / "spendauditor.db"))


def use_db():
    config.DB_PATH = active_db()


use_db()
if not (config.DATA / "spendauditor.db").exists():
    st.error("No database yet. Run: `python scripts/fetch_data.py && python scripts/generate_history.py && "
             "python scripts/build_db.py`")
    st.stop()
has_key = bool(os.getenv("ANTHROPIC_API_KEY"))

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("🔎 SpendAuditor AI")
    st.caption("An AI analyst that audits cloud and AI bills and explains why they changed.")
    spent = llm.spent_today()
    st.metric("This app's AI spend today", f"${spent:.4f}", help="Every model call is metered by the LLM gateway.")
    st.progress(min(spent / config.DAILY_BUDGET_USD, 1.0), text=f"of ${config.DAILY_BUDGET_USD:.2f} daily cap")
    st.caption(f"Model: `{config.MODEL}` · Web search: {'on' if config.WEB_SEARCH_ENABLED else 'off'}")
    src = "uploaded bill" if "db_path" in st.session_state else "sample company bill (synthetic, Jul 2025 – Sep 2026)"
    st.caption(f"Analysing: **{src}**")
    if "db_path" in st.session_state and st.button("Back to the sample bill"):
        del st.session_state["db_path"]
        st.rerun()
    if not has_key:
        st.warning("Set ANTHROPIC_API_KEY in .env to ask questions. The dashboards work without it.")

tab_ask, tab_overview, tab_live, tab_upload, tab_about = st.tabs(
    ["Ask", "Spend overview", "AI spend (live)", "Upload a bill", "How it works"])


# ---------------------------------------------------------------- Ask
def render_investigation(state: dict):
    r = state.get("report")
    if not r:
        st.error(state.get("error", "The investigation did not finish."))
        return
    st.markdown(f"### {r['headline']}")
    st.caption(f"Main driver: **{r['primary_driver'].replace('_', ' ')}** · where: **{r['primary_segment']}**")
    for spec in state.get("charts", []):
        st.vega_lite_chart(spec, width="stretch")
    cols = st.columns(2)
    with cols[0]:
        st.markdown("**Proven by the data**")
        for p in r["proven"]:
            st.markdown(f"- {p}")
        if r.get("plausible"):
            st.markdown("**Plausible, not proven**")
            for p in r["plausible"]:
                st.markdown(f"- {p}")
    with cols[1]:
        if r.get("ruled_out"):
            st.markdown("**Ruled out by the skeptic**")
            for p in r["ruled_out"]:
                st.markdown(f"- {p}")
        st.markdown("**Next steps**")
        for a in r["actions"]:
            st.markdown(f"- {a}")
    with st.expander("Skeptic's review"):
        rv = state.get("review", {})
        st.write(f"Decision: **{rv.get('decision')}** · revisions: {state.get('revisions', 0)}")
        claims = state.get("hypothesis", {}).get("claims", [])
        for v in rv.get("verdicts", []):
            claim = claims[v["claim_index"]]["claim"] if v["claim_index"] < len(claims) else "?"
            st.markdown(f"- **{v['verdict']}**: {claim}  \n  _{v['reason']}_")
        if rv.get("missing"):
            st.markdown("Missing: " + "; ".join(rv["missing"]))
    with st.expander("Checks the skeptic ran (computed, not generated)"):
        st.json(state.get("checks", {}), expanded=False)
    with st.expander("Policies and contracts consulted"):
        for p in state.get("policy", []):
            st.markdown(f"**{p['source']}**")
            st.caption(p["text"][:600])


def render(result: dict):
    kind = result["route"]
    if kind == "investigate":
        render_investigation(result["state"])
    elif kind == "metric":
        st.markdown(result["answer"])
        with st.expander(f"MCP tool calls ({len(result['calls'])})"):
            for c in result["calls"]:
                st.markdown(f"`{c['tool']}` {c['args']}")
    elif kind == "sql":
        st.markdown(result["answer"])
        st.code(result["sql"], language="sql")
        if result["rows"]:
            st.dataframe(pd.DataFrame(result["rows"]).head(200), width="stretch")
    elif kind in ("policy", "web"):
        st.markdown(result["answer"])
        if result.get("sources"):
            st.caption("Sources: " + " · ".join(s if isinstance(s, str) else f"[{s['title']}]({s['url']})"
                                                 for s in result["sources"]))
    else:
        st.markdown(result["answer"])
    cost = result.get("cost")
    if cost:
        st.caption(f"Route: `{kind}` · this answer cost ${cost['total_usd']:.4f} in model calls "
                   f"({sum(s['tokens'] for s in cost['by_step'].values()):,} tokens)")


def answer_question(q: str, history: list[dict]) -> dict:
    from spendauditor import investigate, metrics_agent, policy_rag, router, sql_agent, web_agent

    qid = llm.new_question()
    rng = metrics.catalogue()["data_range"]
    decision = router.route(q, history, f"{rng['first_day']} to {rng['last_day']}")
    sq = decision.standalone_question
    out = {"route": decision.route, "question": sq}
    if decision.route == "investigate":
        with st.status("Investigating…", expanded=True) as status:
            state = investigate.investigate(sq, on_step=lambda n, s: st.write("✓ " + STEP_LABELS.get(n, n)))
            status.update(label="Investigation complete", state="complete", expanded=False)
        out["state"] = state
        out["text"] = state.get("report", {}).get("headline", "")
    elif decision.route == "metric":
        a = metrics_agent.answer(sq)
        out.update(answer=a.answer, calls=a.calls, text=a.answer)
    elif decision.route == "sql":
        a = sql_agent.answer(sq)
        out.update(answer=a.answer, sql=a.sql, rows=a.rows, text=a.answer)
    elif decision.route == "policy":
        a = policy_rag.answer(sq)
        out.update(answer=a.answer, sources=a.sources, text=a.answer)
    elif decision.route == "web":
        a = web_agent.search(sq)
        out.update(answer=a.answer, sources=a.sources, text=a.answer)
    else:
        msg = llm.complete("direct", [{"role": "user", "content": sq}], max_tokens=300,
                           system="You are SpendAuditor AI, an assistant that explains changes in cloud and AI "
                                  "bills. Answer briefly; suggest an example cost question if off-topic.")
        out.update(answer=llm.text_of(msg), text=llm.text_of(msg))
    out["cost"] = llm.question_cost(qid)
    return out


with tab_ask:
    st.session_state.setdefault("chat", [])
    if not st.session_state.chat:
        st.markdown("Ask why spend changed, who is over budget, what a policy says, or anything about the bill.")
        cols = st.columns(2)
        for i, ex in enumerate(EXAMPLES):
            if cols[i % 2].button(ex, key=f"ex{i}", width="stretch", disabled=not has_key):
                st.session_state.pending = ex
                st.rerun()
    for turn in st.session_state.chat:
        with st.chat_message(turn["role"]):
            if turn["role"] == "user":
                st.markdown(turn["content"])
            else:
                render(turn["result"])
    q = st.chat_input("Why did our bill change?", disabled=not has_key) or st.session_state.pop("pending", None)
    if q:
        history = [{"role": t["role"], "content": t["content"] if t["role"] == "user" else t["result"].get("text", "")}
                   for t in st.session_state.chat]
        st.session_state.chat.append({"role": "user", "content": q})
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            try:
                use_db()
                result = answer_question(q, history)
                render(result)
                st.session_state.chat.append({"role": "assistant", "result": result})
            except llm.BudgetExceeded as e:
                st.warning(str(e))
            except Exception as e:  # noqa: BLE001
                st.error(f"Something went wrong: {e}")

# ---------------------------------------------------------------- Spend overview (no model calls)
with tab_overview:
    rng = metrics.catalogue()["data_range"]
    monthly = pd.DataFrame(db.query("SELECT BillingPeriodStart AS month, x_Team AS team, SUM(EffectiveCost) AS cost "
                                    "FROM charges GROUP BY 1, 2 ORDER BY 1"))
    total = monthly.groupby("month").cost.sum()
    c1, c2, c3 = st.columns(3)
    c1.metric("Latest month", f"${total.iloc[-1]:,.0f}", f"{(total.iloc[-1] / total.iloc[-2] - 1) * 100:+.1f}% vs prior"
              if len(total) > 1 else None, delta_color="inverse")
    c2.metric("Data range", f"{rng['first_day']} → {rng['last_day']}")
    un = metrics.untagged_spend(rng["last_day"][:8] + "01", rng["last_day"])
    c3.metric("Unallocated (latest month)", f"{un['unallocated_share_pct'] or 0:.1f}%")
    st.vega_lite_chart({"data": {"values": monthly.to_dict("records")}, "mark": "bar", "width": "container",
                        "height": 320, "title": "Monthly effective cost by team",
                        "encoding": {"x": {"field": "month", "type": "ordinal", "timeUnit": "yearmonth",
                                           "title": None},
                                     "y": {"field": "cost", "type": "quantitative", "aggregate": "sum",
                                           "title": "USD"},
                                     "color": {"field": "team", "type": "nominal"},
                                     "tooltip": [{"field": "team"}, {"field": "cost", "format": ",.0f"}]}},
                       width="stretch")
    months = sorted(monthly.month.str[:7].unique(), reverse=True)
    m = st.selectbox("Budget month", months)
    bva = metrics.budget_vs_actual(m)
    if bva["teams"]:
        st.dataframe(pd.DataFrame(bva["teams"]), width="stretch", hide_index=True)
    else:
        st.caption("No budgets for this bill.")

# ---------------------------------------------------------------- live AI spend of this app
with tab_live:
    st.markdown("Every Claude call this app makes is priced and logged as FOCUS-shaped rows by the LLM gateway. "
                "This is the app watching its own AI bill.")
    with db.connect_rw(config.LIVE_DB_PATH) as con:
        con.execute(llm.LIVE_SCHEMA)
        live = pd.read_sql("SELECT * FROM live_charges ORDER BY ChargePeriodStart DESC", con)
    if live.empty:
        st.info("No calls yet. Ask a question in the Ask tab.")
    else:
        live["day"] = live.ChargePeriodStart.str[:10]
        a, b, c = st.columns(3)
        a.metric("Total spent", f"${live.EffectiveCost.sum():.4f}")
        b.metric("Questions answered", live.x_QuestionId.nunique())
        c.metric("Avg cost per question", f"${live.EffectiveCost.sum() / max(live.x_QuestionId.nunique(), 1):.4f}")
        by_step = live.groupby("x_Step", as_index=False).EffectiveCost.sum()
        st.vega_lite_chart(C.bar_from_breakdown({"segments": [{"segment": r.x_Step, "change": r.EffectiveCost}
                                                              for r in by_step.itertuples()]}, "Cost by agent step (USD)"),
                           width="stretch")
        st.dataframe(live[["ChargePeriodStart", "x_Step", "x_Model", "x_TokenType", "ConsumedQuantity", "EffectiveCost"]]
                     .head(200), width="stretch", hide_index=True)

# ---------------------------------------------------------------- upload
with tab_upload:
    st.markdown("Upload a **FOCUS** billing export (CSV). AWS, Azure, Google Cloud and Oracle can all export FOCUS. "
                "Required columns: `ChargePeriodStart`, `EffectiveCost`, `ServiceName`, `ProviderName`. "
                "Teams are read from a `team` tag if present.")
    f = st.file_uploader("FOCUS CSV", type=["csv"])
    if f is not None and st.button("Analyse this bill"):
        from spendauditor.upload import UploadError, load_focus_csv

        try:
            path, summary = load_focus_csv(f.getvalue())
            st.session_state["db_path"] = path
            st.session_state.chat = []
            st.success(f"Loaded {summary['rows']:,} rows ({summary['first_day']} to {summary['last_day']}).")
            st.json(summary)
        except UploadError as e:
            st.error(str(e))
    st.caption("Tip: the FinOps Foundation's real sample (`data/raw/focus_sample_10000.csv`) works here.")

# ---------------------------------------------------------------- about
with tab_about:
    readme = (config.ROOT / "README.md").read_text() if (config.ROOT / "README.md").exists() else ""
    st.markdown(readme.split("## Run it")[0] if readme else "See README.md")
