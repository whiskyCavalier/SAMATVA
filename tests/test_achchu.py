"""Achchu: the fatigue mathematics, the module, and its place in the API.

Expected values are worked by hand wherever possible; the working is in the comments.
"""

import math

import numpy as np
import pandas as pd
import pytest

from samatva.analytics import fatigue as F
from samatva.modules.achchu import Achchu, AxleProfile, calibrate_profile, equanimity_from_trades

NO_HEAL = 0.0


# --------------------------------------------------------------------------- rainflow
def test_rainflow_astm_example():
    # ASTM E1049-85, section 5.4.4 worked example (linear ranges): one full cycle of 4 units, and half cycles
    # of 3, 4, 6, 8, 8 and 9 units.
    series = [-2, 1, -3, 5, -1, 3, -4, 4, -2]
    cycles = F.rainflow(series, log_scale=False, include_open_rises=True)
    full = sorted(c.size for c in cycles if c.count == 1.0)
    half = sorted(c.size for c in cycles if c.count == 0.5)
    assert full == [4.0]
    assert half == [3.0, 4.0, 6.0, 8.0, 8.0, 9.0]


def test_rainflow_matches_reference_library():
    rf = pytest.importorskip("rainflow")
    rng = np.random.default_rng(11)
    for _ in range(100):
        x = np.cumsum(rng.normal(size=rng.integers(5, 120)))
        ours = sorted((round(c.size, 9), c.count) for c in F.rainflow(x, log_scale=False, include_open_rises=True))
        ref = sorted((round(r, 9), n) for r, n in rf.count_cycles(x))
        # The reference library groups equal ranges; expand ours the same way
        grouped = {}
        for r, n in ours:
            grouped[r] = grouped.get(r, 0) + n
        assert sorted(grouped.items()) == ref


def test_rainflow_counts_falls_not_rises():
    # One fall that recovered, one rise that has not been given back, one fall still open
    up_only = F.rainflow([100, 120])
    assert up_only == []  # gains cause no strain
    open_fall = F.rainflow([100, 90])
    assert len(open_fall) == 1 and open_fall[0].open_fall and open_fall[0].falls == 1
    assert open_fall[0].size == pytest.approx(0.10)  # fractional fall: 1 - 90/100


# --------------------------------------------------------------------------- damage
def test_damage_worked_examples():
    # Capacity C = 15%, endurance 3%, exponent 2, no healing.
    # A: one 12% fall that fully recovers: (0.12 / 0.15)^2 = 0.64.
    a = F.fatigue_path([100, 88, 100], 0.15, 0.03, 2.0, NO_HEAL)
    assert a.now == pytest.approx(0.64)
    # B: six separate 7% falls, each recovered: 6 x (0.07 / 0.15)^2 = 1.3067. No single fall comes near the
    # capacity, yet together they strain twice as much as A: Kural 475's peacock feathers.
    path = [100]
    for _ in range(6):
        path += [93, 100]
    b = F.fatigue_path(path, 0.15, 0.03, 2.0, NO_HEAL)
    assert b.now == pytest.approx(6 * (0.07 / 0.15) ** 2)
    assert b.now == pytest.approx(1.3067, abs=1e-4)
    # C: a 10% fall still in progress: (0.10 / 0.15)^2 = 0.4444, all of it open.
    c = F.fatigue_path([100, 90], 0.15, 0.03, 2.0, NO_HEAL)
    assert c.now == pytest.approx(0.4444, abs=1e-4) and c.open_damage == pytest.approx(c.now)
    # D: a rise and its full give-back is a fall of 1 - 100/110 = 9.09%: (0.0909 / 0.15)^2 = 0.3673.
    d = F.fatigue_path([100, 110, 100], 0.15, 0.03, 2.0, NO_HEAL)
    assert d.now == pytest.approx(0.3673, abs=1e-4)


def test_falls_below_endurance_cause_no_damage():
    path = [100]
    for _ in range(50):
        path += [98, 100]  # fifty 2% falls
    assert F.fatigue_path(path, 0.15, 0.03, 2.0, NO_HEAL).now == 0.0
    assert F.fatigue_path(path, 0.15, 0.01, 2.0, NO_HEAL).now > 0.0


