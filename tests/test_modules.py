"""The eight modules and the explainer, run on the sample portfolio."""

import numpy as np
import pandas as pd
import pytest
from openpyxl import load_workbook

from samatva.analytics import risk as R
from samatva.modules import compliance as C
from samatva.modules import hub, performance as P, private_assets as PA, reports
from samatva.modules import risk_dashboard as RD, scenarios as S, trading as T
from samatva.modules.explainer import Explainer


# --------------------------------------------------------------------------- 1. hub
def test_nav_weights_and_allocations_reconcile(ws):
    v = ws.valuation
    assert ws.nav == pytest.approx(v["market_value"].sum() + ws.portfolio.cash)
    assert v["weight"].sum() + ws.cash_weight == pytest.approx(1.0)
    for dim in hub.DIMENSIONS:
        assert hub.allocation(ws, dim).sum() == pytest.approx(1.0), dim


def test_lookthrough_splits_the_fund(ws):
    with_lt = hub.allocation(ws, "country", lookthrough=True)
    without = hub.allocation(ws, "country", lookthrough=False)
    assert "Emerging Markets" in without and "Emerging Markets" not in with_lt
    assert with_lt["India"] == pytest.approx(ws.weights["EMF"] * 0.20)


def test_summary_and_description(ws):
    s = hub.summary(ws)
    assert s["holdings"] == 11 and s["nav"]["meaning"]
    assert "Global Balanced Fund is worth $" in hub.describe(ws)


# --------------------------------------------------------------------------- 2. risk
def test_headline_metrics_have_meanings(ws):
    rows = RD.headline(ws)
    keys = {r["key"] for r in rows}
    assert {"volatility", "var_95", "es_95", "max_drawdown", "beta"} <= keys
    assert all(r["meaning"] and r["formula"] for r in rows)
    var = next(r for r in rows if r["key"] == "var_95")
    es = next(r for r in rows if r["key"] == "es_95")
    assert 0 < var["value"] < es["value"]


def test_var_methods_broadly_agree(ws):
    vm = RD.var_methods(ws)
    vals = [vm[k] for k in ("historical", "parametric", "monte_carlo", "bootstrap")]
    assert max(vals) / min(vals) < 1.25
    ten = RD.var_methods(ws, horizon_days=10)
    assert ten["historical"] == pytest.approx(vm["historical"] * np.sqrt(10))


def test_risk_contributions_sum_to_one(ws):
    rc = RD.risk_contributions(ws)
    assert rc["percent"].sum() == pytest.approx(1.0)
    assert rc.index[0] == "NWT"  # the most volatile large equity holding leads


def test_bond_analytics_match_scenario_repricing(ws):
    b = RD.bond_analytics(ws)
    dv01_total = b["dv01"]["value"]
    shock = S.custom(ws, rates=0.0001)
    assert -shock["pnl"] == pytest.approx(dv01_total, rel=0.01)


def test_liquidity_profile_is_monotonic(ws):
    l = RD.liquidity(ws)
    p = list(l["profile"].values())
    assert p == sorted(p) and p[-1] <= 1.0 + 1e-9
    assert l["positions"][0]["id"] == "CORP5"  # the thinly traded corporate bond is slowest


# --------------------------------------------------------------------------- 3. scenarios
def test_zero_scenario_changes_nothing(ws):
    assert S.run(ws, S.Scenario("Nothing"))["pnl"] == pytest.approx(0.0)


def test_equity_shock_hits_exactly_the_equities(ws):
    eq_value = ws.valuation.query("asset_class == 'Equity'")["market_value"].sum()
    res = S.custom(ws, equity=-0.10)
    assert res["pnl"] == pytest.approx(-0.10 * eq_value)


def test_currency_shock_only_hits_foreign_holdings(ws):
    foreign = ws.valuation.query("currency == 'EUR'")["market_value"].sum()
    res = S.run(ws, S.Scenario("EUR down", fx={"EUR": -0.05}))
    assert res["pnl"] == pytest.approx(-0.05 * foreign)


def test_library_sorted_and_explained(ws):
    lib = S.run_library(ws)
    assert [r["pnl"] for r in lib] == sorted(r["pnl"] for r in lib)
    assert all(r["plain"].startswith("Under '") for r in lib)


def test_monte_carlo_projection_is_ordered_and_reproducible(ws):
    a = S.monte_carlo_projection(ws, n_sims=2000, seed=5)
    b = S.monte_carlo_projection(ws, n_sims=2000, seed=5)
    p = a["percentiles"]
    assert p["p5"] < p["p25"] < p["p50"] < p["p75"] < p["p95"]
    assert a["percentiles"] == b["percentiles"]
    assert sum(a["histogram"]["counts"]) == 2000


