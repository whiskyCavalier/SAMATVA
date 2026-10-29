# Testing with live market data

This guide covers what you need to stream real prices through the platform, where your API keys go, and how to check that each step works. The whole process takes about ten minutes. The first test needs no key at all.

## What a live test needs

| You need | Why | Where it goes |
|---|---|---|
| **1. A data provider** | Somewhere for live prices to come from | `--provider` option, or `OPP_DATA_PROVIDER` in `.env` |
| **2. An API key** (for most providers) | Proves to the provider who you are | `.env` file in the project folder (see below) |
| **3. A portfolio of real instruments** | The sample fund's companies are fictional, so no exchange trades them | A CSV file, for example `examples/live_stocks.csv` |
| **4. Each provider's symbol for each holding** | Providers spell instruments differently (BTC-USD, BINANCE:BTCUSDT, BTC/USD) | Columns in that CSV |
| **5. Price history** | Volatility, VaR and "today's open" all come from history | Downloaded automatically from Yahoo Finance (no key), with Coinbase and Alpaca as fallbacks |
| **6. An open market** | Stocks trade only in market hours | Crypto trades around the clock; Alpaca has an always-on test stream |
| **7. Internet access** | To reach the provider | Ports 443 (HTTPS and secure WebSockets) |

## Choosing a provider

| Provider | Key needed | What you get | Good for |
|---|---|---|---|
| `coinbase` | **None** | Live crypto prices, 24 hours a day, 7 days a week | Your first test, at any hour |
| `finnhub` | `FINNHUB_API_KEY` | Live US stock trades and crypto trades (free plan) | Stocks during market hours; crypto at any time |
| `alpaca` | `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY` | Live US stock trades. The free plan uses the IEX exchange feed, which covers a share of all trading; the paid SIP feed covers all US exchanges | Stocks during market hours |
| `alpaca-test` | The same two Alpaca keys | Alpaca's always-on stream of a made-up symbol, FAKEPACA. Its percentage moves are applied to one of your holdings | Checking your keys and the whole pipeline outside market hours |
| `yahoo` | None | Delayed quotes, polled every 5 seconds | A quick look without signing up; not suitable for production |
| `simulated` | None | Realistic simulated ticks (the default) | Demonstrations and testing |

Free keys:

- **Finnhub**: sign up at finnhub.io; the key is on your dashboard.
- **Alpaca**: open a free account at alpaca.markets. Under *API Keys*, generate a key and secret. Keys from a paper-trading account work for market data, and they cannot move real money, which makes them the safest choice for testing.

## Where the API key goes

Keys go in a file named **`.env`** in the folder you run `opp` from (normally the project folder). The platform reads it automatically every time it starts.

**Step 1.** Copy the template:

```bash
cp .env.example .env
```

**Step 2.** Open `.env` in a text editor and paste your keys after the `=` signs. No quotes or spaces are needed:

```bash
OPP_DATA_PROVIDER=finnhub
OPP_PORTFOLIO_FILE=examples/live_stocks.csv

FINNHUB_API_KEY=your-finnhub-key-here

ALPACA_API_KEY_ID=your-alpaca-key-id-here
ALPACA_API_SECRET_KEY=your-alpaca-secret-here
ALPACA_FEED=iex
```

**Step 3.** Check that the keys were found:

```bash
opp keys
```

```
  FINNHUB_API_KEY          d0ab…9f2c    Real-time US stock trades and crypto trades over WebSocket
  ALPACA_API_KEY_ID        PKQ7…M2XA    Real-time US stock trades (IEX feed on the free plan), …
  ALPACA_API_SECRET_KEY    k8Fz…w1Lq    The secret that goes with ALPACA_API_KEY_ID
  ANTHROPIC_API_KEY        (not set)    Optional: lets the explainer pass unusual questions to a language model
```

Keys are always shown masked: the first and last four characters only.

### The variable names

