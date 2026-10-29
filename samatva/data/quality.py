"""Data quality checks that run before any risk number is produced.

Bad inputs make confident-looking but wrong outputs, which is the worst
outcome in finance. These checks flag the usual culprits in plain language.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np
import pandas as pd

from .market_data import MarketData
from .models import Portfolio
from .security_master import SecurityMaster


@dataclass
class Issue:
    severity: str  # "error" stops the analysis; "warning" is worth a look
    instrument: str
    message: str

    def as_dict(self) -> dict:
        return {"severity": self.severity, "instrument": self.instrument, "message": self.message}


def check(portfolio: Portfolio, master: SecurityMaster, market: MarketData,
          stale_days: int = 5, jump_threshold: float = 0.25) -> List[Issue]:
    issues: List[Issue] = []
    prices = market.prices
    for pos in portfolio.positions:
        iid = pos.instrument_id
        if iid not in master:
            issues.append(Issue("error", iid, "This holding is not in the security master, so it cannot be valued."))
            continue
        inst = master.get(iid)
        if iid not in prices.columns:
            issues.append(Issue("error", iid, f"No prices found for {inst.name}."))
            continue
        s = prices[iid]
        if pd.isna(s.iloc[-1]):
            issues.append(Issue("warning", iid, f"The latest price for {inst.name} is missing; "
                                                 "the last known price is being used."))
        last_valid = s.last_valid_index()
        if last_valid is not None:
            lag = len(prices.loc[last_valid:]) - 1
            if lag >= stale_days:
                issues.append(Issue("warning", iid, f"{inst.name} has not had a new price for {lag} days."))
        r = s.pct_change(fill_method=None).dropna()
        if len(r):
            big = r[np.abs(r) > jump_threshold]
            for d, v in big.items():
                issues.append(Issue("warning", iid, f"{inst.name} moved {v:+.0%} on {d.date()}. "
                                                     "Please confirm this is genuine and not a data error."))
        if (s.dropna() <= 0).any():
            issues.append(Issue("error", iid, f"{inst.name} has a zero or negative price."))
        if inst.currency not in market.fx.columns:
            issues.append(Issue("error", iid, f"No exchange rate for {inst.currency}."))
        if pos.quantity == 0:
            issues.append(Issue("warning", iid, f"{inst.name} has a quantity of zero."))
    return issues
