# Real-time processing

The real-time module answers *what is happening right now?* It takes a stream of price ticks and, for every tick, keeps the portfolio's value, profit or loss and risk current, raising an alert the moment something unusual happens. This document explains how it works, what it guarantees, how the guarantees are tested, and how to connect a live feed.

## The design in one paragraph

A tick changes one price, so it changes one position, so the portfolio's value changes by exactly that position's difference. The processor therefore never recalculates the portfolio on a tick. It adjusts the running total by the difference, which takes the same few microseconds whether the portfolio holds ten instruments or ten thousand. Anything that genuinely needs the whole portfolio, such as live Value at Risk, top movers and statistics, runs on a timer a few times a second, which is faster than anyone can read.

## The three parts

```
 feed  ──ticks──▶  processor  ──snapshots──▶  subscribers
 (async stream)    (O(1) per tick)            (dashboards, WebSockets, your code)
```

**Feeds** (`samatva/realtime/feed.py`). Each feed is an asynchronous stream of `Tick(instrument_id, price, ts, seq)`.

- `SimulatedFeed` generates realistic prices with no network and no licence. Each step draws one move for every instrument from the Cholesky factor of the holdings' historical covariance matrix, so assets keep their real volatilities and their real relationships to one another. `day_seconds` sets how many real seconds a simulated trading day lasts. At the default of 390, one second is one market minute: lively enough to watch, but statistically faithful. Shocks (`inject_shock`) and outages (`pause`) can be triggered on demand.
- `PollingFeed` wraps any quote source that returns `{ticker: price}`, such as the included free `YahooQuoteSource` or a licensed vendor, and turns it into the same stream.

**The processor** (`processor.py`). On each tick, in this order:

1. It updates the price, the position's value and the portfolio's value, by difference only.
2. It updates the instrument's exponentially weighted tick volatility (λ = 0.97).
3. It flags a **jump** if the move exceeds six times that volatility as measured *before* this tick, after a warm-up of fifty ticks per instrument.
4. It tracks the day's high and the deepest fall from it.
5. It checks the **position limit**: the holding's weight against the 21% cap.
6. It checks the **day's loss** against the morning's one-day 95% VaR.
7. It records how long all of this took, in nanoseconds.

Steps 5 and 6 are *debounced*: an alert fires when a condition becomes true, not on every tick while it stays true. One event therefore produces one alert. A separate timed check flags **stale** instruments that have had no price for longer than a set time.

`snapshot()` assembles the full picture: value, profit or loss, live parametric VaR and Expected Shortfall (from live weights and the historical daily covariance), positions ranked by impact, recent alerts, and engine statistics (ticks, throughput, latency percentiles).

**The engine** (`engine.py`). Two small asynchronous loops share one event loop. The ingest loop pulls ticks and processes them. The publish loop takes a snapshot every 250 ms and offers it to each subscriber. Each subscriber has a queue that holds one snapshot, and a newer snapshot replaces an unread older one. A slow browser therefore always sees the latest state and can never slow down ingestion.

## What is guaranteed, and how it is tested

The tests in `tests/test_realtime_showcase.py` measure each claim and print the results.

```bash
pytest tests/test_realtime_showcase.py -s
```

| Claim | Test | Result on one core |
|---|---|---|
| The running NAV is exact | After 300,000 incremental updates, compare with a full recalculation | Drift of $0.0000003 |
| It is fast | Process 500,000 pre-generated ticks | about 275,000 ticks a second; 2.8 µs median, 7.0 µs at the 99th percentile |
| The cost per tick is constant | Time the first and last 100,000 of 400,000 ticks | Last ÷ first = 1.18× |
| A full snapshot is cheap | Time `snapshot()` | about 6 ms |
| The simulation is realistic | Aggregate 400 simulated days and compare with history | Volatility 0.94–1.06× history; mean correlation error 0.03 |
| Jumps are caught at once | Inject a 5% move into one holding | Flagged on the same tick, one alert |
| Limits and VaR breaches alert once | Inject large moves | Exactly one alert per event |
| Outages are noticed | Let one instrument go quiet | A single stale alert for that instrument |
| Bad data cannot corrupt state | Send an unknown instrument and a negative price | Both rejected, NAV unchanged |
| The pacing is accurate | Run the engine at 5,000 ticks a second | 5,071 measured |
| Back-pressure works | Leave one subscriber unread for half a second | It holds one snapshot, the newest |
| The WebSocket API works end to end | Connect, send a shock over the socket | The day's P/L falls and jump alerts arrive |

The speed figures come from an ordinary cloud machine. Expect similar or better on a modern laptop. The test thresholds are deliberately generous (more than 50,000 ticks a second) so that they pass on slow CI runners.

## Three ways to watch it

**In the terminal.**

```bash
samatva stream --tps 5000 --seconds 10 --shock -0.04 --shock-at 5
```

```
 time           NAV      day P/L     VaR 95%      ticks   ticks/s   p99 µs
   3s       $10.00m       +5,907    $111,509     15,048     4,993     28.0
   4s        $9.82m     -168,805    $108,008     20,064     4,998     28.5
        !! Today's loss of $134,141 is larger than the 1-day 95% VaR of $111,436. A 1-in-20 day is under way.
         ! Northwind Technologies jumped -4.00% in one tick, about 760 times its usual tick move.
```

**As a scripted story.** `python examples/realtime_showcase.py` streams 20,000 ticks a second for twelve seconds, crashes equities by 5% at second six, silences one price feed at second eight, then prints every alert in order and runs a one-million-tick speed test.

**In the dashboard.** Open the *Live stream* module and use the event buttons. In server mode the dashboard connects to `/ws/live`. In the self-contained demo page, a line-for-line JavaScript copy of the processor runs in the browser, so the showcase works with no server at all.

## Connecting a real feed

Ready-made live feeds for Coinbase, Finnhub, Alpaca and Yahoo are in `samatva/realtime/providers.py`. [Live market data](live-data.md) explains how to set up their keys and test them. To add another polling source, any object with a `latest(tickers) -> {ticker: price}` method will do:

```python
from samatva.data.connectors import YahooQuoteSource
from samatva.realtime import PollingFeed, RealTimeEngine

engine = RealTimeEngine(ws, PollingFeed(ws, YahooQuoteSource(), interval=5.0))
await engine.start()
queue = engine.subscribe()
snapshot = await queue.get()
```

For a push feed (a vendor WebSocket or a message bus), write a class with an `async def stream()` that yields `Tick` objects. Nothing else in the system changes.

## Scaling further

Pure Python at about 275,000 ticks a second on one core is far beyond what a single portfolio needs. Even a busy equity book rarely sees more than a few thousand relevant ticks a second. If you need more:

- **Many portfolios.** Run one processor per portfolio, one per process. The processor holds no shared state.
- **Many instruments per tick.** Batch ticks and apply them as a vector with NumPy. The maths is unchanged.
- **A message bus.** Put Kafka or Redis Streams between the feed and the processor. The `Tick` shape stays the same.

## Settings

`AlertConfig` controls the alerts:

| Setting | Default | Meaning |
|---|---|---|
| `jump_sigma` | 6.0 | A move larger than this many tick standard deviations is a jump |
| `max_position_weight` | 0.21 | The live position limit (matches compliance rule R1) |
| `loss_vs_var` | 1.0 | Alert when the day's loss exceeds this multiple of the morning's VaR |
| `stale_seconds` | 5.0 | Seconds without a price before an instrument is flagged |
| `warmup_ticks` | 50 | Ticks per instrument before jump detection starts |
| `ewma_lambda` | 0.97 | Decay for the tick-volatility estimate |
