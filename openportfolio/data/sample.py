"""A realistic, fully synthetic sample universe.

The companies here are fictional and the prices are simulated. They exist so
that anyone can run the platform within minutes, with no data licence, and see
every module working on sensible numbers.

The simulation is a simple factor model: each day, a handful of common drivers
(the market, company size, value, momentum and interest rates) move, and each
instrument responds according to its sensitivities plus its own noise. Bonds
are priced from a simulated yield, so their behaviour matches the bond maths.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from ..analytics.bonds import bond_price
from .market_data import MarketData
from .models import Instrument, Portfolio, Position
from .security_master import SecurityMaster

FACTOR_NAMES = ["Market", "Size", "Value", "Momentum", "Rates"]
# Annualised volatility of each factor (Rates is a daily change in yield, in decimal)
_FACTOR_VOL = np.array([0.16, 0.08, 0.07, 0.09, 0.009])
_FACTOR_DRIFT = np.array([0.07, 0.01, 0.01, 0.02, 0.0])


def sample_instruments() -> list[Instrument]:
    em_lookthrough = {
        "country": {"China": 0.27, "India": 0.20, "Taiwan": 0.18, "Korea": 0.11, "Brazil": 0.06, "Other EM": 0.18},
        "sector": {"Technology": 0.30, "Financials": 0.22, "Consumer": 0.14, "Energy": 0.06,
                   "Industrials": 0.08, "Other": 0.20},
    }
    return [
        Instrument("NWT", "Northwind Technologies", "Equity", "Technology", "United States", "USD",
                   ticker="NWT", isin="US0000000NWT", avg_daily_volume=4_000_000),
        Instrument("HLX", "Helix Health", "Equity", "Healthcare", "United States", "USD",
                   ticker="HLX", isin="US0000000HLX", avg_daily_volume=2_500_000),
        Instrument("ORB", "Orbital Energy", "Equity", "Energy", "United States", "USD",
                   ticker="ORB", isin="US0000000ORB", avg_daily_volume=3_000_000, tags=["fossil-fuels"]),
        Instrument("KST", "Kestrel Industrials", "Equity", "Industrials", "Germany", "EUR",
                   ticker="KST", isin="DE0000000KST", avg_daily_volume=900_000),
        Instrument("SAK", "Sakura Consumer", "Equity", "Consumer", "Japan", "JPY",
                   ticker="SAK", isin="JP0000000SAK", avg_daily_volume=1_200_000),
        Instrument("EMF", "Emerging Markets Equity ETF", "Equity", "Diversified", "Emerging Markets", "USD",
                   ticker="EMF", isin="US0000000EMF", avg_daily_volume=6_000_000, lookthrough=em_lookthrough),
        Instrument("UST10", "US Treasury 4.25% 2036", "Fixed Income", "Government", "United States", "USD",
                   isin="US0000UST10", coupon=0.0425, maturity_years=10, avg_daily_volume=5_000_000),
        Instrument("CORP5", "Atlas Corp 5.10% 2031", "Fixed Income", "Corporate", "United States", "USD",
                   isin="US000CORP05", coupon=0.051, maturity_years=5, avg_daily_volume=8_000),
        Instrument("BUND8", "German Bund 2.50% 2034", "Fixed Income", "Government", "Germany", "EUR",
                   isin="DE000BUND08", coupon=0.025, maturity_years=8, avg_daily_volume=3_000_000),
        Instrument("GLDX", "Gold Bullion ETF", "Commodity", "Commodities", "Global", "USD",
                   ticker="GLDX", isin="US000000GLDX", avg_daily_volume=5_000_000),
        Instrument("HRB", "Harbour REIT", "Real Estate", "Real Estate", "United States", "USD",
                   ticker="HRB", isin="US0000000HRB", avg_daily_volume=40_000),
        # Held by nobody in the sample portfolio; used to show ESG screening in compliance.
        Instrument("EMB", "Ember Tobacco", "Equity", "Consumer", "United Kingdom", "GBP",
                   ticker="EMB", isin="GB0000000EMB", avg_daily_volume=1_000_000, tags=["tobacco"]),
    ]


# Factor sensitivities: Market, Size, Value, Momentum, Rates(per 1.00 yield change)
_EQUITY_SPEC = {
    #        start   alpha  idio_vol  betas
    "NWT": (180.0, 0.03, 0.22, [1.25, -0.2, -0.5, 0.4, -3.0]),
    "HLX": (95.0, 0.01, 0.16, [0.80, 0.0, 0.1, 0.1, -1.0]),
    "ORB": (62.0, 0.00, 0.20, [1.05, 0.1, 0.6, -0.1, 1.0]),
    "KST": (48.0, 0.00, 0.17, [1.10, 0.3, 0.3, 0.0, 0.0]),
    "SAK": (2400.0, 0.00, 0.15, [0.85, 0.2, 0.2, 0.0, 0.0]),
    "EMF": (41.0, -0.01, 0.12, [1.15, 0.2, 0.2, 0.0, -1.0]),
    "GLDX": (190.0, 0.02, 0.13, [0.10, 0.0, 0.0, 0.0, -2.0]),
    "HRB": (35.0, 0.00, 0.15, [0.90, 0.3, 0.3, -0.1, -6.0]),
    "EMB": (28.0, 0.00, 0.14, [0.60, 0.0, 0.5, 0.0, 0.0]),
}
_BOND_SPEC = {  # starting yield, spread vol (annual, decimal), sensitivity to the Rates factor
    "UST10": (0.0430, 0.000, 1.0),
    "CORP5": (0.0520, 0.004, 0.9),
    "BUND8": (0.0260, 0.002, 0.7),
}


def generate_market_data(days: int = 1600, end: str = "2026-10-23", seed: int = 4) -> MarketData:
    """Simulate `days` business days of prices, FX, factors and a benchmark."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=end, periods=days)
    dt = 1 / 252

    # Factors: mild correlation between Market and Rates, the rest independent
    corr = np.eye(5)
    corr[0, 4] = corr[4, 0] = -0.2
    vol_d = _FACTOR_VOL * np.sqrt(dt)
    cov = np.outer(vol_d, vol_d) * corr
    shocks = rng.multivariate_normal(_FACTOR_DRIFT * dt, cov, size=days)
    # Fat tails: occasionally scale a whole day up (a "stress day")
    stress = rng.random(days) < 0.02
    shocks[stress] *= 3.0
    factors = pd.DataFrame(shocks, index=dates, columns=FACTOR_NAMES)

    prices = {}
    for iid, (start, alpha, idio, betas) in _EQUITY_SPEC.items():
        r = alpha * dt + shocks @ np.array(betas) + rng.normal(0, idio * np.sqrt(dt), days)
        prices[iid] = start * np.exp(np.cumsum(r))

    yields = {}
    insts = {i.id: i for i in sample_instruments()}
    level = np.cumsum(shocks[:, 4])
    for iid, (y0, spread_vol, sens) in _BOND_SPEC.items():
        spread = np.cumsum(rng.normal(0, spread_vol * np.sqrt(dt), days))
        y = np.clip(y0 + sens * level + spread, 0.001, 0.2)
        inst = insts[iid]
        prices[iid] = np.array([bond_price(v, inst.coupon, inst.maturity_years) for v in y])
        yields[iid] = float(y[-1])

    price_df = pd.DataFrame(prices, index=dates)

    fx = pd.DataFrame(index=dates)
    fx["USD"] = 1.0
    fx["EUR"] = 1.08 * np.exp(np.cumsum(rng.normal(0, 0.07 * np.sqrt(dt), days)))
    fx["JPY"] = 0.0068 * np.exp(np.cumsum(rng.normal(0, 0.09 * np.sqrt(dt), days)))
    fx["GBP"] = 1.27 * np.exp(np.cumsum(rng.normal(0, 0.08 * np.sqrt(dt), days)))

    # Benchmark: 60% global equity (market factor) + 40% government bonds
    bond_ret = price_df["UST10"].pct_change(fill_method=None).fillna(0).values
    eq_ret = factors["Market"].values
    bench = 100 * np.cumprod(1 + 0.6 * eq_ret + 0.4 * bond_ret)
    benchmark = pd.Series(bench, index=dates, name="60/40 Benchmark")

    segments = {
        "Equity": (0.6, pd.Series(eq_ret, index=dates).iloc[1:]),
        "Fixed Income": (0.4, pd.Series(bond_ret, index=dates).iloc[1:]),
    }
    return MarketData(price_df, fx, benchmark, factors, risk_free_rate=0.04, yields=yields,
                      benchmark_segments=segments)


