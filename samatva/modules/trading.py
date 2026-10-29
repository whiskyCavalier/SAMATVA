"""Module 6 — Trading.

The simple question it answers: *what should I trade to reach my target?*

Contents:
* an order blotter to record and track orders,
* a rebalancer that turns target weights into exact trades, with cost estimates,
* an optimiser offering four well-known portfolio construction methods.
"""

from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from ..analytics import risk as R
from ..glossary import format_value
from ..workspace import Workspace

_order_ids = itertools.count(1)


# --------------------------------------------------------------------------- blotter
@dataclass
class Order:
    instrument_id: str
    side: str  # BUY | SELL
    quantity: float
    limit_price: Optional[float] = None
    status: str = "NEW"  # NEW | FILLED | CANCELLED | REJECTED
    filled_price: Optional[float] = None
    id: int = 0
    created: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.side not in ("BUY", "SELL"):
            raise ValueError("Side must be BUY or SELL")
        if self.quantity <= 0:
            raise ValueError("Quantity must be positive; use the side to say buy or sell")
        if not self.id:
            self.id = next(_order_ids)
        if not self.created:
            self.created = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def as_dict(self) -> dict:
        return asdict(self)


class Blotter:
    """An in-memory order book. Swap for a database-backed one in production."""

    def __init__(self) -> None:
        self.orders: Dict[int, Order] = {}

    def add(self, order: Order) -> Order:
        self.orders[order.id] = order
        return order

    def fill(self, order_id: int, price: float) -> Order:
        o = self.orders[order_id]
        if o.status != "NEW":
            raise ValueError(f"Order {order_id} is {o.status} and cannot be filled")
        o.status, o.filled_price = "FILLED", price
        return o

    def cancel(self, order_id: int) -> Order:
        o = self.orders[order_id]
        if o.status != "NEW":
            raise ValueError(f"Order {order_id} is {o.status} and cannot be cancelled")
        o.status = "CANCELLED"
        return o

    def open_orders(self) -> List[Order]:
        return [o for o in self.orders.values() if o.status == "NEW"]

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([o.as_dict() for o in self.orders.values()])


# --------------------------------------------------------------------------- helpers
def apply_trades(ws: Workspace, trades: Dict[str, float], cash_change: Optional[float] = None) -> Workspace:
    """A copy of the workspace after the trades. Cash moves by the trade value unless given."""
    q = ws.portfolio.quantities()
    latest = ws.market.latest_prices()
    spent = 0.0
    for iid, dq in trades.items():
        if iid not in ws.master:
            raise KeyError(f"Unknown instrument '{iid}'")
        q[iid] = q.get(iid, 0.0) + dq
        spent += dq * float(latest[iid]) * ws.market.fx_rate(ws.master.get(iid).currency)
    cash = ws.portfolio.cash + (cash_change if cash_change is not None else -spent)
    return ws.with_portfolio(ws.portfolio.copy_with(q, cash))


def estimate_cost(ws: Workspace, iid: str, quantity: float, commission_bps: float = 5.0,
                  half_spread_bps: float = 5.0, impact_coeff: float = 0.1) -> float:
    """Estimated trading cost in base currency.

    commission + half the bid-ask spread + market impact, where impact follows
    the widely used square-root law: k × daily volatility × √(quantity ÷ ADV).
    """
    inst = ws.master.get(iid)
    price = float(ws.market.latest_prices()[iid]) * ws.market.fx_rate(inst.currency)
    value = abs(quantity) * price
    fixed = value * (commission_bps + half_spread_bps) / 10_000
    impact = 0.0
    if inst.avg_daily_volume:
        px = ws.base_prices[iid].dropna()
        daily_vol = float(px.pct_change(fill_method=None).std())
        impact = value * impact_coeff * daily_vol * np.sqrt(abs(quantity) / inst.avg_daily_volume)
    return float(fixed + impact)


def rebalance(ws: Workspace, targets: Dict[str, float], min_trade_value: float = 1_000.0,
              cash_target: Optional[float] = None) -> Dict:
    """Turn target weights (instrument id -> share of NAV) into a list of trades.

    Holdings not mentioned in `targets` are sold. Trades smaller than
    `min_trade_value` are skipped to avoid needless costs.
    """
    total_target = sum(targets.values()) + (cash_target or 0.0)
    if total_target > 1.0 + 1e-9:
        raise ValueError(f"Targets add up to {total_target:.1%}, which is more than 100%.")
    nav = ws.nav
    latest = ws.market.latest_prices()
    current = ws.portfolio.quantities()
    trades, rows = {}, []
    for iid in sorted(set(current) | set(targets)):
        inst = ws.master.get(iid)
        unit = float(latest[iid]) * ws.market.fx_rate(inst.currency)
        want_q = targets.get(iid, 0.0) * nav / unit
        lot = 100 if inst.asset_class == "Fixed Income" else 1
        want_q = round(want_q / lot) * lot
        dq = want_q - current.get(iid, 0.0)
        value = dq * unit
        if abs(value) < min_trade_value:
            continue
        trades[iid] = dq
        rows.append({"id": iid, "name": inst.name, "side": "BUY" if dq > 0 else "SELL", "quantity": abs(dq),
                     "value": value, "est_cost": estimate_cost(ws, iid, dq)})
    total_cost = sum(r["est_cost"] for r in rows)
    turnover = sum(abs(r["value"]) for r in rows) / 2 / nav
    after = apply_trades(ws, trades)
    return {
        "trades": rows,
        "trade_map": trades,
        "turnover": turnover,
        "estimated_cost": total_cost,
        "cash_after": after.portfolio.cash,
        "weights_after": after.weights.to_dict(),
        "plain": (f"{len(rows)} trades are needed, turning over {turnover:.1%} of the portfolio, at an estimated "
                  f"cost of {format_value(total_cost, 'money')} ({total_cost / nav:.3%} of NAV)."),
    }


