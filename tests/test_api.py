"""The web API: every endpoint answers, and answers sensibly."""

import pytest
from fastapi.testclient import TestClient

from openportfolio.api.app import app, state
from openportfolio.workspace import Workspace


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    state.ws = Workspace.sample()  # undo any import done in these tests


GETS = ["/api/health", "/api/summary", "/api/holdings", "/api/allocation?by=sector", "/api/data-quality",
        "/api/risk", "/api/risk/var?confidence=0.99&horizon_days=10", "/api/risk/contributions?ewma=true",
        "/api/scenarios", "/api/scenarios/projection?simulations=500", "/api/performance",
        "/api/performance/attribution", "/api/compliance", "/api/compliance/log", "/api/trading/orders",
        "/api/private", "/api/explain?q=how%20risky%20is%20it", "/api/commentary", "/api/glossary",
        "/api/live/benchmark?ticks=5000"]


@pytest.mark.parametrize("path", GETS)
def test_get_endpoints(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text
    assert r.json() is not None


def test_dashboard_page(client):
    r = client.get("/")
    assert r.status_code == 200 and r.text.startswith("<!doctype html>") and "Open Portfolio Platform" in r.text


def test_bad_input_gets_a_clear_message(client):
    r = client.get("/api/allocation?by=colour")
    assert r.status_code == 400 and "grouped by" in r.json()["detail"]
    assert client.get("/api/risk/var?confidence=2").status_code == 422


def test_scenario_and_trading_flow(client):
    s = client.post("/api/scenarios/custom", json={"equity": -0.1, "rates": 0.01}).json()
    assert s["pnl"] < 0
    w = client.post("/api/trading/optimise", json={"method": "risk_parity"}).json()["weights"]
    plan = client.post("/api/trading/rebalance?create_orders=true", json=w).json()
    assert plan["compliance"]["approved"] and len(plan["orders"]) == len(plan["trades"])
    rejected = client.post("/api/trading/orders", json={"instrument_id": "EMB", "side": "BUY", "quantity": 5000}).json()
    assert rejected["order"]["status"] == "REJECTED"
    oid = client.post("/api/trading/orders", json={"instrument_id": "NWT", "side": "BUY", "quantity": 1}).json()["order"]["id"]
    assert client.post(f"/api/trading/orders/{oid}/cancel").json()["status"] == "CANCELLED"


def test_pretrade_and_rule_replacement(client):
    assert not client.post("/api/compliance/pretrade", json={"EMB": 1000}).json()["approved"]
    r = client.put("/api/compliance/rules", json=[{"id": "only", "type": "min_cash", "limit": 0.01}])
    assert r.json()["rules"] == 1
    assert client.get("/api/compliance").json()["counts"]["breach"] == 0
    client.put("/api/compliance/rules", json=[{"id": "bad", "type": "nonsense", "limit": 1}])
    from openportfolio.modules.compliance import default_rulebook

    state.rulebook = default_rulebook()


def test_pdf_and_excel_downloads(client):
    assert client.get("/api/report.pdf").content[:5] == b"%PDF-"
    assert client.get("/api/report.xlsx").content[:2] == b"PK"


def test_import_holdings(client):
    r = client.post("/api/import/holdings", json={"csv": "ticker,shares\nNWT,1000\nHLX,500\nZZZ,1\nCASH,10000"})
    assert r.status_code == 200 and r.json()["skipped"] == ["ZZZ"]
    assert client.get("/api/summary").json()["holdings"] == 2
    assert client.post("/api/import/holdings", json={"csv": "ticker,shares\nZZZ,1"}).status_code == 400
