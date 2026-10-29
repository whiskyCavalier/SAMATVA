"""Achchu (அச்சு, the axle): how close is this investor to breaking, and what can they safely do now?

Built from the Thirukkural and the Bhagavad Gita:

* Kural 475 — even a cart of peacock feathers breaks its axle if loaded too
  high. Investors rarely give up after one fall; they give up under the
  accumulated strain of many. Achchu measures that strain with the fatigue
  counting engineers use for bridges and aircraft (see analytics/fatigue.py).
* Kural 478 and Gita 2.56 set the axle's capacity: the weaker of the
  investor's means and their steadiness of mind.
* Gita 2.38 sets the endurance threshold (swings below it cause no strain),
  which rises with the investor's measured equanimity.
* Kural 621 and Gita 6.35 set healing: strain fades in calm periods, faster
  with practised discipline.
* Kural 471 weighs four strengths before any trade; Kural 476 sets a ceiling
  no trade may cross; Kural 467 and Gita 18.63 ask for reflection before
  acting; Kurals 481, 490 and 495 decide when to act and when to wait.

Every result carries a plain-English explanation and the verse behind it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..analytics import fatigue as F
from ..analytics import risk as R
from ..glossary import format_value
from ..workspace import Workspace

VERSES = {
    "475": "Even a cart of peacock feathers breaks its axle if loaded too heavily (Kural 475).",
    "476": "Whoever climbs past the tip of the branch falls to their end (Kural 476).",
    "478": "A narrow inflow does no harm, provided the outflow is not wider (Kural 478).",
    "471": "Act after weighing the strength of the deed, your own, your opponent's and your allies' (Kural 471).",
    "467": "Think, then act; to think only after acting is a fault (Kural 467).",
    "490": "Wait still like the crane; when the moment comes, strike as it strikes (Kural 490).",
    "495": "In deep water the crocodile wins; out of it, others overcome it (Kural 495).",
    "621": "When trouble comes, smile; nothing overcomes trouble so well (Kural 621).",
    "2.38": "Treat alike gain and loss, victory and defeat (Bhagavad Gita 2.38).",
    "2.56": "One unshaken in sorrow is called steady in wisdom (Bhagavad Gita 2.56).",
    "6.35": "The mind is held steady by practice and detachment (Bhagavad Gita 6.35).",
    "18.63": "Reflect on this fully, then do as you choose (Bhagavad Gita 18.63).",
    "2.47": "Your right is to the action alone, never to its fruits (Bhagavad Gita 2.47).",
}

STATES = ("Rising and calm", "Rising and turbulent", "Falling and calm", "Falling and turbulent")


@dataclass
class AxleProfile:
    """What Achchu needs to know about the investor. Every field has a sensible default."""

    # Means (Kural 478): money in hand and flowing in and out, in the base currency
    reserves: float = 0.0  # cash outside the portfolio that can meet needs
    monthly_inflow: float = 0.0  # income or contributions available for needs
    monthly_outflow: float = 0.0  # committed spending to be met from these resources
    horizon_months: int = 12
    # Mind (Gita 2.56)
    stated_tolerance: float = 0.20  # the largest fall the investor says they can bear
    observed_tolerance: Optional[float] = None  # the largest fall they actually sat through, if known
    safety_margin: float = 0.80  # applied to a stated (unproven) tolerance
    # Fatigue settings (calibrate with `calibrate_profile` when history allows)
    exponent: float = 2.0  # how much more large swings strain than small ones
    endurance_base: float = 0.04  # endurance threshold for an investor of average equanimity
    equanimity: float = 0.5  # 0 = very reactive, 1 = perfectly even (Gita 2.38)
    healing_half_life_days: float = 126.0  # half the strain fades in about six months of calm
    discipline: float = 0.0  # share of past decisions that followed the investor's own rules (Gita 6.35)
    # Action settings
    spare_share: float = 0.25  # no single trade may use more than this share of remaining life
    max_break_probability: float = 0.20  # the tip of the branch (Kural 476)
    horizon_days: int = 252
    risk_aversion: float = 0.5
    tip_tolerance: float = 0.01  # when already past the ceiling, the most a trade may add to the odds of breaking

    @property
    def endurance(self) -> float:
        return self.endurance_base * (0.5 + float(np.clip(self.equanimity, 0.0, 1.0)))

    @property
    def healing_rate(self) -> float:
        return float(np.log(2) / self.healing_half_life_days) * (1.0 + float(np.clip(self.discipline, 0.0, 1.0)))

    def as_dict(self) -> dict:
        return {**asdict(self), "endurance": self.endurance, "healing_rate": self.healing_rate}


def equanimity_from_trades(trades: pd.Series, values: pd.Series, lookback: int = 21) -> float:
    """Measured equanimity from behaviour (Gita 2.38): 1.0 means trading does not react to recent swings.

    `trades` is the absolute value traded each day; `values` the portfolio
    value. The score is one minus the rank correlation between how much was
    traded and how large the previous `lookback` days' move was, floored at 0.
    """
    move = values.pct_change(lookback).abs().shift(1)
    df = pd.concat([trades.abs().rename("t"), move.rename("m")], axis=1, join="inner").dropna()
    if len(df) < 30 or df["t"].sum() == 0:
        return 0.5  # not enough evidence: assume an average investor
    corr = df["t"].rank().corr(df["m"].rank())
    return float(np.clip(1.0 - max(0.0, corr if np.isfinite(corr) else 0.0), 0.0, 1.0))


class Achchu:
    def __init__(self, ws: Workspace, profile: Optional[AxleProfile] = None, n_sims: int = 600,
                 seed: int = 7, block: int = 5) -> None:
        self.ws = ws
        self.p = profile or AxleProfile()
        self.n_sims = n_sims
        self.seed = seed
        self.block = block
        self._fatigue: Optional[F.FatigueResult] = None
        self._starts: Optional[np.ndarray] = None

    # ------------------------------------------------------------------ step 1: the axle
    def capacity(self) -> Dict:
        p, nav = self.p, self.ws.nav
        need = max(0.0, (p.monthly_outflow - p.monthly_inflow) * p.horizon_months - p.reserves)
        c_means = float(np.clip(1.0 - need / nav, 0.0, 1.0)) if nav > 0 else 0.0
        if p.observed_tolerance is not None:
            c_mind, mind_source = float(p.observed_tolerance), "observed"
        else:
            c_mind, mind_source = float(p.stated_tolerance * p.safety_margin), "stated"
        c = max(1e-6, min(c_means, c_mind))
        binding = "means" if c_means < c_mind else "mind"
        plain = (f"This investor's axle can take a single fall of about {c:.0%} before breaking. "
                 + (f"The limit is set by their means: covering {format_value(need, 'money')} of needs over the "
                    f"next {p.horizon_months} months leaves room for only a {c_means:.0%} fall. {VERSES['478']}"
                    if binding == "means" else
                    f"The limit is set by their steadiness of mind ({mind_source} tolerance) "
                    f"rather than their means. {VERSES['2.56']}"))
        return {"capacity": c, "means": c_means, "mind": c_mind, "binding": binding, "mind_source": mind_source,
                "needs_not_covered": need, "plain": plain}

    # ------------------------------------------------------------------ step 2: the feathers
    def loads(self) -> pd.DataFrame:
        """Each holding's share of the portfolio's losses on falling days (Kural 475)."""
        rets = self.ws.asset_returns.fillna(0.0)
        w = self.ws.weights.reindex(rets.columns).fillna(0.0)
        rp = rets @ w
        down = rp < 0
        port_down = float(rp[down].mean())
        share = (w * rets[down].mean()) / port_down if port_down else w * 0
        out = pd.DataFrame({"name": [self.ws.master.get(i).name for i in rets.columns], "weight": w.values,
                            "load_share": share.values}, index=rets.columns)
        return out.sort_values("load_share", ascending=False)

    # ------------------------------------------------------------------ step 3: fatigue
    def fatigue(self) -> F.FatigueResult:
        if self._fatigue is None:
            nav = self.ws.nav_history
            p = self.p
            self._fatigue = F.fatigue_path(nav.values, self.capacity()["capacity"], p.endurance, p.exponent,
                                           p.healing_rate, index=nav.index)
        return self._fatigue

    def _block_starts(self, n_obs: int) -> np.ndarray:
        if self._starts is None:
            rng = np.random.default_rng(self.seed)
            n_blocks = int(np.ceil(self.p.horizon_days / self.block))
            self._starts = rng.integers(0, n_obs - self.block, size=(self.n_sims, n_blocks))
        return self._starts

    def _paths(self, extra: Optional[Dict[str, float]] = None) -> np.ndarray:
        """Future value paths, starting from the last high so that a fall now in progress is included.

        Uses the same random blocks every time (common random numbers), so the
        difference made by a trade is not hidden by simulation noise.
        """
        ws = self.ws
        rets = ws.asset_returns.fillna(0.0)
        q = pd.Series(ws.portfolio.quantities())
        prices = ws.base_prices[rets.columns].ffill().iloc[-1]
        values = q.reindex(rets.columns).fillna(0) * prices
        cash = ws.portfolio.cash
        if extra:
            for iid, dq in extra.items():
                dv = dq * float(prices[iid])
                values[iid] = values.get(iid, 0.0) + dv
                cash -= dv
        nav = float(values.sum() + cash)
        w = (values / nav).values
        rp = rets.values @ w + (cash / nav) * self.ws.market.risk_free_rate / 252
        starts = self._block_starts(len(rp))
        daily = rp[starts[..., None] + np.arange(self.block)].reshape(self.n_sims, -1)[:, : self.p.horizon_days]
        growth = np.cumprod(1 + daily, axis=1)
        hist = ws.nav_history
        since_peak = hist.loc[hist.idxmax():].values / hist.iloc[-1]  # the path from the last high, scaled to today
        prefix = np.tile(since_peak, (self.n_sims, 1))
        return np.hstack([prefix, growth])

    def _break_stats(self, extra: Optional[Dict[str, float]] = None) -> Dict:
        p = self.p
        f = self.fatigue()
        cap = self.capacity()["capacity"]
        peaks = F.peak_damage_paths(self._paths(extra), cap, p.endurance, p.exponent, p.healing_rate,
                                    start_damage=f.closed_damage_now)
        return {"break_probability": float((peaks >= 1.0).mean()), "expected_peak_damage": float(peaks.mean())}

    def break_probability(self) -> float:
        return self._break_stats()["break_probability"]

    # ------------------------------------------------------------------ step 5: the crane
    def market_states(self) -> pd.Series:
        """Each day's state: trend (63-day direction) and turbulence (21-day volatility versus its one-year median)."""
        ws = self.ws
        r = ws.benchmark_returns if ws.benchmark_returns is not None else ws.portfolio_returns
        r = r.reindex(ws.asset_returns.index).fillna(0.0)
        trend = (1 + r).rolling(63).apply(np.prod, raw=True) - 1
        vol = r.rolling(21).std()
        turbulent = vol > vol.rolling(252, min_periods=63).median()
        state = np.where(trend >= 0, np.where(turbulent, 1, 0), np.where(turbulent, 3, 2))
        s = pd.Series(state, index=r.index)
        return s[trend.notna()]

    def home_ground(self, min_t: float = 1.0) -> pd.DataFrame:
        """Where each holding has done well (Kural 495), judged on the day after each state was observed."""
        states = self.market_states()
        nxt = self.ws.asset_returns.shift(-1).reindex(states.index)
        now = int(states.iloc[-1])
        rows = []
        for iid in nxt.columns:
            r = nxt[iid]
            home = []
            stats = {}
            for k, label in enumerate(STATES):
                x = r[states == k].dropna()
                if len(x) < 20:
                    continue
                t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if x.std(ddof=1) > 0 else 0.0
                stats[label] = {"mean_annual": float(x.mean() * 252), "t": float(t), "days": int(len(x))}
                if x.mean() > 0 and t >= min_t:
                    home.append(label)
            inside = STATES[now] in home
            rows.append({"id": iid, "name": self.ws.master.get(iid).name, "home_ground": home,
                         "inside_now": inside if home else None, "states": stats})
        df = pd.DataFrame(rows).set_index("id")
        df.attrs["state_now"] = STATES[now]
        return df

    # ------------------------------------------------------------------ step 4: the four-strengths gate
    def _tail_cost(self, extra: Optional[Dict[str, float]] = None) -> float:
        """Monthly 95% expected shortfall of the portfolio, in money."""
        ws = self.ws
        rets = ws.asset_returns.fillna(0.0)
        prices = ws.base_prices[rets.columns].ffill().iloc[-1]
        q = pd.Series(ws.portfolio.quantities()).reindex(rets.columns).fillna(0)
        if extra:
            for iid, dq in extra.items():
                q[iid] = q.get(iid, 0.0) + dq
        pnl = rets.values @ (q * prices).values
        cutoff = np.quantile(pnl, 0.05)
        return float(-pnl[pnl <= cutoff].mean() * np.sqrt(21))

    def gate(self, instrument_id: str, quantity: float, search_size: bool = True) -> Dict:
        """Decide whether a trade may go ahead, and at what size (Kural 471, 476, 467; Gita 18.63)."""
        from .trading import estimate_cost, expected_returns

        ws, p = self.ws, self.p
        iid = ws.master.resolve(instrument_id)
        if iid is None:
            raise KeyError(f"Unknown instrument '{instrument_id}'")
        if quantity == 0:
            raise ValueError("Quantity must not be zero.")
        if iid not in ws.asset_returns.columns:
            raise ValueError(f"{ws.master.get(iid).name} has no price history in this workspace, so its strain "
                             "cannot be measured.")
        price = float(ws.base_prices[iid].ffill().iloc[-1])
        name = ws.master.get(iid).name
        mu = float(expected_returns(ws).get(iid, 0.0))
        rets_i = ws.asset_returns[iid].dropna()
        es_i = R.historical_es(rets_i, 0.95) * np.sqrt(21)

        hg = self.home_ground()
        row = hg.loc[iid]
        has_home = bool(row["home_ground"])
        inside = bool(row["inside_now"]) if has_home else None
        base_tail = self._tail_cost()
        base = self._break_stats()
        f = self.fatigue()
        life = f.life_left

        def evaluate(q: float) -> Dict:
            v = q * price
            deed = v * mu if (inside is not False) else 0.0  # out of its water, the crocodile's strength does not count
            d_tail = self._tail_cost({iid: q}) - base_tail
            standalone = abs(v) * es_i
            allies = p.risk_aversion * (standalone - d_tail)
            opponent = estimate_cost(ws, iid, q) + p.risk_aversion * standalone
            net = deed + allies - opponent
            after = self._break_stats({iid: q})
            d_damage = after["expected_peak_damage"] - base["expected_peak_damage"]
            risk_reducing = after["break_probability"] <= base["break_probability"] + 1e-12 and d_damage <= 1e-9
            # Past the tip already? Then only trades that barely move the odds (by under one point) may climb.
            tip_ok = (after["break_probability"] <= p.max_break_probability or risk_reducing
                      or after["break_probability"] <= base["break_probability"] + p.tip_tolerance)
            spare_ok = d_damage <= p.spare_share * life or risk_reducing
            return {"quantity": q, "value": v, "deed": deed, "allies": allies, "opponent": opponent, "net": net,
                    "break_probability_after": after["break_probability"], "added_damage": d_damage,
                    "tip_ok": tip_ok, "spare_ok": spare_ok, "net_ok": net > 0 or risk_reducing,
                    "risk_reducing": risk_reducing}

        full = evaluate(quantity)
        buying = quantity > 0
        if not full["tip_ok"]:
            decision = "block"
        elif buying and inside is False and not full["net_ok"]:
            decision = "wait"
        elif not (full["net_ok"] and full["spare_ok"]):
            decision = "hold"
        else:
            decision = "allow"

        safe_q = quantity if decision == "allow" else 0.0
        if search_size and decision in ("hold", "block") and buying:
            lo, hi = 0.0, 1.0
            for _ in range(6):
                mid = (lo + hi) / 2
                e = evaluate(quantity * mid)
                if e["tip_ok"] and e["spare_ok"] and e["net_ok"]:
                    lo = mid
                else:
                    hi = mid
            unit = 1 if ws.master.get(iid).asset_class != "Fixed Income" else 100
            safe_q = float(np.floor(quantity * lo / unit) * unit)

        side = "buy" if buying else "sell"
        reasons = {
            "allow": f"Allowed. The trade is worth more than the market's resistance, uses only a small share of the "
                     f"remaining strength, and leaves the chance of breaking within a year at "
                     f"{full['break_probability_after']:.0%}.",
            "hold": "Held for reflection. " + (
                "The trade's expected benefit does not outweigh its cost and added risk. " if not full["net_ok"] else
                "") + ("It would use more than a quarter of the investor's remaining strength. " if not full["spare_ok"]
                       else "") + f"Write down the reason and confirm after a pause. {VERSES['467']}",
            "wait": f"Wait. Today's market ({hg.attrs['state_now'].lower()}) is outside the home ground of {name}: its "
                    f"record in this kind of market does not support buying more now. {VERSES['495']} {VERSES['490']}",
            "block": (f"Blocked. The chance of the investor breaking within a year would rise from "
                      f"{base['break_probability']:.0%} to {full['break_probability_after']:.0%}, "
                      + (f"past the {p.max_break_probability:.0%} ceiling. " if base['break_probability']
                         <= p.max_break_probability else
                         f"and the investor is already past the {p.max_break_probability:.0%} ceiling. ")
                      + VERSES['476']),
        }
        plain = reasons[decision]
        if decision != "allow" and buying and safe_q > 0:
            plain += f" A smaller {side} of {safe_q:,.0f} would pass all the tests."
        return {
            "instrument": iid, "name": name, "side": side, "quantity": quantity, "decision": decision,
            "safe_quantity": safe_q, "inside_home_ground": inside, "state_now": hg.attrs["state_now"],
            "life_left": life, "break_probability_now": base["break_probability"],
            "strengths": {"deed": full["deed"], "self_added_damage": full["added_damage"],
                          "opponent": full["opponent"], "allies": full["allies"], "net": full["net"]},
            "tests": {"net_strength": bool(full["net_ok"]), "spare_strength": bool(full["spare_ok"]),
                      "tip_of_branch": bool(full["tip_ok"])},
            "break_probability_after": full["break_probability_after"], "plain": plain,
            "verse": VERSES["471"],
        }

    # ------------------------------------------------------------------ the whole picture
    def report(self) -> Dict:
        cap = self.capacity()
        f = self.fatigue()
        stats = self._break_stats()
        loads = self.loads()
        hg = self.home_ground()
        p = self.p
        dmg = f.damage
        weekly = dmg.iloc[::-5].iloc[::-1]  # every fifth day, always including today (with the open fall)
        worst = sorted(f.charged, key=lambda cd: -cd[1])[:5]
        hist_dates = list(self.ws.nav_history.index)
        state = ("strong" if f.now < 0.4 else "strained" if f.now < 0.7 else "near breaking" if f.now < 1 else
                 "past breaking point")
        plain = (f"The axle has {f.life_left:.0%} of its strength left ({state}). Over the history of these holdings, "
                 f"{len(f.charged)} falls were large enough to strain this investor; the rest were smaller than their "
                 f"{p.endurance:.1%} endurance threshold. The chance of reaching breaking point within a year is "
                 f"{stats['break_probability']:.0%}. Heaviest feather: {loads.iloc[0]['name']} "
                 f"({loads.iloc[0]['load_share']:.0%} of the load in falls). Market now: {hg.attrs['state_now'].lower()}.")
        return {
            "profile": p.as_dict(),
            "capacity": cap,
            "fatigue": {"damage_now": f.now, "life_left": f.life_left, "closed_damage": f.closed_damage_now,
                        "open_fall_damage": f.open_damage, "state": state,
                        "history": {"dates": [str(d.date()) for d in weekly.index], "damage": weekly.round(4).tolist()},
                        "worst_falls": [{"size": c.size, "damage": d, "from": str(hist_dates[c.start].date()),
                                         "to": str(hist_dates[min(c.end, len(hist_dates) - 1)].date()),
                                         "still_open": c.open_fall} for c, d in worst]},
            "break_probability": stats["break_probability"],
            "expected_peak_damage": stats["expected_peak_damage"],
            "loads": loads.reset_index().rename(columns={"index": "id"}).to_dict(orient="records"),
            "home_ground": {"state_now": hg.attrs["state_now"],
                            "holdings": hg.reset_index()[["id", "name", "home_ground", "inside_now"]]
                            .to_dict(orient="records")},
            "verses": VERSES,
            "plain": plain,
        }


def calibrate_profile(values: pd.Series, events, profile: Optional[AxleProfile] = None, capacity: float = None) -> AxleProfile:
    """Fit exponent, endurance and healing to the moments an investor gave up, and return an updated profile."""
    p = profile or AxleProfile()
    cap = capacity if capacity is not None else p.stated_tolerance * p.safety_margin
    cal = F.calibrate(values, events, cap)
    eq = float(np.clip(p.equanimity, 0.0, 1.0))
    return AxleProfile(**{**asdict(p), "exponent": cal.exponent, "endurance_base": cal.endurance / (0.5 + eq),
                          "healing_half_life_days": cal.healing_half_life / (1.0 + p.discipline)})