def test_factor_scenario_direction(ws):
    down = S.factor_scenario(ws, {"Market": -0.10})
    up = S.factor_scenario(ws, {"Market": 0.10})
    assert down["pnl"] < 0 < up["pnl"]
    with pytest.raises(ValueError):
        S.factor_scenario(ws, {"Weather": 1})


# --------------------------------------------------------------------------- 4. performance
def test_performance_summary(ws):
    s = P.summary(ws)
    assert {m["key"] for m in s["metrics"]} >= {"twr", "mwr", "sharpe", "information_ratio"}
    assert "a year against" in s["plain"]
    # With no cash flows, money-weighted equals annualised time-weighted
    twr = P.P.time_weighted_returns(ws.nav_history)
    mwr = next(m for m in s["metrics"] if m["key"] == "mwr")["value"]
    assert mwr == pytest.approx(P.P.annualised_return(twr), abs=0.003)


def test_attribution_reconciles(ws):
    a = P.attribution(ws)
    assert sum(a["totals"][k] for k in ("allocation", "selection", "interaction")) == pytest.approx(a["active_return"])


def test_growth_series_and_contributors(ws):
    g = P.growth_series(ws)
    assert list(g.columns) == ["Portfolio", "Benchmark"]
    c = P.contributors(ws)
    assert len(c["pnl"]) == 11 and "added the most" in c["plain"]


# --------------------------------------------------------------------------- 5. compliance
def test_default_rules_find_the_liquidity_breach(ws):
    s = C.default_rulebook().summary(ws)
    assert s["counts"]["breach"] == 1
    assert "take more than 5 days" in s["plain"]


def test_exclusion_and_pretrade_blocking(ws):
    rb = C.default_rulebook()
    res = rb.pre_trade(ws, {"EMB": 2000})
    assert not res["approved"]
    assert any("tobacco" in b["message"] for b in res["new_breaches"])
    assert rb.pre_trade(ws, {"HLX": 10})["approved"]


def test_warning_band_and_rules_from_data(ws):
    rb = C.RuleBook.from_dicts([
        {"id": "A", "type": "max_position_weight", "limit": 0.205},
        {"id": "B", "type": "min_cash", "limit": 0.05},
    ])
    res = {(r.rule_id, r.subject): r.status for r in rb.evaluate(ws)}
    assert res[("A", "US Treasury 4.25% 2036")] == "warning"
    assert res[("B", "Cash")] == "breach"
    assert rb.log  # non-pass results are logged with a timestamp
    with pytest.raises(ValueError):
        C.RuleBook.from_dicts([{"id": "X", "type": "astrology", "limit": 1}])


# --------------------------------------------------------------------------- 6. trading
def test_rebalance_to_current_weights_needs_no_trades(ws):
    plan = T.rebalance(ws, ws.weights.to_dict(), min_trade_value=5_000)
    assert plan["trades"] == []
    with pytest.raises(ValueError):
        T.rebalance(ws, {"NWT": 0.9, "HLX": 0.2})


@pytest.mark.parametrize("method", ["min_variance", "max_sharpe", "risk_parity", "mean_variance"])
def test_optimiser_respects_constraints(ws, method):
    o = T.optimise(ws, method, max_weight=0.2, min_weight=0.01)
    w = pd.Series(o["weights"])
    assert w.sum() == pytest.approx(1 - ws.cash_weight, abs=1e-6)
    assert w.max() <= 0.2 + 1e-6 and w.min() >= 0.01 - 1e-6


def test_min_variance_lowers_risk_and_risk_parity_equalises(ws):
    mv = T.optimise(ws, "min_variance")
    assert mv["proposed"]["volatility"] < mv["current"]["volatility"]
    rp = pd.Series(T.optimise(ws, "risk_parity", max_weight=1.0)["weights"])
    cov = ws.asset_returns.cov() * 252
    pct = R.risk_contributions(cov, rp)["percent"]
    assert pct.max() - pct.min() < 0.01


def test_blotter_lifecycle_and_apply_trades(ws):
    b = T.Blotter()
    o = b.add(T.Order("NWT", "BUY", 10))
    assert b.open_orders() == [o]
    b.fill(o.id, 100.0)
    with pytest.raises(ValueError):
        b.cancel(o.id)
    with pytest.raises(ValueError):
        T.Order("NWT", "HOLD", 1)
    after = T.apply_trades(ws, {"NWT": 100})
    spent = 100 * ws.market.latest_prices()["NWT"]
    assert after.portfolio.cash == pytest.approx(ws.portfolio.cash - spent)
    assert after.nav == pytest.approx(ws.nav)  # buying with cash leaves NAV unchanged


