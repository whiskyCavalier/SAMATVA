"""Live market data: keys, provider protocols, reconnection and real-portfolio building.

Everything here runs offline. Each provider's protocol is reproduced by a
small local WebSocket server that behaves as the real one does (including
rejecting bad keys and dropping the connection), so the tests are fast and
repeatable. Tests that talk to the real providers live in
``test_live_online.py`` and run only when you ask for them.
"""

import asyncio
import json
import os
from http import HTTPStatus

import numpy as np
import pandas as pd
import pytest
from websockets.asyncio.server import serve

from openportfolio import config
from openportfolio.data.live_universe import build_live_workspace, read_portfolio_file
from openportfolio.realtime import RealTimeEngine
from openportfolio.realtime.providers import (AlpacaFeed, AuthError, CoinbaseFeed, FeedError, FinnhubFeed,
                                              _iso_to_epoch, make_feed, symbol_map)

GOOD_KEY = "test-key-123456"


# --------------------------------------------------------------------------- keys and settings
def test_env_file_parsing(tmp_path, monkeypatch):
    for v in ("OPP_A", "OPP_B", "OPP_C", "OPP_D", "OPP_KEEP"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("OPP_KEEP", "from-environment")
    f = tmp_path / ".env"
    f.write_text('# comment\n\nOPP_A=plain\nexport OPP_B="quoted value"\nOPP_C=abc  # note\nOPP_KEEP=from-file\n'
                 "OPP_D='single'\nnot a setting\n")
    loaded = config.load_env(f)
    assert os.environ["OPP_A"] == "plain"
    assert os.environ["OPP_B"] == "quoted value"
    assert os.environ["OPP_C"] == "abc"
    assert os.environ["OPP_D"] == "single"
    assert os.environ["OPP_KEEP"] == "from-environment"  # a real environment variable wins
    assert "OPP_KEEP" not in loaded
    assert config.load_env(tmp_path / "missing.env") == {}


def test_keys_are_masked_and_errors_name_the_variable(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "abcd1234secretwxyz")
    assert config.mask("abcd1234secretwxyz") == "abcd…wxyz"
    assert config.mask("short") == "•••••"
    assert config.mask(None) == "(not set)"
    shown = json.dumps(config.key_status(), ensure_ascii=False)
    assert "abcd1234secretwxyz" not in shown and "abcd…wxyz" in shown
    monkeypatch.delenv("FINNHUB_API_KEY")
    with pytest.raises(config.MissingKeyError, match="FINNHUB_API_KEY"):
        config.get_key("FINNHUB_API_KEY", "finnhub")


def test_settings_from_environment(monkeypatch):
    monkeypatch.setenv("OPP_DATA_PROVIDER", "coinbase")
    monkeypatch.delenv("OPP_STALE_SECONDS", raising=False)
    st = config.Settings.from_env()
    assert st.provider == "coinbase" and st.stale_seconds == 120  # live feeds get a longer stale window
    monkeypatch.setenv("OPP_DATA_PROVIDER", "bloomberg-by-magic")
    with pytest.raises(ValueError, match="Choose one of"):
        config.Settings.from_env()


# --------------------------------------------------------------------------- message parsing
def test_timestamp_parsing_handles_nanoseconds():
    assert _iso_to_epoch("2021-02-22T15:51:44.208123456Z") == pytest.approx(1614009104.208123)
    assert _iso_to_epoch("2021-02-22T15:51:44Z") == pytest.approx(1614009104.0)


def test_finnhub_parser():
    f = FinnhubFeed({"AAPL": "AAPL", "BINANCE:BTCUSDT": "BTC"}, api_key=GOOD_KEY)
    msg = {"type": "trade", "data": [{"s": "AAPL", "p": 190.5, "t": 1700000000123, "v": 10},
                                     {"s": "BINANCE:BTCUSDT", "p": 65000.0, "t": 1700000000456, "v": 0.1},
                                     {"s": "UNKNOWN", "p": 1.0, "t": 1, "v": 1}]}
    ticks = f.parse(json.dumps(msg))
    assert [(t.instrument_id, t.price) for t in ticks] == [("AAPL", 190.5), ("BTC", 65000.0)]
    assert ticks[0].source_ts == pytest.approx(1700000000.123)
    assert f.parse('{"type":"ping"}') == []
    assert f.parse('{"type":"error","msg":"Subscribing to too many symbols"}') == []
    assert "too many" in f.last_error
    assert "token=" + GOOD_KEY in f.url()


def test_alpaca_parser_and_error_codes():
    f = AlpacaFeed({"AAPL": "AAPL"}, key_id="k", secret="s", feed="iex")
    raw = json.dumps([{"T": "t", "S": "AAPL", "p": 126.55, "s": 1, "t": "2021-02-22T15:51:44.208Z"},
                      {"T": "q", "S": "AAPL", "bp": 1, "ap": 2}])
    ticks = f.parse(raw)
    assert len(ticks) == 1 and ticks[0].price == 126.55
    with pytest.raises(FeedError, match="one at a time"):
        f.parse(json.dumps([{"T": "error", "code": 406, "msg": "connection limit exceeded"}]))
    assert f.url().endswith("/v2/iex")
    assert AlpacaFeed({"BTC/USD": "BTC"}, key_id="k", secret="s", feed="crypto").url().endswith("/v1beta3/crypto/us")


def test_alpaca_test_stream_moves_a_proxy_holding_by_percentage():
    f = AlpacaFeed({"AAPL": "AAPL"}, key_id="k", secret="s", feed="test", proxy_instrument="AAPL")
    f.proxy_open = 200.0
    a = f.parse(json.dumps([{"T": "t", "S": "FAKEPACA", "p": 50.0, "t": "2026-01-01T00:00:00Z"}]))
    b = f.parse(json.dumps([{"T": "t", "S": "FAKEPACA", "p": 51.0, "t": "2026-01-01T00:00:01Z"}]))
    assert a[0].instrument_id == "AAPL" and a[0].price == 200.0
    assert b[0].price == pytest.approx(204.0)  # FAKEPACA rose 2%, so the proxy rises 2%


def test_coinbase_parser():
    f = CoinbaseFeed({"BTC-USD": "BTC", "ETH-USD": "ETH"})
    msg = {"channel": "ticker", "timestamp": "2026-10-27T12:00:00.123456Z", "sequence_num": 1,
           "events": [{"type": "update", "tickers": [{"type": "ticker", "product_id": "BTC-USD", "price": "80000.5"},
                                                     {"type": "ticker", "product_id": "ETH-USD", "price": "2400"}]}]}
    ticks = f.parse(json.dumps(msg))
    assert [(t.instrument_id, t.price) for t in ticks] == [("BTC", 80000.5), ("ETH", 2400.0)]
    assert f.parse(json.dumps({"channel": "heartbeats", "events": []})) == []
    assert f.parse(json.dumps({"channel": "subscriptions", "events": []})) == []


def test_bad_prices_and_unknown_symbols_are_ignored():
    f = CoinbaseFeed({"BTC-USD": "BTC"})
    msg = {"channel": "ticker", "events": [{"tickers": [{"product_id": "BTC-USD", "price": "0"},
                                                        {"product_id": "DOGE-USD", "price": "0.1"}]}]}
    assert f.parse(json.dumps(msg)) == []


# --------------------------------------------------------------------------- local protocol servers
async def _finnhub_server(handler_state):
    """Behaves like Finnhub: rejects a wrong token with HTTP 401, then streams trades for subscriptions."""

    def check(connection, request):
        if f"token={GOOD_KEY}" not in request.path:
            return connection.respond(HTTPStatus.UNAUTHORIZED, "Invalid API key\n")
        return None

    async def handler(conn):
        handler_state["connections"] += 1
        subs = []
        msg = json.loads(await conn.recv())
        subs.append(msg["symbol"])
        price = 100.0
        for i in range(handler_state.get("per_connection", 50)):
            price *= 1.001
            await conn.send(json.dumps({"type": "trade", "data": [{"s": subs[0], "p": price, "t": 1, "v": 1}]}))
            await asyncio.sleep(0.002)
        if handler_state.get("drop"):
            return  # closing the connection makes the client reconnect
        await asyncio.sleep(5)

    return serve(handler, "127.0.0.1", 0, process_request=check)


def _port(server):
    return next(iter(server.sockets)).getsockname()[1]


def test_finnhub_round_trip_and_rejected_key():
    async def run():
        state = {"connections": 0}
        async with await _finnhub_server(state) as server:
            url = f"ws://127.0.0.1:{_port(server)}"
            good = FinnhubFeed({"AAPL": "AAPL"}, api_key=GOOD_KEY, base_url=url)
            got = []
            async for t in good.stream():
                got.append(t)
                if len(got) == 20:
                    good.stop()
                    break
            bad = FinnhubFeed({"AAPL": "AAPL"}, api_key="wrong", base_url=url, max_retries=3)
            with pytest.raises(AuthError, match="HTTP 401"):
                async for _ in bad.stream():
                    pass
            return got, bad
    got, bad = asyncio.run(run())
    assert len(got) == 20 and got[-1].price > got[0].price
    assert bad.status == "stopped: key rejected" and bad.reconnects == 0  # no pointless retries on a bad key


def test_feed_reconnects_after_the_server_drops_it():
    async def run():
        state = {"connections": 0, "per_connection": 10, "drop": True}
        async with await _finnhub_server(state) as server:
            f = FinnhubFeed({"AAPL": "AAPL"}, api_key=GOOD_KEY, base_url=f"ws://127.0.0.1:{_port(server)}",
                            backoff_start=0.05)
            n = 0
            async for _ in f.stream():
                n += 1
                if n == 25:  # needs three separate connections at 10 trades each
                    f.stop()
                    break
            return state["connections"], f.reconnects
    connections, reconnects = asyncio.run(run())
    assert connections >= 3 and reconnects >= 2


def test_alpaca_handshake_success_and_failure():
    async def handler(conn):
        await conn.send(json.dumps([{"T": "success", "msg": "connected"}]))
        auth = json.loads(await conn.recv())
        if auth.get("key") != "good" or auth.get("secret") != "secret":
            await conn.send(json.dumps([{"T": "error", "code": 402, "msg": "auth failed"}]))
            return
        await conn.send(json.dumps([{"T": "success", "msg": "authenticated"}]))
        sub = json.loads(await conn.recv())
        assert sub == {"action": "subscribe", "trades": ["AAPL"]}
        for i in range(5):
            await conn.send(json.dumps([{"T": "t", "S": "AAPL", "p": 100 + i, "t": "2026-10-27T15:00:00.1Z"}]))
        await asyncio.sleep(5)

    async def run():
        async with serve(handler, "127.0.0.1", 0) as server:
            base = f"ws://127.0.0.1:{_port(server)}"
            ok = AlpacaFeed({"AAPL": "AAPL"}, key_id="good", secret="secret", feed="iex", base_url=base)
            prices = []
            async for t in ok.stream():
                prices.append(t.price)
                if len(prices) == 5:
                    ok.stop()
                    break
            bad = AlpacaFeed({"AAPL": "AAPL"}, key_id="good", secret="wrong", feed="iex", base_url=base)
            with pytest.raises(AuthError, match="rejected"):
                async for _ in bad.stream():
                    pass
            return prices
    assert asyncio.run(run()) == [100, 101, 102, 103, 104]


def test_engine_runs_end_to_end_on_a_websocket_feed(ws):
    """A Coinbase-style server drives the real engine; the portfolio value moves with it."""
    async def handler(conn):
        sub = json.loads(await conn.recv())
        await conn.recv()  # heartbeats subscription
        product = sub["product_ids"][0]
        price = 100.0
        for _ in range(400):
            price *= 0.9995
            await conn.send(json.dumps({"channel": "ticker", "timestamp": "2026-10-27T12:00:00Z",
                                        "events": [{"tickers": [{"product_id": product, "price": str(price)}]}]}))
            await asyncio.sleep(0.001)
        await asyncio.sleep(5)

    inst = ws.master.get("NWT")
    inst.symbols["coinbase"] = "NWT-USD"
    try:
        async def run():
            async with serve(handler, "127.0.0.1", 0) as server:
                feed = CoinbaseFeed(symbol_map(ws, "coinbase"), base_url=f"ws://127.0.0.1:{_port(server)}")
                eng = RealTimeEngine(ws, feed, snapshot_interval=0.1)
                q = eng.subscribe()
                await eng.start()
                snap = None
                for _ in range(30):
                    snap = await q.get()
                    if snap["stats"]["ticks"] >= 400:
                        break
                await eng.stop()
                return snap
        snap = asyncio.run(run())
    finally:
        inst.symbols.pop("coinbase")
    assert snap["stats"]["ticks"] >= 400
    assert snap["source"]["provider"] == "coinbase" and snap["source"]["status"] in ("connected", "stopped")
    nwt = next(p for p in snap["positions"] if p["id"] == "NWT")
    assert nwt["pnl"] < 0  # the server's price kept falling, so this holding lost money


# --------------------------------------------------------------------------- choosing a feed
def test_make_feed_checks_keys_first(ws, monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    with pytest.raises(config.MissingKeyError, match="FINNHUB_API_KEY"):
        make_feed("finnhub", ws)
    with pytest.raises(config.MissingKeyError, match="ALPACA_API_KEY_ID"):
        make_feed("alpaca", ws)
    with pytest.raises(ValueError, match="No holdings have a symbol"):
        make_feed("coinbase", ws)  # the sample fund's fictional companies trade nowhere
    assert make_feed("simulated", ws).__class__.__name__ == "SimulatedFeed"


def test_alpaca_test_mode_picks_a_proxy_holding(ws, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "k")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "s")
    f = make_feed("alpaca-test", ws)
    assert f.symbol_map == {"FAKEPACA": f.proxy_instrument}
    assert f.proxy_open == pytest.approx(float(ws.market.latest_prices()[f.proxy_instrument]))


# --------------------------------------------------------------------------- real-portfolio building
def _fake_history(start="2024-01-01", days=500, seed=0, fail=()):
    rng = np.random.default_rng(seed)
    calls = []

    def fetch(symbol):
        calls.append(symbol)
        if symbol in fail:
            raise RuntimeError("HTTP Error 429: Too Many Requests")
        idx = pd.date_range(start, periods=days, freq="D")
        px = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, days)))
        return pd.DataFrame({"close": px, "volume": 1_000_000.0}, index=idx)

    fetch.calls = calls
    return fetch