def orders_from_rebalance(plan: Dict, blotter: Optional[Blotter] = None) -> List[Order]:
    blotter = blotter or Blotter()
    return [blotter.add(Order(t["id"], t["side"], t["quantity"], note="rebalance")) for t in plan["trades"]]


# --------------------------------------------------------------------------- optimiser
def expected_returns(ws: Workspace, shrink: float = 0.5) -> pd.Series:
    """Historical mean returns, shrunk halfway toward their average.

    Raw historical means are notoriously noisy; shrinking them stops the
    optimiser from betting heavily on a lucky past.
    """
    mu = ws.asset_returns.mean() * 252
    return (1 - shrink) * mu + shrink * mu.mean()


def optimise(ws: Workspace, method: str = "max_sharpe", max_weight: float = 0.25, min_weight: float = 0.0,
             invest_share: Optional[float] = None, risk_aversion: float = 4.0,
             mu: Optional[pd.Series] = None) -> Dict:
    """Suggest target weights.

    methods:
      * ``min_variance`` — the lowest-risk mix
      * ``max_sharpe``   — the best return per unit of risk
      * ``risk_parity``  — every holding contributes equal risk
      * ``mean_variance``— return minus a risk penalty (set by `risk_aversion`)
    """
    rets = ws.asset_returns.fillna(0.0)
    ids = list(rets.columns)
    n = len(ids)
    cov = rets.cov().values * 252
    mu_v = (mu if mu is not None else expected_returns(ws)).reindex(ids).values
    rf = ws.market.risk_free_rate
    budget = invest_share if invest_share is not None else 1 - ws.cash_weight
    note = ""
    if max_weight * n < budget - 1e-9:
        # Too few holdings for the cap: widen it just enough to stay invested, and say so.
        widened = min(1.0, float(np.ceil(budget / n * 1.2 * 100) / 100))
        note = (f" With only {n} holdings a {max_weight:.0%} cap cannot invest the whole portfolio, so the cap was "
                f"raised to {widened:.0%}.")
        max_weight = widened
    w0 = np.full(n, budget / n)
    bounds = [(min_weight, max_weight)] * n
    cons = [{"type": "eq", "fun": lambda w: w.sum() - budget}]

    def vol(w):
        return np.sqrt(w @ cov @ w)

    if method == "min_variance":
        obj = lambda w: w @ cov @ w  # noqa: E731
    elif method == "max_sharpe":
        obj = lambda w: -(w @ mu_v - rf * w.sum()) / vol(w)  # noqa: E731
    elif method == "mean_variance":
        obj = lambda w: -(w @ mu_v - 0.5 * risk_aversion * w @ cov @ w)  # noqa: E731
    elif method == "risk_parity":
        def obj(w):
            rc = w * (cov @ w) / vol(w)
            return ((rc - rc.mean()) ** 2).sum() * 1e4
    else:
        raise ValueError("method must be one of: min_variance, max_sharpe, risk_parity, mean_variance")

    res = minimize(obj, w0, method="SLSQP", bounds=bounds, constraints=cons,
                   options={"maxiter": 500, "ftol": 1e-12})
    if not res.success:
        raise RuntimeError(f"The optimiser did not converge: {res.message}")
    w = pd.Series(np.where(np.abs(res.x) < 1e-6, 0.0, res.x), index=ids)
    cur = ws.weights.reindex(ids).fillna(0).values

    def stats(x):
        x = np.asarray(x)
        ret = float(x @ mu_v + (1 - x.sum()) * rf)
        v = float(np.sqrt(x @ cov @ x))
        return {"expected_return": ret, "volatility": v, "sharpe": (ret - rf) / v if v else 0.0}

    proposed, current = stats(w.values), stats(cur)
    return {
        "method": method,
        "weights": w.round(6).to_dict(),
        "proposed": proposed,
        "current": current,
        "plain": (f"The {method.replace('_', ' ')} mix has expected volatility {proposed['volatility']:.1%} "
                  f"(now {current['volatility']:.1%}) and expected return {proposed['expected_return']:.1%} "
                  f"(now {current['expected_return']:.1%}). Expected returns are estimates, not promises." + note),
    }
