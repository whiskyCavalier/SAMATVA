"""The web API and dashboard server.

Run with ``samatva serve`` (or ``uvicorn samatva.api.app:app``) and open
http://localhost:8000. Interactive API documentation is at /docs.

Every analytic in the platform is available as a plain JSON endpoint, so the
dashboard is just one client among many: a spreadsheet, a notebook or another
system can call exactly the same endpoints.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from fastapi import Body, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from .. import __version__
from ..data import connectors, quality
from ..glossary import GLOSSARY
from ..modules import compliance as C
from ..modules import hub, performance as P, private_assets as PA, reports, risk_dashboard as RD
from ..modules import scenarios as S, trading as T
from ..modules.achchu import Achchu, AxleProfile
from ..modules.explainer import Explainer
from ..config import Settings, load_env
from ..realtime import AlertConfig, RealTimeEngine, SimulatedFeed, benchmark
from ..realtime.providers import make_feed
from ..workspace import Workspace

STATIC = Path(__file__).parent / "static"
load_env()  # API keys and settings from ./.env, when started directly with uvicorn


def clean(obj):
    """Make any result JSON-safe: numpy types become Python types, NaN and infinity become null."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (pd.Timestamp,)):
        return str(obj.date())
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, pd.Series):
        return clean(obj.to_dict())
    if isinstance(obj, pd.DataFrame):
        return clean(obj.reset_index().to_dict(orient="records"))
    return obj


class State:
    """Everything the server holds in memory. Swap for a database in production."""

    def __init__(self) -> None:
        self.settings = Settings.from_env()
        if self.settings.portfolio_file:
            from ..data.live_universe import build_live_workspace

            self.ws = build_live_workspace(self.settings.portfolio_file, benchmark=self.settings.benchmark)
            self.funds = []  # the sample private funds only belong with the sample portfolio
        else:
            self.ws = Workspace.sample()
            self.funds = PA.sample_funds()
        self.rulebook = C.default_rulebook()
        self.blotter = T.Blotter()
        self.engine: Optional[RealTimeEngine] = None
        self.axle_profile = AxleProfile()
        self._achchu: Optional[Achchu] = None

    def achchu(self) -> Achchu:
        """One Achchu per portfolio and profile, so its simulations are reused between requests."""
        a = self._achchu
        if a is None or a.ws is not self.ws or a.p is not self.axle_profile:
            a = self._achchu = Achchu(self.ws, self.axle_profile)
        return a

    def explainer(self) -> Explainer:
        llm = None
        if os.environ.get("ANTHROPIC_API_KEY"):
            try:
                from ..modules.explainer import AnthropicBackend

                llm = AnthropicBackend()
            except Exception:
                llm = None
        return Explainer(self.ws, self.rulebook, self.funds, llm, achchu=self.achchu)

    async def restart_engine(self) -> None:
        if self.engine:
            await self.engine.stop()
        st = self.settings
        feed = make_feed(st.provider, self.ws, ticks_per_second=st.ticks_per_second)
        self.engine = RealTimeEngine(self.ws, feed, AlertConfig(stale_seconds=st.stale_seconds))
        await self.engine.start()


state = State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.environ.get("SAMATVA_LIVE", "1") == "1":
        await state.restart_engine()
    yield
    if state.engine:
        await state.engine.stop()


app = FastAPI(title="Samatva", version=__version__, lifespan=lifespan,
              description="One open platform to see your whole portfolio, understand its risk in plain English, "
                          "and act on it.")


def ok(data) -> JSONResponse:
    return JSONResponse(clean(data))


# --------------------------------------------------------------------------- pages
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    body = (STATIC / "index.html").read_text(encoding="utf-8")
    return HTMLResponse('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
                        f"</head><body>{body}</body></html>")


@app.get("/api/health")
def health():
    eng = state.engine
    return {"status": "ok", "version": __version__, "live": bool(eng and eng.running),
            "provider": state.settings.provider, "feed": eng.feed.describe() if eng and hasattr(eng.feed, "describe")
            else None, "feed_error": eng.error if eng else None}


