"""Market data feeds for the real-time module.

Two feeds share one shape — an async stream of :class:`Tick` objects — so the
rest of the system cannot tell them apart:

* :class:`SimulatedFeed` produces realistic, correlated price ticks from the
  portfolio's own history. It needs no network and no licence, which makes it
  ideal for demonstrations, testing and training. Shocks and outages can be
  injected on demand to show how the platform reacts.
* :class:`PollingFeed` turns any quote source (for example the free
  ``YahooQuoteSource``, or a licensed vendor) into the same stream.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import AsyncIterator, Dict, Iterator, List, Optional, Set

import numpy as np

from ..workspace import Workspace


@dataclass(slots=True)
class Tick:
    instrument_id: str
    price: float  # local currency
    ts: float  # seconds since the epoch, when the tick reached us
    seq: int
    source_ts: float = 0.0  # when the exchange says the trade happened (0 if unknown)


class SimulatedFeed:
    """Correlated random-walk ticks calibrated to each holding's real behaviour.

    Each *step* draws one correlated move for every instrument (using the
    Cholesky factor of the historical covariance matrix) and emits one tick per
    instrument. ``day_seconds`` sets how many real seconds a simulated trading
    day lasts: at the default of 390, one real second is one market minute, so
    movements are lively enough to watch but statistically faithful.
    """

    def __init__(self, ws: Workspace, ticks_per_second: float = 500.0, day_seconds: float = 390.0,
                 seed: Optional[int] = None, instruments: Optional[List[str]] = None) -> None:
        self.ws = ws
        self.ids = instruments or ws.held_ids
        rets = ws.market.prices[self.ids].ffill().pct_change(fill_method=None).iloc[1:].fillna(0.0)
        cov = np.cov(rets.values, rowvar=False)
        self.chol = np.linalg.cholesky(cov + np.eye(len(self.ids)) * 1e-12)
        self.prices = ws.market.latest_prices()[self.ids].values.astype(float).copy()
        self.ticks_per_second = ticks_per_second
        self.steps_per_second = max(ticks_per_second / len(self.ids), 1e-9)
        self.step_fraction = 1.0 / (self.steps_per_second * day_seconds)  # share of a trading day per step
        self.rng = np.random.default_rng(seed)
        self.seq = 0
        self._pending: Dict[str, float] = {}
        self._paused: Set[str] = set()
        self._running = False

    # ------------------------------------------------------------------ controls
    def inject_shock(self, move: float, instrument: Optional[str] = None, asset_class: Optional[str] = None) -> List[str]:
        """Apply a one-off jump (e.g. -0.05 for a 5% fall) on the next step.

        With no instrument or asset class given, every instrument moves (a market shock).
        """
        targets = []
        for iid in self.ids:
            inst = self.ws.master.get(iid)
            if instrument and iid != instrument:
                continue
            if asset_class and inst.asset_class != asset_class:
                continue
            targets.append(iid)
        for iid in targets:
            self._pending[iid] = (1 + self._pending.get(iid, 0.0)) * (1 + move) - 1
        return targets

    def pause(self, instrument: str) -> None:
        """Simulate a data outage: this instrument stops ticking."""
        self._paused.add(instrument)

    def resume(self, instrument: str) -> None:
        self._paused.discard(instrument)

    def stop(self) -> None:
        self._running = False

    def describe(self) -> Dict:
        return {"provider": "simulated", "status": "running" if self._running else "stopped",
                "symbols": len(self.ids)}

    # ------------------------------------------------------------------ generation
    def _step(self) -> np.ndarray:
        z = self.rng.standard_normal(len(self.ids))
        moves = self.chol @ z * np.sqrt(self.step_fraction)
        if self._pending:
            for iid, jump in self._pending.items():
                k = self.ids.index(iid)
                moves[k] = (1 + moves[k]) * (1 + jump) - 1
            self._pending.clear()
        self.prices *= 1 + moves
        return self.prices

    def generate(self, n_ticks: int, start_ts: Optional[float] = None) -> Iterator[Tick]:
        """Synchronous generator (no waiting) — used for benchmarks and tests."""
        ts = start_ts or time.time()
        dt = 1.0 / self.ticks_per_second
        emitted = 0
        while emitted < n_ticks:
            px = self._step()
            for k, iid in enumerate(self.ids):
                if iid in self._paused:
                    continue
                self.seq += 1
                emitted += 1
                ts += dt
                yield Tick(iid, float(px[k]), ts, self.seq)
                if emitted >= n_ticks:
                    return

    async def stream(self) -> AsyncIterator[Tick]:
        """Ticks paced in real time at roughly ``ticks_per_second``."""
        self._running = True
        interval = 1.0 / self.steps_per_second
        next_t = time.perf_counter()
        while self._running:
            px = self._step()
            now = time.time()
            for k, iid in enumerate(self.ids):
                if iid in self._paused:
                    continue
                self.seq += 1
                yield Tick(iid, float(px[k]), now, self.seq)
            next_t += interval
            delay = next_t - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            elif delay < -1.0:  # fell far behind (e.g. a slow machine); catch up rather than burst
                next_t = time.perf_counter()
            else:
                await asyncio.sleep(0)


class PollingFeed:
    """Wrap any quote source that returns {ticker: price} into a tick stream."""

    def __init__(self, ws: Workspace, source, interval: float = 5.0) -> None:
        self.ws = ws
        self.source = source
        self.interval = interval
        self.ticker_to_id = {ws.master.get(i).ticker: i for i in ws.held_ids if ws.master.get(i).ticker}
        self.seq = 0
        self.status = "starting"
        self.last_error: Optional[str] = None
        self._running = False

    def stop(self) -> None:
        self._running = False

    def describe(self) -> Dict:
        return {"provider": "polling", "status": self.status, "symbols": len(self.ticker_to_id),
                "last_error": self.last_error}

    async def stream(self) -> AsyncIterator[Tick]:
        self._running = True
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                quotes = await loop.run_in_executor(None, self.source.latest, list(self.ticker_to_id))
                self.status = "polling"
            except Exception as exc:  # a failed poll must not stop the stream; try again next round
                quotes, self.status, self.last_error = {}, "retrying", f"{type(exc).__name__}: {exc}"
            now = time.time()
            for ticker, price in quotes.items():
                if ticker in self.ticker_to_id and price and price > 0:
                    self.seq += 1
                    yield Tick(self.ticker_to_id[ticker], float(price), now, self.seq)
            await asyncio.sleep(self.interval)
