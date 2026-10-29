"""Build a workspace from real instruments, ready for a live feed.

A live stream needs three things that the sample fund invents for you:

1. **Real instruments** that the provider recognises, with the symbol each
   provider uses (AAPL on Finnhub and Alpaca; BTC-USD on Coinbase;
   BINANCE:BTCUSDT on Finnhub).
2. **Quantities** held.
3. **Price history**, so that risk (volatility, VaR, covariance) can be
   measured and today's open is known. History comes from Yahoo Finance's
   free daily data, cached on disk for the day.

All three come from one CSV file — see ``examples/live_stocks.csv``.

Columns (only ``id``, ``asset_class`` and ``quantity`` are required):

    id, name, asset_class, quantity, currency, sector, country, ticker,
    yahoo, finnhub, alpaca, coinbase, avg_daily_volume, cost_price, tags

A row with id ``CASH`` sets the cash balance.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from ..workspace import Workspace
from .market_data import MarketData
from .models import Instrument, Portfolio, Position
from .security_master import SecurityMaster

PROVIDER_COLUMNS = ("yahoo", "finnhub", "alpaca", "coinbase")
_BROWSER_UA = "Mozilla/5.0"  # Yahoo throttles unknown clients more readily than a generic browser string


def read_portfolio_file(path: str | Path, base_currency: str = "USD") -> Tuple[SecurityMaster, Portfolio]:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [c.strip().lower() for c in df.columns]
    for col in ("id", "asset_class", "quantity"):
        if col not in df.columns:
            raise ValueError(f"The portfolio file needs a '{col}' column. See examples/live_stocks.csv.")
    instruments, positions, cash = [], [], 0.0
    for _, r in df.iterrows():
        iid = r["id"].strip()
        if not iid:
            continue
        qty = float(r["quantity"] or 0)
        if iid.upper() == "CASH":
            cash += qty
            continue
        get = lambda c: (r.get(c) or "").strip() or None  # noqa: E731
        adv = get("avg_daily_volume")
        instruments.append(Instrument(
            id=iid, name=get("name") or iid, asset_class=r["asset_class"].strip(),
            sector=get("sector") or "Other", country=get("country") or "Other",
            currency=(get("currency") or base_currency).upper(), ticker=get("ticker") or iid,
            avg_daily_volume=float(adv) if adv else None,
            tags=[t.strip() for t in (get("tags") or "").split(";") if t.strip()],
            symbols={c: get(c) for c in PROVIDER_COLUMNS if get(c)},
        ))
        cost = get("cost_price")
        positions.append(Position(iid, qty, float(cost) if cost else None))
    if not positions:
        raise ValueError("The portfolio file has no holdings.")
    return SecurityMaster(instruments), Portfolio(Path(path).stem.replace("_", " ").capitalize(), positions, cash,
                                                  base_currency)


# --------------------------------------------------------------------------- history
class YahooHistory:
    """Daily closes and volumes from Yahoo Finance's public chart endpoint (no key).

    Results are cached in ``.opp_cache/`` for the rest of the day, so repeated
    runs start instantly and do not hammer the service.
    """

    URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval=1d"

    def __init__(self, range_: str = "2y", cache_dir: str | Path = ".opp_cache", timeout: float = 15.0) -> None:
        self.range = range_
        self.cache = Path(cache_dir)
        self.timeout = timeout

    def __call__(self, symbol: str) -> pd.DataFrame:
        f = self.cache / f"{symbol.replace('/', '_').replace('=', '_')}_{self.range}.csv"
        if f.exists() and time.time() - f.stat().st_mtime < 12 * 3600:
            return pd.read_csv(f, index_col=0, parse_dates=True)
        data, last_exc = None, None
        # Yahoo throttles bursts (HTTP 429); alternate its two hosts and back off briefly.
        for attempt, host in enumerate(("query1", "query2", "query1", "query2")):
            url = self.URL.format(symbol=symbol, range=self.range).replace("query1", host)
            req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (fixed https URL)
                    data = json.load(resp)
                break
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code != 429:
                    break
                time.sleep(1.5 * (attempt + 1))
            except Exception as exc:
                last_exc = exc
                break
        if data is None:
            raise RuntimeError(f"Could not download price history for {symbol} from Yahoo Finance ({last_exc}). "
                               "Check the symbol in the 'yahoo' column and your internet connection.")
        res = data["chart"]["result"][0]
        quote = res["indicators"]["quote"][0]
        idx = pd.to_datetime(res["timestamp"], unit="s").normalize()
        df = pd.DataFrame({"close": quote["close"], "volume": quote.get("volume")}, index=idx)
        df = df[~df.index.duplicated(keep="last")].dropna(subset=["close"])
        self.cache.mkdir(parents=True, exist_ok=True)
        df.to_csv(f)
        return df


HistoryFetcher = Callable[[str], pd.DataFrame]  # symbol -> DataFrame with 'close' (and optionally 'volume')


def _get_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 15.0):
    req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA, "Accept": "application/json",
                                               **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https URLs)
        return json.load(resp)


class CoinbaseHistory:
    """Daily crypto candles from Coinbase's public market data (no key). Used when Yahoo is unavailable."""

    URL = "https://api.exchange.coinbase.com/products/{product}/candles?granularity=86400&start={start}&end={end}"

    def __init__(self, days: int = 730, opener=_get_json) -> None:
        self.days = days
        self.opener = opener

    def __call__(self, product: str) -> pd.DataFrame:
        end = pd.Timestamp.now(tz="UTC").normalize()
        rows = []
        while len(rows) < self.days:  # Coinbase returns at most 300 candles per request
            start = end - pd.Timedelta(days=299)
            rows += self.opener(self.URL.format(product=product, start=start.isoformat(), end=end.isoformat()))
            end = start - pd.Timedelta(days=1)
            if len(rows) % 300:
                break
        if not rows:
            return pd.DataFrame(columns=["close", "volume"])
        df = pd.DataFrame(rows, columns=["time", "low", "high", "open", "close", "volume"])
        df.index = pd.to_datetime(df["time"], unit="s").dt.normalize()
        df["volume"] = df["volume"] * df["close"]  # coins -> currency, matching Yahoo's convention
        return df[["close", "volume"]].sort_index().loc[lambda x: ~x.index.duplicated()]


