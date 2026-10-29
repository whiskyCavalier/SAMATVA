"""Settings and API keys.

Keys are read from environment variables. For convenience, a file named
``.env`` in the folder you run from is loaded first, so you can keep keys in
one place without exporting them by hand. Rules that keep keys safe:

* Keys live only in ``.env`` or the environment, never in code, CSV files or
  rule files. ``.env`` is listed in ``.gitignore`` so it is never committed.
* A real environment variable always wins over the same name in ``.env``.
* Keys are never printed or logged in full; :func:`mask` shows only the ends.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

# Every key the platform understands: the variable name, who issues it, and what it unlocks.
KEYS: List[Dict[str, str]] = [
    {"var": "FINNHUB_API_KEY", "provider": "finnhub", "issuer": "finnhub.io",
     "unlocks": "Real-time US stock trades and crypto trades over WebSocket"},
    {"var": "ALPACA_API_KEY_ID", "provider": "alpaca", "issuer": "alpaca.markets",
     "unlocks": "Real-time US stock trades (IEX feed on the free plan), the FAKEPACA test stream, crypto"},
    {"var": "ALPACA_API_SECRET_KEY", "provider": "alpaca", "issuer": "alpaca.markets",
     "unlocks": "The secret that goes with ALPACA_API_KEY_ID"},
    {"var": "ANTHROPIC_API_KEY", "provider": "explainer", "issuer": "console.anthropic.com",
     "unlocks": "Optional: lets the explainer pass unusual questions to a language model"},
]

PROVIDERS = {
    "simulated": "Simulated ticks from the holdings' history (no key, no network)",
    "coinbase": "Live crypto prices from Coinbase (no key, 24 hours a day)",
    "finnhub": "Live stock and crypto trades from Finnhub (needs FINNHUB_API_KEY)",
    "alpaca": "Live US stock trades from Alpaca (needs ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY)",
    "alpaca-test": "Alpaca's always-on test stream of the FAKEPACA symbol (needs the Alpaca keys)",
    "yahoo": "Delayed quotes polled from Yahoo Finance every few seconds (no key)",
}


class MissingKeyError(RuntimeError):
    """Raised when a provider is chosen but its key has not been set."""


def load_env(path: str | os.PathLike = ".env", override: bool = False) -> Dict[str, str]:
    """Read KEY=VALUE lines from a .env file into the environment.

    Blank lines and lines starting with # are ignored; surrounding quotes are
    removed; ``export KEY=VALUE`` is accepted. Existing environment variables
    are kept unless `override` is true. Returns the names loaded (not values).
    """
    p = Path(path)
    loaded: Dict[str, str] = {}
    if not p.is_file():
        return loaded
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1]:
            value = value[1:-1]
        elif " #" in value:  # inline comment after an unquoted value
            value = value.split(" #", 1)[0].rstrip()
        if not name:
            continue
        if override or name not in os.environ:
            os.environ[name] = value
            loaded[name] = "file"
    return loaded


def mask(value: Optional[str]) -> str:
    """Show enough of a key to recognise it, never enough to use it."""
    if not value:
        return "(not set)"
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"


def get_key(var: str, provider: str) -> str:
    value = os.environ.get(var, "").strip()
    if not value:
        raise MissingKeyError(
            f"The '{provider}' feed needs {var}. Add a line '{var}=your-key' to the .env file in the folder you "
            f"run from (copy .env.example to start), or set it as an environment variable. "
            f"Run 'opp keys' to see what is set."
        )
    return value


@dataclass
class Settings:
    provider: str = "simulated"
    portfolio_file: Optional[str] = None
    stale_seconds: float = 5.0
    ticks_per_second: float = 500.0
    benchmark: Optional[str] = None

    @classmethod
    def from_env(cls) -> "Settings":
        provider = os.environ.get("OPP_DATA_PROVIDER", "simulated").strip().lower()
        if provider not in PROVIDERS:
            raise ValueError(f"OPP_DATA_PROVIDER '{provider}' is not known. Choose one of: {', '.join(PROVIDERS)}")
        live = provider != "simulated"
        return cls(
            provider=provider,
            portfolio_file=os.environ.get("OPP_PORTFOLIO_FILE") or None,
            # Real instruments trade irregularly, so allow longer gaps before calling a price stale
            stale_seconds=float(os.environ.get("OPP_STALE_SECONDS", "120" if live else "5")),
            ticks_per_second=float(os.environ.get("OPP_TICKS_PER_SECOND", "500")),
            benchmark=os.environ.get("OPP_BENCHMARK") or None,
        )


def key_status() -> List[Dict[str, str]]:
    """Which keys are set, shown masked. Safe to print."""
    return [{**k, "set": "yes" if os.environ.get(k["var"]) else "no", "value": mask(os.environ.get(k["var"]))}
            for k in KEYS]