@app.get("/api/glossary")
def glossary():
    return ok({k: vars(v) for k, v in GLOSSARY.items()})


# --------------------------------------------------------------------------- 1. hub
@app.get("/api/summary")
def summary():
    return ok({**hub.summary(state.ws), "description": hub.describe(state.ws)})


@app.get("/api/holdings")
def holdings():
    return ok(hub.holdings(state.ws))


@app.get("/api/allocation")
def allocation(by: str = "asset_class", lookthrough: bool = True):
    try:
        return ok(hub.allocation(state.ws, by, lookthrough))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/data-quality")
def data_quality():
    return ok([i.as_dict() for i in quality.check(state.ws.portfolio, state.ws.master, state.ws.market)])


class HoldingsUpload(BaseModel):
    csv: str = Field(..., description="CSV text with an identifier column and a quantity column")
    name: str = "Imported Portfolio"


@app.post("/api/import/holdings")
async def import_holdings(body: HoldingsUpload):
    try:
        pf, rep = connectors.load_holdings(io.StringIO(body.csv), state.ws.master, body.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not pf.positions:
        raise HTTPException(400, "No rows matched a known instrument. " + rep.summary())
    state.ws = state.ws.with_portfolio(pf)
    if state.engine:
        await state.restart_engine()
    return ok({"message": rep.summary(), "skipped": rep.skipped})


# --------------------------------------------------------------------------- 2. risk
@app.get("/api/risk")
def risk():
    return ok(RD.report(state.ws))


@app.get("/api/risk/var")
def var(confidence: float = Query(0.95, gt=0.5, lt=1.0), horizon_days: int = Query(1, ge=1, le=250)):
    return ok(RD.var_methods(state.ws, confidence, horizon_days))


@app.get("/api/risk/contributions")
def contributions(ewma: bool = False):
    return ok(RD.risk_contributions(state.ws, ewma))


# --------------------------------------------------------------------------- 3. scenarios
@app.get("/api/scenarios")
def scenarios():
    return ok(S.run_library(state.ws))


class CustomScenario(BaseModel):
    name: str = "Custom"
    equity: float = 0.0
    real_estate: float = 0.0
    commodity: float = 0.0
    rates: float = 0.0
    credit_spread: float = 0.0
    fx: Dict[str, float] = {}


@app.post("/api/scenarios/custom")
def custom_scenario(body: CustomScenario):
    return ok(S.custom(state.ws, **body.model_dump()))


@app.post("/api/scenarios/factor")
def factor_scenario(shocks: Dict[str, float] = Body(..., examples=[{"Market": -0.1, "Rates": 0.005}])):
    try:
        return ok(S.factor_scenario(state.ws, shocks))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/scenarios/projection")
def projection(horizon_days: int = Query(252, ge=5, le=2520), simulations: int = Query(10_000, ge=100, le=100_000)):
    return ok(S.monte_carlo_projection(state.ws, horizon_days, simulations))


# --------------------------------------------------------------------------- 4. performance
@app.get("/api/performance")
def performance():
    g = P.growth_series(state.ws)
    g = g.iloc[::5]  # weekly points are plenty for a chart
    return ok({**P.summary(state.ws), "growth": {"dates": [str(d.date()) for d in g.index],
                                                 **{c: g[c].round(3).tolist() for c in g.columns}},
               "contributors": P.contributors(state.ws), "factor_attribution": P.factor_attribution(state.ws)})


@app.get("/api/performance/attribution")
def attribution(start: Optional[str] = None, end: Optional[str] = None):
    try:
        return ok(P.attribution(state.ws, start, end))
    except ValueError as e:
        raise HTTPException(400, str(e))


# --------------------------------------------------------------------------- 5. compliance
@app.get("/api/compliance")
def compliance():
    rules = [{"id": r.id, "type": r.type, "limit": r.limit, "description": r.description, **r.params}
             for r in state.rulebook.rules]
    return ok({**state.rulebook.summary(state.ws), "rules": rules})


@app.get("/api/compliance/log")
def compliance_log():
    return ok(state.rulebook.log[-200:])


@app.post("/api/compliance/pretrade")
def pretrade(trades: Dict[str, float] = Body(..., examples=[{"NWT": 500}])):
    try:
        return ok(state.rulebook.pre_trade(state.ws, trades))
    except KeyError as e:
        raise HTTPException(400, str(e))


@app.put("/api/compliance/rules")
def set_rules(rules: List[Dict] = Body(...)):
    try:
        state.rulebook = C.RuleBook.from_dicts(rules)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    return ok({"rules": len(state.rulebook.rules)})


# --------------------------------------------------------------------------- 6. trading
class OptimiseRequest(BaseModel):
    method: str = "max_sharpe"
    max_weight: float = 0.20
    min_weight: float = 0.01


@app.post("/api/trading/optimise")
def optimise(body: OptimiseRequest):
    try:
        return ok(T.optimise(state.ws, body.method, body.max_weight, body.min_weight))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/trading/rebalance")
def rebalance(targets: Dict[str, float] = Body(...), create_orders: bool = False):
    try:
        plan = T.rebalance(state.ws, targets)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    plan["compliance"] = state.rulebook.pre_trade(state.ws, plan["trade_map"])
    if create_orders and plan["compliance"]["approved"]:
        plan["orders"] = [o.as_dict() for o in T.orders_from_rebalance(plan, state.blotter)]
    elif create_orders:
        plan["orders"] = []
    return ok(plan)


class OrderRequest(BaseModel):
    instrument_id: str
    side: str
    quantity: float
    limit_price: Optional[float] = None


@app.get("/api/trading/orders")
def orders():
    return ok([o.as_dict() for o in state.blotter.orders.values()])


@app.post("/api/trading/orders")
def new_order(body: OrderRequest):
    iid = state.ws.master.resolve(body.instrument_id)
    if iid is None:
        raise HTTPException(400, f"Unknown instrument '{body.instrument_id}'")
    dq = body.quantity if body.side == "BUY" else -body.quantity
    check = state.rulebook.pre_trade(state.ws, {iid: dq})
    try:
        order = T.Order(iid, body.side, body.quantity, body.limit_price)
    except ValueError as e:
        raise HTTPException(400, str(e))
    axle = None
    if body.quantity > 0 and iid in state.ws.asset_returns.columns:
        axle = state.achchu().gate(iid, dq)
    if not check["approved"]:
        order.status, order.note = "REJECTED", check["plain"]
    elif axle and axle["decision"] == "block":
        order.status, order.note = "REJECTED", axle["plain"]
    elif axle and axle["decision"] in ("hold", "wait"):
        order.note = axle["plain"]  # recorded, with Achchu's advice to reflect or wait
    state.blotter.add(order)
    return ok({"order": order.as_dict(), "compliance": check, "achchu": axle})


# --------------------------------------------------------------------------- 6b. Achchu, the axle
class GateRequest(BaseModel):
    instrument_id: str
    quantity: float = Field(..., description="Quantity change: positive to buy, negative to sell")


@app.get("/api/achchu")
def achchu_report():
    return ok(state.achchu().report())


@app.post("/api/achchu/gate")
def achchu_gate(body: GateRequest):
    try:
        return ok(state.achchu().gate(body.instrument_id, body.quantity))
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e).strip("'\""))


