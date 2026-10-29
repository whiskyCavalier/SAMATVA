"""Getting data in: holdings files, price files and live feeds.

The platform ships *connectors*, never data. Free sources work out of the box;
licensed feeds (Bloomberg, LSEG, FactSet and the like) plug in through the same
small interface, using the customer's own licence.
"""

from __future__ import annotations

import io
import json
import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Protocol, Union

import pandas as pd

from .models import Portfolio, Position
from .security_master import SecurityMaster

PathOrBuffer = Union[str, io.StringIO, io.BytesIO]

_QTY_NAMES = ("quantity", "qty", "units", "shares", "nominal", "holding")
_ID_NAMES = ("id", "instrument", "instrument_id", "ticker", "isin", "cusip", "figi", "symbol", "security")
_COST_NAMES = ("cost_price", "cost", "avg_cost", "average_cost", "book_price")


@dataclass
class ImportReport:
    """What happened during an import, written so a non-specialist can act on it."""

    loaded: int
    skipped: List[str]

    def summary(self) -> str:
        if not self.skipped:
            return f"Imported {self.loaded} positions. Nothing was skipped."
        return (f"Imported {self.loaded} positions. Skipped {len(self.skipped)} rows we could not match "
                f"to a known instrument: {', '.join(self.skipped[:10])}"
                + (" and more." if len(self.skipped) > 10 else "."))


def _pick(columns: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    # "Avg Cost", "avg-cost" and "avg_cost" are all treated as the same name
    lowered = {str(c).lower().strip().replace(" ", "_").replace("-", "_"): c for c in columns}
    for name in candidates:
        if name in lowered:
            return lowered[name]
    return None


def load_holdings(source: PathOrBuffer, master: SecurityMaster, name: str = "Imported Portfolio",
                  base_currency: str = "USD") -> tuple[Portfolio, ImportReport]:
    """Read a holdings CSV or Excel file and return a portfolio.

    The file needs just two columns: something that identifies the instrument
    (ticker, ISIN, CUSIP, FIGI or internal id) and a quantity. Column names are
    matched loosely, so "Ticker", "ISIN" or "Shares" all work. A row whose
    identifier is ``CASH`` is treated as cash in the base currency.
    """
    if isinstance(source, str) and source.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(source)
    else:
        df = pd.read_csv(source)

    id_col = _pick(df.columns, _ID_NAMES)
    qty_col = _pick(df.columns, _QTY_NAMES)
    if id_col is None or qty_col is None:
        raise ValueError(
            "The holdings file needs an identifier column (for example 'ticker' or 'isin') and a quantity "
            f"column (for example 'quantity' or 'shares'). Found: {', '.join(map(str, df.columns))}"
        )
    cost_col = _pick(df.columns, _COST_NAMES)

    positions: List[Position] = []
    skipped: List[str] = []
    cash = 0.0
    for _, row in df.iterrows():
        key = str(row[id_col]).strip()
        qty = float(row[qty_col])
        if key.upper() == "CASH":
            cash += qty
            continue
        iid = master.resolve(key)
        if iid is None:
            skipped.append(key)
            continue
        cost = float(row[cost_col]) if cost_col and pd.notna(row[cost_col]) else None
        positions.append(Position(iid, qty, cost))

    return Portfolio(name, positions, cash, base_currency), ImportReport(len(positions), skipped)


def load_prices(source: PathOrBuffer, master: Optional[SecurityMaster] = None) -> pd.DataFrame:
    """Read a wide price file: a 'date' column, then one column per instrument."""
    df = pd.read_csv(source)
    date_col = _pick(df.columns, ("date", "as_of", "timestamp"))
    if date_col is None:
        raise ValueError("The price file needs a 'date' column.")
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.set_index(date_col).sort_index()
    if master is not None:
        df.columns = [master.resolve(c) or c for c in df.columns]
    return df.astype(float)


# --------------------------------------------------------------------------- live feeds
class QuoteSource(Protocol):
    """Anything that can return the latest prices for a list of tickers."""

    def latest(self, tickers: List[str]) -> Dict[str, float]: ...


class YahooQuoteSource:
    """Free delayed quotes from Yahoo Finance's public chart endpoint.

    Suitable for demos and personal use. For production, use a licensed feed.
    Network access is needed; nothing here runs at import time.
    """

    URL = "https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=1m"

    def __init__(self, timeout: float = 5.0) -> None:
        self.timeout = timeout
        self.errors: Dict[str, str] = {}

    def latest(self, tickers: List[str]) -> Dict[str, float]:
        """Latest price per ticker. A ticker that fails is skipped (and noted in `errors`), not fatal."""
        out: Dict[str, float] = {}
        for i, t in enumerate(tickers):
            host = "query1" if i % 2 == 0 else "query2"  # spread requests across Yahoo's two hosts
            req = urllib.request.Request(self.URL.format(host=host, ticker=t), headers={"User-Agent": "Mozilla/5.0"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (fixed https URL)
                    data = json.load(resp)
                meta = data["chart"]["result"][0]["meta"]
                out[t] = float(meta["regularMarketPrice"])
                self.errors.pop(t, None)
            except Exception as exc:
                self.errors[t] = f"{type(exc).__name__}: {exc}"
        if tickers and not out:
            raise RuntimeError(f"Yahoo returned no prices ({next(iter(self.errors.values()), 'unknown error')})")
        return out


class FredSeriesSource:
    """Economic series (rates, inflation) from the St. Louis Fed's FRED service."""

    URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"

    def series(self, series_id: str) -> pd.Series:
        df = pd.read_csv(self.URL.format(series=series_id))
        df.iloc[:, 0] = pd.to_datetime(df.iloc[:, 0])
        s = pd.to_numeric(df.iloc[:, 1], errors="coerce")
        s.index = df.iloc[:, 0]
        return s.dropna() / 100.0
