# Changelog

## 1.1.0 — 28 October 2026

Live market data.

- **Providers**: live feeds for Coinbase (no key), Finnhub, Alpaca (IEX, SIP, crypto, and the FAKEPACA test stream) and Yahoo (polled). They reconnect automatically with back-off and stop at once on a rejected key.
- **Keys**: a `.env` file (template in `.env.example`) or environment variables; `opp keys` shows what is set, masked, and `opp keys --test` tries each connection.
- **Real portfolios**: one CSV of instruments, quantities and provider symbols; history is downloaded from Yahoo Finance, with Coinbase and Alpaca as fallbacks, and cached for 12 hours.
- **Interfaces**: `opp live` for the terminal; `opp serve --portfolio … --provider …` for the dashboard, which shows the feed's source, status and delay.
- **Tests**: 21 offline tests using local copies of each provider's protocol, and 5 opt-in online tests.
- Crypto is now an asset class. The optimiser widens its per-holding cap, and says so, when a portfolio has too few holdings to stay fully invested under it.

## 1.0.0 — 27 October 2026

The first complete release: every planned phase in one package.

- **Data**: instrument, position and portfolio models; a security master with ticker, ISIN, CUSIP and FIGI lookup; CSV and Excel import; Yahoo and FRED connectors; data-quality checks; a six-year simulated sample universe.
- **Phase 1**: Portfolio hub (valuation, allocation, look-through) and Risk (volatility, EWMA, VaR and Expected Shortfall four ways, drawdown, beta, tracking error, risk contributions, factor exposures, bond duration and DV01, liquidity).
- **Phase 2**: Scenario lab (seven stress scenarios, a scenario builder, factor scenarios, Monte Carlo projection), Performance (TWR, MWR, ratios, period returns, Brinson-Fachler and factor attribution), Reports (PDF and Excel) and the AI explainer.
- **Phase 3**: Compliance (nine rule types, pre- and post-trade checks, breach log), Trading (four optimisers, rebalancing with cost estimates, an order blotter) and Private assets (IRR, TVPI, DPI, RVPI, PME, NAV roll-forward, Takahashi-Alexander forecasts, a combined view).
- **Real time**: a simulated and a polling feed, an O(1) stream processor with debounced alerts, an asynchronous engine with back-pressure, a WebSocket API and a terminal showcase.
- **Interfaces**: a REST API with interactive documentation, a single-file dashboard that also exports as a self-contained demo, and the `opp` command line.
- **Quality**: 114 tests, including a measured real-time showcase.
