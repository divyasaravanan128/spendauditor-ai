"""The whole investigation graph and the LLM gateway, with the model replaced by a stub.
Checks the plumbing: MCP calls, parallel evidence, the skeptic's revision loop, charts, cost metering."""
from types import SimpleNamespace

from spendauditor import investigate as inv
from spendauditor import llm, skeptic


def test_investigation_graph_runs_and_revises_once(monkeypatch):
    calls = {"skeptic": 0, "hypothesize": 0}

    def fake_structured(step, prompt, schema, system="", **kw):
        if schema is inv.InvestigationPlan:
            return schema(start="2026-03-01", end="2026-03-31")
        if schema is skeptic.Hypothesis:
            calls["hypothesize"] += 1
            driver = "usage" if calls["hypothesize"] == 1 else "mix"  # first draft is wrong on purpose
            return schema(claims=[skeptic.Claim(claim="support-ai cost rose", driver=driver,
                                                segment="support-ai", amount_usd=18700, cause_date="2026-03-02",
                                                evidence=["usage_price_split"])])
        if schema is skeptic.SkepticReview:
            calls["skeptic"] += 1
            if calls["skeptic"] == 1:
                return schema(verdicts=[skeptic.ClaimVerdict(claim_index=0, verdict="rejected",
                                                             reason="mix is 99% of the change")], decision="revise")
            return schema(verdicts=[skeptic.ClaimVerdict(claim_index=0, verdict="supported", reason="ok")],
                          decision="pass")
        if schema is inv.Report:
            return schema(headline="h", primary_driver="mix", primary_segment="support-ai", proven=["p [data]"],
                          actions=["a"])
        raise AssertionError(schema)

    monkeypatch.setattr(llm, "structured", fake_structured)
    steps = []
    state = inv.investigate("Why did our AI spend jump in March 2026?", on_step=lambda n, s: steps.append(n))

    assert state["m"]["focus"]["team"] == "support-ai"  # found the team that moved most
    assert state["checks"]["usage_or_price"]["largest_effect"] == "mix"
    assert state["checks"]["timing"]["change_first_flagged_day"] == "2026-03-03"
    assert calls == {"skeptic": 2, "hypothesize": 2}  # one revision, then pass
    assert steps.count("hypothesize") == 2 and steps[-1] == "write"
    assert {"policy", "web"} <= set(steps)
    assert any("model" in p["source"].lower() or "AI" in p["source"] for p in state["policy"])
    assert len(state["charts"]) == 2 and state["report"]["primary_driver"] == "mix"


def test_skeptic_checks_flag_decoy_event_after_change():
    m = {"split": {"effects": {"volume": 1200, "mix": 250, "rate": 0, "credits": 0, "one_off_purchases": 0,
                               "other_charges": 0}, "total_change": 1450, "largest_effect": "volume"},
         "change": {"pattern": "short_burst", "first_flagged_day": "2026-09-10"},
         "events": {"events": [{"event_date": "2026-09-15", "team": "marketing", "kind": "launch",
                                "description": "Autumn campaign launched"}]},
         "by_team": {"change": 1450, "segments": []}, "focus": {"team": "marketing"}}
    c = skeptic.run_checks(m)
    assert c["timing"]["events"][0]["relative_to_change_start"] == "after"


def test_gateway_meters_every_call_and_enforces_budget(monkeypatch, tmp_path):
    from spendauditor import config

    monkeypatch.setattr(config, "LIVE_DB_PATH", tmp_path / "live.db")
    usage = SimpleNamespace(input_tokens=200_000, output_tokens=20_000, cache_read_input_tokens=0,
                            cache_creation_input_tokens=0, server_tool_use=SimpleNamespace(web_search_requests=1))
    fake_msg = SimpleNamespace(content=[SimpleNamespace(type="text", text="hi")], usage=usage)
    monkeypatch.setattr(llm, "_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=lambda **k: fake_msg)))
    monkeypatch.setattr(config, "DAILY_BUDGET_USD", 0.05)

    qid = llm.new_question()
    llm.complete("router", [{"role": "user", "content": "x"}], model="claude-haiku-5-5")
    cost = llm.question_cost(qid)
    # 200k input * $0.10/M + 20k output * $0.50/M + 1 search * $0.01 = 0.02 + 0.01 + 0.01
    assert abs(cost["total_usd"] - 0.04) < 1e-9 and cost["by_step"]["router"]["tokens"] == 220_000
    llm.complete("router", [{"role": "user", "content": "x"}], model="claude-haiku-5-5")  # now at $0.08
    try:
        llm.complete("router", [{"role": "user", "content": "x"}], model="claude-haiku-5-5")
        raise AssertionError("budget not enforced")
    except llm.BudgetExceeded:
        pass
