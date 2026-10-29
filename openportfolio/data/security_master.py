"""The security master: one place that knows every instrument.

Each instrument has a single internal id, and the master can find it by any
of the common market identifiers (ticker, ISIN, CUSIP or FIGI). Importers use
this so that a holdings file quoting ISINs and a price feed quoting tickers
still land on the same instrument.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from .models import Instrument

_ID_FIELDS = ("ticker", "isin", "cusip", "figi")


class SecurityMaster:
    def __init__(self, instruments: Iterable[Instrument] = ()) -> None:
        self._by_id: Dict[str, Instrument] = {}
        self._alias: Dict[str, str] = {}
        for inst in instruments:
            self.add(inst)

    def add(self, inst: Instrument) -> None:
        if inst.id in self._by_id:
            raise ValueError(f"Instrument id '{inst.id}' already exists in the security master")
        self._by_id[inst.id] = inst
        self._alias[inst.id.upper()] = inst.id
        for f in _ID_FIELDS:
            value = getattr(inst, f)
            if value:
                key = value.upper()
                if key in self._alias and self._alias[key] != inst.id:
                    raise ValueError(f"Identifier {value} is already mapped to {self._alias[key]}")
                self._alias[key] = inst.id

    def get(self, key: str) -> Instrument:
        resolved = self.resolve(key)
        if resolved is None:
            raise KeyError(f"No instrument found for identifier '{key}'")
        return self._by_id[resolved]

    def resolve(self, key: str) -> Optional[str]:
        """Translate any known identifier into the internal id, or None."""
        return self._alias.get(str(key).strip().upper())

    def __contains__(self, key: str) -> bool:
        return self.resolve(key) is not None

    def __iter__(self):
        return iter(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)

    def ids(self) -> List[str]:
        return list(self._by_id)
