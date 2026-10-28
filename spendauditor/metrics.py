"""Governed cost metrics: defined once, in code, and exposed to agents only through the MCP server.

Agents never write SQL for these numbers. They choose a metric, a dimension and filters from fixed
lists; every name is whitelisted and every value is bound as a parameter. So the same question always
gets the same number, and a model cannot quietly redefine "cost".
"""
from __future__ import annotations

import statistics
from datetime import date, timedelta

from .db import query

MEASURES = {
    "effective_cost": "SUM(EffectiveCost)",
    "billed_cost": "SUM(BilledCost)",
    "list_cost": "SUM(ListCost)",
    "tokens": "SUM(CASE WHEN ConsumedUnit = 'Tokens' THEN ConsumedQuantity ELSE 0 END)",
}
DIMENSIONS = {
    "team": "x_Team",
    "provider": "ProviderName",
    "service": "ServiceName",
    "service_category": "ServiceCategory",
    "account": "SubAccountName",
    "model": "x_Model",
    "token_type": "x_TokenType",
    "charge_category": "ChargeCategory",
    "pricing_category": "PricingCategory",
    "region": "RegionName",
    "sku": "SkuId",
}
GRAINS = {"day": "%Y-%m-%d", "week": "%Y-W%W", "month": "%Y-%m"}


class MetricError(ValueError):
    """A request outside the governed definitions (unknown metric, dimension, filter or bad date)."""


# ---------------------------------------------------------------- helpers
def _d(s: str) -> date:
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError as e:
        raise MetricError(f"bad date {s!r}; use YYYY-MM-DD") from e


def _days(start: str, end: str) -> int:
    n = (_d(end) - _d(start)).days + 1
    if n <= 0:
        raise MetricError(f"end {end} is before start {start}")
    return n


def _where(start: str, end: str, filters: dict | None) -> tuple[str, list]:
    _d(start), _d(end)
    clauses, params = ["ChargePeriodStart BETWEEN ? AND ?"], [str(start)[:10], str(end)[:10]]
    for key, val in (filters or {}).items():
        if val in (None, "", []):
            continue
        if key not in DIMENSIONS:
            raise MetricError(f"unknown filter {key!r}; allowed: {sorted(DIMENSIONS)}")
        col = DIMENSIONS[key]
        if isinstance(val, (list, tuple)):
            clauses.append(f"{col} IN ({','.join('?' * len(val))})")
            params.extend(val)
        else:
            clauses.append(f"{col} = ?")
            params.append(val)
    return " AND ".join(clauses), params


def _measure(m: str) -> str:
    if m not in MEASURES:
        raise MetricError(f"unknown measure {m!r}; allowed: {sorted(MEASURES)}")
    return MEASURES[m]


def _r(x, n=2):
    return None if x is None else round(float(x), n)


def previous_period(start: str, end: str) -> tuple[str, str]:
    """The baseline: a window of the same length immediately before. Whole months map to the previous month."""
    s, e = _d(start), _d(end)
    if s.day == 1 and (e + timedelta(days=1)).day == 1:  # whole calendar month(s)
        pe = s - timedelta(days=1)
        months = (e.year - s.year) * 12 + e.month - s.month + 1
        ps = pe.replace(day=1)
        for _ in range(months - 1):
            ps = (ps - timedelta(days=1)).replace(day=1)
        return ps.isoformat(), pe.isoformat()
    n = _days(start, end)
    return (s - timedelta(days=n)).isoformat(), (s - timedelta(days=1)).isoformat()


# ---------------------------------------------------------------- metrics
def cost_trend(start: str, end: str, measure: str = "effective_cost", grain: str = "month",
               filters: dict | None = None) -> dict:
    """The measure over time, one value per day, week or month."""
    if grain not in GRAINS:
        raise MetricError(f"unknown grain {grain!r}; allowed: {sorted(GRAINS)}")
    where, params = _where(start, end, filters)
    rows = query(f"SELECT strftime('{GRAINS[grain]}', ChargePeriodStart) AS period, {_measure(measure)} AS value "
                 f"FROM charges WHERE {where} GROUP BY period ORDER BY period", params)
    return {"measure": measure, "grain": grain, "filters": filters or {},
            "series": [{"period": r["period"], "value": _r(r["value"])} for r in rows]}


