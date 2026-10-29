"""Risk mathematics.

Pure functions only: they take numbers in and give numbers back, which makes
each one easy to test against hand-worked examples. Conventions:

* Returns are simple daily returns unless stated otherwise.
* Losses are reported as positive numbers (a VaR of 0.02 means a 2% loss).
* "Annualised" uses 252 trading days.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

TRADING_DAYS = 252


# --------------------------------------------------------------------------- volatility
def volatility(returns, annualise: bool = True) -> float:
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    sd = r.std(ddof=1)
    return float(sd * np.sqrt(TRADING_DAYS) if annualise else sd)


def ewma_volatility(returns, lam: float = 0.94, annualise: bool = True) -> float:
    """Exponentially weighted volatility (RiskMetrics): recent days count for more."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    var = r[: min(20, len(r))].var()
    for x in r:
        var = lam * var + (1 - lam) * x * x
    sd = np.sqrt(var)
    return float(sd * np.sqrt(TRADING_DAYS) if annualise else sd)


def ewma_covariance(returns: pd.DataFrame, lam: float = 0.94) -> pd.DataFrame:
    """Exponentially weighted covariance matrix of daily returns."""
    x = returns.dropna().values
    n = len(x)
    w = (1 - lam) * lam ** np.arange(n - 1, -1, -1)
    w = w / w.sum()
    mu = (w[:, None] * x).sum(axis=0)
    xc = x - mu
    cov = (w[:, None] * xc).T @ xc
    return pd.DataFrame(cov, index=returns.columns, columns=returns.columns)


# --------------------------------------------------------------------------- VaR and ES
def historical_var(returns, confidence: float = 0.95) -> float:
    """Loss not exceeded on `confidence` share of past days."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    return float(-np.quantile(r, 1 - confidence, method="linear"))


def historical_es(returns, confidence: float = 0.95) -> float:
    """Average loss on the days worse than the VaR threshold."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    cutoff = np.quantile(r, 1 - confidence, method="linear")
    tail = r[r <= cutoff]
    return float(-tail.mean())


def parametric_var(mean: float, sd: float, confidence: float = 0.95) -> float:
    """Normal-distribution VaR: mean minus z times standard deviation."""
    z = stats.norm.ppf(confidence)
    return float(z * sd - mean)


def parametric_es(mean: float, sd: float, confidence: float = 0.95) -> float:
    z = stats.norm.ppf(confidence)
    return float(sd * stats.norm.pdf(z) / (1 - confidence) - mean)


def monte_carlo_returns(returns: pd.DataFrame, weights: np.ndarray, n_sims: int = 20_000,
                        horizon_days: int = 1, seed: Optional[int] = 42, method: str = "normal") -> np.ndarray:
    """Simulated portfolio returns over the horizon.

    method="normal" draws from a multivariate normal fitted to history.
    method="bootstrap" resamples whole historical days, keeping fat tails and
    the true relationships between assets.
    """
    rng = np.random.default_rng(seed)
    x = returns.dropna().values
    w = np.asarray(weights, dtype=float)
    if method == "bootstrap":
        idx = rng.integers(0, len(x), size=(n_sims, horizon_days))
        daily = x[idx] @ w  # (n_sims, horizon)
        return np.prod(1 + daily, axis=1) - 1
    mu = x.mean(axis=0) * horizon_days
    cov = np.cov(x, rowvar=False) * horizon_days
    sims = rng.multivariate_normal(mu, cov, size=n_sims, method="cholesky")
    return sims @ w


def scale_to_horizon(one_day_value: float, days: int) -> float:
    """Square-root-of-time rule, the industry standard approximation."""
    return float(one_day_value * np.sqrt(days))


# --------------------------------------------------------------------------- drawdown
@dataclass
class Drawdown:
    max_drawdown: float  # positive number, e.g. 0.18 for an 18% fall
    peak: Optional[pd.Timestamp]
    trough: Optional[pd.Timestamp]
    recovery: Optional[pd.Timestamp]  # None if not yet recovered
    current_drawdown: float


