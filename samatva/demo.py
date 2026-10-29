"""Export the dashboard as a single, self-contained demo page.

The exported page needs no server: every API answer is captured in advance
(by calling the real API in-process, so the shapes are identical), and the
live module runs a faithful in-browser copy of the stream processor. Custom
scenarios are recomputed in the browser using the same bond maths.

    samatva export-demo dashboard-demo.html
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote

from .api.app import STATIC, clean

GET_PATHS = [
    "/api/summary", "/api/holdings", "/api/risk", "/api/scenarios", "/api/scenarios/projection",
    "/api/performance", "/api/performance/attribution", "/api/compliance", "/api/private", "/api/commentary",
    "/api/glossary", "/api/trading/orders", "/api/live/benchmark?ticks=200000", "/api/achchu",
] + [f"/api/allocation?by={d}" for d in ("asset_class", "sector", "country", "currency")]

SAMPLE_QUESTIONS = [
    "How much is my portfolio worth?", "How much could I lose?", "Where is my risk coming from?",
    "How am I doing against the benchmark?", "Why did the portfolio move today?", "How liquid is the portfolio?",
    "Am I breaking any rules?", "How close am I to breaking?", "How exposed am I to interest rates?", "Is the portfolio concentrated?",
    "How risky is it?", "What about my private funds?", "What happens in a 2008 crisis?",
    "What happens in a 2022-style inflation shock?", "What does Sharpe ratio mean?", "What is VaR?",
    "What is drawdown?", "What is duration?", "What is TVPI?",
]


def build_payload() -> dict:
    os.environ["SAMATVA_LIVE"] = "0"
    from fastapi.testclient import TestClient

    from .api.app import app, state

    client = TestClient(app)
    out = {}
    for p in GET_PATHS:
        r = client.get(p)
        r.raise_for_status()
        out["GET " + p] = r.json()
    for m in ("min_variance", "max_sharpe", "risk_parity", "mean_variance"):
        opt = client.post("/api/trading/optimise", json={"method": m}).json()
        out[f"POST /api/trading/optimise {m}"] = opt
        out[f"POST /api/trading/rebalance {m}"] = client.post("/api/trading/rebalance", json=opt["weights"]).json()
    from .api.static_presets import GATE_PRESETS

    for iid, q in GATE_PRESETS:
        out[f"POST /api/achchu/gate {iid} {q}"] = client.post("/api/achchu/gate", json={"instrument_id": iid,
                                                                                        "quantity": q}).json()
    out["answers"] = {q: client.get("/api/explain?q=" + quote(q)).json() for q in SAMPLE_QUESTIONS}

    ws = state.ws
    ids = ws.held_ids
    meta = []
    for iid in ids:
        inst = ws.master.get(iid)
        v = ws.valuation.loc[iid]
        meta.append({"id": iid, "name": inst.name, "asset_class": inst.asset_class, "sector": inst.sector,
                     "country": inst.country, "currency": inst.currency, "coupon": inst.coupon,
                     "maturity": inst.maturity_years, "freq": inst.coupon_frequency,
                     "ytm": ws.market.yields.get(iid), "market_value": float(v["market_value"]),
                     "weight": float(v["weight"])})
    cov = ws.asset_returns[ids].fillna(0.0).cov().values.tolist()
    out["meta"] = {"instruments": meta, "cov": cov, "nav": ws.nav, "cash": ws.portfolio.cash,
                   "base_currency": ws.portfolio.base_currency, "as_of": str(ws.market.as_of.date())}
    return clean(out)


def export_html(path: str | Path, standalone: bool = True) -> Path:
    """Write the demo page. With `standalone`, it is wrapped in a full HTML document
    so it opens directly from disk; without, it is the bare page body for hosts
    that supply their own document skeleton."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    payload = json.dumps(build_payload(), separators=(",", ":")).replace("</", "<\\/")
    marker = '<script type="application/json" id="demo-data"></script>'
    if marker not in html:
        raise RuntimeError("The dashboard template is missing its demo-data placeholder.")
    html = html.replace(marker, f'<script type="application/json" id="demo-data">{payload}</script>')
    if standalone:
        html = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
                f"</head><body>{html}</body></html>")
    path = Path(path)
    path.write_text(html, encoding="utf-8")
    return path
