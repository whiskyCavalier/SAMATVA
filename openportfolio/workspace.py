"""The Workspace: one object that ties a portfolio to its data.

Every module receives a Workspace. Because they all read the same valuation,
weights and return history from here, numbers never disagree between screens.
Expensive results are computed once and cached until the inputs change.
"""

from __future__ import annotations

from functools import cached_property
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .data.market_data import MarketData
from .data.models import Portfolio
from .data.security_master import SecurityMaster

CASH = "CASH"


class Workspace:
    def __init__(self, master: SecurityMaster, market: MarketData, portfolio: Portfolio) -> None:
        self.master = master
        self.market = market
        self.portfolio = portfolio
        missing = [p.instrument_id for p in portfolio.positions if p.instrument_id not in master]
        if missing:
            raise KeyError(f"These holdings are not in the security master: {', '.join(missing)}")

    @classmethod
    def sample(cls, seed: int = 4) -> "Workspace":
        from .data.sample import sample_universe

        return cls(*sample_universe(seed))

    def with_portfolio(self, portfolio: Portfolio) -> "Workspace":
        return Workspace(self.master, self.market, portfolio)

    def with_market(self, market: MarketData) -> "Workspace":
        return Workspace(self.master, market, self.portfolio)

    # ------------------------------------------------------------------ basics
    @property
    def held_ids(self) -> list[str]:
        return list(self.portfolio.quantities().keys())

    @cached_property
    def currencies(self) -> Dict[str, str]:
        return {i.id: i.currency for i in self.master}

    @cached_property
    def base_prices(self) -> pd.DataFrame:
        return self.market.base_prices(self.currencies)

    # ------------------------------------------------------------------ valuation
    @cached_property
    def valuation(self) -> pd.DataFrame:
        """One row per holding: what it is, what we hold and what it is worth."""
        q = self.portfolio.quantities()
        latest = self.market.latest_prices()
        rows = []
        costs = {p.instrument_id: p.cost_price for p in self.portfolio.positions}
        for iid, qty in q.items():
            inst = self.master.get(iid)
            px = float(latest[iid])
            fx = self.market.fx_rate(inst.currency)
            mv = qty * px * fx
            cost = costs.get(iid)
            cost_value = qty * cost * fx if cost is not None else np.nan
            rows.append({
                "id": iid, "name": inst.name, "asset_class": inst.asset_class, "sector": inst.sector,
                "country": inst.country, "currency": inst.currency, "quantity": qty, "price": px,
                "fx_rate": fx, "market_value": mv, "cost_value": cost_value,
                "unrealised_pnl": mv - cost_value if cost is not None else np.nan,
            })
        df = pd.DataFrame(rows).set_index("id")
        nav = df["market_value"].sum() + self.portfolio.cash
        df["weight"] = df["market_value"] / nav
        return df.sort_values("market_value", ascending=False)

    @property
    def nav(self) -> float:
        return float(self.valuation["market_value"].sum() + self.portfolio.cash)

    @property
    def cash_weight(self) -> float:
        return self.portfolio.cash / self.nav if self.nav else 0.0

    @property
    def weights(self) -> pd.Series:
        """Weights of each holding as a share of NAV (cash excluded from the series)."""
        return self.valuation["weight"]

    # ------------------------------------------------------------------ history
    @cached_property
    def asset_returns(self) -> pd.DataFrame:
        """Daily returns of each holding, in the base currency."""
        px = self.base_prices[self.held_ids].ffill()
        return px.pct_change(fill_method=None).iloc[1:]

    @cached_property
    def portfolio_returns(self) -> pd.Series:
        """History of daily returns *if today's weights had been held throughout*.

        This "constant-weight" history is the standard input for VaR and risk
        models, because it describes the risk of what you own now.
        """
        w = self.weights.reindex(self.asset_returns.columns).fillna(0.0)
        cash_ret = self.market.risk_free_rate / 252
        r = self.asset_returns.fillna(0.0) @ w + self.cash_weight * cash_ret
        return r.rename("portfolio")

    @cached_property
    def nav_history(self) -> pd.Series:
        """Value through time of the current holdings (a back-cast, not the real track record)."""
        q = pd.Series(self.portfolio.quantities())
        px = self.base_prices[q.index].ffill()
        return (px @ q + self.portfolio.cash).rename("nav")

    @property
    def benchmark_returns(self) -> Optional[pd.Series]:
        return self.market.benchmark_returns()

    def returns_window(self, days: Optional[int] = None) -> pd.Series:
        r = self.portfolio_returns
        return r.iloc[-days:] if days else r
