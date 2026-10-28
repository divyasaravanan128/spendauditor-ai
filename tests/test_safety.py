"""The read-only guarantees: nothing an agent writes can change data or read outside the whitelist."""
import pytest

from spendauditor.db import UnsafeQuery, query
from spendauditor.metrics import MetricError, cost_breakdown, cost_trend
from spendauditor.sql_agent import check_sql


@pytest.mark.parametrize("sql", [
    "DELETE FROM charges",
    "DROP TABLE budgets",
    "UPDATE budgets SET monthly_budget_usd = 0",
    "ATTACH DATABASE 'x.db' AS x",
    "PRAGMA table_info(charges)",
    "SELECT * FROM sqlite_master",
    "SELECT load_extension('evil')",
])
def test_database_layer_refuses(sql):
    with pytest.raises(UnsafeQuery):
        query(sql)


@pytest.mark.parametrize("sql", [
    "SELECT 1; DROP TABLE charges",
    "DELETE FROM charges",
    "SELECT * FROM charges -- sneaky",
    "SELECT * FROM charges /* x */",
    "WITH x AS (SELECT 1) DELETE FROM charges",
])
def test_static_checks_refuse(sql):
    with pytest.raises(UnsafeQuery):
        check_sql(sql)


def test_static_checks_wrap_with_limit():
    assert check_sql("SELECT x_Team FROM charges").endswith("LIMIT 200")


def test_select_still_works():
    rows = query(check_sql("WITH t AS (SELECT x_Team, SUM(EffectiveCost) c FROM charges GROUP BY 1) "
                           "SELECT * FROM t ORDER BY c DESC"))
    assert rows and "x_Team" in rows[0]


def test_metric_names_are_whitelisted():
    with pytest.raises(MetricError):
        cost_breakdown("x_Team; DROP TABLE charges", "2026-03-01", "2026-03-31")
    with pytest.raises(MetricError):
        cost_trend("2026-03-01", "2026-03-31", filters={"1=1 OR team": "x"})
    with pytest.raises(MetricError):
        cost_trend("2026-03-01", "2026-03-31", measure="SUM(1)")


def test_filter_values_are_bound_not_interpolated():
    # an injection attempt in a VALUE simply matches nothing
    out = cost_trend("2026-03-01", "2026-03-31", filters={"team": "payments' OR '1'='1"})
    assert out["series"] == []


def test_upload_real_focus_sample_and_query_it(tmp_path, monkeypatch):
    """Bring-your-own-bill: the real FinOps Foundation sample loads and the governed metrics work on it."""
    from spendauditor import config, metrics
    from spendauditor.upload import UploadError, load_focus_csv

    monkeypatch.setattr(config, "UPLOADS", tmp_path)
    path, summary = load_focus_csv((config.RAW / "focus_sample_10000.csv").read_bytes())
    assert summary["rows"] == 10_000 and "AWS" in summary["providers"]
    monkeypatch.setattr(config, "DB_PATH", path)
    b = metrics.cost_breakdown("provider", "2024-09-16", "2024-09-30")
    assert b["segments"] and b["current_total"] != 0
    with pytest.raises(UploadError):
        load_focus_csv(b"a,b\n1,2\n")
