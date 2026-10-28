"""The governed metrics must recover every planted event in the answer key, with no model involved.
If these fail, no agent built on top can be right."""
from spendauditor import metrics as M
from spendauditor.allocation import derive_team, tag_issues


def test_e1_model_switch_is_mix_not_usage():
    s = M.usage_price_split("2026-03-01", "2026-03-31", filters={"team": "support-ai"})
    assert s["largest_effect"] == "mix"
    assert s["effects_share_pct"]["mix"] > 90
    c = M.find_change_start("2026-03-01", "2026-03-31", filters={"team": "support-ai"})
    assert c["first_flagged_day"] == "2026-03-03" and c["pattern"] == "level_shift"


def test_e2_savings_plan_expiry_is_rate():
    s = M.usage_price_split("2026-05-01", "2026-05-31", filters={"team": "payments"})
    assert s["largest_effect"] == "rate"
    b = M.cost_breakdown("pricing_category", "2026-05-01", "2026-05-31", filters={"team": "payments"})
    segs = {x["segment"]: x for x in b["segments"]}
    assert segs["Committed"]["current"] == 0 and segs["Standard"]["change"] > 0


def test_e3_annual_support_is_one_off():
    s = M.usage_price_split("2026-06-01", "2026-06-30")
    assert s["largest_effect"] == "one_off_purchases"
    assert M.find_change_start("2026-06-01", "2026-06-30")["pattern"] == "one_day_spike"


def test_e4_untagged_gpu_cluster():
    u = M.untagged_spend("2026-07-01", "2026-07-31")
    top = u["unallocated_by_account_service"][0]
    assert top["account"] == "Pioneer Nimbus" and top["sku"] == "g5.2xlarge"
    assert (top["first_day"], top["last_day"]) == ("2026-07-08", "2026-07-29")


def test_e5_usage_growth_masked_by_price_cut():
    s = M.usage_price_split("2026-08-01", "2026-08-31", filters={"team": "search"})
    assert s["effects"]["volume"] > 0 > s["effects"]["rate"]
    assert s["largest_effect"] == "volume"


def test_e6_credits_ending():
    s = M.usage_price_split("2026-09-01", "2026-09-30", filters={"team": "data-platform"})
    assert s["effects"]["credits"] > 2000
    assert s["effects"]["mix"] == 0 and abs(s["effects"]["rate"]) < 1


def test_e7_retry_storm_is_short_burst_before_campaign():
    c = M.find_change_start("2026-09-01", "2026-09-30", filters={"team": "marketing"})
    assert c["pattern"] == "short_burst"
    assert c["first_flagged_day"] == "2026-09-10" and c["last_flagged_day_in_run"] == "2026-09-14"
    campaign = [e for e in M.list_events("2026-09-01", "2026-09-30", "marketing")["events"] if "campaign" in e["description"]]
    assert campaign and campaign[0]["event_date"] > c["first_flagged_day"]  # the decoy comes after


def test_split_adds_up_exactly():
    for team in ["support-ai", "payments", "search", "data-platform", "marketing"]:
        s = M.usage_price_split("2026-04-01", "2026-04-30", filters={"team": team})
        assert abs(sum(s["effects"].values()) - s["total_change"]) < 0.05


def test_breakdown_scales_unequal_periods():
    b = M.cost_breakdown("team", "2026-09-01", "2026-09-30")  # 30 days vs August's 31
    assert b["baseline_scaled_to_current_length"] is True


def test_budget_status():
    teams = {t["team"]: t for t in M.budget_vs_actual("2026-09")["teams"]}
    assert teams["support-ai"]["status"] == "over_120"


def test_tag_allocation_is_lenient_and_compliance_is_strict():
    assert derive_team('{" team": "marketing", "environment": "prod", "application": "x"}') == "marketing"
    assert tag_issues('{" team": "marketing", "environment": "prod", "application": "x"}') == ["' team' should be 'team'"]
    assert tag_issues('{"team": "a", "env": "prod", "application": "x"}') == ["'env' should be 'environment'"]
    assert derive_team("{}") == "unallocated" and tag_issues("{}") == ["no tags"]