def test_trading_costs_grow_faster_than_size(ws):
    small = T.estimate_cost(ws, "CORP5", 100) / 100
    big = T.estimate_cost(ws, "CORP5", 10_000) / 10_000
    assert big > small


# --------------------------------------------------------------------------- 7. private assets
def test_private_fund_metrics():
    f = PA.sample_funds()[0]
    m = PA.fund_metrics(f)
    assert m["tvpi"]["value"] == pytest.approx(m["dpi"]["value"] + m["rvpi"]["value"])
    assert m["paid_in"] == pytest.approx(1_330_000)
    assert m["nav"] == pytest.approx(1_105_000)  # last statement is the latest event, nothing to roll forward
    assert 0 < m["irr"]["value"] < 0.5


def test_nav_roll_forward_and_irr_on_simple_fund():
    from datetime import date

    f = PA.PrivateFund("T", "Test", "Private Equity", 2024, 100,
                       cashflows=[PA.CashFlow(date(2024, 1, 1), "call", 100),
                                  PA.CashFlow(date(2025, 1, 1), "distribution", 20)],
                       nav_reports=[(date(2024, 12, 31), 110)])
    assert f.current_nav() == pytest.approx(90)
    m = PA.fund_metrics(f, as_of=date(2025, 1, 1))
    assert m["tvpi"]["value"] == pytest.approx(1.10)
    assert m["irr"]["value"] == pytest.approx(0.10, abs=0.002)


def test_pme_equals_one_when_fund_matches_index():
    from datetime import date

    idx = pd.Series([100.0, 110.0, 121.0], index=pd.to_datetime(["2024-01-01", "2025-01-01", "2026-01-01"]))
    f = PA.PrivateFund("T", "Index twin", "Private Equity", 2024, 100,
                       cashflows=[PA.CashFlow(date(2024, 1, 1), "call", 100)],
                       nav_reports=[(date(2026, 1, 1), 121.0)])
    assert PA.kaplan_schoar_pme(f, idx) == pytest.approx(1.0)


def test_forecast_draws_down_commitment(ws):
    fc = PA.forecast(PA.sample_funds()[1], years=5, start_year=2026)
    assert (fc["unfunded"].diff().dropna() <= 0).all()
    view = PA.combined_view(ws, PA.sample_funds())
    assert sum(view["allocation"].values()) == pytest.approx(1.0)


# --------------------------------------------------------------------------- explainer
@pytest.mark.parametrize("question,intent", [
    ("How much could I lose?", "downside"),
    ("Where is my risk coming from?", "risk_sources"),
    ("What if equities fall 20%?", "what_if"),
    ("How am I doing against the benchmark?", "performance"),
    ("Why did the portfolio move today?", "why_moved"),
    ("How liquid is the portfolio?", "liquidity"),
    ("Am I breaking any rules?", "compliance"),
    ("What does Sharpe ratio mean?", "define"),
    ("How exposed am I to interest rates?", "rates"),
])
def test_explainer_routes_questions(ws, question, intent):
    a = Explainer(ws).ask(question)
    assert a["intent"] == intent and len(a["answer"]) > 20


def test_explainer_parses_numbers_into_a_scenario(ws):
    a = Explainer(ws).ask("what if stocks drop 10% and rates rise 50 bps")
    assert a["shocks"] == {"equity": -0.10, "rates": 0.005}
    expected = S.custom(ws, equity=-0.10, rates=0.005)["pnl"]
    assert a["sources"][0]["value"] == pytest.approx(expected)


def test_explainer_falls_back_politely_and_uses_llm_when_given(ws):
    assert Explainer(ws).ask("tell me a joke")["intent"] == "unknown"

    class Fake:
        def complete(self, system, prompt):
            assert "Answer ONLY from the JSON facts" in system and '"nav"' in prompt
            return "grounded answer"

    assert Explainer(ws, llm=Fake()).ask("tell me a joke")["answer"] == "grounded answer"


def test_commentary_and_anomalies(ws):
    ex = Explainer(ws)
    assert ex.commentary().startswith("The portfolio ")
    assert any(f["type"] == "compliance" for f in ex.anomalies())


# --------------------------------------------------------------------------- 8. reports
def test_reports_are_written(ws, tmp_path):
    pdf = reports.to_pdf(ws, tmp_path / "r.pdf", funds=PA.sample_funds())
    xlsx = reports.to_excel(ws, tmp_path / "r.xlsx", funds=PA.sample_funds())
    assert open(pdf, "rb").read(5) == b"%PDF-"
    sheets = load_workbook(xlsx).sheetnames
    assert {"Summary", "Holdings", "Risk", "Scenarios", "Compliance", "Private assets"} <= set(sheets)
