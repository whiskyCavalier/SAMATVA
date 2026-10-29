"""Module 3 — Scenario Lab.

The simple question it answers: *what happens if the market moves?*

Three complementary tools:

1. **Stress scenarios** — apply named shocks (an equity fall, a rate rise, a
   currency move) and reprice every holding. Bonds are repriced exactly from
   their yields rather than approximated.
2. **Factor scenarios** — shock the underlying drivers (market, rates, …) and
   let each holding respond according to its own measured sensitivity.
3. **Monte Carlo projection** — thousands of possible futures, built from the
   portfolio's own history, summarised as a range of outcomes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..analytics import bonds as B
from ..analytics import risk as R
from ..glossary import format_value
from ..workspace import Workspace


@dataclass
class Scenario:
    """A set of shocks. All moves are fractions: -0.20 means a 20% fall;
    rates and spreads are yield changes in decimal: 0.01 means +1 percentage point."""

    name: str
    description: str = ""
    asset_class: Dict[str, float] = field(default_factory=dict)  # e.g. {"Equity": -0.2}
    sector: Dict[str, float] = field(default_factory=dict)  # overrides asset-class shock
    country: Dict[str, float] = field(default_factory=dict)  # overrides sector shock
    instrument: Dict[str, float] = field(default_factory=dict)  # overrides everything
    rates: float = 0.0  # parallel yield shift for all bonds
    credit_spread: float = 0.0  # extra yield shift for non-government bonds
    fx: Dict[str, float] = field(default_factory=dict)  # change in the foreign currency's value vs base
    illustrative: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


# Approximate, rounded moves inspired by well-known episodes. They are
# illustrative stress tests, not exact replays of history.
LIBRARY: List[Scenario] = [
    Scenario("Global Financial Crisis (2008-style)",
             "Banking crisis: equities and property collapse, government bonds rally, credit spreads blow out.",
             asset_class={"Equity": -0.40, "Real Estate": -0.45, "Commodity": 0.05},
             sector={"Financials": -0.55, "Energy": -0.45},
             rates=-0.015, credit_spread=0.03, fx={"EUR": -0.10, "JPY": 0.15, "GBP": -0.20}),
    Scenario("Pandemic crash (2020-style)",
             "A sudden global shutdown: a sharp equity fall, rates cut to near zero, a flight to safety.",
             asset_class={"Equity": -0.30, "Real Estate": -0.35, "Commodity": -0.03},
             sector={"Energy": -0.50, "Healthcare": -0.15, "Technology": -0.20},
             rates=-0.010, credit_spread=0.02, fx={"EUR": -0.03, "JPY": 0.03, "GBP": -0.08}),
    Scenario("Inflation and rate shock (2022-style)",
             "Inflation forces rapid rate rises: bonds and equities fall together.",
             asset_class={"Equity": -0.20, "Real Estate": -0.28, "Commodity": 0.0},
             sector={"Technology": -0.33, "Energy": 0.30},
             rates=0.025, credit_spread=0.01, fx={"EUR": -0.12, "JPY": -0.20, "GBP": -0.12}),
    Scenario("Technology sell-off",
             "A sharp de-rating of technology shares while the wider market holds up.",
             asset_class={"Equity": -0.06}, sector={"Technology": -0.25}, rates=-0.003),
    Scenario("Rates up 1 percentage point",
             "A parallel rise in all interest rates, everything else unchanged.", rates=0.01, illustrative=False),
    Scenario("Dollar strengthens 10%",
             "The base currency rises 10% against all others.",
             fx={"EUR": -0.10, "JPY": -0.10, "GBP": -0.10}, illustrative=False),
    Scenario("Emerging markets crisis",
             "Capital flight from emerging markets; developed markets wobble.",
             asset_class={"Equity": -0.08}, country={"Emerging Markets": -0.30}, credit_spread=0.015),
]


def _price_shock(ws: Workspace, iid: str, sc: Scenario) -> float:
    inst = ws.master.get(iid)
    if iid in sc.instrument:
        return sc.instrument[iid]
    if inst.is_bond:
        ytm = ws.market.yields.get(iid)
        if ytm is None:
            ytm = B.bond_yield(float(ws.market.latest_prices()[iid]), inst.coupon, inst.maturity_years)
        shift = sc.rates + (sc.credit_spread if inst.sector != "Government" else 0.0)
        return B.price_change_for_yield_shift(ytm, inst.coupon, inst.maturity_years, shift, inst.coupon_frequency)
    if inst.country in sc.country:
        return sc.country[inst.country]
    if inst.sector in sc.sector:
        return sc.sector[inst.sector]
    return sc.asset_class.get(inst.asset_class, 0.0)


def run(ws: Workspace, sc: Scenario) -> Dict:
    """Reprice every holding under the scenario. Returns totals and a line per holding."""
    rows = []
    for iid, v in ws.valuation.iterrows():
        inst = ws.master.get(iid)
        price_move = _price_shock(ws, iid, sc)
        fx_move = sc.fx.get(inst.currency, 0.0) if inst.currency != ws.portfolio.base_currency else 0.0
        total_move = (1 + price_move) * (1 + fx_move) - 1
        pnl = v["market_value"] * total_move
        rows.append({"id": iid, "name": inst.name, "market_value": v["market_value"], "price_move": price_move,
                     "fx_move": fx_move, "total_move": total_move, "pnl": pnl})
    df = pd.DataFrame(rows).sort_values("pnl")
    total = float(df["pnl"].sum())
    nav = ws.nav
    worst = df.iloc[0]
    best = df.iloc[-1]
    plain = (f"Under '{sc.name}', the portfolio would {'lose' if total < 0 else 'gain'} about "
             f"{format_value(abs(total), 'money')} ({total / nav:+.1%}).")
    if total < 0:
        plain += f" The biggest hit comes from {worst['name']} ({format_value(worst['pnl'], 'money')})"
        plain += (f", while {best['name']} cushions the blow ({format_value(best['pnl'], 'money')})."
                  if best["pnl"] > 0 else ".")
    elif best["pnl"] > 0:
        plain += f" The biggest gain comes from {best['name']} ({format_value(best['pnl'], 'money')})."
    return {"scenario": sc.as_dict(), "pnl": total, "pnl_pct": total / nav, "nav_after": nav + total,
            "positions": df.to_dict(orient="records"), "plain": plain}


def run_library(ws: Workspace) -> List[Dict]:
    out = [run(ws, sc) for sc in LIBRARY]
    return sorted(out, key=lambda r: r["pnl"])


def custom(ws: Workspace, name: str = "Custom", **shocks) -> Dict:
    """Quick custom stress, e.g. custom(ws, equity=-0.2, rates=0.01, fx={'EUR': -0.05})."""
    ac = {}
    for key, label in (("equity", "Equity"), ("real_estate", "Real Estate"), ("commodity", "Commodity")):
        if key in shocks:
            ac[label] = shocks.pop(key)
    sc = Scenario(name, "User-defined", asset_class=ac, illustrative=False, **shocks)
    return run(ws, sc)


# --------------------------------------------------------------------------- factor scenarios
def factor_scenario(ws: Workspace, shocks: Dict[str, float], name: str = "Factor scenario") -> Dict:
    """Shock the risk factors and project each holding using its measured sensitivities.

    For example ``{"Market": -0.10, "Rates": 0.005}`` asks: if global equities
    fall 10% and yields rise half a point, what happens, given how each of my
    holdings has actually behaved?
    """
    factors = ws.market.factors
    if factors is None:
        raise ValueError("This market data set has no factor returns.")
    unknown = set(shocks) - set(factors.columns)
    if unknown:
        raise ValueError(f"Unknown factors: {', '.join(unknown)}. Available: {', '.join(factors.columns)}")
    shock_vec = pd.Series(shocks).reindex(factors.columns).fillna(0.0)
    rows = []
    for iid, v in ws.valuation.iterrows():
        fit = R.factor_exposures(ws.asset_returns[iid], factors)
        move = float((fit.exposures * shock_vec).sum())
        rows.append({"id": iid, "name": ws.master.get(iid).name, "move": move, "r_squared": fit.r_squared,
                     "pnl": v["market_value"] * move})
    df = pd.DataFrame(rows).sort_values("pnl")
    total = float(df["pnl"].sum())
    return {"name": name, "shocks": shocks, "pnl": total, "pnl_pct": total / ws.nav,
            "positions": df.to_dict(orient="records"),
            "plain": f"Based on how each holding has responded in the past, this would move the portfolio by "
                     f"{total / ws.nav:+.1%} ({format_value(total, 'money')})."}


# --------------------------------------------------------------------------- monte carlo
def monte_carlo_projection(ws: Workspace, horizon_days: int = 252, n_sims: int = 10_000,
                           seed: Optional[int] = 11, block: int = 5) -> Dict:
    """Range of portfolio values at the horizon, by block-bootstrapping history.

    Blocks of consecutive days are resampled together so that calm and
    turbulent spells, and the links between assets, are preserved.
    """
    rng = np.random.default_rng(seed)
    r = ws.portfolio_returns.values
    n_blocks = int(np.ceil(horizon_days / block))
    starts = rng.integers(0, len(r) - block, size=(n_sims, n_blocks))
    paths = r[starts[..., None] + np.arange(block)].reshape(n_sims, -1)[:, :horizon_days]
    growth = np.prod(1 + paths, axis=1)
    nav = ws.nav
    end_values = nav * growth
    pct = {f"p{p}": float(np.percentile(end_values, p)) for p in (5, 25, 50, 75, 95)}
    prob_loss = float((growth < 1).mean())
    prob_loss_10 = float((growth < 0.9).mean())
    return {
        "horizon_days": horizon_days,
        "simulations": n_sims,
        "start_value": nav,
        "percentiles": pct,
        "probability_of_loss": prob_loss,
        "probability_of_loss_over_10pct": prob_loss_10,
        "histogram": dict(zip(("counts", "edges"), (a.tolist() for a in np.histogram(end_values, bins=40)))),
        "plain": (f"Across {n_sims:,} simulated years, the middle outcome is {format_value(pct['p50'], 'money')}. "
                  f"In 9 out of 10 cases the value ends between {format_value(pct['p5'], 'money')} and "
                  f"{format_value(pct['p95'], 'money')}. The chance of ending lower than today is "
                  f"{prob_loss:.0%}, and of losing more than 10% is {prob_loss_10:.0%}."),
    }


def worst_days(ws: Workspace, n: int = 5) -> List[Dict]:
    """The worst days in the history of today's holdings, with the money at stake now."""
    r = ws.portfolio_returns.nsmallest(n)
    return [{"date": str(d.date()), "return": float(v), "pnl_today": float(v * ws.nav)} for d, v in r.items()]
