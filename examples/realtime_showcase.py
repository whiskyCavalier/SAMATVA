"""Real-time showcase: stream ticks, inject a crash, and watch the platform react.

Run from the project folder:  python examples/realtime_showcase.py

The script runs the real asynchronous engine for twelve seconds at 20,000
ticks a second, drops equities by 5% at second six, takes a data feed offline
at second eight, and prints what the processor saw and when.
"""

import asyncio

from samatva.realtime import AlertConfig, RealTimeEngine, SimulatedFeed, benchmark
from samatva.workspace import Workspace


async def main() -> None:
    ws = Workspace.sample()
    feed = SimulatedFeed(ws, ticks_per_second=20_000, seed=7)
    engine = RealTimeEngine(ws, feed, AlertConfig(stale_seconds=2), snapshot_interval=1.0)
    snapshots = engine.subscribe()
    await engine.start()

    print("sec        NAV     day P/L   VaR used   ticks/s   p99 µs")
    for second in range(1, 13):
        snap = await snapshots.get()
        if second == 6:
            feed.inject_shock(-0.05, asset_class="Equity")
            print("     >>> equities fall 5% in an instant")
        if second == 8:
            feed.pause("KST")
            print("     >>> the price feed for Kestrel Industrials goes silent")
        s = snap["stats"]
        print(f"{second:>3}  {snap['nav']:>11,.0f}  {snap['day_pnl']:>+10,.0f}   {snap['var_used']:>7.0%}  "
              f"{s['ticks_per_second']:>8,.0f}  {s['p99_us']:>7.1f}")
    await engine.stop()

    print("\nAlerts raised, in order:")
    for a in engine.processor.alerts:
        print(f"  [{a.severity:<8}] {a.message}")

    print("\nRaw speed, with no pacing:")
    print("  " + benchmark(ws, 1_000_000)["plain"])


if __name__ == "__main__":
    asyncio.run(main())
