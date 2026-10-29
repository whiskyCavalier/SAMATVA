# Open Portfolio Platform

One open-source system for seeing a whole investment portfolio, understanding its risk in plain English, and acting on it.

Large institutions run their portfolios on expensive, closed platforms that take months to install and need specialists to operate. Most investment teams (smaller fund managers, family offices, wealth advisers, treasuries, universities) need the same core answers but have neither the budget nor the patience. This project offers those teams a free core platform, measured results, and the reasoning behind every number.

It is built on three principles:

- **Simple to understand.** Every number arrives with a one-line meaning beside it and its formula one click away. Nothing is a black box.
- **Strong to analyse.** It uses the methods professionals use: historical, parametric and Monte Carlo Value at Risk, Euler risk attribution, factor regression, exact bond repricing, Brinson attribution, constrained optimisation and private-market cash-flow models.
- **Quick to start.** It installs with one command and works from a sample portfolio within minutes. Your own data loads from an ordinary CSV file.

---

## Five minutes to a working platform

```bash
pip install -e .            # from this folder; Python 3.10 or later
opp demo                    # a tour of every module, printed in the terminal
opp serve                   # the dashboard, at http://localhost:8000
```

There is also a Docker route:

```bash
docker compose up           # then open http://localhost:8000
```

To look at your own holdings, export them to a CSV with a ticker (or ISIN) column and a quantity column, then run:

```bash
opp import my_holdings.csv
```

`examples/my_holdings.csv` shows the format.

---

## What is inside: eight modules under one roof

Each module answers one plain question. All of them read the same holdings, prices and reference data, so a figure on one screen always matches the same figure on another.

| # | Module | The question it answers | What it calculates |
|---|---|---|---|
| 1 | Portfolio hub | What do I own, and what is it worth now? | Valuation in a base currency, allocation by asset class, sector, country and currency, fund look-through, unrealised gains |
| 2 | Risk | How much could I lose, and where is the risk coming from? | Volatility, EWMA volatility, VaR and Expected Shortfall four ways, drawdown, beta, tracking error, risk contribution by holding, factor exposures, duration and DV01, liquidity |
| 3 | Scenario lab | What happens if the market moves? | Seven stress scenarios, a scenario builder, factor-based scenarios, a 10,000-path Monte Carlo projection |
| 4 | Performance | Am I beating my benchmark, and why? | Time- and money-weighted returns, Sharpe, Sortino, information ratio, period returns, Brinson-Fachler and factor attribution |
| 5 | Compliance | Am I breaking any rules? | A readable rule book with nine rule types, checked both before trades and on current holdings, with a breach log |
| 6 | Trading | What should I trade to reach my target? | Four optimisers, a rebalancer with trading-cost estimates, an order blotter, and pre-trade compliance on every order |
| 7 | Private assets | How do my private investments fit in? | IRR, TVPI, DPI, RVPI, public market equivalent, NAV roll-forward, cash-flow forecasting, and a combined public and private view |
| 8 | Reports | How do I show this to my board? | A PDF report and an Excel workbook, generated in one step |
| AI | Explainer | Can you explain this to me? | Answers questions in plain words from the portfolio's own numbers; can optionally use a language model, restricted to those facts |

A **real-time module** sits alongside them. It streams prices tick by tick, keeps the portfolio's value and risk up to date, and raises alerts as events unfold (see below).

---

## Real-time processing

The stream processor updates the portfolio in constant time on every price tick. A tick changes one price, so it changes one position, and the portfolio's value is adjusted by that difference alone. Heavier calculations, such as live VaR, run a few times a second on a timer.

The test suite measures this rather than asserting it. On a single core of an ordinary cloud machine:

| Measurement | Result |
|---|---|
| Throughput | about 275,000–290,000 ticks per second |
| Latency per tick | 2.8 µs median, about 7 µs at the 99th percentile |
| Full live risk snapshot | about 6 ms |
| Drift between running NAV and a full recalculation after 300,000 ticks | $0.0000003 |
| Per-tick cost after 300,000 ticks compared with the first 100,000 | 1.18× (1.00 would be perfectly constant) |
| Simulated feed's daily volatility compared with history | 0.94–1.06× |
| Jump detection delay | none (flagged on the tick that moved) |

See it for yourself:

```bash
opp stream                                   # live table in the terminal, with a crash at second five
python examples/realtime_showcase.py         # crash, data outage and a speed test
pytest tests/test_realtime_showcase.py -s    # the measured showcase report
```

The full design is in [docs/realtime.md](docs/realtime.md).

### Live market data

The same engine runs on real prices. Coinbase needs no key, so this works straight away, at any hour:

```bash
opp live --portfolio examples/live_crypto.csv --provider coinbase
```

For stocks, put a Finnhub or Alpaca key in a `.env` file (copy `.env.example`), check it with `opp keys --test`, then:

```bash
opp serve --portfolio examples/live_stocks.csv --provider finnhub --benchmark SPY
```

[docs/live-data.md](docs/live-data.md) lists what a live test needs, where each key goes and how to keep keys safe.

---

## Documentation

| Document | Read it when you want to… |
|---|---|
| [Getting started](docs/getting-started.md) | install the platform, run it and load your own data |
| [User guide](docs/user-guide.md) | understand each screen and what to do with it |
| [Methodology](docs/methodology.md) | check exactly how every number is calculated, with its assumptions and limits |
| [Real-time processing](docs/realtime.md) | understand the streaming design and its guarantees |
| [Live market data](docs/live-data.md) | stream real prices: providers, API keys, portfolio files and tests |
| [Architecture](docs/architecture.md) | find your way around the code or extend it |
| [API reference](docs/api.md) | call the platform from another system |
| [Testing](docs/testing.md) | see what is tested and how |

---

## Project layout

```
openportfolio/
  data/          models, security master, importers, data-quality checks, sample universe
  analytics/     pure maths: risk, bonds, performance (no I/O, easy to test)
  modules/       the eight modules and the explainer
  realtime/      feeds (simulated and live providers), stream processor and async engine
  config.py      settings and API keys from .env or the environment
  api/           FastAPI server and the single-file dashboard
  glossary.py    the plain-English meaning and formula of every number
  workspace.py   ties a portfolio to its data; shared by every module
  cli.py         the `opp` command
tests/           140 tests: 135 offline, plus 5 online tests against real providers
examples/        quick start, real-time showcase, live portfolios, a holdings file, a rule book, sample reports
docs/            the documentation listed above
```

---

## About the sample data

The sample companies (Northwind Technologies, Helix Health and the rest) are fictional, and their six years of prices are simulated with a factor model calibrated to realistic volatilities and correlations. Bonds are priced from simulated yields using the same bond maths the platform uses for analysis. This means anyone can run the full platform without a data licence. Replace the sample with your own data as described in [Getting started](docs/getting-started.md).

## Important

This is analytics software. It describes portfolios and estimates risk from history and stated assumptions. It does not give investment advice, and past behaviour does not guarantee future results. Check figures against an independent source before relying on them for decisions or regulatory reporting.

## Licence

Apache License 2.0. See [LICENSE](LICENSE).
