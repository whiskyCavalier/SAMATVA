# Architecture

The platform is four layers, each of which depends only on the layers below it.

```
 ┌───────────────────────────────────────────────────────────────┐
 │ Interfaces    dashboard · REST + WebSocket API · CLI · Python │
 ├───────────────────────────────────────────────────────────────┤
 │ Modules       hub · risk · scenarios · performance ·          │
 │               compliance · trading · private · reports ·      │
 │               explainer · realtime                            │
 ├───────────────────────────────────────────────────────────────┤
 │ Analytics     risk · bonds · performance maths (pure)         │
 ├───────────────────────────────────────────────────────────────┤
 │ Data          models · security master · market data ·        │
 │               connectors · quality checks                     │
 └───────────────────────────────────────────────────────────────┘
```

## The ideas that hold it together

**One data model.** Every module reads the same `Instrument`, `Position`, `Portfolio` and `MarketData` objects (`openportfolio/data`). There is no module-specific copy of the holdings, so numbers cannot drift apart between screens.

**The Workspace.** `Workspace(master, market, portfolio)` is the single object passed to every module. It computes the valuation, weights, base-currency prices, return history and NAV history once, and caches them. Modules take a workspace and return plain dictionaries, which the API serialises without translation. To ask *what if*, a module builds a new workspace with `with_portfolio()` or `with_market()`. The original is never mutated, so pre-trade checks and scenarios cannot leak into the real state.

**Pure analytics.** `openportfolio/analytics` contains only mathematics: arrays and series in, numbers out, with no I/O and no knowledge of portfolios. This is the part most worth auditing, and its plainness makes that easy.

**Words alongside numbers.** `glossary.py` holds the label, plain meaning and formula of every metric. Modules wrap values with `metric(key, value)`, so the meaning travels with the number all the way to the screen, the PDF and the API.

**Plain data for rules.** Compliance rules are dictionaries (JSON-friendly) dispatched by `type` to a small check function. Adding a rule type means writing one function and registering it.

## Module map

| Package | File | Responsibility |
|---|---|---|
| `data` | `models.py` | Instrument, Position, Portfolio |
| | `security_master.py` | One id per instrument; lookup by ticker, ISIN, CUSIP or FIGI |
| | `market_data.py` | Prices, FX, benchmark, factors, yields |
| | `connectors.py` | CSV and Excel import, Yahoo and FRED sources |
| | `quality.py` | Pre-analysis data checks |
| | `sample.py` | The simulated sample universe |
| `analytics` | `risk.py`, `bonds.py`, `performance_math.py` | Pure maths |
| `modules` | `hub.py` … `reports.py` | The eight modules |
| | `explainer.py` | Question answering, commentary, anomaly flags |
| `realtime` | `feed.py`, `processor.py`, `engine.py` | Streaming |
| `api` | `app.py`, `static/index.html` | FastAPI server and dashboard |
| | `demo.py` | Exports the dashboard as a self-contained demo page |

## The dashboard

The dashboard is a single HTML file with no build step and no front-end framework. Charts are drawn as SVG and canvas by small functions inside it. It runs in two modes:

- **Server mode** (served by `opp serve`): it calls the JSON API and listens to `/ws/live`.
- **Demo mode** (`opp export-demo demo.html`): every API response is captured in advance by calling the real API in-process, so the shapes are identical, and embedded in the page. Custom scenarios and the explainer's *what if* questions are recomputed in the browser with the same bond maths. The live module runs a JavaScript copy of the stream processor. The result is one file that works anywhere, including offline.

## Extending the platform

**A new data source.** Implement `latest(tickers) -> {ticker: price}` for live quotes, or produce a price `DataFrame` (dates × instrument ids) for history. Register instruments in a `SecurityMaster`.

**A new compliance rule.** Write `def _my_rule(ws, rule) -> list[Result]`, add it to `CHECKS` in `modules/compliance.py`, and it becomes available in rule files.

**A new scenario.** Append a `Scenario(...)` to `LIBRARY` in `modules/scenarios.py`, or build one at run time with `scenarios.custom()`.

**A new metric.** Add a `Definition` to `GLOSSARY`, compute it in the relevant module, and return `metric("your_key", value)`. It will then appear with its meaning everywhere.

**A different language model.** Supply any object with `complete(system, prompt) -> str` as `Explainer(..., llm=...)`. The built-in engine still handles every question it recognises. Only unrecognised questions reach the model, and they arrive with a fact sheet the model is told to stay within.

**Persistence.** The server keeps state in memory, which is right for a single user or a demonstration. For production, replace the `State` class in `api/app.py` with database-backed storage for portfolios, rule books, orders and the breach log. Nothing else needs to change.

## Choices and their reasons

| Choice | Reason |
|---|---|
| Python, pandas, NumPy, SciPy | The common language of quantitative finance; contributors can read and check the maths |
| FastAPI | Typed endpoints and automatic, interactive API documentation at `/docs` |
| No front-end framework | One file, no build, nothing to keep up to date; easy to restyle |
| Synthetic sample data | Anyone can run everything without a data licence |
| Constant-weight risk history | The standard way to describe the risk of what is owned now |
| Exact bond repricing in scenarios | Duration approximations fail for large rate moves |
| Debounced alerts | One event, one alert; alert fatigue is a real operational risk |
| Apache 2.0 licence | Permissive and business-friendly, with an explicit patent grant |
