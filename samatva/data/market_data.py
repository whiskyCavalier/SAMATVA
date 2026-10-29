"""Market data container.

Prices are stored in each instrument's local currency. FX rates are stored as
"base currency per one unit of the foreign currency" (for a USD base, EUR at
1.08 means one euro buys 1.08 dollars). Converting to the base currency is
therefore a simple multiplication.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class MarketData:
    prices: pd.DataFrame  # index: dates, columns: instrument ids (local currency)
    fx: pd.DataFrame  # index: dates, columns: currency codes, base per unit
    benchmark: Optional[pd.Series] = None  # benchmark level series in base currency
    factors: Optional[pd.DataFrame] = None  # daily factor returns
    risk_free_rate: float = 0.04  # annual, continuously quoted
    yields: Dict[str, float] = field(default_factory=dict)  # bond id -> yield to maturity
    base_currency: str = "USD"
    # Benchmark broken into segments for attribution: {"Equity": (0.6, daily returns), ...}
    benchmark_segments: Dict[str, Tuple[float, pd.Series]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.prices = self.prices.sort_index()
        self.fx = self.fx.reindex(self.prices.index).ffill().bfill()
        if self.base_currency not in self.fx.columns:
            self.fx[self.base_currency] = 1.0

    # ------------------------------------------------------------------ prices
    @property
    def as_of(self) -> pd.Timestamp:
        return self.prices.index[-1]

    def latest_prices(self) -> pd.Series:
        return self.prices.ffill().iloc[-1]

    def fx_rate(self, currency: str, date: Optional[pd.Timestamp] = None) -> float:
        if currency not in self.fx.columns:
            raise KeyError(f"No FX rate for {currency}. Add a column '{currency}' to the FX table.")
        row = self.fx.loc[date] if date is not None else self.fx.iloc[-1]
        return float(row[currency])

    def base_prices(self, currencies: Dict[str, str]) -> pd.DataFrame:
        """Prices converted into the base currency, column by column."""
        out = self.prices.copy()
        for col in out.columns:
            ccy = currencies.get(col, self.base_currency)
            out[col] = out[col] * self.fx[ccy]
        return out

    def returns(self, currencies: Optional[Dict[str, str]] = None, log: bool = False) -> pd.DataFrame:
        px = self.base_prices(currencies) if currencies else self.prices
        px = px.ffill()
        r = np.log(px / px.shift(1)) if log else px.pct_change(fill_method=None)
        return r.iloc[1:]

    def benchmark_returns(self) -> Optional[pd.Series]:
        if self.benchmark is None:
            return None
        return self.benchmark.pct_change(fill_method=None).iloc[1:]

    def with_prices(self, prices: pd.DataFrame) -> "MarketData":
        """Return a copy with a different price table (used by scenarios)."""
        return MarketData(prices, self.fx.copy(), self.benchmark, self.factors, self.risk_free_rate,
                          dict(self.yields), self.base_currency, dict(self.benchmark_segments))