def total(start: str, end: str, measure: str = "effective_cost", filters: dict | None = None) -> float:
    where, params = _where(start, end, filters)
    return float(query(f"SELECT COALESCE({_measure(measure)}, 0) AS v FROM charges WHERE {where}", params)[0]["v"])


def cost_breakdown(dimension: str, start_b: str, end_b: str, start_a: str | None = None, end_a: str | None = None,
                   measure: str = "effective_cost", filters: dict | None = None, top_n: int = 10) -> dict:
    """Compare period A (baseline) with period B, segment by segment.

    If the periods differ in length, A is scaled to B's length (daily average x days in B), so a
    31-day month is not "growth" over a 30-day one.
    """
    if dimension not in DIMENSIONS:
        raise MetricError(f"unknown dimension {dimension!r}; allowed: {sorted(DIMENSIONS)}")
    if not start_a or not end_a:
        start_a, end_a = previous_period(start_b, end_b)
    scale = _days(start_b, end_b) / _days(start_a, end_a)
    col, m = DIMENSIONS[dimension], _measure(measure)

    def by_seg(s, e):
        where, params = _where(s, e, filters)
        return {r["seg"]: float(r["v"] or 0) for r in query(
            f"SELECT COALESCE({col}, '(none)') AS seg, {m} AS v FROM charges WHERE {where} GROUP BY seg", params)}

    a, b = by_seg(start_a, end_a), by_seg(start_b, end_b)
    total_a, total_b = sum(a.values()) * scale, sum(b.values())
    change = total_b - total_a
    segs = []
    for seg in set(a) | set(b):
        va, vb = a.get(seg, 0.0) * scale, b.get(seg, 0.0)
        segs.append({"segment": seg, "baseline": _r(va), "current": _r(vb), "change": _r(vb - va),
                     "change_pct": _r((vb - va) / va * 100, 1) if va else None,
                     "share_of_total_change_pct": _r((vb - va) / change * 100, 1) if change else None})
    segs.sort(key=lambda x: abs(x["change"]), reverse=True)
    return {"measure": measure, "dimension": dimension, "filters": filters or {},
            "baseline_period": [start_a, end_a], "current_period": [start_b, end_b],
            "baseline_scaled_to_current_length": round(scale, 4) != 1,
            "baseline_total": _r(total_a), "current_total": _r(total_b), "change": _r(change),
            "change_pct": _r(change / total_a * 100, 1) if total_a else None,
            "segments": segs[:top_n], "segments_not_shown": max(0, len(segs) - top_n)}