| Variable | Used by | Required? |
|---|---|---|
| `FINNHUB_API_KEY` | `finnhub` provider | Only for Finnhub |
| `ALPACA_API_KEY_ID` | `alpaca`, `alpaca-test`, Alpaca history fallback | Only for Alpaca |
| `ALPACA_API_SECRET_KEY` | the same | Only for Alpaca |
| `ALPACA_FEED` | `alpaca`: `iex` (free) or `sip` (paid) | No, defaults to `iex` |
| `ANTHROPIC_API_KEY` | The explainer's optional language model | No |
| `OPP_DATA_PROVIDER` | Which feed the server uses | No, defaults to `simulated` |
| `OPP_PORTFOLIO_FILE` | Which portfolio the server streams | No, defaults to the sample fund |
| `OPP_BENCHMARK` | Benchmark ticker for beta and tracking error, e.g. `SPY` | No |
| `OPP_STALE_SECONDS` | Seconds without a trade before an instrument is flagged | No, 120 for live feeds |
| `OPP_POLL_SECONDS` | How often the `yahoo` provider polls | No, 5 |

### Other places a key can go

The `.env` file suits a single machine. Elsewhere, set the same names as environment variables. A real environment variable always takes priority over `.env`.

| Where you run it | How to supply the key |
|---|---|
| Your terminal, for one session | `export FINNHUB_API_KEY=...` (Windows PowerShell: `$env:FINNHUB_API_KEY="..."`) |
| Docker Compose | `docker-compose.yml` already reads `.env` through `env_file` |
| Plain Docker | `docker run --env-file .env -p 8000:8000 open-portfolio` |
| GitHub Actions or other CI | Store it as a repository secret and map it to the same variable name |
| A cloud server | The platform's secret manager (AWS Secrets Manager, Azure Key Vault, Google Secret Manager), injected as environment variables |

### Keeping keys safe

- `.env` is listed in `.gitignore`, so Git will not commit it. Never put a key in code, a CSV file, a rule file, an issue, a chat message or a screenshot.
- Use the least powerful key that works. For Alpaca, a paper-trading key is enough for market data.
- The platform never prints or logs a key in full. Error messages name the variable (`FINNHUB_API_KEY`), never its value.
- One caution: Finnhub requires the key inside its WebSocket address. The platform never logs that address, but a corporate proxy that records full URLs could. If that matters to you, prefer Alpaca, which sends keys inside the encrypted connection after it opens.
- If a key may have leaked, revoke it on the provider's dashboard and create a new one. Then update `.env`.

## Describing your portfolio

A live portfolio is one CSV file. The two examples show the format:

```csv
id,name,asset_class,quantity,sector,country,currency,yahoo,finnhub,alpaca,tags
AAPL,Apple,Equity,400,Technology,United States,USD,AAPL,AAPL,AAPL,
XOM,Exxon Mobil,Equity,400,Energy,United States,USD,XOM,XOM,XOM,fossil-fuels
IEF,iShares 7-10 Year Treasury Bond ETF,Fixed Income,1500,Government,United States,USD,IEF,IEF,IEF,
CASH,,,25000,,,,,,,
```

```csv
id,name,asset_class,quantity,sector,country,currency,yahoo,coinbase,finnhub,alpaca
BTC,Bitcoin,Crypto,1.5,Crypto,Global,USD,BTC-USD,BTC-USD,BINANCE:BTCUSDT,BTC/USD
```

- **Required columns:** `id`, `asset_class` and `quantity`. Asset classes are Equity, Fixed Income, Commodity, Real Estate, Crypto, Cash, Private Equity and Private Credit.
- **Symbol columns:** `yahoo` (history), `finnhub`, `alpaca` and `coinbase`. Stock providers fall back to the `ticker` column (or the `id`) when their own column is empty. Coinbase needs its own column, because its product ids (BTC-USD) are not tickers.
- **Optional columns:** `cost_price` (for unrealised gains), `avg_daily_volume` (otherwise worked out from history), and `tags`, separated by semicolons, for compliance exclusions.
- A row with id `CASH` sets the cash balance.

Bond ETFs (IEF, LQD) stand in for individual bonds, because live bond prices are rarely available on free feeds.

## Five tests, in order

Run them from the project folder.

**Test 1: plumbing, with no key** (any time of day).

```bash
opp live --portfolio examples/live_crypto.csv --provider coinbase --seconds 30
```

