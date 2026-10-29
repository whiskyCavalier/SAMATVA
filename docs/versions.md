# Versions and upgrading

This repository holds both versions of the platform. Git tags mark each release, so either can be checked out at any time.

| | 1.1.0 | 2.0.0 |
|---|---|---|
| **Name** | Open Portfolio Platform | Samatva |
| **Git tag** | `v1.1.0` | `v2.0.0` |
| **Python package** | `openportfolio` | `samatva` |
| **Command** | `opp` | `samatva` |
| **Environment variables** | `OPP_…` | `SAMATVA_…` |
| **History cache folder** | `.opp_cache/` | `.samatva_cache/` |
| **Modules** | 8, plus the explainer and the real-time engine | 9: adds **Achchu** |
| **Offline tests** | 135 | 159 |

## Getting either version

```bash
git clone <your repository URL> samatva
cd samatva

git checkout v2.0.0     # Samatva (the default branch, main, is the same)
git checkout v1.1.0     # Open Portfolio Platform
```

Then install as usual with `pip install -e .`. Install only one version into a given Python environment at a time. Use a separate virtual environment for each if you want both side by side.

## What changed in 2.0

**Everything in 1.1 still works.** No calculation in the eight original modules has changed. The same sample portfolio gives the same figures, and the same API paths return the same answers.

**New**

- **Achchu**, module 9: how close the investor is to breaking, and whether a trade is safe now. See [Achchu](achchu.md).
- New endpoints: `GET /api/achchu`, `POST /api/achchu/gate`, `GET` and `PUT /api/achchu/profile`.
- A new command: `samatva achchu`.
- A new dashboard screen, and a new kind of question for the explainer (*How close am I to breaking?*).
- `samatva demo` includes Achchu in its tour.

**Changed behaviour**

- `POST /api/trading/orders` now also runs the Achchu gate on every buy and returns its verdict in a new `achchu` field. A buy that Achchu **blocks** is rejected, with the reason in the order's `note`. A *hold* or *wait* verdict is recorded as advice; the order stands. Sells are not gated. To trade without the gate in 2.0, raise the ceiling, for example `PUT /api/achchu/profile` with `{"max_break_probability": 1}`.

**Renamed**

| 1.1 | 2.0 |
|---|---|
| `import openportfolio` | `import samatva` |
| `from openportfolio.modules import risk_dashboard` | `from samatva.modules import risk_dashboard` |
| `opp serve`, `opp live`, `opp keys` … | `samatva serve`, `samatva live`, `samatva keys` … |
| `OPP_DATA_PROVIDER` | `SAMATVA_DATA_PROVIDER` |
| `OPP_PORTFOLIO_FILE` | `SAMATVA_PORTFOLIO_FILE` |
| `OPP_BENCHMARK` | `SAMATVA_BENCHMARK` |
| `OPP_STALE_SECONDS` | `SAMATVA_STALE_SECONDS` |
| `OPP_POLL_SECONDS` | `SAMATVA_POLL_SECONDS` |
| `OPP_TICKS_PER_SECOND` | `SAMATVA_TICKS_PER_SECOND` |
| `OPP_LLM_MODEL` | `SAMATVA_LLM_MODEL` |
| `OPP_LIVE` | `SAMATVA_LIVE` |
| `OPP_RUN_LIVE_TESTS` | `SAMATVA_RUN_LIVE_TESTS` |
| `.opp_cache/` | `.samatva_cache/` |

API keys from providers keep their names (`FINNHUB_API_KEY`, `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `ALPACA_FEED`, `ANTHROPIC_API_KEY`), because they belong to the provider, not the platform.

## Upgrading from 1.1 to 2.0

The whole upgrade takes about five minutes.

**1. Remove the old package and install the new one.**

```bash
pip uninstall -y open-portfolio-platform
git checkout v2.0.0
pip install -e .
samatva --help
```

**2. Rename the settings in your `.env` file.** Keys stay as they are; only the platform's own settings change prefix. On macOS or Linux:

```bash
sed -i.bak 's/^OPP_/SAMATVA_/; s/^export OPP_/export SAMATVA_/' .env
```

On Windows, open `.env` in a text editor and replace `OPP_` with `SAMATVA_`. Do the same anywhere else these variables are set: shell profiles, Docker files, CI secrets or a cloud secret manager.

**3. Update scripts and code.** Replace `opp ` with `samatva ` in shell scripts and scheduled jobs, and `openportfolio` with `samatva` in Python imports. To find what needs changing:

```bash
grep -rn "openportfolio\|\bopp \|OPP_" your-scripts/
```

**4. Optionally, delete the old cache.** `rm -rf .opp_cache`. The new version downloads history again on first use and caches it in `.samatva_cache/`.

**5. Check.**

```bash
samatva keys            # keys still found
pytest                  # 159 offline tests pass
samatva achchu          # the new module
```

**6. Set the investor's profile.** Achchu starts from defaults: a stated tolerance of 20%, no spending needs, average equanimity. They are reasonable, but they are not facts about your investor. Set the real figures on the dashboard or with `PUT /api/achchu/profile` before relying on its verdicts. If you need an order flow that behaves exactly as in 1.1, see *Changed behaviour* above.

## Why the new name

*Samatva* (समत्व) is the Bhagavad Gita's word for evenness of mind: *samatvaṁ yoga ucyate*, "evenness of mind is called yoga" (2.48). It is what risk management is ultimately for. The expanded form names what the platform aims to be:

- **Self-Aware**: it measures the investor as well as the portfolio (Achchu).
- **Multi-Asset**: equities, bonds, property, commodities, crypto, cash and private markets in one view.
- **Transparent**: every number carries its meaning and formula; the code is open.
- **Verifiable**: every method is tested against hand-worked or independent figures, and anyone can rerun the tests.
- **Analytics**: it describes and estimates; it does not give advice.
