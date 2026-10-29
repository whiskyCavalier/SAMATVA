# Getting started

This guide takes you from nothing to a running platform, and then to your own portfolio on it. Allow about fifteen minutes.

## 1. Install

You need Python 3.10 or later. From the project folder:

```bash
pip install -e .
```

That installs the platform and the `opp` command. To use a language model in the explainer as well, install the optional extra and set your key:

```bash
pip install -e ".[ai]"
export ANTHROPIC_API_KEY=...        # optional; the built-in explainer works without it
```

If you prefer containers, `docker compose up` builds and starts everything. No Python is needed on the host.

## 2. Take the tour

```bash
opp demo
```

This prints a short report from every module, using the sample portfolio: a $10 million balanced fund of eleven holdings across shares, bonds, property and gold. Reading it end to end is the quickest way to see what the platform does.

## 3. Open the dashboard

```bash
opp serve
```

Open http://localhost:8000. The left-hand rail lists the modules. Each opens with the question it answers, then gives the summary first and the detail below. The **Live stream** module starts streaming straight away.

The same server provides a JSON API. Interactive documentation for every endpoint is at http://localhost:8000/docs.

Useful settings, all optional. Put them in a `.env` file in the project folder (copy `.env.example`) or set them as environment variables:

| Variable | Default | Effect |
|---|---|---|
| `OPP_LIVE` | `1` | Set to `0` to stop the live engine starting with the server |
| `OPP_TICKS_PER_SECOND` | `500` | Speed of the simulated price feed |
| `ANTHROPIC_API_KEY` | not set | Lets the explainer pass unusual questions to a language model |
| `OPP_DATA_PROVIDER` | `simulated` | Where live prices come from; see [Live market data](live-data.md) |
| `OPP_PORTFOLIO_FILE` | not set | A CSV of real instruments to analyse and stream |
| `OPP_LLM_MODEL` | `claude-sonnet-5-5` | The model the explainer uses when a key is set |

## 4. Bring your own holdings

The quickest route is a CSV file with one row per holding. Only two columns are required: something that identifies the instrument, and a quantity.

```csv
Ticker,Shares,Avg Cost
NWT,2500,250.00
US0000000EMF,6000,120.00
UST10,15000,95.50
CASH,250000,
```

Column names are matched loosely, so `Ticker`, `ISIN`, `CUSIP`, `FIGI` or `Symbol` all identify the instrument, and `Shares`, `Quantity`, `Units` or `Nominal` all give the amount. A row whose identifier is `CASH` becomes cash. An optional cost column (`Avg Cost`, `Cost Price`, `Book Price`) enables unrealised gains. Bonds are quoted per 100 of face value, so a quantity of 15,000 means 1.5 million of face value.

```bash
opp import my_holdings.csv
```

The importer tells you how many rows it matched and lists any it could not, so nothing is dropped without your knowledge. You can also import through the dashboard's API with `POST /api/import/holdings`.

## 5. Bring your own instruments and prices

Real use means real instruments and prices. In Python:

```python
from openportfolio.data import Instrument, SecurityMaster, MarketData
from openportfolio.data.connectors import load_holdings, load_prices
from openportfolio.workspace import Workspace
import pandas as pd

master = SecurityMaster([
    Instrument("AAPL", "Apple Inc.", "Equity", sector="Technology", country="United States",
               currency="USD", ticker="AAPL", avg_daily_volume=55_000_000),
    Instrument("GILT30", "UK Gilt 4.25% 2030", "Fixed Income", sector="Government",
               country="United Kingdom", currency="GBP", coupon=0.0425, maturity_years=4),
])
prices = load_prices("prices.csv", master)          # a date column, then one column per instrument
fx = pd.DataFrame({"USD": 1.0, "GBP": 1.27}, index=prices.index)  # base currency per unit
market = MarketData(prices, fx, risk_free_rate=0.04)
portfolio, report = load_holdings("holdings.csv", master)
print(report.summary())

ws = Workspace(master, market, portfolio)           # every module now works on your data
```

Two optional extras improve the analysis:

- a **benchmark** series in `MarketData(benchmark=...)` enables beta, tracking error and the information ratio;
- **factor returns** in `MarketData(factors=...)` enable factor exposures and factor scenarios.

The quickest route to real instruments is a portfolio CSV with symbol columns, which `opp live` and `opp serve --portfolio` load directly, downloading history for you. [Live market data](live-data.md) shows the format. For free price data in code, `YahooQuoteSource` (delayed quotes) and `FredSeriesSource` (interest rates and other economic series) are included in `openportfolio.data.connectors`. Licensed feeds plug in through the same small interface and use your own licence; the platform ships connectors, never data.

## 6. Check your data before trusting the numbers

```python
from openportfolio.data import quality
for issue in quality.check(portfolio, master, market):
    print(issue.severity, issue.message)
```

This reports holdings missing from the security master, missing or stale prices, implausible one-day jumps, non-positive prices and missing exchange rates, in plain language. The dashboard exposes the same check at `/api/data-quality`.

## 7. Produce a report

```bash
opp report --pdf board-pack.pdf --xlsx board-pack.xlsx
```

The PDF is written for reading: commentary, charts and every key figure with its meaning. The workbook is written for working: one sheet per module, with every number.

## 8. Run the tests

```bash
pip install -e ".[dev]"
pytest                                         # 135 offline tests, about two minutes
OPP_RUN_LIVE_TESTS=1 pytest -m live -s         # 5 online tests against real providers
pytest tests/test_realtime_showcase.py -s      # with the real-time measurements printed
```

## Where next

- [User guide](user-guide.md): what each screen tells you and how to act on it.
- [Methodology](methodology.md): exactly how each number is produced.
- [Real-time processing](realtime.md): connecting a live price feed.