def test_portfolio_file_reading(tmp_path):
    f = tmp_path / "p.csv"
    f.write_text("id,name,asset_class,quantity,yahoo,coinbase,tags\nBTC,Bitcoin,Crypto,2,BTC-USD,BTC-USD,a;b\n"
                 "CASH,,,500,,,\n")
    master, pf = read_portfolio_file(f)
    btc = master.get("BTC")
    assert pf.cash == 500 and pf.quantities() == {"BTC": 2.0}
    assert btc.symbol_for("coinbase") == "BTC-USD" and btc.symbol_for("finnhub") == "BTC" and btc.tags == ["a", "b"]
    (tmp_path / "bad.csv").write_text("name,quantity\nX,1\n")
    with pytest.raises(ValueError, match="'id' column"):
        read_portfolio_file(tmp_path / "bad.csv")


def test_build_live_workspace_from_history(tmp_path):
    hist = _fake_history()
    ws = build_live_workspace("examples/live_stocks.csv", history=hist, benchmark="SPY", fallbacks=False, log=None)
    assert len(ws.held_ids) == 11 and ws.portfolio.cash == 25_000
    assert (ws.market.prices.index.dayofweek < 5).all()  # stocks: weekdays only
    assert ws.market.benchmark is not None and "SPY" in hist.calls
    assert ws.master.get("AAPL").avg_daily_volume == pytest.approx(1_000_000)
    from openportfolio.modules import compliance, risk_dashboard

    assert risk_dashboard.headline(ws)[0]["value"] > 0
    assert "fossil" not in compliance.default_rulebook().summary(ws)["plain"]  # tags load; no exclusion rule for it