def drawdown(values: pd.Series) -> Drawdown:
    v = values.dropna()
    running_max = v.cummax()
    dd = v / running_max - 1
    trough = dd.idxmin()
    mdd = float(-dd.min())
    if mdd == 0:
        return Drawdown(0.0, None, None, None, 0.0)
    peak = v.loc[:trough].idxmax()
    after = v.loc[trough:]
    rec = after[after >= v.loc[peak]]
    recovery = rec.index[0] if len(rec) else None
    return Drawdown(mdd, peak, trough, recovery, float(-dd.iloc[-1]))


def drawdown_series(values: pd.Series) -> pd.Series:
    v = values.dropna()
    return v / v.cummax() - 1


# --------------------------------------------------------------------------- relative risk
def beta(returns, benchmark) -> float:
    a, b = _align(returns, benchmark)
    return float(np.cov(a, b, ddof=1)[0, 1] / np.var(b, ddof=1))


def correlation(returns, benchmark) -> float:
    a, b = _align(returns, benchmark)
    return float(np.corrcoef(a, b)[0, 1])


def tracking_error(returns, benchmark, annualise: bool = True) -> float:
    a, b = _align(returns, benchmark)
    return volatility(a - b, annualise)


def _align(a, b) -> Tuple[np.ndarray, np.ndarray]:
    if isinstance(a, pd.Series) and isinstance(b, pd.Series):
        df = pd.concat([a, b], axis=1, join="inner").dropna()
        return df.iloc[:, 0].values, df.iloc[:, 1].values
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    mask = ~(np.isnan(a) | np.isnan(b))
    return a[mask], b[mask]


# --------------------------------------------------------------------------- contributions
def risk_contributions(cov: pd.DataFrame, weights: pd.Series) -> pd.DataFrame:
    """Split total portfolio volatility into each holding's share.

    Marginal contribution = (Σw)_i / σ_p; component contribution = w_i × marginal.
    Components add up exactly to the portfolio volatility (Euler allocation).
    """
    w = weights.reindex(cov.index).fillna(0).values
    sigma = cov.values
    port_var = float(w @ sigma @ w)
    port_vol = np.sqrt(port_var)
    if port_vol == 0:
        zeros = np.zeros(len(w))
        return pd.DataFrame({"weight": w, "marginal": zeros, "component": zeros, "percent": zeros}, index=cov.index)
    marginal = sigma @ w / port_vol
    component = w * marginal
    out = pd.DataFrame({"weight": w, "marginal": marginal, "component": component,
                        "percent": component / port_vol}, index=cov.index)
    return out


def diversification_ratio(cov: pd.DataFrame, weights: pd.Series) -> float:
    """Weighted average stand-alone volatility divided by portfolio volatility.

    1.0 means no diversification benefit; higher is better.
    """
    w = weights.reindex(cov.index).fillna(0).values
    vols = np.sqrt(np.diag(cov.values))
    port = np.sqrt(w @ cov.values @ w)
    return float((np.abs(w) @ vols) / port) if port > 0 else 1.0


def herfindahl(weights: pd.Series) -> Tuple[float, float]:
    """Concentration: Herfindahl index and the 'effective number of holdings'."""
    w = weights[weights != 0].abs()
    w = w / w.sum()
    h = float((w ** 2).sum())
    return h, 1.0 / h if h > 0 else 0.0


# --------------------------------------------------------------------------- factors
@dataclass
class FactorFit:
    exposures: pd.Series  # betas to each factor
    alpha_annual: float
    r_squared: float
    residual_vol: float  # annualised, the part of risk the factors do not explain


def factor_exposures(returns: pd.Series, factors: pd.DataFrame) -> FactorFit:
    """Ordinary least squares of returns on factor returns, with an intercept."""
    df = pd.concat([returns.rename("y"), factors], axis=1, join="inner").dropna()
    y = df["y"].values
    X = np.column_stack([np.ones(len(df)), df[factors.columns].values])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    fitted = X @ coef
    resid = y - fitted
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - (resid ** 2).sum() / ss_tot if ss_tot > 0 else 0.0
    return FactorFit(pd.Series(coef[1:], index=factors.columns), float(coef[0] * TRADING_DAYS),
                     float(r2), volatility(resid))


# --------------------------------------------------------------------------- liquidity
def days_to_liquidate(quantity: float, avg_daily_volume: Optional[float], participation: float = 0.20) -> float:
    """Trading days needed to sell, trading at most `participation` of daily volume."""
    if not avg_daily_volume:
        return float("inf")
    return float(abs(quantity) / (participation * avg_daily_volume))
