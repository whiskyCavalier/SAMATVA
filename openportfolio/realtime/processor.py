"""The stream processor: turns raw price ticks into live portfolio intelligence.

Design rule: **every tick is handled in constant time.** A tick changes one
price, so it changes one position's value; the portfolio value is updated by
that difference alone rather than recomputed from scratch. Heavier work — live
Value at Risk, top movers, statistics — runs on a timer in :meth:`snapshot`,
a few times a second, which is as fast as any human can read.

On each tick the processor:
1. updates the price, the position value and the portfolio value (O(1));
2. updates that instrument's exponentially weighted tick volatility (O(1));
3. raises an alert if the move is abnormally large for that instrument;
4. checks the position-size limit and the day's loss against Value at Risk;
5. records how long all of that took, in nanoseconds.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Deque, Dict, List, Optional

import numpy as np
from scipy import stats

from ..workspace import Workspace
from .feed import Tick


@dataclass
class Alert:
    ts: float
    kind: str  # jump | limit | loss | stale
    severity: str  # info | warning | critical
    subject: str
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class AlertConfig:
    jump_sigma: float = 6.0  # flag a tick move bigger than this many tick-standard-deviations
    max_position_weight: float = 0.21  # live version of compliance rule R1
    loss_vs_var: float = 1.0  # flag when today's loss exceeds this multiple of 1-day 95% VaR
    stale_seconds: float = 5.0  # flag an instrument with no tick for this long
    warmup_ticks: int = 50  # ticks per instrument before jump alerts switch on
    ewma_lambda: float = 0.97


class StreamProcessor:
    def __init__(self, ws: Workspace, config: Optional[AlertConfig] = None, latency_samples: int = 50_000) -> None:
        self.ws = ws
        self.cfg = config or AlertConfig()
        self.ids: List[str] = list(ws.held_ids)
        self.index: Dict[str, int] = {iid: k for k, iid in enumerate(self.ids)}
        q = ws.portfolio.quantities()
        latest = ws.market.latest_prices()
        self.names = [ws.master.get(i).name for i in self.ids]
        # Multiplier from local price to base-currency value: quantity × FX rate
        self.mult = np.array([q[i] * ws.market.fx_rate(ws.master.get(i).currency) for i in self.ids])
        self.open_price = latest[self.ids].values.astype(float)
        self.price = self.open_price.copy()
        self.value = self.mult * self.price
        self.cash = ws.portfolio.cash
        self.nav = float(self.value.sum() + self.cash)
        self.nav_open = self.nav
        self.nav_peak = self.nav
        self.max_intraday_dd = 0.0
        # Per-instrument tick statistics
        self.tick_var = np.zeros(len(self.ids))
        self.tick_count = np.zeros(len(self.ids), dtype=np.int64)
        self.last_seen = np.full(len(self.ids), time.time())
        # Daily covariance (base currency) for live VaR
        self.cov = ws.asset_returns[self.ids].fillna(0.0).cov().values
        self.z95 = stats.norm.ppf(0.95)
        self.var95_money_open = self._parametric_var()
        # Alerting state (debounced so one event raises one alert)
        self.alerts: Deque[Alert] = deque(maxlen=500)
        self._limit_on = np.zeros(len(self.ids), dtype=bool)
        self._loss_on = False
        self._stale_on = np.zeros(len(self.ids), dtype=bool)
        # Performance counters
        self.ticks = 0
        self.unknown = 0
        self.feed_delay = 0.0  # EWMA of (arrival time - exchange time), seconds; live feeds only
        self.latency_ns: Deque[int] = deque(maxlen=latency_samples)
        self.started = time.perf_counter()
        self._rate_mark = (self.started, 0)
        self.nav_trail: Deque[tuple] = deque(maxlen=600)

    # ------------------------------------------------------------------ hot path
    def process(self, tick: Tick) -> None:
        t0 = time.perf_counter_ns()
        k = self.index.get(tick.instrument_id)
        if k is None or tick.price <= 0:
            self.unknown += 1
            return
        old = self.price[k]
        r = tick.price / old - 1.0
        # 1. value and NAV, by difference only
        new_value = self.mult[k] * tick.price
        self.nav += new_value - self.value[k]
        self.value[k] = new_value
        self.price[k] = tick.price
        # 2. EWMA tick volatility
        n = self.tick_count[k]
        prev_var = self.tick_var[k]
        lam = self.cfg.ewma_lambda
        self.tick_var[k] = r * r if n == 0 else lam * prev_var + (1 - lam) * r * r
        self.tick_count[k] = n + 1
        self.last_seen[k] = tick.ts
        # 3. jump detection against the volatility *before* this tick
        if n >= self.cfg.warmup_ticks and prev_var > 0 and abs(r) > self.cfg.jump_sigma * prev_var ** 0.5:
            self._alert(tick.ts, "jump", "warning", self.names[k],
                        f"{self.names[k]} jumped {r:+.2%} in one tick, about {abs(r) / prev_var ** 0.5:.0f} times "
                        f"its usual tick move.")
        # 4a. drawdown tracking
        if self.nav > self.nav_peak:
            self.nav_peak = self.nav
        else:
            dd = 1 - self.nav / self.nav_peak
            if dd > self.max_intraday_dd:
                self.max_intraday_dd = dd
        # 4b. live position limit (debounced)
        w = new_value / self.nav
        over = w > self.cfg.max_position_weight
        if over and not self._limit_on[k]:
            self._alert(tick.ts, "limit", "critical", self.names[k],
                        f"{self.names[k]} is now {w:.1%} of the portfolio, above the {self.cfg.max_position_weight:.0%} limit.")
        self._limit_on[k] = over
        # 4c. day's loss versus VaR (debounced)
        loss = self.nav_open - self.nav
        breach = loss > self.cfg.loss_vs_var * self.var95_money_open
        if breach and not self._loss_on:
            self._alert(tick.ts, "loss", "critical", "Portfolio",
                        f"Today's loss of ${loss:,.0f} is larger than the 1-day 95% VaR of "
                        f"${self.var95_money_open:,.0f}. A 1-in-20 day is under way.")
        self._loss_on = breach
        if tick.source_ts:
            d = max(0.0, tick.ts - tick.source_ts)  # clocks can disagree by a few ms; never report negative
            self.feed_delay = d if self.ticks == 0 else 0.95 * self.feed_delay + 0.05 * d
        self.ticks += 1
        self.latency_ns.append(time.perf_counter_ns() - t0)

    # ------------------------------------------------------------------ periodic work
    def _alert(self, ts: float, kind: str, severity: str, subject: str, message: str) -> None:
        self.alerts.append(Alert(ts, kind, severity, subject, message))

    def _parametric_var(self) -> float:
        w = self.value / self.nav
        sd = float(np.sqrt(w @ self.cov @ w))
        return self.z95 * sd * self.nav

    def check_stale(self, now: Optional[float] = None) -> None:
        now = now or time.time()
        for k, seen in enumerate(self.last_seen):
            stale = now - seen > self.cfg.stale_seconds
            if stale and not self._stale_on[k]:
                self._alert(now, "stale", "warning", self.names[k],
                            f"No price for {self.names[k]} for {now - seen:.0f} seconds; its last price is being used.")
            self._stale_on[k] = stale

    def latency_stats(self) -> Dict[str, float]:
        if not self.latency_ns:
            return {"p50_us": 0.0, "p95_us": 0.0, "p99_us": 0.0, "max_us": 0.0, "mean_us": 0.0}
        a = np.fromiter(self.latency_ns, dtype=np.int64) / 1_000.0
        return {"p50_us": float(np.percentile(a, 50)), "p95_us": float(np.percentile(a, 95)),
                "p99_us": float(np.percentile(a, 99)), "max_us": float(a.max()), "mean_us": float(a.mean())}

    def throughput(self) -> float:
        """Ticks per second since the previous call (or since start)."""
        now = time.perf_counter()
        t_prev, n_prev = self._rate_mark
        self._rate_mark = (now, self.ticks)
        dt = now - t_prev
        return (self.ticks - n_prev) / dt if dt > 0 else 0.0

    def snapshot(self, check_stale: bool = True) -> Dict:
        if check_stale:
            self.check_stale()
        now = time.time()
        self.nav_trail.append((now, self.nav))
        w = self.value / self.nav
        sd = float(np.sqrt(w @ self.cov @ w))
        var95 = self.z95 * sd * self.nav
        es95 = sd * stats.norm.pdf(self.z95) / 0.05 * self.nav
        moves = self.price / self.open_price - 1
        pnl = self.value - self.mult * self.open_price
        order = np.argsort(-np.abs(pnl))
        positions = [{"id": self.ids[k], "name": self.names[k], "price": float(self.price[k]),
                      "move": float(moves[k]), "pnl": float(pnl[k]), "weight": float(w[k]),
                      "ticks": int(self.tick_count[k])} for k in order]
        day_pnl = self.nav - self.nav_open
        return {
            "ts": now,
            "nav": self.nav,
            "nav_open": self.nav_open,
            "day_pnl": day_pnl,
            "day_return": day_pnl / self.nav_open,
            "intraday_drawdown": self.max_intraday_dd,
            "var95": var95,
            "es95": es95,
            "var_used": max(0.0, -day_pnl) / self.var95_money_open if self.var95_money_open else 0.0,
            "positions": positions,
            "alerts": [a.as_dict() for a in list(self.alerts)[-20:]][::-1],
            "stats": {"ticks": self.ticks, "ticks_per_second": self.throughput(), "rejected": self.unknown,
                      "feed_delay_ms": self.feed_delay * 1000,
                      "uptime_s": time.perf_counter() - self.started, **self.latency_stats()},
            "trail": [v for _, v in self.nav_trail],
        }


def benchmark(ws: Workspace, n_ticks: int = 1_000_000, seed: int = 1) -> Dict:
    """Measure raw processing speed with no network or pacing in the way.

    Ticks are generated first, so the timing covers processing only.
    """
    from .feed import SimulatedFeed

    feed = SimulatedFeed(ws, ticks_per_second=1e6, seed=seed)
    ticks = list(feed.generate(n_ticks))
    proc = StreamProcessor(ws)
    t0 = time.perf_counter()
    for t in ticks:
        proc.process(t)
    elapsed = time.perf_counter() - t0
    snap_t0 = time.perf_counter()
    snap = proc.snapshot(check_stale=False)
    snap_ms = (time.perf_counter() - snap_t0) * 1000
    lat = proc.latency_stats()
    return {
        "ticks": n_ticks,
        "seconds": elapsed,
        "ticks_per_second": n_ticks / elapsed,
        "latency": lat,
        "snapshot_ms": snap_ms,
        "nav_end": snap["nav"],
        "nav_check": float(proc.value.sum() + proc.cash),
        "alerts": len(proc.alerts),
        "plain": (f"Processed {n_ticks:,} ticks in {elapsed:.2f} seconds — about {n_ticks / elapsed:,.0f} ticks a "
                  f"second on one CPU core. Of the last 50,000 ticks, half were handled within {lat['p50_us']:.1f} µs and 99% "
                  f"within {lat['p99_us']:.1f} µs. A full live risk snapshot takes {snap_ms:.2f} ms."),
    }