def usage_price_split(start_b: str, end_b: str, start_a: str | None = None, end_a: str | None = None,
                      filters: dict | None = None) -> dict:
    """Split a change in effective cost into volume, mix, rate, credits and one-off purchases.

    For usage charges, within each unit (hours, GB, tokens), with items = model or SKU (+ token type):
        cost = Q * sum_i(w_i * r_i)      Q = total quantity, w_i = item share, r_i = cost per unit
        volume = (Q_b - Q_a) * R_a        more or less used at the old average rate
        mix    = Q_b * sum_i((w_b - w_a) * r_a)   shift towards dearer or cheaper items
        rate   = Q_b * sum_i(w_b * (r_b - r_a))   same items, new price per unit
    The three add up exactly to the usage change. Credits, purchases and other charges are reported
    separately, because a credit ending or a prepayment is not usage growth.
    """
    if not start_a or not end_a:
        start_a, end_a = previous_period(start_b, end_b)
    scale = _days(start_b, end_b) / _days(start_a, end_a)

    def items(s, e):
        where, params = _where(s, e, filters)
        return query(
            "SELECT ConsumedUnit AS unit, COALESCE(x_Model, SkuId, ServiceName) || COALESCE(':' || x_TokenType, '') AS item, "
            "SUM(ConsumedQuantity) AS q, SUM(EffectiveCost) AS c FROM charges "
            f"WHERE {where} AND ChargeCategory = 'Usage' AND ConsumedQuantity > 0 GROUP BY unit, item", params)

    def other(s, e):
        where, params = _where(s, e, filters)
        return {r["k"]: float(r["c"] or 0) for r in query(
            f"SELECT ChargeCategory AS k, SUM(EffectiveCost) AS c FROM charges WHERE {where} "
            "AND ChargeCategory != 'Usage' GROUP BY k", params)}

    a_rows, b_rows = items(start_a, end_a), items(start_b, end_b)
    units = {}
    for tag, rows, sc in (("a", a_rows, scale), ("b", b_rows, 1.0)):
        for r in rows:
            u = units.setdefault(r["unit"], {})
            it = u.setdefault(r["item"], {"qa": 0.0, "ca": 0.0, "qb": 0.0, "cb": 0.0})
            it["q" + tag] += r["q"] * sc
            it["c" + tag] += r["c"] * sc

    per_unit, vol_t, mix_t, rate_t = [], 0.0, 0.0, 0.0
    for unit, its in units.items():
        qa, qb = sum(i["qa"] for i in its.values()), sum(i["qb"] for i in its.values())
        ca, cb = sum(i["ca"] for i in its.values()), sum(i["cb"] for i in its.values())
        ra_avg = ca / qa if qa else 0.0
        vol = (qb - qa) * ra_avg
        mix = rate = 0.0
        for i in its.values():
            r_a = i["ca"] / i["qa"] if i["qa"] else (i["cb"] / i["qb"] if i["qb"] else 0.0)
            r_b = i["cb"] / i["qb"] if i["qb"] else r_a
            w_a, w_b = (i["qa"] / qa if qa else 0.0), (i["qb"] / qb if qb else 0.0)
            mix += qb * (w_b - w_a) * r_a
            rate += qb * w_b * (r_b - r_a)
        if not qa:  # brand-new unit: all of it is volume
            vol, mix, rate = cb, 0.0, 0.0
        top = sorted(its.items(), key=lambda kv: abs(kv[1]["cb"] - kv[1]["ca"]), reverse=True)[:5]
        per_unit.append({
            "unit": unit, "baseline_cost": _r(ca), "current_cost": _r(cb),
            "quantity_change_pct": _r((qb - qa) / qa * 100, 1) if qa else None,
            "volume_effect": _r(vol), "mix_effect": _r(mix), "rate_effect": _r(rate),
            "top_items": [{"item": k, "baseline_cost": _r(v["ca"]), "current_cost": _r(v["cb"]),
                           "baseline_qty": _r(v["qa"]), "current_qty": _r(v["qb"]),
                           "baseline_rate": _r(v["ca"] / v["qa"], 8) if v["qa"] else None,
                           "current_rate": _r(v["cb"] / v["qb"], 8) if v["qb"] else None} for k, v in top]})
        vol_t, mix_t, rate_t = vol_t + vol, mix_t + mix, rate_t + rate

    oa, ob = other(start_a, end_a), other(start_b, end_b)
    oa = {k: v * scale for k, v in oa.items()}
    credit = ob.get("Credit", 0) - oa.get("Credit", 0)
    purchase = ob.get("Purchase", 0) - oa.get("Purchase", 0)
    other_cats = sum(ob.get(k, 0) - oa.get(k, 0) for k in set(oa) | set(ob) if k not in ("Credit", "Purchase"))
    effects = {"volume": vol_t, "mix": mix_t, "rate": rate_t, "credits": credit, "one_off_purchases": purchase,
               "other_charges": other_cats}
    total_change = sum(effects.values())
    biggest = max(effects, key=lambda k: abs(effects[k])) if total_change else None
    return {
        "baseline_period": [start_a, end_a], "current_period": [start_b, end_b], "filters": filters or {},
        "total_change": _r(total_change),
        "effects": {k: _r(v) for k, v in effects.items()},
        "effects_share_pct": {k: _r(v / total_change * 100, 1) if total_change else None for k, v in effects.items()},
        "largest_effect": biggest,
        "by_unit": sorted(per_unit, key=lambda u: abs((u["current_cost"] or 0) - (u["baseline_cost"] or 0)),
                          reverse=True),
        "note": "credits: a positive effect means credits shrank or ended (cost went up). "
                "one_off_purchases: one-time prepayments, not recurring usage.",
    }


