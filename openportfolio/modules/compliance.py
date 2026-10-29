"""Module 5 — Compliance.

The simple question it answers: *am I breaking any rules?*

Rules are plain data (so they can live in a JSON or YAML file that a
compliance officer can read). Each check returns one of three statuses:

* ``pass``    — comfortably inside the limit
* ``warning`` — inside the limit but within 10% of it
* ``breach``  — outside the limit

The same rule book runs *post-trade* (on today's holdings) and *pre-trade*
(on the holdings as they would be after proposed trades), so a breach can be
caught before it happens.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional

from ..analytics import risk as R
from ..workspace import Workspace
from . import hub
from . import risk_dashboard as RD

WARN_BUFFER = 0.90  # warn once a measure reaches 90% of its limit


@dataclass
class Rule:
    id: str
    type: str
    limit: float
    description: str = ""
    params: Dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Dict) -> "Rule":
        d = dict(d)
        known = {k: d.pop(k) for k in ("id", "type", "limit", "description") if k in d}
        return cls(params=d, **known)


@dataclass
class Result:
    rule_id: str
    status: str  # pass | warning | breach
    measured: float
    limit: float
    message: str
    subject: str = "Portfolio"

    def as_dict(self) -> dict:
        return asdict(self)


def _status_max(measured: float, limit: float) -> str:
    if measured > limit + 1e-12:
        return "breach"
    return "warning" if measured >= WARN_BUFFER * limit else "pass"


def _status_min(measured: float, limit: float) -> str:
    if measured < limit - 1e-12:
        return "breach"
    return "warning" if measured <= limit / WARN_BUFFER else "pass"


# --------------------------------------------------------------------------- checks
def _max_position_weight(ws: Workspace, rule: Rule) -> List[Result]:
    out = []
    for iid, w in ws.weights.items():
        name = ws.master.get(iid).name
        st = _status_max(w, rule.limit)
        out.append(Result(rule.id, st, float(w), rule.limit,
                          f"{name} is {w:.1%} of the portfolio (limit {rule.limit:.0%}).", name))
    return out


def _group_weight(ws: Workspace, rule: Rule, mode: str) -> List[Result]:
    dim = rule.params.get("dimension", "asset_class")
    group = rule.params["group"]
    alloc = hub.allocation(ws, dim, lookthrough=rule.params.get("lookthrough", True))
    w = float(alloc.get(group, 0.0))
    st = _status_max(w, rule.limit) if mode == "max" else _status_min(w, rule.limit)
    word = "at most" if mode == "max" else "at least"
    return [Result(rule.id, st, w, rule.limit,
                   f"{group} is {w:.1%} of the portfolio; the rule says {word} {rule.limit:.0%}.", group)]


def _exclusion(ws: Workspace, rule: Rule) -> List[Result]:
    tag = rule.params["tag"]
    out = []
    for iid in ws.held_ids:
        inst = ws.master.get(iid)
        if tag in inst.tags:
            w = float(ws.weights[iid])
            out.append(Result(rule.id, "breach", w, 0.0,
                              f"{inst.name} is on the '{tag}' exclusion list but is held ({w:.1%}).", inst.name))
    if not out:
        out.append(Result(rule.id, "pass", 0.0, 0.0, f"No holdings carry the '{tag}' exclusion tag."))
    return out


def _min_cash(ws: Workspace, rule: Rule) -> List[Result]:
    w = ws.cash_weight
    return [Result(rule.id, _status_min(w, rule.limit), w, rule.limit,
                   f"Cash is {w:.1%} of the portfolio (minimum {rule.limit:.0%}).", "Cash")]


def _max_var(ws: Workspace, rule: Rule) -> List[Result]:
    conf = rule.params.get("confidence", 0.95)
    v = R.historical_var(ws.portfolio_returns, conf)
    return [Result(rule.id, _status_max(v, rule.limit), v, rule.limit,
                   f"One-day VaR ({conf:.0%}) is {v:.2%} of the portfolio (limit {rule.limit:.2%}).")]


def _max_duration(ws: Workspace, rule: Rule) -> List[Result]:
    d = RD.bond_analytics(ws)["portfolio_duration"]["value"]
    return [Result(rule.id, _status_max(d, rule.limit), d, rule.limit,
                   f"Portfolio duration is {d:.2f} (limit {rule.limit:.2f}).")]


def _max_illiquid(ws: Workspace, rule: Rule) -> List[Result]:
    days = rule.params.get("days", 5)
    part = rule.params.get("participation", 0.20)
    nav = ws.nav
    illiquid = 0.0
    for iid, row in ws.valuation.iterrows():
        d = R.days_to_liquidate(row["quantity"], ws.master.get(iid).avg_daily_volume, part)
        if d > days:
            illiquid += row["market_value"]
    w = illiquid / nav
    return [Result(rule.id, _status_max(w, rule.limit), w, rule.limit,
                   f"{w:.1%} of the portfolio would take more than {days} days to sell (limit {rule.limit:.0%}).")]


def _min_holdings(ws: Workspace, rule: Rule) -> List[Result]:
    n = float(len(ws.held_ids))
    return [Result(rule.id, _status_min(n, rule.limit), n, rule.limit,
                   f"The portfolio holds {n:.0f} instruments (minimum {rule.limit:.0f}).")]


CHECKS: Dict[str, Callable[[Workspace, Rule], List[Result]]] = {
    "max_position_weight": _max_position_weight,
    "max_group_weight": lambda ws, r: _group_weight(ws, r, "max"),
    "min_group_weight": lambda ws, r: _group_weight(ws, r, "min"),
    "exclusion": _exclusion,
    "min_cash": _min_cash,
    "max_var": _max_var,
    "max_duration": _max_duration,
    "max_illiquid": _max_illiquid,
    "min_holdings": _min_holdings,
}


class RuleBook:
    def __init__(self, rules: Iterable[Rule]) -> None:
        self.rules = list(rules)
        for r in self.rules:
            if r.type not in CHECKS:
                raise ValueError(f"Unknown rule type '{r.type}'. Available: {', '.join(CHECKS)}")
        self.log: List[Dict] = []

    @classmethod
    def from_dicts(cls, items: Iterable[Dict]) -> "RuleBook":
        return cls(Rule.from_dict(d) for d in items)

    def evaluate(self, ws: Workspace, record: bool = True) -> List[Result]:
        results: List[Result] = []
        for rule in self.rules:
            results.extend(CHECKS[rule.type](ws, rule))
        if record:
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for r in results:
                if r.status != "pass":
                    self.log.append({"time": stamp, **r.as_dict()})
        return results

    def summary(self, ws: Workspace) -> Dict:
        res = self.evaluate(ws)
        counts = {s: sum(1 for r in res if r.status == s) for s in ("pass", "warning", "breach")}
        issues = [r.as_dict() for r in res if r.status != "pass"]
        if counts["breach"]:
            n = counts["breach"]
            plain = f"{n} rule {'breach needs' if n == 1 else 'breaches need'} attention. " + " ".join(
                r["message"] for r in issues if r["status"] == "breach")
        elif counts["warning"]:
            n = counts["warning"]
            plain = f"No breaches, but {n} {'measure is' if n == 1 else 'measures are'} close to {'its limit' if n == 1 else 'their limits'}."
        else:
            plain = "All rules pass comfortably."
        return {"counts": counts, "issues": issues, "results": [r.as_dict() for r in res], "plain": plain}

    def pre_trade(self, ws: Workspace, trades: Dict[str, float], cash_change: Optional[float] = None) -> Dict:
        """Would these trades (instrument id -> quantity change) cause a breach?"""
        from .trading import apply_trades

        after = apply_trades(ws, trades, cash_change)
        before = {(r.rule_id, r.subject): r.status for r in self.evaluate(ws, record=False)}
        res_after = self.evaluate(after, record=False)
        new = [r.as_dict() for r in res_after
               if r.status == "breach" and before.get((r.rule_id, r.subject)) != "breach"]
        return {"approved": not new, "new_breaches": new,
                "plain": "Trades approved: no new breaches." if not new else
                "Trades blocked. " + " ".join(r["message"] for r in new)}


DEFAULT_RULES = [
    {"id": "R1", "type": "max_position_weight", "limit": 0.21, "description": "No single holding above 21%"},
    {"id": "R2", "type": "max_group_weight", "limit": 0.60, "dimension": "asset_class", "group": "Equity",
     "description": "Equities at most 60%"},
    {"id": "R3", "type": "min_group_weight", "limit": 0.30, "dimension": "asset_class", "group": "Fixed Income",
     "description": "Bonds at least 30%"},
    {"id": "R4", "type": "exclusion", "limit": 0, "tag": "tobacco", "description": "No tobacco companies"},
    {"id": "R5", "type": "max_group_weight", "limit": 0.06, "dimension": "sector", "group": "Energy",
     "description": "Energy at most 6%"},
    {"id": "R6", "type": "min_cash", "limit": 0.02, "description": "Keep at least 2% in cash"},
    {"id": "R7", "type": "max_var", "limit": 0.015, "confidence": 0.95, "description": "One-day 95% VaR under 1.5%"},
    {"id": "R8", "type": "max_duration", "limit": 4.0, "description": "Portfolio duration under 4"},
    {"id": "R9", "type": "max_illiquid", "limit": 0.10, "days": 5,
     "description": "At most 10% needing more than 5 days to sell"},
    {"id": "R10", "type": "min_holdings", "limit": 10, "description": "At least 10 holdings"},
]


def default_rulebook() -> RuleBook:
    return RuleBook.from_dicts(DEFAULT_RULES)
