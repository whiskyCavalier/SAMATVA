"""Analytics maths, checked against hand-worked and textbook values."""

from datetime import date

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from samatva.analytics import bonds as B
from samatva.analytics import performance_math as P
from samatva.analytics import risk as R


# --------------------------------------------------------------------------- bonds
def test_par_bond_prices_at_100():
    assert B.bond_price(0.05, 0.05, 10) == pytest.approx(100.0, abs=1e-9)


def test_textbook_discount_bond_price():
    # 5-year, 5% semi-annual coupon at a 6% yield: 95.7349 (standard textbook value)
    assert B.bond_price(0.06, 0.05, 5) == pytest.approx(95.7349, abs=1e-4)


def test_yield_round_trip():
    p = B.bond_price(0.0437, 0.04, 7.5)
    assert B.bond_yield(p, 0.04, 7.5) == pytest.approx(0.0437, abs=1e-10)


def test_duration_and_dv01_are_consistent():
    y, c, m = 0.045, 0.04, 10
    md = B.modified_duration(y, c, m)
    p = B.bond_price(y, c, m)
    assert B.dv01(y, c, m) == pytest.approx(md * p * 0.0001, rel=1e-4)
    # Zero-like long bond has duration close to its maturity; short bond smaller
    assert B.macaulay_duration(y, 0.0001, 10) == pytest.approx(10, abs=0.01)
    assert B.modified_duration(y, c, 2) < md


def test_convexity_improves_on_duration_approximation():
    y, c, m, dy = 0.04, 0.04, 20, 0.02
    exact = B.price_change_for_yield_shift(y, c, m, dy)
    md, cx = B.modified_duration(y, c, m), B.convexity(y, c, m)
    first = -md * dy
    second = first + 0.5 * cx * dy ** 2
    assert abs(second - exact) < abs(first - exact)


# --------------------------------------------------------------------------- risk
def test_historical_var_and_es_on_hand_worked_sample():
    r = np.array([-0.05, -0.04, -0.03, -0.02, -0.01, 0.0, 0.01, 0.02, 0.03, 0.04] * 2)
    # Sorted, the 20 values start -0.05, -0.05, -0.04, -0.04 … The 10th percentile sits at
    # position 0.1 × 19 = 1.9, so it is -0.05 + 0.9 × 0.01 = -0.041.
    assert R.historical_var(r, 0.90) == pytest.approx(0.041)
    # Only the two -5% days are worse than that threshold, so their average is the ES.
    assert R.historical_es(r, 0.90) == pytest.approx(0.05)


def test_parametric_var_and_es_formulae():
    z = stats.norm.ppf(0.99)
    assert R.parametric_var(0.0, 0.02, 0.99) == pytest.approx(z * 0.02)
    assert R.parametric_es(0.0, 0.01, 0.975) == pytest.approx(0.01 * stats.norm.pdf(stats.norm.ppf(0.975)) / 0.025)


def test_monte_carlo_var_converges_to_parametric():
    rng = np.random.default_rng(0)
    x = pd.DataFrame(rng.normal(0, 0.01, (5000, 2)), columns=["a", "b"])
    sims = R.monte_carlo_returns(x, np.array([0.5, 0.5]), n_sims=200_000, seed=1)
    sd = np.sqrt(np.array([0.5, 0.5]) @ x.cov().values @ np.array([0.5, 0.5]))
    assert R.historical_var(sims, 0.95) == pytest.approx(R.parametric_var(x.mean().mean(), sd, 0.95), rel=0.02)


def test_drawdown_finds_peak_trough_and_recovery():
    idx = pd.bdate_range("2026-01-01", periods=7)
    v = pd.Series([100, 120, 90, 60, 100, 121, 110], index=idx)
    dd = R.drawdown(v)
    assert dd.max_drawdown == pytest.approx(0.5)
    assert dd.peak == idx[1] and dd.trough == idx[3] and dd.recovery == idx[5]
    assert dd.current_drawdown == pytest.approx(1 - 110 / 121)


def test_beta_correlation_and_tracking_error():
    rng = np.random.default_rng(1)
    b = pd.Series(rng.normal(0, 0.01, 500))
    assert R.beta(2 * b, b) == pytest.approx(2.0)
    assert R.correlation(-b, b) == pytest.approx(-1.0)
    assert R.tracking_error(b, b) == pytest.approx(0.0)