You should see live Coinbase prices moving the portfolio's value every second:

```
Live crypto is worth $242,928 across 4 holdings, split Crypto 96%, Cash 4%.
 time           NAV      day P/L     VaR 95%    ticks   ticks/s  delay ms  feed
   1s      $243,022          +95     $10,947        6       5.8         0  connected
   2s      $243,012          +85     $10,947       13       7.0         0  connected
```

**Test 2: your keys.**

```bash
opp keys --test
```

This connects briefly to Coinbase and to each provider you have keys for. With a valid key, the line reads `PASS` and gives the time until the first price arrived:

```
  coinbase     PASS  first price after 0.5 s
```

A rejected key gives a clear failure rather than endless retries:

```
  finnhub      FAIL  finnhub refused the connection (HTTP 401). Check the API key in your .env file; …
  alpaca-test  FAIL  Alpaca: the API key or secret was rejected (code 402).
```

**Test 3: live stocks**, during US market hours (9:30–16:00 New York time, Monday to Friday):

```bash
opp live --portfolio examples/live_stocks.csv --provider finnhub --benchmark SPY --seconds 60
opp live --portfolio examples/live_stocks.csv --provider alpaca  --benchmark SPY --seconds 60
```

Outside market hours, use `--provider alpaca-test`. It proves the keys and the pipeline work, but only one holding moves, driven by the FAKEPACA test symbol.

**Test 4: the dashboard on live data.**

```bash
opp serve --portfolio examples/live_stocks.csv --provider finnhub --benchmark SPY
```

Open http://localhost:8000/#live. The source line under the controls shows the provider, its connection status, the reconnect count and the feed delay. Every other module (risk, scenarios, compliance and so on) now works on your real portfolio. The *Inject an event* buttons are hidden, because you cannot crash a real market.

**Test 5: the automated online tests.**

```bash
OPP_RUN_LIVE_TESTS=1 pytest -m live -s
```

These connect to Coinbase, build a real portfolio from Yahoo history, and run the full engine on live prices for ten seconds. They also cover Finnhub and the Alpaca test stream when those keys are set. Without `OPP_RUN_LIVE_TESTS=1` they are skipped, so the normal test suite never needs the internet or a key.

## What "day P/L" means on live data

The portfolio opens at the **last completed daily close** from history. Live prices are compared with that close, so *today's profit or loss* is measured as markets measure it: against the previous close. Live VaR uses the live weights with the covariance estimated from roughly two years of daily history.

## Troubleshooting

| What you see | Likely cause | What to do |
|---|---|---|
| `needs FINNHUB_API_KEY` | The key is not set | Add it to `.env` and run `opp keys` |
| `HTTP 401` from Finnhub | The key is wrong or revoked | Copy it again from the Finnhub dashboard |
| Alpaca `code 402` | Wrong key or secret, or the two belong to different accounts | Regenerate both together |
| Alpaca `code 406` | Another program is already connected with these keys (Alpaca allows one stream at a time) | Close the other connection |
| Alpaca `code 409` | Your plan does not include the chosen feed | Set `ALPACA_FEED=iex` |
| Connected, but no ticks | The market is closed, or a symbol is spelled wrongly | Try crypto or `alpaca-test`; check the symbol columns |
| `No holdings have a symbol for the 'coinbase' feed` | The portfolio has no `coinbase` column | Add Coinbase product ids such as BTC-USD |
| `HTTP Error 429` from Yahoo | Yahoo is throttling requests | Wait a minute. History is cached in `.opp_cache/` for 12 hours, so retries are cheap, and crypto falls back to Coinbase automatically |
| Many stale alerts | Thinly traded instruments | Raise `OPP_STALE_SECONDS` |
| `reconnecting in 4s` | The network or the provider dropped the connection | No action needed; the feed reconnects with back-off (1, 2, 4 … 30 seconds) |

## Licensing

Free data is for personal and evaluation use. Showing live exchange data to other people, or using it commercially, usually needs a licence from the provider and sometimes from the exchange. Check your provider's terms before you go beyond testing. The platform ships connectors, not data. You bring the licence.