TARGET_WEIGHTS = {
    "NWT": 0.10, "HLX": 0.07, "ORB": 0.05, "KST": 0.07, "SAK": 0.06, "EMF": 0.09,
    "HRB": 0.04, "GLDX": 0.04, "UST10": 0.20, "CORP5": 0.13, "BUND8": 0.12,
}
SAMPLE_NAV = 10_000_000.0
SAMPLE_CASH_WEIGHT = 0.03


def sample_portfolio(market: MarketData | None = None, nav: float = SAMPLE_NAV) -> Portfolio:
    """A balanced multi-asset portfolio worth about $10 million on the sample date.

    Quantities are worked out from target weights and the latest prices, then
    rounded to whole units, so the portfolio looks as a real one would. The
    cost price of each holding is its price one year earlier.
    """
    market = market or generate_market_data()
    latest = market.latest_prices()
    year_ago = market.prices.iloc[-253]
    insts = {i.id: i for i in sample_instruments()}
    positions = []
    for iid, w in TARGET_WEIGHTS.items():
        ccy = insts[iid].currency
        fx = market.fx_rate(ccy)
        qty = w * nav / (latest[iid] * fx)
        qty = round(qty, -2) if insts[iid].asset_class == "Fixed Income" else round(qty)
        positions.append(Position(iid, float(qty), round(float(year_ago[iid]), 2)))
    return Portfolio(
        name="Global Balanced Fund",
        base_currency="USD",
        benchmark="60/40 Benchmark",
        cash=round(SAMPLE_CASH_WEIGHT * nav, 2),
        positions=positions,
    )


def sample_universe(seed: int = 4) -> Tuple[SecurityMaster, MarketData, Portfolio]:
    """Everything needed to run the platform: instruments, market data and a portfolio."""
    market = generate_market_data(seed=seed)
    return SecurityMaster(sample_instruments()), market, sample_portfolio(market)