def find_change_start(start: str, end: str, measure: str = "effective_cost", filters: dict | None = None,
                      baseline_days: int = 28, threshold: float = 4.0) -> dict:
    """When did it move, and how? Each day is compared with the trailing median of the same kind of day
    (weekdays with weekdays, weekends with weekends, because AI traffic drops at weekends), using a robust
    z-score (median absolute deviation). Then the movement is classified:
      one_day_spike   a single unusual day, then back to normal
      short_burst     a few unusual days, then back to normal
      level_shift     a new level that persists to the end of the period (a step change)
    """
    s = _d(start) - timedelta(days=baseline_days)
    series = cost_trend(s.isoformat(), end, measure, "day", filters)["series"]
    vals = [(p["period"], p["value"] or 0.0) for p in series]

    def is_weekend(day: str) -> bool:
        return _d(day).weekday() >= 5

    def baseline(i: int) -> tuple[float, float]:
        same = [v for d, v in vals[max(0, i - baseline_days):i] if is_weekend(d) == is_weekend(vals[i][0])]
        med = statistics.median(same)
        mad = statistics.median([abs(v - med) for v in same]) or max(abs(med) * 0.02, 1e-9)
        return med, 1.4826 * mad

    flagged = []
    for i in range(baseline_days, len(vals)):
        med, scale = baseline(i)
        z = (vals[i][1] - med) / scale
        if abs(z) >= threshold and vals[i][0] >= start:
            flagged.append({"i": i, "day": vals[i][0], "value": _r(vals[i][1]), "expected": _r(med), "robust_z": _r(z, 1)})
    if not flagged:
        return {"filters": filters or {}, "pattern": "no_unusual_days", "first_flagged_day": None,
                "note": "No day stands out from comparable recent days. A monthly change may be a gradual trend."}
    runs, cur = [], [flagged[0]]
    for f in flagged[1:]:
        if f["i"] - cur[-1]["i"] <= 3 and (f["robust_z"] > 0) == (cur[-1]["robust_z"] > 0):
            cur.append(f)
        else:
            runs.append(cur)
            cur = [f]
    runs.append(cur)
    main_run = max(runs, key=lambda r: sum(abs(x["robust_z"]) for x in r))
    first = main_run[0]
    # Level shift: after the unusual run ends, does spend settle at a new level? Compare weekdays with
    # weekdays (median), the 28 days before the run against the days after it.
    last = main_run[-1]
    before = [v for d, v in vals[max(0, first["i"] - baseline_days):first["i"]] if not is_weekend(d)]
    post = [v for d, v in vals[last["i"] + 1:] if not is_weekend(d)]
    length = last["i"] - first["i"] + 1
    if len(post) >= 4 and before and statistics.median(before):
        ratio_after = statistics.median(post) / statistics.median(before)
        stays = abs(ratio_after - 1) > 0.10
    else:  # the run reaches the end of the period
        during = [v for d, v in vals[first["i"]:last["i"] + 1] if not is_weekend(d)] or [last["value"]]
        ratio_after = statistics.median(during) / statistics.median(before) if before and statistics.median(before) else None
        stays = length >= 5
    pattern = "level_shift" if stays else ("one_day_spike" if length == 1 else "short_burst")
    clean = [{k: v for k, v in f.items() if k != "i"} for f in flagged]
    return {"filters": filters or {}, "pattern": pattern, "direction": "up" if first["robust_z"] > 0 else "down",
            "first_flagged_day": first["day"], "last_flagged_day_in_run": main_run[-1]["day"],
            "run_length_days": length, "level_after_vs_before": _r(ratio_after, 2),
            "flagged_days": clean[:12], "flagged_days_not_shown": max(0, len(clean) - 12)}


def untagged_spend(start: str, end: str) -> dict:
    """Unallocated spend (no usable team tag) and tag-policy breaches, by account and issue."""
    where, params = _where(start, end, None)
    tot = total(start, end)
    un = query(f"SELECT SubAccountName AS account, ServiceName AS service, SkuId AS sku, SUM(EffectiveCost) AS cost, "
               f"MIN(ChargePeriodStart) AS first_day, MAX(ChargePeriodStart) AS last_day FROM charges "
               f"WHERE {where} AND x_Team = 'unallocated' GROUP BY 1,2,3 ORDER BY cost DESC LIMIT 10", params)
    issues = query(f"SELECT x_TagIssues AS issue, x_Team AS team, COUNT(*) AS rows, SUM(EffectiveCost) AS cost "
                   f"FROM charges WHERE {where} AND x_TagCompliant = 0 GROUP BY 1,2 ORDER BY cost DESC LIMIT 10", params)
    un_cost = sum(r["cost"] for r in un)
    return {"period": [start, end], "total_cost": _r(tot), "unallocated_cost": _r(un_cost),
            "unallocated_share_pct": _r(un_cost / tot * 100, 2) if tot else None,
            "unallocated_by_account_service": [{**r, "cost": _r(r["cost"])} for r in un],
            "non_compliant_tags": [{**r, "cost": _r(r["cost"])} for r in issues]}