def test_healing_reduces_damage_over_calm_time():
    # A 12% fall and recovery (completed on day 2), then 126 days of tiny swings below the endurance threshold
    calm = [100, 88, 100] + [99.9 if k % 2 else 100.0 for k in range(1, 127)]
    no_heal = F.fatigue_path(calm, 0.15, 0.03, 2.0, NO_HEAL).now
    heal = F.fatigue_path(calm, 0.15, 0.03, 2.0, math.log(2) / 126).now
    assert no_heal == pytest.approx(0.64)
    assert heal == pytest.approx(0.64 / 2, rel=0.02)  # 126 days at a 126-day half-life halves it


def test_fast_peak_damage_matches_reference():
    rng = np.random.default_rng(3)
    paths = np.cumprod(1 + rng.normal(0.0003, 0.015, (50, 300)), axis=1) * 100
    fast = F.peak_damage_paths(paths, 0.15, 0.03, 2.0, math.log(2) / 126, start_damage=0.2)
    for p, f in zip(paths, fast):
        r = F.fatigue_path(p, 0.15, 0.03, 2.0, math.log(2) / 126, start_damage=0.2)
        assert f == pytest.approx(max(float(r.damage.max()), r.now), abs=1e-12)


def test_calibration_recovers_healing_and_matches_events():
    rng = np.random.default_rng(1)
    v = pd.Series(100 * np.cumprod(1 + rng.normal(0.0003, 0.012, 4000)),
                  index=pd.bdate_range("2000-01-03", periods=4000))
    true = dict(endurance=0.04, exponent=2.5, half_life=63)
    D = F.fatigue_path(v.values, 0.15, true["endurance"], true["exponent"], math.log(2) / true["half_life"]).damage.values
    events = v.index[F._crossings(D, 0.9)]  # this synthetic investor gives up whenever damage rises through 0.9
    assert len(events) >= 5
    cal = F.calibrate(v, events, 0.15)
    assert cal.match == pytest.approx(1.0)
    assert cal.healing_half_life == true["half_life"]
    perfect = [g for g in cal.grid if g["match"] == pytest.approx(1.0)]
    assert any(g["exponent"] == true["exponent"] and g["endurance"] == true["endurance"] for g in perfect)
    prof = calibrate_profile(v, events, AxleProfile(equanimity=0.5), capacity=0.15)
    assert prof.endurance == pytest.approx(cal.endurance)


# --------------------------------------------------------------------------- the module
@pytest.fixture(scope="module")
def axle(ws):
    return Achchu(ws)


def test_capacity_means_and_mind(ws):
    nav = ws.nav
    # Mind binds: stated 20% x 0.8 margin = 16%
    c = Achchu(ws).capacity()
    assert c["capacity"] == pytest.approx(0.16) and c["binding"] == "mind"
    # Means bind: needs of 90% of NAV not covered leave room for a 10% fall
    p = AxleProfile(monthly_outflow=0.9 * nav / 12, horizon_months=12)
    c = Achchu(ws, p).capacity()
    assert c["means"] == pytest.approx(0.10) and c["binding"] == "means" and c["capacity"] == pytest.approx(0.10)
    # An observed tolerance replaces the stated one, without the margin
    c = Achchu(ws, AxleProfile(observed_tolerance=0.22)).capacity()
    assert c["mind"] == pytest.approx(0.22) and c["mind_source"] == "observed"


def test_loads_sum_to_one(axle):
    loads = axle.loads()
    assert loads["load_share"].sum() == pytest.approx(1.0)
    assert loads.iloc[0]["load_share"] >= loads.iloc[-1]["load_share"]


def test_fatigue_on_sample_portfolio(axle):
    f = axle.fatigue()
    assert 0 < f.now and 0 <= f.life_left <= 1
    assert f.now == pytest.approx(f.closed_damage_now + f.open_damage)
    assert all(c.size > f.endurance for c, _ in f.charged)


def test_break_probability_falls_as_capacity_rises(ws):
    probs = [Achchu(ws, AxleProfile(stated_tolerance=t), n_sims=300).break_probability() for t in (0.15, 0.25, 0.40)]
    assert probs[0] >= probs[1] >= probs[2]
    assert probs[0] > probs[2]


def test_equanimity_raises_endurance():
    assert AxleProfile(equanimity=1.0).endurance > AxleProfile(equanimity=0.0).endurance
    assert AxleProfile(discipline=1.0).healing_rate == pytest.approx(2 * AxleProfile().healing_rate)


