"""Module 7 — Private Assets.

The simple question it answers: *how do my private investments fit in?*

Private funds work differently from listed securities: you commit money, the
manager "calls" it over several years, and returns come back as
"distributions" long before the fund ends. This module tracks those flows,
computes the standard private-market measures, forecasts future cash needs and
puts private holdings beside public ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

import pandas as pd

from ..analytics.performance_math import xirr
from ..glossary import format_value, metric
from ..workspace import Workspace


@dataclass
class CashFlow:
    date: date
    kind: str  # "call" (money paid into the fund) or "distribution" (money returned)
    amount: float  # always positive

    def __post_init__(self) -> None:
        if self.kind not in ("call", "distribution"):
            raise ValueError("Cash flow kind must be 'call' or 'distribution'")
        if self.amount < 0:
            raise ValueError("Cash flow amounts are positive; the kind says which way the money moves")


@dataclass
class PrivateFund:
    id: str
    name: str
    strategy: str  # e.g. "Private Equity", "Private Credit", "Infrastructure"
    vintage: int
    commitment: float
    cashflows: List[CashFlow] = field(default_factory=list)
    nav_reports: List[Tuple[date, float]] = field(default_factory=list)  # quarterly statements
    life_years: int = 10
    currency: str = "USD"

    # --------------------------------------------------------------- basics
    def _sum(self, kind: str, until: Optional[date] = None) -> float:
        return sum(c.amount for c in self.cashflows if c.kind == kind and (until is None or c.date <= until))

    def paid_in(self, until: Optional[date] = None) -> float:
        return self._sum("call", until)

    def distributed(self, until: Optional[date] = None) -> float:
        return self._sum("distribution", until)

    def unfunded(self) -> float:
        return max(0.0, self.commitment - self.paid_in())

    def current_nav(self, as_of: Optional[date] = None) -> float:
        """Last reported NAV, rolled forward for calls and distributions since then.

        This roll-forward is the standard way to estimate today's value when
        the latest statement is a quarter old.
        """
        if not self.nav_reports:
            return 0.0
        reports = sorted(r for r in self.nav_reports if as_of is None or r[0] <= as_of)
        if not reports:
            return 0.0
        d, nav = reports[-1]
        for c in self.cashflows:
            if c.date > d and (as_of is None or c.date <= as_of):
                nav += c.amount if c.kind == "call" else -c.amount
        return max(nav, 0.0)


def fund_metrics(fund: PrivateFund, as_of: Optional[date] = None) -> Dict:
    as_of = as_of or max([c.date for c in fund.cashflows] + [r[0] for r in fund.nav_reports])
    paid = fund.paid_in(as_of)
    dist = fund.distributed(as_of)
    nav = fund.current_nav(as_of)
    flows: Dict[date, float] = {}
    for c in fund.cashflows:
        if c.date <= as_of:
            flows[c.date] = flows.get(c.date, 0.0) + (-c.amount if c.kind == "call" else c.amount)
    flows[as_of] = flows.get(as_of, 0.0) + nav
    try:
        irr = xirr(flows)
    except ValueError:
        irr = None
    dpi = dist / paid if paid else 0.0
    rvpi = nav / paid if paid else 0.0
    return {
        "id": fund.id, "name": fund.name, "strategy": fund.strategy, "vintage": fund.vintage,
        "commitment": fund.commitment, "paid_in": paid, "distributed": dist, "nav": nav,
        "unfunded": fund.unfunded(), "as_of": str(as_of),
        "irr": metric("irr", irr).as_dict(), "tvpi": metric("tvpi", dpi + rvpi).as_dict(),
        "dpi": metric("dpi", dpi).as_dict(), "rvpi": metric("rvpi", rvpi).as_dict(),
        "plain": (f"{fund.name}: {format_value(paid, 'money')} paid in of a {format_value(fund.commitment, 'money')} "
                  f"commitment. Every dollar paid in is now worth {dpi + rvpi:.2f} dollars ({dpi:.2f} already "
                  f"returned in cash)" + (f", an IRR of {irr:.1%}." if irr is not None else ".")),
    }


def kaplan_schoar_pme(fund: PrivateFund, index_levels: pd.Series, as_of: Optional[date] = None) -> float:
    """Public Market Equivalent (Kaplan–Schoar).

    Compares the fund with simply investing the same cash flows in a public
    index. Above 1.0 means the fund beat the public market.
    """
    idx = index_levels.sort_index()
    as_of = pd.Timestamp(as_of) if as_of else idx.index[-1]
    end_level = float(idx.asof(as_of))

    def fv(d, amt):
        return amt * end_level / float(idx.asof(pd.Timestamp(d)))

    calls = sum(fv(c.date, c.amount) for c in fund.cashflows if c.kind == "call" and pd.Timestamp(c.date) <= as_of)
    dists = sum(fv(c.date, c.amount) for c in fund.cashflows
                if c.kind == "distribution" and pd.Timestamp(c.date) <= as_of)
    return (dists + fund.current_nav(as_of.date())) / calls if calls else float("nan")


def forecast(fund: PrivateFund, years: int = 6, growth: float = 0.10, call_rates=(0.25, 0.33, 0.50),
             bow: float = 2.5, start_year: Optional[int] = None) -> pd.DataFrame:
    """Project future calls, distributions and NAV with the Takahashi–Alexander (Yale) model.

    * Calls each year are a share of the remaining unfunded commitment
      (25%, then 33%, then 50% thereafter by default).
    * Distributions are a share of NAV that rises as the fund ages:
      (age ÷ life) ^ bow.
    * NAV grows at `growth` a year.
    """
    start_year = start_year or date.today().year
    nav = fund.current_nav()
    unfunded = fund.unfunded()
    rows = []
    for k in range(1, years + 1):
        year = start_year + k
        age = year - fund.vintage
        rc = call_rates[min(age - 1, len(call_rates) - 1)] if age >= 1 else call_rates[0]
        call = rc * unfunded
        rd = min(1.0, max(0.0, age / fund.life_years)) ** bow
        dist = rd * nav * (1 + growth)
        nav = nav * (1 + growth) + call - dist
        unfunded -= call
        rows.append({"year": year, "calls": call, "distributions": dist, "net_cash_flow": dist - call,
                     "nav": max(nav, 0.0), "unfunded": unfunded})
    return pd.DataFrame(rows).set_index("year")


def combined_view(ws: Workspace, funds: List[PrivateFund]) -> Dict:
    """Public and private holdings together, in one allocation."""
    public = ws.nav
    priv = {f.name: f.current_nav() * ws.market.fx_rate(f.currency) for f in funds}
    total = public + sum(priv.values())
    by_class: Dict[str, float] = {}
    for ac, w in ws.valuation.groupby("asset_class")["market_value"].sum().items():
        by_class[ac] = w
    by_class["Cash"] = ws.portfolio.cash
    for f in funds:
        by_class[f.strategy] = by_class.get(f.strategy, 0.0) + priv[f.name]
    alloc = {k: v / total for k, v in sorted(by_class.items(), key=lambda kv: -kv[1])}
    unfunded = sum(f.unfunded() * ws.market.fx_rate(f.currency) for f in funds)
    return {
        "public_value": public, "private_value": total - public, "total_value": total, "cash": ws.portfolio.cash,
        "private_share": (total - public) / total, "allocation": alloc, "unfunded_commitments": unfunded,
        "unfunded_vs_cash": unfunded / ws.portfolio.cash if ws.portfolio.cash else float("inf"),
        "plain": (f"Including private funds the total is {format_value(total, 'money')}, of which "
                  f"{(total - public) / total:.0%} is private. Outstanding commitments of "
                  f"{format_value(unfunded, 'money')} could be called at short notice, against "
                  f"{format_value(ws.portfolio.cash, 'money')} of cash on hand."),
    }


def sample_funds() -> List[PrivateFund]:
    """Two illustrative funds with realistic cash-flow patterns."""
    buyout = PrivateFund(
        "PE1", "Summit Buyout Fund IV", "Private Equity", 2021, 1_500_000,
        cashflows=[
            CashFlow(date(2021, 6, 30), "call", 300_000), CashFlow(date(2022, 3, 31), "call", 350_000),
            CashFlow(date(2022, 12, 15), "call", 250_000), CashFlow(date(2023, 9, 30), "call", 200_000),
            CashFlow(date(2024, 6, 30), "call", 150_000), CashFlow(date(2024, 11, 30), "distribution", 180_000),
            CashFlow(date(2025, 6, 30), "distribution", 260_000), CashFlow(date(2026, 3, 31), "call", 80_000),
            CashFlow(date(2026, 6, 30), "distribution", 320_000),
        ],
        nav_reports=[(date(2024, 12, 31), 1_210_000), (date(2025, 12, 31), 1_230_000),
                     (date(2026, 6, 30), 1_105_000)],
    )
    credit = PrivateFund(
        "PC1", "Meridian Direct Lending II", "Private Credit", 2023, 1_000_000,
        cashflows=[
            CashFlow(date(2023, 4, 30), "call", 400_000), CashFlow(date(2023, 12, 31), "call", 300_000),
            CashFlow(date(2024, 6, 30), "distribution", 45_000), CashFlow(date(2024, 12, 31), "distribution", 50_000),
            CashFlow(date(2025, 6, 30), "call", 150_000), CashFlow(date(2025, 6, 30), "distribution", 55_000),
            CashFlow(date(2025, 12, 31), "distribution", 60_000), CashFlow(date(2026, 6, 30), "distribution", 62_000),
        ],
        nav_reports=[(date(2025, 12, 31), 900_000), (date(2026, 6, 30), 905_000)],
    )
    return [buyout, credit]