def test_risk_contributions_add_up_to_portfolio_volatility():
    cov = pd.DataFrame([[0.04, 0.006], [0.006, 0.01]], index=["a", "b"], columns=["a", "b"])
    w = pd.Series({"a": 0.6, "b": 0.4})
    rc = R.risk_contributions(cov, w)
    vol = np.sqrt(w.values @ cov.values @ w.values)
    assert rc["component"].sum() == pytest.approx(vol)
    assert rc["percent"].sum() == pytest.approx(1.0)


def test_diversification_and_concentration():
    cov = pd.DataFrame([[0.04, 0.04], [0.04, 0.04]], index=["a", "b"], columns=["a", "b"])
    assert R.diversification_ratio(cov, pd.Series({"a": 0.5, "b": 0.5})) == pytest.approx(1.0)
    h, n = R.herfindahl(pd.Series([0.25] * 4))
    assert h == pytest.approx(0.25) and n == pytest.approx(4)


def test_ewma_volatility_of_constant_magnitude_returns():
    r = np.array([0.01, -0.01] * 200)
    assert R.ewma_volatility(r, annualise=False) == pytest.approx(0.01, rel=1e-6)


def test_factor_regression_recovers_known_exposures():
    rng = np.random.default_rng(3)
    f = pd.DataFrame(rng.normal(0, 0.01, (2000, 2)), columns=["Market", "Rates"])
    y = 0.8 * f["Market"] - 3.0 * f["Rates"] + rng.normal(0, 0.001, 2000)
    fit = R.factor_exposures(y, f)
    assert fit.exposures["Market"] == pytest.approx(0.8, abs=0.01)
    assert fit.exposures["Rates"] == pytest.approx(-3.0, abs=0.02)
    assert fit.r_squared > 0.99


def test_days_to_liquidate():
    assert R.days_to_liquidate(10_000, 10_000, 0.2) == pytest.approx(5.0)
    assert R.days_to_liquidate(1, None) == float("inf")


# --------------------------------------------------------------------------- performance maths
def test_time_weighted_return_ignores_cash_flows():
    idx = pd.bdate_range("2026-01-01", periods=3)
    # +10% on day 2, a deposit of 100 at the start of day 3, then +10% again
    values = pd.Series([100.0, 110.0, 231.0], index=idx)
    flows = pd.Series([0.0, 0.0, 100.0], index=idx)
    r = P.time_weighted_returns(values, flows)
    assert r.tolist() == pytest.approx([0.10, 0.10])
    assert P.cumulative_return(r) == pytest.approx(0.21)


def test_xirr_simple_and_two_period():
    assert P.xirr({date(2025, 1, 1): -100, date(2026, 1, 1): 110}) == pytest.approx(0.10, abs=1e-6)
    r = P.xirr({date(2024, 1, 1): -100, date(2024, 12, 31): -100, date(2025, 12, 31): 231})
    assert r == pytest.approx(0.10, abs=2e-3)
    with pytest.raises(ValueError):
        P.xirr({date(2025, 1, 1): 100, date(2026, 1, 1): 110})


def test_ratios():
    r = pd.Series([0.01, -0.005, 0.007, 0.002] * 100)
    assert P.sharpe_ratio(r) == pytest.approx(r.mean() / r.std() * np.sqrt(252))
    assert P.sortino_ratio(r) > P.sharpe_ratio(r)  # fewer and smaller down days than up days
    assert P.information_ratio(r, r) == 0.0
    assert P.annualised_return(pd.Series([0.001] * 252)) == pytest.approx(1.001 ** 252 - 1)


def test_brinson_fachler_effects_sum_to_active_return():
    wp = pd.Series({"Equity": 0.7, "Bonds": 0.3})
    rp = pd.Series({"Equity": 0.12, "Bonds": 0.02})
    wb = pd.Series({"Equity": 0.6, "Bonds": 0.4})
    rb = pd.Series({"Equity": 0.10, "Bonds": 0.03})
    t = P.brinson_fachler(wp, rp, wb, rb)
    active = (wp * rp).sum() - (wb * rb).sum()
    assert t["total"].sum() == pytest.approx(active)
    # Overweighting the asset class that beat the benchmark adds value
    assert t.loc["Equity", "allocation"] > 0


def test_period_returns_keys():
    idx = pd.bdate_range(end="2026-10-23", periods=800)
    pr = P.period_returns(pd.Series(0.0005, index=idx))
    assert set(pr) == {"MTD", "QTD", "YTD", "1Y", "3Y (ann.)", "Since inception (ann.)"}
    assert pr["1Y"] == pytest.approx(1.0005 ** len(idx[idx > idx[-1] - pd.DateOffset(years=1)]) - 1)