def test_equanimity_from_trades():
    rng = np.random.default_rng(5)
    idx = pd.bdate_range("2020-01-01", periods=600)
    values = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, 600)), index=idx)
    move = values.pct_change(21).abs().shift(1).fillna(0)
    reactive = equanimity_from_trades(move * 1e6, values)  # trades more after big moves
    steady = equanimity_from_trades(pd.Series(rng.uniform(0, 1, 600), index=idx), values)
    assert reactive < 0.2 and steady > 0.8


def test_gate_blocks_a_huge_buy(axle):
    g = axle.gate("NWT", 5000)
    assert g["decision"] == "block"
    assert not g["tests"]["tip_of_branch"]
    assert g["break_probability_after"] > g["break_probability_now"]
    assert "Kural 476" in g["plain"]


def test_gate_allows_a_risk_reducing_sale(axle):
    g = axle.gate("EMF", -3000)
    assert g["decision"] == "allow"
    assert g["break_probability_after"] <= g["break_probability_now"]
    assert g["strengths"]["self_added_damage"] < 0


def test_gate_waits_outside_home_ground(axle):
    hg = axle.home_ground()
    assert hg.loc["NWT", "inside_now"] is False or hg.loc["NWT", "inside_now"] == False  # noqa: E712
    g = axle.gate("NWT", 20)
    assert g["decision"] == "wait"
    assert g["strengths"]["deed"] == 0.0  # out of its water, the crocodile's strength does not count
    assert "Kural 495" in g["plain"]


def test_gate_suggests_a_safe_size(axle):
    g = axle.gate("GLDX", 3000)
    assert g["decision"] in ("block", "hold")
    assert 0 < g["safe_quantity"] < 3000
    smaller = axle.gate("GLDX", g["safe_quantity"], search_size=False)
    assert smaller["decision"] == "allow"


def test_gate_refuses_unknown_instrument(axle):
    with pytest.raises(KeyError):
        axle.gate("NOPE", 10)
    with pytest.raises(ValueError):
        axle.gate("NWT", 0)


def test_report_is_complete(axle):
    r = axle.report()
    for key in ("profile", "capacity", "fatigue", "break_probability", "loads", "home_ground", "verses", "plain"):
        assert key in r
    assert len(r["fatigue"]["history"]["dates"]) == len(r["fatigue"]["history"]["damage"])
    assert "Kural 475" in r["verses"]["475"]


def test_explainer_routes_axle_questions(ws):
    from samatva.modules.explainer import Explainer

    e = Explainer(ws)
    assert e.ask("How close am I to breaking?")["intent"] == "axle"
    assert e.ask("Will I panic and give up?")["intent"] == "axle"
    assert e.ask("Am I breaking any rules?")["intent"] == "compliance"


# --------------------------------------------------------------------------- the API
@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from samatva.api.app import app, state

    with TestClient(app) as c:
        yield c
    state.axle_profile = AxleProfile()


def test_api_report_and_gate(client):
    r = client.get("/api/achchu")
    assert r.status_code == 200 and 0 <= r.json()["fatigue"]["life_left"] <= 1
    g = client.post("/api/achchu/gate", json={"instrument_id": "EMF", "quantity": -3000})
    assert g.status_code == 200 and g.json()["decision"] == "allow"
    bad = client.post("/api/achchu/gate", json={"instrument_id": "NOPE", "quantity": 1})
    assert bad.status_code == 400 and "Unknown instrument" in bad.json()["detail"]


def test_api_profile_update(client):
    before = client.get("/api/achchu").json()["capacity"]["capacity"]
    r = client.put("/api/achchu/profile", json={"stated_tolerance": 0.30})
    assert r.status_code == 200 and r.json()["stated_tolerance"] == 0.30
    after = client.get("/api/achchu").json()["capacity"]["capacity"]
    assert after == pytest.approx(0.24) and after > before
    assert client.put("/api/achchu/profile", json={"colour": 1}).status_code == 422
    assert client.put("/api/achchu/profile", json={"stated_tolerance": 2}).status_code == 422
    client.put("/api/achchu/profile", json={"stated_tolerance": 0.20})


def test_api_order_is_rejected_when_achchu_blocks(client):
    r = client.post("/api/trading/orders", json={"instrument_id": "NWT", "side": "BUY", "quantity": 5000})
    body = r.json()
    assert body["achchu"]["decision"] == "block"
    assert body["order"]["status"] == "REJECTED"
    ok = client.post("/api/trading/orders", json={"instrument_id": "GLDX", "side": "BUY", "quantity": 300}).json()
    assert ok["achchu"]["decision"] == "allow" and ok["order"]["status"] != "REJECTED"
