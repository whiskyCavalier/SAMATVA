"""Real-time data processing: the showcase test module.

These tests do more than pass or fail. Each one measures something about the
stream processor and records it, and the final test prints a short report.
Run them on their own to see it:

    pytest tests/test_realtime_showcase.py -s

What is demonstrated:
  1. Correctness  — the incrementally updated NAV equals a full recomputation,
                    after hundreds of thousands of ticks.
  2. Speed        — sustained throughput and per-tick latency on one CPU core.
  3. Constant time — per-tick cost does not grow with the number of ticks seen.
  4. Realism      — the simulated feed reproduces the holdings' real volatility
                    and correlations.
  5. Detection    — jumps, limit breaches, VaR breaches and data outages each
                    raise exactly one alert, within one tick of happening.
  6. End to end   — the async engine streams to subscribers, a slow subscriber
                    always receives the latest snapshot, and the WebSocket API
                    works.
"""

import asyncio
import time

import numpy as np
import pytest

from openportfolio.realtime import AlertConfig, RealTimeEngine, SimulatedFeed, StreamProcessor, Tick, benchmark

REPORT: dict = {}


def _record(key, value):
    REPORT[key] = value


# --------------------------------------------------------------------------- 1. correctness
def test_incremental_nav_matches_full_recomputation(ws):
    feed = SimulatedFeed(ws, seed=11)
    proc = StreamProcessor(ws)
    for t in feed.generate(300_000):
        proc.process(t)
    full = float((proc.mult * proc.price).sum() + proc.cash)
    drift = abs(proc.nav - full)
    _record("ticks checked for NAV drift", "300,000")
    _record("NAV drift after 300k incremental updates", f"${drift:.2e}")
    assert drift < 1e-4


def test_snapshot_values_are_consistent(ws):
    proc = StreamProcessor(ws)
    for t in SimulatedFeed(ws, seed=2).generate(20_000):
        proc.process(t)
    s = proc.snapshot(check_stale=False)
    assert sum(p["pnl"] for p in s["positions"]) == pytest.approx(s["day_pnl"], abs=1e-6)
    assert sum(p["weight"] for p in s["positions"]) + proc.cash / s["nav"] == pytest.approx(1.0)
    assert s["es95"] > s["var95"] > 0


# --------------------------------------------------------------------------- 2-3. speed and constant time
def test_throughput_and_latency(ws):
    b = benchmark(ws, 500_000)
    _record("throughput (one core)", f"{b['ticks_per_second']:,.0f} ticks/s")
    _record("latency p50 / p99", f"{b['latency']['p50_us']:.1f} / {b['latency']['p99_us']:.1f} µs")
    _record("full live risk snapshot", f"{b['snapshot_ms']:.2f} ms")
    assert b["nav_end"] == pytest.approx(b["nav_check"], abs=1e-4)
    assert b["ticks_per_second"] > 50_000  # deliberately conservative for slow CI machines
    assert b["latency"]["p99_us"] < 200


def test_per_tick_cost_is_constant(ws):
    feed = SimulatedFeed(ws, seed=4)
    ticks = list(feed.generate(400_000))
    proc = StreamProcessor(ws)

    def timed(chunk):
        t0 = time.perf_counter()
        for t in chunk:
            proc.process(t)
        return (time.perf_counter() - t0) / len(chunk)

    early = timed(ticks[:100_000])
    timed(ticks[100_000:300_000])
    late = timed(ticks[300_000:])
    ratio = late / early
    _record("per-tick cost, last 100k vs first 100k", f"{ratio:.2f}x (1.00 = perfectly constant)")
    assert ratio < 1.5


# --------------------------------------------------------------------------- 4. realism
def test_simulated_feed_reproduces_real_volatility_and_correlation(ws):
    feed = SimulatedFeed(ws, ticks_per_second=1000, day_seconds=390, seed=21)
    ids = feed.ids
    n_days = 400
    steps_per_day = int(round(1 / feed.step_fraction))
    closes = []
    for _ in range(n_days):
        for _ in range(steps_per_day):
            px = feed._step()
        closes.append(px.copy())
    sim = np.diff(np.log(np.array(closes)), axis=0)
    hist = ws.market.prices[ids].ffill().pct_change(fill_method=None).dropna()
    vol_ratio = sim.std(axis=0) / hist.std().values
    c_sim = np.corrcoef(sim, rowvar=False)
    c_hist = hist.corr().values
    corr_err = np.abs(c_sim - c_hist)[np.triu_indices(len(ids), 1)].mean()
    _record("simulated / historical daily volatility", f"{vol_ratio.min():.2f} to {vol_ratio.max():.2f}")
    _record("mean correlation error", f"{corr_err:.3f}")
    assert np.all((vol_ratio > 0.85) & (vol_ratio < 1.15))
    assert corr_err < 0.08