class AlpacaHistory:
    """Daily bars from Alpaca (needs ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY)."""

    URL = ("https://data.alpaca.markets/v2/stocks/bars?symbols={symbol}&timeframe=1Day&start={start}"
           "&limit=10000&adjustment=all&feed={feed}")

    def __init__(self, days: int = 730, opener=_get_json) -> None:
        import os

        from ..config import get_key

        self.headers = {"APCA-API-KEY-ID": get_key("ALPACA_API_KEY_ID", "alpaca"),
                        "APCA-API-SECRET-KEY": get_key("ALPACA_API_SECRET_KEY", "alpaca")}
        self.feed = os.environ.get("ALPACA_FEED", "iex")
        self.days = days
        self.opener = opener

    def __call__(self, symbol: str) -> pd.DataFrame:
        start = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=self.days)).strftime("%Y-%m-%d")
        url = self.URL.format(symbol=symbol, start=start, feed=self.feed)
        bars, token = [], None
        while True:
            data = self.opener(url + (f"&page_token={token}" if token else ""), self.headers)
            bars += (data.get("bars") or {}).get(symbol, [])
            token = data.get("next_page_token")
            if not token:
                break
        df = pd.DataFrame(bars)
        if df.empty:
            return pd.DataFrame(columns=["close", "volume"])
        df.index = pd.to_datetime(df["t"]).dt.tz_localize(None).dt.normalize()
        return df.rename(columns={"c": "close", "v": "volume"})[["close", "volume"]]


def default_history_sources(inst: Instrument, yahoo: HistoryFetcher) -> list:
    """Where to look for an instrument's history, in order of preference."""
    import os

    sources = [("Yahoo Finance", yahoo, inst.symbol_for("yahoo"))]
    if inst.symbols.get("coinbase"):
        sources.append(("Coinbase", CoinbaseHistory(), inst.symbols["coinbase"]))
    if os.environ.get("ALPACA_API_KEY_ID") and os.environ.get("ALPACA_API_SECRET_KEY") and inst.asset_class != "Crypto":
        sources.append(("Alpaca", AlpacaHistory(), inst.symbol_for("alpaca")))
    return sources


def build_live_workspace(path: str | Path, history: Optional[HistoryFetcher] = None, benchmark: Optional[str] = None,
                         risk_free_rate: float = 0.04, base_currency: str = "USD",
                         fallbacks: bool = True, log=print) -> Workspace:
    """Read the portfolio file, fetch history, and return a workspace every module can use.

    History is tried from Yahoo Finance first, then (with `fallbacks`) from
    Coinbase for crypto and from Alpaca for stocks when Alpaca keys are set.
    """
    history = history or YahooHistory()
    master, portfolio = read_portfolio_file(path, base_currency)
    closes: Dict[str, pd.Series] = {}
    for iid in portfolio.quantities():
        inst = master.get(iid)
        sources = default_history_sources(inst, history) if fallbacks else [("history", history, inst.symbol_for("yahoo"))]
        df, errors = None, []
        for label, fetch, sym in sources:
            try:
                df = fetch(sym)
                if df is not None and not df.empty:
                    break
                errors.append(f"{label}: no data for {sym}")
            except Exception as exc:
                errors.append(f"{label}: {exc}")
            df = None
        if df is None:
            raise RuntimeError(f"No price history for {inst.name}. Tried " + "; ".join(errors))
        if errors and log:
            log(f"  {inst.name}: history from {label} (" + "; ".join(errors) + ")")
        closes[iid] = df["close"].astype(float)
        if inst.avg_daily_volume is None and "volume" in df and df["volume"].notna().any():
            vol = pd.to_numeric(df["volume"], errors="coerce").tail(63).replace(0, np.nan).mean()
            if np.isfinite(vol):
                # Crypto volume is reported in currency; convert it to coins so it matches the quantity
                last = float(df["close"].iloc[-1])
                inst.avg_daily_volume = float(vol / last) if inst.asset_class == "Crypto" else float(vol)
    prices = pd.DataFrame(closes).sort_index()
    # Stocks trade on weekdays and crypto every day; analyse on weekdays so returns line up.
    if any(master.get(i).asset_class != "Crypto" for i in prices.columns):
        prices = prices[prices.index.dayofweek < 5]
    prices = prices.ffill().dropna()
    if len(prices) < 60:
        raise RuntimeError("Fewer than 60 days of shared price history; risk figures would not be reliable.")
    fx = pd.DataFrame({base_currency: 1.0}, index=prices.index)
    for ccy in {master.get(i).currency for i in prices.columns} - {base_currency}:
        fx[ccy] = history(f"{ccy}{base_currency}=X")["close"].reindex(prices.index).ffill().bfill()
    bench = None
    if benchmark:
        try:
            bench = history(benchmark)["close"].reindex(prices.index).ffill().bfill().rename(benchmark)
        except Exception as exc:  # a missing benchmark costs beta and tracking error, nothing more
            if log:
                log(f"  Benchmark {benchmark} unavailable ({exc}); beta and tracking error will be skipped.")
    market = MarketData(prices, fx, benchmark=bench, risk_free_rate=risk_free_rate, base_currency=base_currency)
    return Workspace(master, market, portfolio)
