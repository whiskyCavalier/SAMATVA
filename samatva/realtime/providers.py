"""Live market-data feeds from real providers.

Each provider speaks its own WebSocket dialect. This module translates all of
them into the platform's one shape, an async stream of :class:`Tick`, so the
stream processor, the engine and the dashboard work unchanged.

Providers:

=============  =====================  ==============================================
Name           Key needed             What it streams
=============  =====================  ==============================================
coinbase       none                   Crypto prices, 24 hours a day
finnhub        FINNHUB_API_KEY        US stock trades and crypto trades
alpaca         ALPACA_API_KEY_ID and  US stock trades (IEX on the free plan; SIP when
               ALPACA_API_SECRET_KEY  paid) or crypto
alpaca-test    the same Alpaca keys   The always-on FAKEPACA test symbol
yahoo          none                   Delayed quotes, polled every few seconds
=============  =====================  ==============================================

Every WebSocket feed reconnects by itself with exponential back-off (1, 2, 4 …
up to 30 seconds) and stops for good only on an authentication error, because
retrying a bad key achieves nothing. Keys are passed to providers but never
logged; error messages name the environment variable, never its value.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime
from typing import Dict, List, Optional

from ..config import get_key
from ..workspace import Workspace
from .feed import PollingFeed, SimulatedFeed, Tick


class FeedError(RuntimeError):
    """The provider refused or broke the connection in a way worth reporting."""


class AuthError(FeedError):
    """The provider rejected the key. Retrying will not help."""


def _iso_to_epoch(s: str) -> float:
    """Parse RFC-3339 timestamps, including nanosecond precision and a trailing Z."""
    s = s.replace("Z", "+00:00")
    if "." in s:
        head, rest = s.split(".", 1)
        frac = "".join(ch for ch in rest if ch.isdigit())
        tz = rest[len(frac):]
        s = f"{head}.{frac[:6].ljust(6, '0')}{tz}"
    return datetime.fromisoformat(s).timestamp()


class WebSocketFeed:
    """Common machinery: connect, subscribe, translate, reconnect."""

    name = "websocket"

    def __init__(self, symbol_map: Dict[str, str], max_retries: Optional[int] = None,
                 backoff_start: float = 1.0, backoff_max: float = 30.0) -> None:
        if not symbol_map:
            raise ValueError(f"No holdings have a symbol for the '{self.name}' feed. Add a '{self.name}' column "
                             "(or a ticker) to the portfolio file.")
        self.symbol_map = dict(symbol_map)  # provider symbol -> instrument id
        self.max_retries = max_retries
        self.backoff_start = backoff_start
        self.backoff_max = backoff_max
        self.seq = 0
        self.status = "starting"
        self.reconnects = 0
        self.messages = 0
        self.last_error: Optional[str] = None
        self._running = False

    # -- provider specifics ---------------------------------------------------
    def url(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    async def on_connect(self, conn) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def parse(self, raw: str | bytes) -> List[Tick]:  # pragma: no cover - overridden
        raise NotImplementedError

    def headers(self) -> Dict[str, str]:
        return {}

    # -- shared behaviour -----------------------------------------------------
    def _tick(self, symbol: str, price: float, source_ts: float = 0.0) -> Optional[Tick]:
        iid = self.symbol_map.get(symbol)
        if iid is None or not price or price <= 0:
            return None
        self.seq += 1
        return Tick(iid, float(price), time.time(), self.seq, source_ts)

    def stop(self) -> None:
        self._running = False

    def describe(self) -> Dict:
        return {"provider": self.name, "status": self.status, "reconnects": self.reconnects,
                "messages": self.messages, "symbols": len(self.symbol_map), "last_error": self.last_error}

    async def stream(self):
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed, InvalidStatus

        self._running = True
        attempt = 0
        while self._running:
            try:
                self.status = "connecting"
                async with connect(self.url(), additional_headers=self.headers() or None, open_timeout=15,
                                   ping_interval=20, ping_timeout=20, max_size=2 ** 22) as conn:
                    await self.on_connect(conn)
                    self.status = "connected"
                    attempt = 0
                    async for raw in conn:
                        if not self._running:
                            break
                        self.messages += 1
                        for t in self.parse(raw):
                            yield t
            except AuthError as exc:
                self.status, self.last_error = "stopped: key rejected", str(exc)
                raise
            except InvalidStatus as exc:
                code = getattr(exc.response, "status_code", None)
                if code in (401, 403):
                    self.status = "stopped: key rejected"
                    self.last_error = (f"{self.name} refused the connection (HTTP {code}). Check the API key "
                                       f"in your .env file; run 'samatva keys' to see what is set.")
                    raise AuthError(self.last_error) from None
                self.last_error = f"HTTP {code} from {self.name}"
            except (OSError, asyncio.TimeoutError, ConnectionClosed, FeedError) as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
            if not self._running:
                break
            attempt += 1
            self.reconnects += 1
            if self.max_retries is not None and attempt > self.max_retries:
                self.status = "stopped: could not connect"
                raise FeedError(f"Could not keep a connection to {self.name} after {self.max_retries} retries. "
                                f"Last error: {self.last_error}")
            wait = min(self.backoff_max, self.backoff_start * 2 ** (attempt - 1))
            self.status = f"reconnecting in {wait:.0f}s"
            await asyncio.sleep(wait)
        self.status = "stopped"


# --------------------------------------------------------------------------- Finnhub
class FinnhubFeed(WebSocketFeed):
    """Finnhub trades. Symbols are plain tickers for US stocks (AAPL) and
    exchange-prefixed pairs for crypto (BINANCE:BTCUSDT)."""

    name = "finnhub"
    BASE = "wss://ws.finnhub.io"

    def __init__(self, symbol_map: Dict[str, str], api_key: Optional[str] = None, base_url: Optional[str] = None,
                 **kw) -> None:
        super().__init__(symbol_map, **kw)
        self._key = api_key or get_key("FINNHUB_API_KEY", "finnhub")
        self.base_url = base_url or self.BASE

    def url(self) -> str:
        return f"{self.base_url}?token={self._key}"

    async def on_connect(self, conn) -> None:
        for symbol in self.symbol_map:
            await conn.send(json.dumps({"type": "subscribe", "symbol": symbol}))

    def parse(self, raw) -> List[Tick]:
        msg = json.loads(raw)
        kind = msg.get("type")
        if kind == "trade":
            ticks = [self._tick(d.get("s"), d.get("p"), (d.get("t") or 0) / 1000.0) for d in msg.get("data", [])]
            return [t for t in ticks if t]
        if kind == "error":
            self.last_error = str(msg.get("msg"))
        return []


# --------------------------------------------------------------------------- Alpaca
class AlpacaFeed(WebSocketFeed):
    """Alpaca market data. `feed` is iex (free plan), sip (paid), test (FAKEPACA) or crypto.

    In ``test`` mode Alpaca only streams the made-up symbol FAKEPACA. To prove
    the whole pipeline works outside market hours, its *percentage moves* are
    applied to one real holding (``proxy_instrument``). Prices are therefore
    not real for that holding; the plumbing is.
    """

    name = "alpaca"
    HOST = "wss://stream.data.alpaca.markets"
    ERRORS = {
        401: "not authenticated", 402: "the API key or secret was rejected", 404: "authentication timed out",
        405: "too many symbols for your plan", 406: "another connection is already open with these keys "
        "(Alpaca allows one at a time; close the other app or script)", 409: "your plan does not include this feed "
        "(the free plan uses ALPACA_FEED=iex)",
    }

    def __init__(self, symbol_map: Dict[str, str], key_id: Optional[str] = None, secret: Optional[str] = None,
                 feed: Optional[str] = None, base_url: Optional[str] = None, proxy_instrument: Optional[str] = None,
                 **kw) -> None:
        self.feed = (feed or os.environ.get("ALPACA_FEED", "iex")).lower()
        if self.feed == "test":
            if not proxy_instrument:
                proxy_instrument = next(iter(symbol_map.values()), None)
            symbol_map = {"FAKEPACA": proxy_instrument} if proxy_instrument else {}
        super().__init__(symbol_map, **kw)
        self.name = "alpaca-test" if self.feed == "test" else "alpaca"
        self._key_id = key_id or get_key("ALPACA_API_KEY_ID", "alpaca")
        self._secret = secret or get_key("ALPACA_API_SECRET_KEY", "alpaca")
        self.base_url = base_url or self.HOST
        self.proxy_instrument = proxy_instrument
        self._proxy_base: Optional[float] = None
        self.proxy_open: Optional[float] = None  # set by the caller: the proxy holding's last close

    def url(self) -> str:
        if self.feed == "crypto":
            return f"{self.base_url}/v1beta3/crypto/us"
        return f"{self.base_url}/v2/{self.feed}"

    async def _recv_list(self, conn) -> list:
        raw = await asyncio.wait_for(conn.recv(), timeout=15)
        data = json.loads(raw)
        return data if isinstance(data, list) else [data]

    async def on_connect(self, conn) -> None:
        await self._recv_list(conn)  # [{"T":"success","msg":"connected"}]
        await conn.send(json.dumps({"action": "auth", "key": self._key_id, "secret": self._secret}))
        for m in await self._recv_list(conn):
            if m.get("T") == "error":
                code = m.get("code")
                reason = self.ERRORS.get(code, m.get("msg", "unknown error"))
                err = AuthError if code in (401, 402, 404, 409) else FeedError
                raise err(f"Alpaca: {reason} (code {code}).")
        await conn.send(json.dumps({"action": "subscribe", "trades": list(self.symbol_map)}))

    def parse(self, raw) -> List[Tick]:
        data = json.loads(raw)
        out: List[Tick] = []
        for m in data if isinstance(data, list) else [data]:
            kind = m.get("T")
            if kind == "t":
                price = float(m.get("p", 0))
                if self.feed == "test":
                    price = self._rescale(price)
                t = self._tick(m.get("S"), price, _iso_to_epoch(m["t"]) if m.get("t") else 0.0)
                if t:
                    out.append(t)
            elif kind == "error":
                code = m.get("code")
                self.last_error = f"Alpaca: {self.ERRORS.get(code, m.get('msg'))} (code {code})."
                if code == 406:
                    raise FeedError(self.last_error)
        return out

    def _rescale(self, price: float) -> float:
        if self._proxy_base is None:
            self._proxy_base = price
        base = self.proxy_open or self._proxy_base
        return base * price / self._proxy_base


# --------------------------------------------------------------------------- Coinbase
class CoinbaseFeed(WebSocketFeed):
    """Coinbase Advanced Trade public ticker channel. No key needed; product ids look like BTC-USD."""

    name = "coinbase"
    URL = "wss://advanced-trade-ws.coinbase.com"

    def __init__(self, symbol_map: Dict[str, str], base_url: Optional[str] = None, **kw) -> None:
        super().__init__(symbol_map, **kw)
        self.base_url = base_url or self.URL

    def url(self) -> str:
        return self.base_url

    async def on_connect(self, conn) -> None:
        products = list(self.symbol_map)
        await conn.send(json.dumps({"type": "subscribe", "channel": "ticker", "product_ids": products}))
        # The heartbeats channel keeps quiet connections open
        await conn.send(json.dumps({"type": "subscribe", "channel": "heartbeats"}))

    def parse(self, raw) -> List[Tick]:
        msg = json.loads(raw)
        if msg.get("type") == "error":
            self.last_error = f"Coinbase: {msg.get('message')}"
            return []
        if msg.get("channel") != "ticker":
            return []
        ts = _iso_to_epoch(msg["timestamp"]) if msg.get("timestamp") else 0.0
        out: List[Tick] = []
        for event in msg.get("events", []):
            for tk in event.get("tickers", []):
                t = self._tick(tk.get("product_id"), float(tk.get("price") or 0), ts)
                if t:
                    out.append(t)
        return out


# --------------------------------------------------------------------------- factory
_SYMBOL_COLUMN = {"finnhub": "finnhub", "alpaca": "alpaca", "alpaca-test": "alpaca", "coinbase": "coinbase",
                  "yahoo": "yahoo"}


def symbol_map(ws: Workspace, provider: str) -> Dict[str, str]:
    """Provider symbol -> instrument id, for every holding the provider can price."""
    col = _SYMBOL_COLUMN.get(provider, provider)
    out: Dict[str, str] = {}
    for iid in ws.held_ids:
        inst = ws.master.get(iid)
        # Stock providers understand plain tickers; Coinbase product ids (BTC-USD) must be given explicitly.
        sym = inst.symbols.get(col) if col == "coinbase" else inst.symbol_for(col)
        if sym:
            out[sym] = iid
    return out


def make_feed(provider: str, ws: Workspace, ticks_per_second: float = 500.0, **kw):
    """Build the feed for a provider name, checking its key first."""
    provider = provider.lower()
    if provider == "simulated":
        return SimulatedFeed(ws, ticks_per_second=ticks_per_second)
    if provider == "finnhub":
        return FinnhubFeed(symbol_map(ws, "finnhub"), **kw)
    if provider in ("alpaca", "alpaca-test"):
        feed = "test" if provider == "alpaca-test" else None
        proxy = kw.pop("proxy_instrument", None)
        if feed == "test" and not proxy:  # use the largest holding Alpaca could price
            proxy = next((i for i in ws.valuation.index if ws.master.get(i).symbol_for("alpaca")), ws.held_ids[0])
        f = AlpacaFeed(symbol_map(ws, "alpaca"), feed=feed, proxy_instrument=proxy, **kw)
        if feed == "test":
            f.proxy_open = float(ws.market.latest_prices()[proxy])
        return f
    if provider == "coinbase":
        return CoinbaseFeed(symbol_map(ws, "coinbase"), **kw)
    if provider == "yahoo":
        from ..data.connectors import YahooQuoteSource

        return YahooPollingFeed(ws, YahooQuoteSource(), interval=float(os.environ.get("SAMATVA_POLL_SECONDS", "5")))
    raise ValueError(f"Unknown provider '{provider}'.")


class YahooPollingFeed(PollingFeed):
    """PollingFeed that uses each holding's Yahoo symbol rather than its plain ticker."""

    name = "yahoo"

    def __init__(self, ws: Workspace, source, interval: float = 5.0) -> None:
        super().__init__(ws, source, interval)
        self.ticker_to_id = symbol_map(ws, "yahoo")
        self.status = "polling"

    def describe(self) -> Dict:
        return {"provider": "yahoo", "status": self.status, "symbols": len(self.ticker_to_id)}
