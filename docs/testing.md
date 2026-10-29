# Testing

```bash
pip install -e ".[dev]"
pytest                                        # 159 offline tests, about two and a half minutes
pytest tests/test_realtime_showcase.py -s     # the real-time measurements, printed
SAMATVA_RUN_LIVE_TESTS=1 pytest -m live -s        # 5 online tests against real providers
```

In finance, a number that is confidently wrong does more harm than no number at all. The tests are therefore written to check *values*, not merely that code runs. Where possible, each method is checked against a figure you could work out by hand or find in a textbook.

## What is covered

| File | Tests | What it proves |
|---|---|---|
| `test_data.py` | 9 | Identifiers resolve in any form; duplicates are refused; CSV imports cope with loose column names, cash rows and unknown rows; quality checks catch stale prices, spikes and unknown holdings; FX conversion is correct |
| `test_analytics.py` | 20 | Bond prices match textbook values (5-year 5% bond at 6% = 95.7349); yields round-trip; DV01 agrees with duration; convexity improves the estimate; VaR and ES match hand calculations; Monte Carlo converges to the formula; drawdown dates are right; risk contributions add up exactly; factor regression recovers known exposures; TWR ignores cash flows; XIRR gives 10% on a 10% investment; Brinson effects sum to the active return |
| `test_modules.py` | 45 | Each module on the sample portfolio: valuations reconcile; look-through splits the fund; four VaR methods agree within 25%; DV01 matches an exact 1 bp scenario; an equity shock hits only equities; compliance finds the known breach and blocks a tobacco trade; optimisers respect their constraints; risk parity equalises risk; private-fund maths matches hand-worked funds; the explainer routes nine kinds of question correctly; reports are valid PDF and Excel files |
| `test_api.py` | 26 | Every GET endpoint answers; bad input gets a clear message; scenario, optimisation, rebalancing, ordering, pre-trade, rule replacement, downloads and import all work through HTTP |
| `test_achchu.py` | 24 | Rainflow counting matches the ASTM worked example and an independent implementation (`rainflow` package); the hand-worked damage examples (0.64, 1.31, 0.44, 0.37); falls below endurance cost nothing; 126 days of calm at a 126-day half-life halve the damage; the fast simulation path matches the reference exactly; calibration matches synthetic events and recovers the healing rate; capacity from means and mind; load shares sum to one; the chance of breaking falls as capacity rises; the gate blocks a huge buy, allows a risk-reducing sale, waits outside home ground and finds a safe size; the API, profile validation, and order rejection on a block |
| `test_realtime_showcase.py` | 14 | See below |
| `test_live_feeds.py` | 21 | Offline: `.env` parsing and key masking; each provider's messages parsed correctly; local servers that copy the Finnhub, Alpaca and Coinbase protocols prove the round trip, that a bad key stops at once without retrying, that a dropped connection reconnects, and that the full engine runs on a WebSocket feed; real-portfolio files load; history falls back between sources |
| `test_live_online.py` | 5 | Online, opt-in: real Coinbase prices, a real portfolio built from Yahoo history, ten seconds of the full engine on live crypto, plus Finnhub and the Alpaca test stream when keys are set |

## The real-time showcase tests

These tests measure as well as check, and the last one prints what they measured:

```
Real-time processing showcase
=============================
  ticks checked for NAV drift               300,000
  NAV drift after 300k incremental updates  $2.81e-07
  throughput (one core)                     275,382 ticks/s
  latency p50 / p99                         2.8 / 7.0 µs
  full live risk snapshot                   5.84 ms
  per-tick cost, last 100k vs first 100k    1.18x (1.00 = perfectly constant)
  simulated / historical daily volatility   0.94 to 1.06
  mean correlation error                    0.029
  jump detection delay                      0 ticks (flagged on the tick that moved)
  paced feed throughput (target 5,000/s)    5,071 ticks/s
```

They cover six areas: correctness of the running NAV, speed, constant cost per tick, realism of the simulated feed, detection of jumps, limit breaches, VaR breaches and outages (each exactly once), and the end-to-end behaviour of the asynchronous engine and the WebSocket API, including back-pressure. [Real-time processing](realtime.md) explains each guarantee.

Speed thresholds are deliberately generous (for example, more than 50,000 ticks a second), so the suite passes on slow continuous-integration machines while still catching a genuine regression.

## Writing new tests

- Use the shared `ws` fixture for the sample workspace. It is built once per run and never changed. To test a variation, create a new workspace with `ws.with_portfolio(...)`.
- Prefer a hand-worked expected value to a snapshot of whatever the code currently returns, and show the working in a comment.
- When a test exposes a genuine bug, fix the code, not the expectation. The holdings importer's handling of column names such as "Avg Cost" was found and fixed this way.