def test_crypto_keeps_weekends_and_converts_volume_to_coins():
    ws = build_live_workspace("examples/live_crypto.csv", history=_fake_history(), fallbacks=False, log=None)
    assert (ws.market.prices.index.dayofweek >= 5).any()
    btc = ws.master.get("BTC")
    assert btc.avg_daily_volume == pytest.approx(1_000_000 / ws.market.prices["BTC"].iloc[-1], rel=0.05)


def test_history_falls_back_when_the_first_source_fails(monkeypatch):
    import openportfolio.data.live_universe as LU

    backup = _fake_history(seed=1)
    monkeypatch.setattr(LU, "CoinbaseHistory", lambda: backup)
    messages = []
    ws = build_live_workspace("examples/live_crypto.csv", history=_fake_history(fail={"BTC-USD"}),
                              log=messages.append)
    assert "BTC" in ws.held_ids
    assert any("Bitcoin: history from Coinbase" in m for m in messages)


def test_a_missing_benchmark_is_not_fatal():
    messages = []
    ws = build_live_workspace("examples/live_stocks.csv", history=_fake_history(fail={"SPY"}), benchmark="SPY",
                              fallbacks=False, log=messages.append)
    assert ws.market.benchmark is None and any("Benchmark SPY unavailable" in m for m in messages)


def test_too_little_history_is_refused():
    with pytest.raises(RuntimeError, match="60 days"):
        build_live_workspace("examples/live_stocks.csv", history=_fake_history(days=40), fallbacks=False, log=None)