def budget_vs_actual(month: str) -> dict:
    """Effective cost per team against its monthly budget. month = YYYY-MM."""
    try:
        first = date.fromisoformat(f"{month[:7]}-01")
    except ValueError as e:
        raise MetricError("month must be YYYY-MM") from e
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    rows = query("SELECT b.team, b.monthly_budget_usd AS budget, COALESCE(SUM(c.EffectiveCost), 0) AS actual "
                 "FROM budgets b LEFT JOIN charges c ON c.x_Team = b.team AND c.ChargePeriodStart BETWEEN ? AND ? "
                 "GROUP BY b.team, b.monthly_budget_usd ORDER BY actual / b.monthly_budget_usd DESC",
                 (first.isoformat(), last.isoformat()))
    out = []
    for r in rows:
        pct = r["actual"] / r["budget"] * 100
        status = "over_120" if pct > 120 else "over" if pct > 100 else "alert_80" if pct >= 80 else "ok"
        out.append({"team": r["team"], "budget": _r(r["budget"]), "actual": _r(r["actual"]), "pct_of_budget": _r(pct, 1),
                    "status": status})
    unbudgeted = query("SELECT x_Team AS team, SUM(EffectiveCost) AS actual FROM charges WHERE ChargePeriodStart "
                       "BETWEEN ? AND ? AND x_Team NOT IN (SELECT team FROM budgets) GROUP BY 1",
                       (first.isoformat(), last.isoformat()))
    return {"month": month[:7], "teams": out,
            "spend_without_a_team_budget": [{"team": r["team"], "actual": _r(r["actual"])} for r in unbudgeted]}


def charge_items(start: str, end: str, charge_category: str, filters: dict | None = None, limit: int = 10) -> dict:
    """Individual non-usage lines (Purchase, Credit, Adjustment, Tax) or the largest usage lines."""
    if charge_category not in {"Usage", "Purchase", "Credit", "Adjustment", "Tax"}:
        raise MetricError("charge_category must be Usage, Purchase, Credit, Adjustment or Tax")
    where, params = _where(start, end, filters)
    rows = query(f"SELECT ChargePeriodStart AS day, ChargeCategory, ChargeFrequency, ProviderName, ServiceName, "
                 f"ChargeDescription, x_Team AS team, EffectiveCost FROM charges WHERE {where} AND ChargeCategory = ? "
                 f"ORDER BY ABS(EffectiveCost) DESC LIMIT ?", [*params, charge_category, min(int(limit), 50)])
    return {"period": [start, end], "charge_category": charge_category,
            "items": [{**r, "EffectiveCost": _r(r["EffectiveCost"])} for r in rows]}


def list_events(start: str, end: str, team: str | None = None) -> dict:
    """The business calendar: launches, contract changes, vendor notices. It is incomplete by nature."""
    _d(start), _d(end)
    sql = "SELECT event_date, team, kind, description FROM events WHERE event_date BETWEEN ? AND ?"
    params = [start, end]
    if team:
        sql += " AND team IN (?, 'shared')"
        params.append(team)
    return {"period": [start, end], "events": query(sql + " ORDER BY event_date", params),
            "note": "The calendar only holds what people logged. Missing events are common."}


def catalogue() -> dict:
    """What can be asked: measures, dimensions, known values, date range."""
    rng = query("SELECT MIN(ChargePeriodStart) AS first_day, MAX(ChargePeriodStart) AS last_day FROM charges")[0]
    values = {}
    for dim in ["team", "provider", "service", "model", "account", "service_category", "charge_category"]:
        col = DIMENSIONS[dim]
        values[dim] = [r["v"] for r in query(f"SELECT DISTINCT {col} AS v FROM charges WHERE {col} IS NOT NULL ORDER BY 1")]
    return {"measures": sorted(MEASURES), "dimensions": sorted(DIMENSIONS), "grains": sorted(GRAINS),
            "data_range": rng, "known_values": values, "currency": "USD",
            "default_measure": "effective_cost (after discounts and credits)"}
