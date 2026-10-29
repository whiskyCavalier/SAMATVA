"""Core data models.

Every module in the platform reads from these few, deliberately plain objects.
Keeping one shared model is what guarantees that a number shown on the risk
screen matches the same number on the performance screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

ASSET_CLASSES = ("Equity", "Fixed Income", "Commodity", "Real Estate", "Crypto", "Cash", "Private Equity",
                 "Private Credit")


@dataclass
class Instrument:
    """A single tradable (or private) instrument.

    Only ``id``, ``name`` and ``asset_class`` are required. Everything else is
    optional and simply enriches the analysis when present.
    """

    id: str
    name: str
    asset_class: str
    sector: str = "Other"
    country: str = "Other"
    currency: str = "USD"
    ticker: Optional[str] = None
    isin: Optional[str] = None
    cusip: Optional[str] = None
    figi: Optional[str] = None
    avg_daily_volume: Optional[float] = None  # in shares / units
    # Bond fields (used by the duration and DV01 calculations)
    coupon: Optional[float] = None  # annual coupon rate, e.g. 0.04 for 4%
    maturity_years: Optional[float] = None
    coupon_frequency: int = 2
    # ESG / screening tags used by the compliance module
    tags: List[str] = field(default_factory=list)
    # Fund look-through: underlying exposures as weights summing to 1
    lookthrough: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # How each data provider spells this instrument, e.g. {"finnhub": "BINANCE:BTCUSDT", "coinbase": "BTC-USD"}.
    # When a provider is missing here, the plain ticker is used.
    symbols: Dict[str, str] = field(default_factory=dict)

    def symbol_for(self, provider: str) -> Optional[str]:
        return self.symbols.get(provider) or self.ticker

    def __post_init__(self) -> None:
        if self.asset_class not in ASSET_CLASSES:
            raise ValueError(
                f"Unknown asset class '{self.asset_class}' for {self.id}. "
                f"Choose one of: {', '.join(ASSET_CLASSES)}"
            )

    @property
    def is_bond(self) -> bool:
        return self.asset_class == "Fixed Income" and self.coupon is not None and self.maturity_years is not None


@dataclass
class Position:
    """How much of an instrument the portfolio holds."""

    instrument_id: str
    quantity: float
    cost_price: Optional[float] = None  # average cost per unit, in local currency


@dataclass
class Portfolio:
    """A named collection of positions plus cash, valued in a base currency."""

    name: str
    positions: List[Position]
    cash: float = 0.0
    base_currency: str = "USD"
    benchmark: Optional[str] = None

    def quantities(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for p in self.positions:
            out[p.instrument_id] = out.get(p.instrument_id, 0.0) + p.quantity
        return out

    def copy_with(self, quantities: Dict[str, float], cash: Optional[float] = None) -> "Portfolio":
        """Return a new portfolio with the given quantities (used by what-if tools)."""
        positions = [Position(k, v) for k, v in quantities.items() if abs(v) > 1e-12]
        return Portfolio(self.name, positions, self.cash if cash is None else cash, self.base_currency, self.benchmark)
