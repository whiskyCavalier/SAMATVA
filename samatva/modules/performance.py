"""Module 4 — Performance.

The simple question it answers: *am I doing better than my benchmark, and why?*

By default the track record is a back-cast: today's holdings valued through
history. Pass a real value series (and any cash flows) to measure an actual
track record instead.
"""

from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

from ..analytics import performance_math as P
from ..analytics import risk as R
from ..glossary import format_value, metric
from ..workspace import Workspace


def _track(ws: Workspace, values: Optional[pd.Series], flows: Optional[pd.Series]):
    values = ws.nav_history if values is None else values
    return values, P.time_weighted_returns(values, flows)


def summary(ws: Workspace, values: Optional[pd.Series] = None, flows: Optional[pd.Series] = None) -> Dict:
    values, r = _track(ws, values, flows)
    rf = ws.market.risk_free_rate
    bench = ws.benchmark_returns
    out = {
        "period_returns": P.period_returns(r),
        "metrics": [
            metric("twr", P.cumulative_return(r)).as_dict(),
            metric("mwr", P.money_weighted_return(values, flows)).as_dict(),
            metric("volatility", R.volatility(r)).as_dict(),
            metric("sharpe", P.sharpe_ratio(r, rf)).as_dict(),
            metric("sortino", P.sortino_ratio(r, rf)).as_dict(),
            metric("max_drawdown", R.drawdown(values).max_drawdown).as_dict(),
        ],
        "start": str(values.index[0].date()),
        "end": str(values.index[-1].date()),
    }
    if bench is not None:
        b = bench.reindex(r.index).dropna()
        out["benchmark_period_returns"] = P.period_returns(b)
        out["metrics"].append(metric("information_ratio", P.information_ratio(r, b)).as_dict())
        out["metrics"].append(metric("beta", R.beta(r, b)).as_dict())
        ann_p, ann_b = P.annualised_return(r), P.annualised_return(b)
        out["active_return_annual"] = ann_p - ann_b
        out["plain"] = (f"Since {out['start']} the portfolio has returned {ann_p:.1%} a year against "
                        f"{ann_b:.1%} for the benchmark, {'ahead' if ann_p >= ann_b else 'behind'} by "
                        f"{abs(ann_p - ann_b):.1%} a year, with a Sharpe ratio of {P.sharpe_ratio(r, rf):.2f}.")
    return out


def growth_series(ws: Workspace, values: Optional[pd.Series] = None, flows: Optional[pd.Series] = None) -> pd.DataFrame:
    """Growth of 100 for the portfolio and its benchmark, for charting."""
    _, r = _track(ws, values, flows)
    df = pd.DataFrame({"Portfolio": 100 * (1 + r).cumprod()})
    if ws.benchmark_returns is not None:
        df["Benchmark"] = 100 * (1 + ws.benchmark_returns.reindex(r.index).fillna(0)).cumprod()
    return df


def rolling(ws: Workspace, window: int = 63) -> pd.DataFrame:
    """Rolling return and volatility over roughly a quarter (63 trading days)."""
    _, r = _track(ws, None, None)
    return pd.DataFrame({
        "rolling_return": (1 + r).rolling(window).apply(lambda x: x.prod() - 1, raw=True),
        "rolling_vol": r.rolling(window).std() * (252 ** 0.5),
    }).dropna()


def attribution(ws: Workspace, start: Optional[str] = None, end: Optional[str] = None) -> Dict:
    """Brinson-Fachler attribution by asset class over the period.

    Explains the gap to the benchmark as: being in the right asset classes
    (allocation), picking good holdings within them (selection), and the
    combination of the two (interaction).
    """
    segs = ws.market.benchmark_segments
    if not segs:
        raise ValueError("Attribution needs the benchmark broken into segments (MarketData.benchmark_segments).")
    px = ws.base_prices[ws.held_ids].ffill()
    end_ts = pd.Timestamp(end) if end else px.index[-1]
    start_ts = pd.Timestamp(start) if start else end_ts - pd.DateOffset(years=1)
    p0 = px.loc[px.index >= start_ts].iloc[0]
    p1 = px.loc[px.index <= end_ts].iloc[-1]
    q = pd.Series(ws.portfolio.quantities())
    v0 = p0 * q
    v1 = p1 * q
    classes = pd.Series({iid: ws.master.get(iid).asset_class for iid in q.index})
    total0 = v0.sum() + ws.portfolio.cash
    wp = v0.groupby(classes).sum() / total0
    rp = v1.groupby(classes).sum() / v0.groupby(classes).sum() - 1
    if ws.portfolio.cash:
        days = len(px.loc[start_ts:end_ts]) - 1
        wp["Cash"] = ws.portfolio.cash / total0
        rp["Cash"] = (1 + ws.market.risk_free_rate / 252) ** days - 1
    wb = pd.Series({k: w for k, (w, _) in segs.items()})
    rb = pd.Series({k: float((1 + s.loc[start_ts:end_ts].iloc[1:]).prod() - 1) for k, (_, s) in segs.items()})
    table = P.brinson_fachler(wp, rp, wb, rb)
    port_ret = float((wp * rp).sum())
    bench_ret = float((wb * rb).sum())
    totals = table[["allocation", "selection", "interaction", "total"]].sum()
    best = table["total"].idxmax()
    return {
        "start": str(start_ts.date()), "end": str(end_ts.date()),
        "portfolio_return": port_ret, "benchmark_return": bench_ret, "active_return": port_ret - bench_ret,
        "table": table.reset_index().rename(columns={"index": "segment"}).to_dict(orient="records"),
        "totals": totals.to_dict(),
        "plain": (f"Over the period the portfolio returned {port_ret:.1%} against {bench_ret:.1%} for the benchmark. "
                  f"Asset-class choices contributed {totals['allocation']:+.1%}, holding selection "
                  f"{totals['selection']:+.1%} and their interaction {totals['interaction']:+.1%}. "
                  f"The biggest single contributor was {best} ({table.loc[best, 'total']:+.1%})."),
    }


def factor_attribution(ws: Workspace, days: int = 252) -> Dict:
    """How much of the recent return came from each risk factor, and how much was left over (skill or luck)."""
    factors = ws.market.factors
    if factors is None:
        return {}
    r = ws.portfolio_returns.iloc[-days:]
    fit = R.factor_exposures(r, factors)
    f = factors.reindex(r.index).fillna(0)
    contrib = (f * fit.exposures).sum()
    total = float(r.sum())
    resid = total - float(contrib.sum())
    return {"contributions": contrib.to_dict(), "unexplained": resid, "total": total,
            "plain": (f"Of the {total:+.1%} earned over the period (simple sum of daily returns), the factors explain "
                      f"{contrib.sum():+.1%}; the remaining {resid:+.1%} is specific to the holdings chosen.")}


def contributors(ws: Workspace, days: int = 21) -> Dict:
    """Which holdings added or took away the most value recently (in money)."""
    px = ws.base_prices[ws.held_ids].ffill()
    q = pd.Series(ws.portfolio.quantities())
    pnl = (px.iloc[-1] - px.iloc[-days - 1]) * q
    pnl = pnl.sort_values()
    named = {ws.master.get(i).name: float(v) for i, v in pnl.items()}
    top, bottom = pnl.index[-1], pnl.index[0]
    return {"days": days, "pnl": named,
            "plain": (f"Over the last {days} trading days, {ws.master.get(top).name} added the most "
                      f"({format_value(pnl[top], 'money')}) and {ws.master.get(bottom).name} detracted the most "
                      f"({format_value(pnl[bottom], 'money')}).")}
