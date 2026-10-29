"""Module 1 — Portfolio Hub.

The simple question it answers: *what do I own, and what is it worth now?*
"""

from __future__ import annotations

from typing import Dict, List

import pandas as pd

from ..glossary import metric
from ..workspace import CASH, Workspace

DIMENSIONS = ("asset_class", "sector", "country", "currency")


def summary(ws: Workspace) -> Dict:
    v = ws.valuation
    hist = ws.nav_history
    day_change = float(hist.iloc[-1] - hist.iloc[-2]) if len(hist) > 1 else 0.0
    upnl = v["unrealised_pnl"].sum(min_count=1)
    return {
        "portfolio": ws.portfolio.name,
        "as_of": str(ws.market.as_of.date()),
        "base_currency": ws.portfolio.base_currency,
        "nav": metric("nav", ws.nav).as_dict(),
        "cash": ws.portfolio.cash,
        "cash_weight": ws.cash_weight,
        "holdings": int(len(v)),
        "day_change": day_change,
        "day_change_pct": day_change / float(hist.iloc[-2]) if len(hist) > 1 else 0.0,
        "unrealised_pnl": None if pd.isna(upnl) else float(upnl),
    }


def holdings(ws: Workspace) -> List[Dict]:
    v = ws.valuation.reset_index()
    cols = ["id", "name", "asset_class", "sector", "country", "currency", "quantity", "price",
            "fx_rate", "market_value", "weight", "unrealised_pnl"]
    rows = v[cols].to_dict(orient="records")
    for r in rows:
        if pd.isna(r["unrealised_pnl"]):
            r["unrealised_pnl"] = None
    return rows


def allocation(ws: Workspace, by: str = "asset_class", lookthrough: bool = True) -> pd.Series:
    """Share of NAV in each group. Funds are split into their underlying exposures
    when `lookthrough` is on and the fund publishes them."""
    if by not in DIMENSIONS:
        raise ValueError(f"Allocation can be grouped by: {', '.join(DIMENSIONS)}")
    out: Dict[str, float] = {}
    for iid, row in ws.valuation.iterrows():
        inst = ws.master.get(iid)
        split = inst.lookthrough.get(by) if lookthrough else None
        if split:
            for k, share in split.items():
                out[k] = out.get(k, 0.0) + row["weight"] * share
        else:
            key = row[by]
            out[key] = out.get(key, 0.0) + row["weight"]
    if ws.portfolio.cash:
        label = ws.portfolio.base_currency if by == "currency" else CASH.title()
        out[label] = out.get(label, 0.0) + ws.cash_weight
    return pd.Series(out).sort_values(ascending=False)


def top_holdings(ws: Workspace, n: int = 10) -> pd.DataFrame:
    return ws.valuation.head(n)[["name", "asset_class", "market_value", "weight"]]


def describe(ws: Workspace) -> str:
    """A two-sentence plain-English description of the portfolio."""
    s = summary(ws)
    ac = allocation(ws, "asset_class")
    top = ws.valuation.iloc[0]
    parts = ", ".join(f"{k} {v:.0%}" for k, v in ac.head(4).items())
    return (f"{s['portfolio']} is worth {metric('nav', s['nav']['value']).display()} across {s['holdings']} "
            f"holdings, split {parts}. The largest holding is {top['name']} at {top['weight']:.1%} of the portfolio.")
