# Changelog

## 2.0.0 — 29 October 2026

The platform is renamed **Samatva** (Self-Aware, Multi-Asset, Transparent and Verifiable Analytics) and gains its ninth module, **Achchu**.

- **Achchu (the axle)**: measures how close an investor is to abandoning their plan, and checks trades before they are placed. Built from the Thirukkural and the Bhagavad Gita:
  - capacity as the weaker of means and mind (Kural 478, Gita 2.56);
  - load shares on falling days (Kural 475);
  - rainflow counting (ASTM E1049-85) and Palmgren–Miner damage, with an endurance threshold set by equanimity (Gita 2.38) and healing set by discipline (Kural 621, Gita 6.35);
  - the chance of breaking within a year, by block-bootstrap simulation that carries today's open fall forward;
  - a four-strengths trade gate (Kural 471) with a ceiling on the chance of breaking (Kural 476), reflection before acting (Kural 467, Gita 18.63) and a search for the largest safe size;
  - home-ground timing from four market states (Kurals 481, 490 and 495, Gita 2.47);
  - calibration of the settings to the dates an investor actually gave up.
- **Interfaces**: `GET /api/achchu`, `POST /api/achchu/gate`, `GET` and `PUT /api/achchu/profile`; a dashboard screen (module 9); `samatva achchu`; an explainer intent (*How close am I to breaking?*). Orders are checked by Achchu, and rejected when it blocks.
- **Renamed**: the package `openportfolio` is now `samatva`, the command `opp` is now `samatva`, and environment variables beginning `OPP_` now begin `SAMATVA_`. See [docs/versions.md](docs/versions.md).
- **Tests**: 24 new tests, including a check of the rainflow counter against an independent implementation and the hand-worked damage examples. 159 offline tests in all.
- **Docs**: [docs/achchu.md](docs/achchu.md) and [docs/versions.md](docs/versions.md); every document updated.


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