@app.get("/api/achchu/profile")
def achchu_profile():
    return ok(state.axle_profile.as_dict())


@app.put("/api/achchu/profile")
def set_achchu_profile(changes: Dict[str, Optional[float]] = Body(..., examples=[{"stated_tolerance": 0.25,
                                                                                    "equanimity": 0.7}])):
    fields = AxleProfile.__dataclass_fields__
    unknown = [k for k in changes if k not in fields]
    if unknown:
        raise HTTPException(422, f"Unknown profile field(s): {', '.join(unknown)}. Known fields: "
                                 f"{', '.join(fields)}.")
    current = {k: getattr(state.axle_profile, k) for k in fields}
    new = AxleProfile(**{**current, **changes})
    if new.observed_tolerance is not None and not 0 < new.observed_tolerance <= 1:
        raise HTTPException(422, "observed_tolerance must be between 0 and 1.")
    if not 0 < new.stated_tolerance <= 1:
        raise HTTPException(422, "stated_tolerance must be between 0 and 1.")
    if int(new.horizon_days) < 21:
        raise HTTPException(422, "horizon_days must be at least 21.")
    new.horizon_days, new.horizon_months = int(new.horizon_days), int(new.horizon_months)
    state.axle_profile = new
    return ok(new.as_dict())


@app.post("/api/trading/orders/{order_id}/cancel")
def cancel_order(order_id: int):
    try:
        return ok(state.blotter.cancel(order_id).as_dict())
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


