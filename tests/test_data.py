"""Data layer: models, security master, importers and quality checks."""

import io

import numpy as np
import pandas as pd
import pytest

from samatva.data import connectors, quality
from samatva.data.market_data import MarketData
from samatva.data.models import Instrument, Portfolio, Position
from samatva.data.sample import generate_market_data, sample_instruments
from samatva.data.security_master import SecurityMaster


def test_instrument_rejects_unknown_asset_class():
    with pytest.raises(ValueError, match="Unknown asset class"):
        Instrument("X", "X", "Crypto-ish")


def test_security_master_resolves_every_identifier():
    m = SecurityMaster(sample_instruments())
    assert m.resolve("nwt") == "NWT"
    assert m.resolve("US0000000NWT") == "NWT"
    assert m.resolve(" us0000000nwt ") == "NWT"
    assert m.resolve("nope") is None
    assert "EMB" in m and len(m) == 12


def test_security_master_rejects_duplicates():
    m = SecurityMaster([Instrument("A", "A", "Equity", ticker="AAA")])
    with pytest.raises(ValueError):
        m.add(Instrument("A", "Again", "Equity"))
    with pytest.raises(ValueError):
        m.add(Instrument("B", "B", "Equity", ticker="AAA"))


def test_holdings_import_matches_loose_column_names_and_cash():
    m = SecurityMaster(sample_instruments())
    csv = "ISIN,Shares,Avg Cost\nUS0000000NWT,100,150\nHLX,50,\nUNKNOWN,5,1\nCASH,1000,\n"
    pf, rep = connectors.load_holdings(io.StringIO(csv), m)
    assert pf.quantities() == {"NWT": 100.0, "HLX": 50.0}
    assert pf.cash == 1000.0
    assert rep.skipped == ["UNKNOWN"]
    assert "Skipped 1" in rep.summary()
    assert pf.positions[0].cost_price == 150.0


def test_holdings_import_explains_missing_columns():
    with pytest.raises(ValueError, match="identifier column"):
        connectors.load_holdings(io.StringIO("foo,bar\n1,2\n"), SecurityMaster(sample_instruments()))


def test_price_import_maps_identifiers():
    m = SecurityMaster(sample_instruments())
    df = connectors.load_prices(io.StringIO("date,NWT,US0000000HLX\n2026-01-02,10,20\n2026-01-01,9,19\n"), m)
    assert list(df.columns) == ["NWT", "HLX"]
    assert df.index.is_monotonic_increasing


def test_quality_checks_flag_stale_and_jumping_prices():
    m = SecurityMaster(sample_instruments())
    dates = pd.bdate_range("2026-01-01", periods=30)
    px = pd.DataFrame({"NWT": np.linspace(100, 110, 30), "HLX": np.linspace(50, 51, 30)}, index=dates)
    px.iloc[-8:, 1] = np.nan  # HLX goes stale
    px.iloc[10, 0] = 200  # NWT has a spurious spike
    fx = pd.DataFrame({"USD": 1.0}, index=dates)
    md = MarketData(px, fx)
    pf = Portfolio("t", [Position("NWT", 1), Position("HLX", 1), Position("ORB", 1)])
    issues = quality.check(pf, m, md)
    text = " ".join(i.message for i in issues)
    assert "has not had a new price for 8 days" in text
    assert "moved +" in text and "confirm" in text
    assert any(i.severity == "error" and i.instrument == "ORB" for i in issues)


def test_sample_market_data_is_deterministic_and_complete():
    a, b = generate_market_data(seed=4), generate_market_data(seed=4)
    pd.testing.assert_frame_equal(a.prices, b.prices)
    assert not a.prices.isna().any().any()
    assert set(a.fx.columns) >= {"USD", "EUR", "JPY", "GBP"}
    assert a.factors.shape[1] == 5


def test_fx_conversion_is_multiplicative(ws):
    bp = ws.base_prices
    lp = ws.market.prices
    assert bp["KST"].iloc[-1] == pytest.approx(lp["KST"].iloc[-1] * ws.market.fx["EUR"].iloc[-1])
    assert bp["NWT"].iloc[-1] == pytest.approx(lp["NWT"].iloc[-1])
