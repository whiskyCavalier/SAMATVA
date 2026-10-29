"""Module 2 — Risk Dashboard.

The simple question it answers: *how much could I lose, and where is the risk
coming from?* Every figure is returned as a Metric with its meaning attached.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..analytics import bonds as B
from ..analytics import risk as R
from ..glossary import format_value, metric
from ..workspace import Workspace


def headline(ws: Workspace, lookback_days: Optional[int] = None) -> List[Dict]:
    """The five-to-ten numbers that matter most, each with its plain meaning."""
    r = ws.returns_window(lookback_days)
    nav = ws.nav
    bench = ws.benchmark_returns
    dd = R.drawdown(ws.nav_history)
    _, eff_n = R.herfindahl(ws.weights)
    cov = ws.asset_returns.cov()
    out = [
        metric("volatility", R.volatility(r)),
        metric("ewma_volatility", R.ewma_volatility(r)),
        metric("var_95", R.historical_var(r, 0.95)),
        metric("var_99", R.historical_var(r, 0.99)),
        metric("es_95", R.historical_es(r, 0.95)),
        metric("max_drawdown", dd.max_drawdown),
        metric("diversification_ratio", R.diversification_ratio(cov, ws.weights)),
        metric("effective_holdings", eff_n),
    ]
    if bench is not None:
        out.insert(6, metric("beta", R.beta(r, bench)))
        out.insert(7, metric("tracking_error", R.tracking_error(r, bench)))
    rows = [m.as_dict() for m in out]
    for row in rows:
        if row["key"] in ("var_95", "var_99", "es_95"):
            row["money"] = row["value"] * nav
    return rows


def var_methods(ws: Workspace, confidence: float = 0.95, horizon_days: int = 1, seed: int = 42) -> Dict:
    """Value at Risk three ways. When they disagree, the tails are behaving unusually."""
    r = ws.portfolio_returns
    rets = ws.asset_returns.fillna(0.0)
    w = ws.weights.reindex(rets.columns).fillna(0.0).values
    mc = R.monte_carlo_returns(rets, w, n_sims=20_000, horizon_days=horizon_days, seed=seed)
    boot = R.monte_carlo_returns(rets, w, n_sims=20_000, horizon_days=horizon_days, seed=seed, method="bootstrap")
    hist_var = R.scale_to_horizon(R.historical_var(r, confidence), horizon_days)
    param_var = R.parametric_var(r.mean() * horizon_days, r.std(ddof=1) * np.sqrt(horizon_days), confidence)
    res = {
        "confidence": confidence,
        "horizon_days": horizon_days,
        "historical": hist_var,
        "parametric": param_var,
        "monte_carlo": R.historical_var(mc, confidence),
        "bootstrap": R.historical_var(boot, confidence),
        "expected_shortfall_historical": R.scale_to_horizon(R.historical_es(r, confidence), horizon_days),
        "expected_shortfall_monte_carlo": R.historical_es(mc, confidence),
    }
    res["money"] = {k: v * ws.nav for k, v in res.items() if isinstance(v, float) and k not in ("confidence",)}
    return res


def risk_contributions(ws: Workspace, ewma: bool = False) -> pd.DataFrame:
    """Which holdings create the risk? Percent contributions sum to 100%."""
    rets = ws.asset_returns.fillna(0.0)
    cov = R.ewma_covariance(rets) if ewma else rets.cov()
    cov = cov * 252
    rc = R.risk_contributions(cov, ws.weights)
    rc["name"] = [ws.master.get(i).name for i in rc.index]
    rc["standalone_vol"] = np.sqrt(np.diag(cov.values))
    return rc.sort_values("percent", ascending=False)


def factor_exposures(ws: Workspace) -> Optional[Dict]:
    factors = ws.market.factors
    if factors is None:
        return None
    fit = R.factor_exposures(ws.portfolio_returns, factors)
    return {
        "exposures": fit.exposures.to_dict(),
        "alpha_annual": fit.alpha_annual,
        "r_squared": metric("r_squared", fit.r_squared).as_dict(),
        "residual_vol": fit.residual_vol,
        "note": ("Market, Size, Value and Momentum exposures read like beta: 0.6 means the portfolio moves about "
                 "0.6% when that factor moves 1%. The Rates exposure reads directly as the % change in value "
                 "for a 1 percentage-point rise in yields (−3 means about −3%)."),
    }


def bond_analytics(ws: Workspace) -> Dict:
    """Duration, convexity and DV01 for each bond, plus portfolio totals."""
    rows = []
    nav = ws.nav
    for iid, v in ws.valuation.iterrows():
        inst = ws.master.get(iid)
        if not inst.is_bond:
            continue
        ytm = ws.market.yields.get(iid)
        if ytm is None:
            ytm = B.bond_yield(v["price"], inst.coupon, inst.maturity_years, inst.coupon_frequency)
        md = B.modified_duration(ytm, inst.coupon, inst.maturity_years, inst.coupon_frequency)
        cx = B.convexity(ytm, inst.coupon, inst.maturity_years, inst.coupon_frequency)
        dv = B.dv01(ytm, inst.coupon, inst.maturity_years, inst.coupon_frequency) * v["quantity"] * v["fx_rate"]
        rows.append({"id": iid, "name": inst.name, "yield": ytm, "modified_duration": md, "convexity": cx,
                     "dv01": dv, "weight": v["weight"], "market_value": v["market_value"]})
    df = pd.DataFrame(rows)
    if df.empty:
        return {"bonds": [], "portfolio_duration": metric("duration", 0.0).as_dict(),
                "dv01": metric("dv01", 0.0).as_dict()}
    port_dur = float((df["weight"] * df["modified_duration"]).sum())
    total_dv01 = float(df["dv01"].sum())
    return {
        "bonds": df.to_dict(orient="records"),
        "portfolio_duration": metric("duration", port_dur).as_dict(),
        "dv01": metric("dv01", total_dv01).as_dict(),
        "plain": (f"If all interest rates rose by 1 percentage point, the bonds would lose about "
                  f"{format_value(total_dv01 * 100, 'money')} ({port_dur / 100:.2%} of the portfolio)."),
    }


def liquidity(ws: Workspace, participation: float = 0.20, horizons=(1, 5, 20)) -> Dict:
    """How quickly could we turn the portfolio into cash without moving prices?"""
    v = ws.valuation
    days = {}
    for iid, row in v.iterrows():
        days[iid] = R.days_to_liquidate(row["quantity"], ws.master.get(iid).avg_daily_volume, participation)
    d = pd.Series(days)
    mv = v["market_value"]
    nav = ws.nav

    def share_within(h: float) -> float:
        sold = (mv * np.minimum(1.0, h / d.replace(0, np.nan)).fillna(1.0)).sum()
        return float((sold + ws.portfolio.cash) / nav)

    lo, hi = 0.0, 1000.0
    if share_within(hi) < 0.9:
        days_90 = float("inf")
    else:
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if share_within(mid) < 0.9 else (lo, mid)
        days_90 = hi
    return {
        "positions": [{"id": i, "name": ws.master.get(i).name, "days": float(x)} for i, x in d.sort_values(ascending=False).items()],
        "profile": {f"{h} day{'s' if h > 1 else ''}": share_within(h) for h in horizons},
        "days_to_liquidate_90": metric("liquidity_days", days_90).as_dict(),
        "participation": participation,
    }


def stress_summary_sentence(ws: Workspace) -> str:
    r = ws.portfolio_returns
    var = R.historical_var(r, 0.95)
    es = R.historical_es(r, 0.95)
    return (f"On a typical bad day (1 in 20) the portfolio could lose about {format_value(var * ws.nav, 'money')}; "
            f"on the very worst days the average loss is closer to {format_value(es * ws.nav, 'money')}.")


def report(ws: Workspace) -> Dict:
    rc = risk_contributions(ws)
    return {
        "headline": headline(ws),
        "summary": stress_summary_sentence(ws),
        "var_methods": var_methods(ws),
        "contributions": rc.reset_index().rename(columns={"index": "id"}).to_dict(orient="records"),
        "factors": factor_exposures(ws),
        "bonds": bond_analytics(ws),
        "liquidity": liquidity(ws),
    }