def test_paused_instrument_stops_ticking(ws):
    feed = SimulatedFeed(ws, seed=1)
    feed.pause("NWT")
    assert all(t.instrument_id != "NWT" for t in feed.generate(5_000))
    feed.resume("NWT")
    assert any(t.instrument_id == "NWT" for t in feed.generate(100))


# --------------------------------------------------------------------------- 5. detection
def _warm(ws, cfg=None, n=20_000, seed=3):
    feed = SimulatedFeed(ws, seed=seed)
    proc = StreamProcessor(ws, cfg)
    for t in feed.generate(n):
        proc.process(t)
    return feed, proc


def test_jump_is_detected_on_the_very_next_tick(ws):
    feed, proc = _warm(ws)
    before = len(proc.alerts)
    feed.inject_shock(-0.05, instrument="HLX")
    for t in feed.generate(len(feed.ids)):  # exactly one step: one tick per instrument
        proc.process(t)
    new = list(proc.alerts)[before:]
    jumps = [a for a in new if a.kind == "jump"]
    assert len(jumps) == 1 and jumps[0].subject == "Helix Health"
    _record("jump detection delay", "0 ticks (flagged on the tick that moved)")


def test_var_breach_alert_fires_once(ws):
    feed, proc = _warm(ws)
    feed.inject_shock(-0.06, asset_class="Equity")
    for t in feed.generate(5_000):
        proc.process(t)
    losses = [a for a in proc.alerts if a.kind == "loss"]
    assert len(losses) == 1  # debounced: one event, one alert
    assert proc.nav_open - proc.nav > proc.var95_money_open


def test_position_limit_breach(ws):
    feed, proc = _warm(ws, AlertConfig(max_position_weight=0.12))
    feed.inject_shock(0.30, instrument="NWT")
    for t in feed.generate(len(feed.ids)):
        proc.process(t)
    limits = [a for a in proc.alerts if a.kind == "limit" and a.subject == "Northwind Technologies"]
    assert len(limits) == 1


def test_stale_feed_is_flagged(ws):
    proc = StreamProcessor(ws, AlertConfig(stale_seconds=2))
    now = time.time()
    for k, iid in enumerate(proc.ids):
        proc.process(Tick(iid, float(proc.price[k]), now if iid != "KST" else now - 10, k))
    proc.check_stale(now)
    stale = [a for a in proc.alerts if a.kind == "stale"]
    assert [a.subject for a in stale] == ["Kestrel Industrials"]


def test_bad_ticks_are_rejected_not_crashing(ws):
    proc = StreamProcessor(ws)
    nav = proc.nav
    proc.process(Tick("UNKNOWN", 10.0, time.time(), 1))
    proc.process(Tick("NWT", -1.0, time.time(), 2))
    assert proc.unknown == 2 and proc.nav == nav


# --------------------------------------------------------------------------- 6. end to end
def test_engine_streams_live_snapshots(ws):
    async def run():
        feed = SimulatedFeed(ws, ticks_per_second=5_000, seed=8)
        eng = RealTimeEngine(ws, feed, snapshot_interval=0.1)
        fast, slow = eng.subscribe(), eng.subscribe()
        await eng.start()
        snaps = [await fast.get() for _ in range(8)]
        await asyncio.sleep(0.5)  # the slow subscriber reads nothing for a while…
        backlog = slow.qsize()  # …yet holds at most one waiting snapshot…
        latest = await slow.get()  # …which is the newest one
        rate = snaps[-1]["stats"]["ticks_per_second"]
        await eng.stop()
        return snaps, latest, rate, eng.running, backlog

    snaps, latest, rate, running, backlog = asyncio.run(run())
    ticks = [s["stats"]["ticks"] for s in snaps]
    assert ticks == sorted(ticks) and ticks[-1] > 0
    assert latest["ts"] >= snaps[-1]["ts"]
    assert backlog == 1 and not running
    _record("paced feed throughput (target 5,000/s)", f"{rate:,.0f} ticks/s")
    assert 3_000 < rate < 7_000


def test_websocket_api_streams_and_accepts_shocks():
    from fastapi.testclient import TestClient

    from openportfolio.api.app import app, state

    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as sock:
            first = sock.receive_json()
            sock.send_json({"action": "shock", "move": -0.05, "asset_class": "Equity"})
            seen = first
            for _ in range(12):
                seen = sock.receive_json()
                if any(a["kind"] == "jump" for a in seen["alerts"]):
                    break
        assert {"nav", "var95", "positions", "alerts", "stats"} <= set(first)
        assert seen["day_pnl"] < first["day_pnl"]
        asyncio.run(state.engine.stop())


def test_print_showcase_report():
    """Prints the measurements collected above (visible with `pytest -s`)."""
    width = max(len(k) for k in REPORT) if REPORT else 0
    print("\n\nReal-time processing showcase")
    print("=" * 29)
    for k, v in REPORT.items():
        print(f"  {k:<{width}}  {v}")
    assert REPORT
