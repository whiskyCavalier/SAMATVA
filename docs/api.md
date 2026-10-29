# API reference

Start the server with `opp serve`. Every endpoint returns JSON unless stated otherwise. Interactive documentation, where you can try each call in the browser, is at `/docs` on the running server.

Conventions:

- Fractions are decimals. `0.05` means 5%, and in rates and spreads `0.01` means one percentage point.
- Money is in the portfolio's base currency.
- Wherever a metric appears, it carries `label`, `value`, `meaning`, `formula` and `fmt` (one of `pct`, `money`, `ratio`, `days` or `number`).
- Errors return an HTTP status of 400, 409 or 422 with a `detail` message that says what went wrong and how to fix it.

## Portfolio hub

| Method | Path | Returns |
|---|---|---|
| GET | `/api/summary` | NAV, cash, day change, unrealised gain and a one-sentence description |
| GET | `/api/holdings` | One row per holding: quantity, price, FX rate, value, weight, unrealised gain |
| GET | `/api/allocation?by=asset_class&lookthrough=true` | Weights by `asset_class`, `sector`, `country` or `currency` |
| GET | `/api/data-quality` | Data issues found, each with a severity and a plain message |
| POST | `/api/import/holdings` | Body `{"csv": "...", "name": "..."}`. Replaces the portfolio and reports skipped rows |

## Risk

| Method | Path | Returns |
|---|---|---|
| GET | `/api/risk` | Headline metrics, a summary sentence, VaR methods, contributions, factors, bonds and liquidity |
| GET | `/api/risk/var?confidence=0.99&horizon_days=10` | VaR and ES by four methods, as fractions and in money |
| GET | `/api/risk/contributions?ewma=true` | Each holding's share of risk; `ewma` weights recent days more |

## Scenarios

| Method | Path | Returns |
|---|---|---|
| GET | `/api/scenarios` | The seven library scenarios, worst first, each with a per-holding breakdown |
| POST | `/api/scenarios/custom` | Body: `equity`, `real_estate`, `commodity`, `rates`, `credit_spread`, `fx` (`{"EUR": -0.05}`) and an optional `name` |
| POST | `/api/scenarios/factor` | Body: factor shocks, for example `{"Market": -0.1, "Rates": 0.005}` |
| GET | `/api/scenarios/projection?horizon_days=252&simulations=10000` | Monte Carlo percentiles, loss probabilities and a histogram |

Example:

```bash
curl -s -X POST localhost:8000/api/scenarios/custom \
  -H 'Content-Type: application/json' \
  -d '{"equity": -0.2, "rates": 0.01}' | jq .plain
"Under 'Custom', the portfolio would lose about $1.17m (-11.7%). ..."
```

## Performance

| Method | Path | Returns |
|---|---|---|
| GET | `/api/performance` | Period returns against the benchmark, metrics, weekly growth series, contributors and factor attribution |
| GET | `/api/performance/attribution?start=2025-10-23&end=2026-10-23` | Brinson-Fachler table by asset class and a plain summary |

## Compliance

| Method | Path | Returns |
|---|---|---|
| GET | `/api/compliance` | Counts, issues, every result and the rule book |
| GET | `/api/compliance/log` | The last 200 warnings and breaches, with timestamps |
| POST | `/api/compliance/pretrade` | Body `{"NWT": 500, "HLX": -200}` (quantity changes). Returns `approved` and any new breaches |
| PUT | `/api/compliance/rules` | Body: a list of rule objects (see `examples/rules.json`). Replaces the rule book |

## Trading

| Method | Path | Returns |
|---|---|---|
| POST | `/api/trading/optimise` | Body `{"method": "risk_parity", "max_weight": 0.2, "min_weight": 0.01}`. Methods: `min_variance`, `max_sharpe`, `risk_parity`, `mean_variance` |
| POST | `/api/trading/rebalance?create_orders=true` | Body: target weights `{"NWT": 0.08, ...}`. Returns the trades, costs and pre-trade verdict. Orders are created only if the check passes |
| GET | `/api/trading/orders` | The blotter |
| POST | `/api/trading/orders` | Body `{"instrument_id", "side": "BUY" or "SELL", "quantity", "limit_price"}`. Checked before it is recorded; rejected orders are kept with their reason |
| POST | `/api/trading/orders/{id}/cancel` | Cancels an open order |

## Private assets

| Method | Path | Returns |
|---|---|---|
| GET | `/api/private` | Each fund's metrics, PME and forecast, plus the combined public and private view |

## Reports

| Method | Path | Returns |
|---|---|---|
| GET | `/api/report.pdf` | The PDF report |
| GET | `/api/report.xlsx` | The Excel workbook |

## Explainer

| Method | Path | Returns |
|---|---|---|
| GET | `/api/explain?q=how+much+could+I+lose` | `answer`, `intent`, `engine` (`built-in` or `llm`) and `sources`: the figures used |
| GET | `/api/commentary` | The daily commentary and today's anomaly flags |

## Real time

| Method | Path | Returns |
|---|---|---|
| WS | `/ws/live` | A snapshot four times a second. Send `{"action": "shock", "move": -0.03, "asset_class": "Equity"}` to inject an event |
| GET | `/api/live/snapshot` | The current snapshot |
| POST | `/api/live/start`, `/api/live/stop` | Starts or stops the engine |
| POST | `/api/live/shock` | Body `{"move": -0.05, "instrument": null, "asset_class": "Equity"}` |
| POST | `/api/live/outage/{instrument}?on=true` | Silences (or restores) one instrument's prices |
| GET | `/api/live/benchmark?ticks=200000` | Runs the speed test and returns throughput and latency |

A snapshot looks like this (abridged). `source` describes the feed; `stats.feed_delay_ms` is the average gap between the exchange's trade time and its arrival, for live providers that report trade times:

```json
{
  "nav": 9826343.1, "nav_open": 9999479.4, "day_pnl": -173136.3, "day_return": -0.0173,
  "var95": 107568.0, "es95": 134894.2, "var_used": 1.56, "intraday_drawdown": 0.0181,
  "positions": [{"id": "NWT", "name": "Northwind Technologies", "move": -0.041, "pnl": -41012.7, "weight": 0.098}],
  "alerts": [{"ts": 1793145559.2, "kind": "loss", "severity": "critical", "message": "Today's loss of ..."}],
  "stats": {"ticks": 4799, "ticks_per_second": 1831.3, "p50_us": 3.4, "p99_us": 22.1},
  "trail": [9999479.4, 9998211.0],
  "source": {"provider": "coinbase", "status": "connected", "reconnects": 0, "messages": 58, "symbols": 4}
}
```

## General

| Method | Path | Returns |
|---|---|---|
| GET | `/api/health` | Status, version, whether the live engine is running, the provider, the feed's connection status, and any feed error (for example a rejected key) |
| GET | `/api/glossary` | The label, meaning and formula of every metric |
| GET | `/` | The dashboard |