# --------------------------------------------------------------------------- 7. private assets
@app.get("/api/private")
def private():
    funds = state.funds
    return ok({"funds": [{**PA.fund_metrics(f), "pme": PA.kaplan_schoar_pme(f, state.ws.market.benchmark),
                          "forecast": PA.forecast(f).reset_index().to_dict(orient="records")} for f in funds],
               "combined": PA.combined_view(state.ws, funds)})


# --------------------------------------------------------------------------- 8. reports
@app.get("/api/report.pdf")
def report_pdf():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        reports.to_pdf(state.ws, f.name, state.rulebook, state.funds)
        return FileResponse(f.name, media_type="application/pdf", filename="portfolio-report.pdf")


@app.get("/api/report.xlsx")
def report_xlsx():
    buf = io.BytesIO()
    reports.to_excel(state.ws, buf, state.rulebook, state.funds)
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=portfolio-report.xlsx"})


# --------------------------------------------------------------------------- AI explainer
@app.get("/api/explain")
def explain(q: str = Query(..., min_length=2, max_length=500)):
    return ok(state.explainer().ask(q))


@app.get("/api/commentary")
def commentary():
    ex = state.explainer()
    return ok({"commentary": ex.commentary(), "anomalies": ex.anomalies()})


# --------------------------------------------------------------------------- real time
class ShockRequest(BaseModel):
    move: float = Field(-0.03, gt=-0.9, lt=1.0)
    instrument: Optional[str] = None
    asset_class: Optional[str] = None


@app.get("/api/live/snapshot")
def live_snapshot():
    if not state.engine:
        raise HTTPException(409, "The live engine is not running. Start it with POST /api/live/start.")
    return ok(state.engine.processor.snapshot())


@app.post("/api/live/start")
async def live_start():
    await state.restart_engine()
    return {"status": "running"}


@app.post("/api/live/stop")
async def live_stop():
    if state.engine:
        await state.engine.stop()
    return {"status": "stopped"}


@app.post("/api/live/shock")
def live_shock(body: ShockRequest):
    if not state.engine or not isinstance(state.engine.feed, SimulatedFeed):
        raise HTTPException(409, "Shocks can only be injected into the simulated feed.")
    hit = state.engine.feed.inject_shock(body.move, body.instrument, body.asset_class)
    return {"applied_to": hit}


@app.post("/api/live/outage/{instrument}")
def live_outage(instrument: str, on: bool = True):
    if not state.engine or not isinstance(state.engine.feed, SimulatedFeed):
        raise HTTPException(409, "Outages can only be simulated on the simulated feed.")
    (state.engine.feed.pause if on else state.engine.feed.resume)(instrument)
    return {"instrument": instrument, "paused": on}


@app.get("/api/live/benchmark")
def live_benchmark(ticks: int = Query(200_000, ge=1_000, le=2_000_000)):
    return ok(benchmark(state.ws, ticks))


@app.websocket("/ws/live")
async def ws_live(sock: WebSocket):
    await sock.accept()
    if not state.engine:
        await state.restart_engine()
    q = state.engine.subscribe()
    engine = state.engine

    async def receive():
        while True:
            msg = json.loads(await sock.receive_text())
            if msg.get("action") == "shock" and isinstance(engine.feed, SimulatedFeed):
                engine.feed.inject_shock(float(msg.get("move", -0.03)), msg.get("instrument"), msg.get("asset_class"))

    receiver = asyncio.create_task(receive())
    try:
        while True:
            snap = await q.get()
            await sock.send_text(json.dumps(clean(snap)))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        receiver.cancel()
        engine.unsubscribe(q)
