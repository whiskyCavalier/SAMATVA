"""Performance mathematics: returns, ratios and attribution.

* Time-weighted return (TWR) measures the manager's skill, ignoring when money
  was added or withdrawn. It is the industry standard (GIPS) for comparing
  managers.
* Money-weighted return (MWR, an internal rate of return) measures the
  investor's own experience, including the timing of their contributions.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, Optional

import numpy as np
import pandas as pd
from scipy.optimize import brentq

TRADING_DAYS = 252


def time_weighted_returns(values: pd.Series, flows: Optional[pd.Series] = None) -> pd.Series:
    """Daily time-weighted returns.

    `values` is the end-of-day portfolio value; `flows` holds external cash
    flows on each day (positive = money in), assumed to arrive at the start of
    the day so that they earn that day's return.
    """
    v = values.astype(float)
    f = (flows.reindex(v.index).fillna(0.0) if flows is not None else pd.Series(0.0, index=v.index))
    prev = v.shift(1)
    # Start-of-day flows: the day's base is yesterday's value plus today's flow
    r = (v - prev - f) / (prev + f)
    return r.iloc[1:]


def cumulative_return(returns: pd.Series) -> float:
    return float((1 + returns.dropna()).prod() - 1)


def annualised_return(returns: pd.Series, periods: int = TRADING_DAYS) -> float:
    r = returns.dropna()
    if len(r) == 0:
        return 0.0
    growth = (1 + r).prod()
    return float(growth ** (periods / len(r)) - 1)


def xirr(cashflows: Dict[date, float]) -> float:
    """Annualised internal rate of return for dated cash flows.

    Investor convention: contributions are negative, withdrawals and the final
    value are positive.
    """
    items = sorted((pd.Timestamp(d), float(v)) for d, v in cashflows.items())
    t0 = items[0][0]
    years = np.array([(d - t0).days / 365.0 for d, _ in items])
    amounts = np.array([v for _, v in items])
    if not (amounts > 0).any() or not (amounts < 0).any():
        raise ValueError("IRR needs at least one negative and one positive cash flow.")
    npv = lambda r: float((amounts / (1 + r) ** years).sum())  # noqa: E731
    return brentq(npv, -0.9999, 100.0, xtol=1e-10)


def money_weighted_return(values: pd.Series, flows: Optional[pd.Series] = None) -> float:
    """IRR of the portfolio: start value invested, flows added, end value received."""
    cf: Dict[date, float] = {values.index[0]: -float(values.iloc[0])}
    if flows is not None:
        for d, amt in flows.items():
            if amt and d != values.index[0]:
                cf[d] = cf.get(d, 0.0) - float(amt)
    cf[values.index[-1]] = cf.get(values.index[-1], 0.0) + float(values.iloc[-1])
    return xirr(cf)


def sharpe_ratio(returns: pd.Series, risk_free_annual: float = 0.0) -> float:
    r = returns.dropna()
    excess = r - risk_free_annual / TRADING_DAYS
    sd = excess.std(ddof=1)
    return float(excess.mean() / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else 0.0


def sortino_ratio(returns: pd.Series, risk_free_annual: float = 0.0) -> float:
    """Like Sharpe, but only penalises downside swings."""
    r = returns.dropna()
    excess = r - risk_free_annual / TRADING_DAYS
    downside = np.sqrt((np.minimum(excess, 0) ** 2).mean())
    return float(excess.mean() / downside * np.sqrt(TRADING_DAYS)) if downside > 0 else 0.0


def information_ratio(returns: pd.Series, benchmark: pd.Series) -> float:
    df = pd.concat([returns, benchmark], axis=1, join="inner").dropna()
    active = df.iloc[:, 0] - df.iloc[:, 1]
    sd = active.std(ddof=1)
    return float(active.mean() / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else 0.0


def calmar_ratio(returns: pd.Series) -> float:
    values = (1 + returns.dropna()).cumprod()
    mdd = float(-(values / values.cummax() - 1).min())
    return annualised_return(returns) / mdd if mdd > 0 else 0.0


def period_returns(returns: pd.Series) -> Dict[str, Optional[float]]:
    """Month-to-date, quarter-to-date, year-to-date, 1-year, 3-year and since inception."""
    r = returns.dropna()
    end = r.index[-1]
    out: Dict[str, Optional[float]] = {}
    starts = {
        "MTD": end.replace(day=1),
        "QTD": pd.Timestamp(end.year, 3 * ((end.month - 1) // 3) + 1, 1),
        "YTD": pd.Timestamp(end.year, 1, 1),
    }
    for k, s in starts.items():
        out[k] = cumulative_return(r[r.index >= s])
    for label, years in (("1Y", 1), ("3Y (ann.)", 3)):
        s = end - pd.DateOffset(years=years)
        if r.index[0] <= s + pd.Timedelta(days=7):
            sub = r[r.index > s]
            out[label] = cumulative_return(sub) if years == 1 else annualised_return(sub)
        else:
            out[label] = None
    out["Since inception (ann.)"] = annualised_return(r)
    return out


# --------------------------------------------------------------------------- attribution
def brinson_fachler(port_weights: pd.Series, port_returns: pd.Series,
                    bench_weights: pd.Series, bench_returns: pd.Series) -> pd.DataFrame:
    """Single-period Brinson-Fachler attribution by segment (e.g. asset class).

    allocation  = (wp - wb) × (rb_segment - rb_total)   did we over-weight the right areas?
    selection   = wb × (rp_segment - rb_segment)          did we pick better holdings within them?
    interaction = (wp - wb) × (rp_segment - rb_segment)   the combined effect
    The three effects sum exactly to portfolio return minus benchmark return.
    """
    segs = sorted(set(port_weights.index) | set(bench_weights.index))
    wp = port_weights.reindex(segs).fillna(0.0)
    wb = bench_weights.reindex(segs).fillna(0.0)
    rp = port_returns.reindex(segs).fillna(0.0)
    rb = bench_returns.reindex(segs).fillna(0.0)
    # A segment the portfolio does not hold earns the benchmark return (no selection effect)
    rp = rp.where(wp != 0, rb)
    rb_total = float((wb * rb).sum())
    alloc = (wp - wb) * (rb - rb_total)
    select = wb * (rp - rb)
    inter = (wp - wb) * (rp - rb)
    df = pd.DataFrame({"portfolio_weight": wp, "benchmark_weight": wb, "portfolio_return": rp,
                       "benchmark_return": rb, "allocation": alloc, "selection": select,
                       "interaction": inter})
    df["total"] = df[["allocation", "selection", "interaction"]].sum(axis=1)
    return df
