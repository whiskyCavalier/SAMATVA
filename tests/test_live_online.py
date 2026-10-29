"""Online tests against the real providers. Skipped unless you ask for them.

    SAMATVA_RUN_LIVE_TESTS=1 pytest -m live -s

Each test needs internet access. Coinbase and Yahoo need no key. The Finnhub
and Alpaca tests run only when their keys are set in .env or the environment,
and they never print the keys. US stock trades arrive only while the market
is open (9:30 to 16:00 New York time on weekdays); outside those hours the
Alpaca test uses its always-on FAKEPACA stream and the Finnhub test uses a
crypto pair, so every test can pass at any hour.
"""

import asyncio
import os
import time

import pytest

from samatva.config import load_env

load_env()
RUN = os.environ.get("SAMATVA_RUN_LIVE_TESTS") == "1"
pytestmark = [pytest.mark.live, pytest.mark.skipif(not RUN, reason="set SAMATVA_RUN_LIVE_TESTS=1 to run online tests")]


def _first_ticks(feed, n=3, timeout=30.0):
    async def run():
        got, t0 = [], time.perf_counter()
        async for t in feed.stream():
            got.append(t)
            if len(got) >= n:
                feed.stop()
                break
        return got, time.perf_counter() - t0

    return asyncio.run(asyncio.wait_for(run(), timeout))


def test_coinbase_streams_real_prices():
    from samatva.realtime.providers import CoinbaseFeed

    ticks, secs = _first_ticks(CoinbaseFeed({"BTC-USD": "BTC", "ETH-USD": "ETH"}))
    print(f"\n  Coinbase: {len(ticks)} ticks in {secs:.1f} s; BTC-USD around {ticks[0].price:,.2f}")
    assert all(t.price > 0 for t in ticks)


@pytest.mark.skipif(not os.environ.get("FINNHUB_API_KEY"), reason="FINNHUB_API_KEY not set")
def test_finnhub_streams_real_trades():
    from samatva.realtime.providers import FinnhubFeed

    ticks, secs = _first_ticks(FinnhubFeed({"BINANCE:BTCUSDT": "BTC", "AAPL": "AAPL"}), timeout=45)
    print(f"\n  Finnhub: {len(ticks)} trades in {secs:.1f} s")
    assert ticks


@pytest.mark.skipif(not (os.environ.get("ALPACA_API_KEY_ID") and os.environ.get("ALPACA_API_SECRET_KEY")),
                    reason="Alpaca keys not set")
def test_alpaca_test_stream():
    from samatva.realtime.providers import AlpacaFeed

    feed = AlpacaFeed({"X": "X"}, feed="test", proxy_instrument="X")
    ticks, secs = _first_ticks(feed, timeout=45)
    print(f"\n  Alpaca test stream: {len(ticks)} trades in {secs:.1f} s")
    assert ticks


def test_real_portfolio_builds_from_yahoo_history():
    from samatva.data.live_universe import build_live_workspace
    from samatva.modules import hub

    ws = build_live_workspace("examples/live_stocks.csv", benchmark="SPY")
    print("\n  " + hub.describe(ws))
    assert len(ws.market.prices) > 250


def test_full_engine_on_real_coinbase_prices():
    from samatva.data.live_universe import build_live_workspace
    from samatva.realtime import AlertConfig, RealTimeEngine
    from samatva.realtime.providers import make_feed

    ws = build_live_workspace("examples/live_crypto.csv")

    async def run():
        eng = RealTimeEngine(ws, make_feed("coinbase", ws), AlertConfig(stale_seconds=120), snapshot_interval=1.0)
        q = eng.subscribe()
        await eng.start()
        snap = None
        for _ in range(10):
            snap = await q.get()
        await eng.stop()
        return snap

    snap = asyncio.run(run())
    print(f"\n  10 s of live crypto: {snap['stats']['ticks']} ticks, NAV {snap['nav']:,.0f}, "
          f"day P/L {snap['day_pnl']:+,.0f}")
    assert snap["stats"]["ticks"] > 0 and snap["source"]["status"] == "connected"
