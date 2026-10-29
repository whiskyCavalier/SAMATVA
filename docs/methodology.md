# Methodology

This document sets out how each number is produced, what it assumes and where it falls short. Every method here is standard industry practice, and the code that implements it is short enough to read alongside this page. Tests in `tests/test_analytics.py` check each method against hand-worked or textbook values.

## Conventions

- **Returns** are simple daily returns, *r = Pₜ ÷ Pₜ₋₁ − 1*, measured in the portfolio's base currency (local price × exchange rate).
- **Annualising** multiplies a daily standard deviation by √252 and compounds a daily return over 252 trading days.
- **Losses are positive** in risk measures. A 95% VaR of 0.0105 is a 1.05% loss.
- **Constant weights.** Risk figures apply today's weights to the full price history. This describes the risk of the portfolio as it stands, which is what a risk manager needs, rather than the risk of past portfolios.
- **FX** is held as base currency per unit of foreign currency, so a conversion is a single multiplication.

## Valuation (module 1)

Market value = quantity × latest local price × FX rate. Bonds are quoted per 100 of face value, so the quantity counts units of 100 face. Weights are market value ÷ NAV, where NAV includes cash. Unrealised gain = market value − quantity × cost price × FX rate. This is valued at today's FX rate, so it combines price and currency effects.

**Look-through.** When a fund publishes its exposures (for example country and sector weights), its weight is split across them in proportion. Without that information, the fund counts as a single holding.

## Risk (module 2)

**Volatility.** Sample standard deviation of daily returns × √252.

**EWMA volatility** (RiskMetrics). σ²ₜ = λσ²ₜ₋₁ + (1 − λ)r²ₜ with λ = 0.94, seeded from the variance of the first twenty days. It reacts to changing conditions within weeks, where equally weighted volatility takes years.

**Value at Risk.** Four methods are reported side by side because each fails differently.

| Method | How | Strength | Weakness |
|---|---|---|---|
| Historical simulation | The (1 − c) quantile of past daily returns, linearly interpolated | No distribution assumed; keeps real fat tails | Only knows what has happened |
| Parametric (normal) | z_c × σ − μ | Fast and smooth | Understates extreme losses |
| Monte Carlo, normal | 20,000 draws from a multivariate normal fitted to history | Handles any portfolio mix | Inherits the normal assumption |
| Monte Carlo, bootstrap | 20,000 resampled historical days | Keeps fat tails and real co-movement | Only knows the past sample |

Multi-day VaR uses the square-root-of-time rule (one-day VaR × √h). This assumes independent days, which understates risk when markets trend.

**Expected Shortfall** is the average loss on the days worse than the VaR threshold (or σφ(z)/(1 − c) − μ in the normal case). Basel rules favour it over VaR because it describes how bad the bad days get, not just where they begin.

**Drawdown.** Value ÷ running maximum − 1. The maximum drawdown is the deepest point. The peak, trough and recovery dates are reported, and *recovery* is left empty if the old high has not been regained.

**Beta, correlation, tracking error.** Beta = cov(p, b) ÷ var(b). Tracking error = standard deviation of (p − b) daily returns × √252.

**Risk contribution** (Euler allocation). For weights w and annualised covariance Σ, portfolio volatility σ = √(wᵀΣw). Marginal contribution = (Σw)ᵢ ÷ σ, component contribution = wᵢ × marginal, and the components sum exactly to σ. The dashboard shows each component's percentage of σ.

**Diversification ratio.** Σ|wᵢ|σᵢ ÷ σ. A value of 1 means no diversification benefit, and 1.5 means diversification removes about a third of the stand-alone risk.

**Concentration.** The Herfindahl index H = Σwᵢ² over holdings, renormalised to sum to one. 1/H is the *effective number of holdings*.

**Factor exposures.** Ordinary least squares of portfolio returns on factor returns with an intercept. The sample factors are Market, Size, Value, Momentum (all returns) and Rates (the daily change in the ten-year yield, in decimal). Because of the Rates units, its coefficient reads directly as the percentage change in value per one-point rise in yields. R² reports how much of the movement the factors explain.

**Bonds.** Fixed-coupon cash flows are discounted at the yield to maturity, with coupon frequency f:

```
P = Σₖ CFₖ ÷ (1 + y/f)^k
Macaulay duration D = Σ tₖ·PV(CFₖ) ÷ P
Modified duration = D ÷ (1 + y/f)
Convexity = Σ CFₖ·k(k+1)/(1 + y/f)^(k+2) ÷ (P·f²)
DV01 = (P(y − 1bp) − P(y + 1bp)) ÷ 2
```

The yield is solved by Brent's method when only a price is known. Portfolio duration = Σ weightᵢ × modified durationᵢ, so it measures the percentage of the *whole portfolio* lost per one-point rise in rates. Known limitations: the model takes no account of accrued interest, day-count conventions, embedded options or inflation linking.

**Liquidity.** Days to sell a position = |quantity| ÷ (20% × average daily volume). The portfolio's *days to liquidate 90%* is found by bisection: the smallest number of days d for which cash plus Σ min(1, d/daysᵢ) × valueᵢ reaches 90% of NAV. The 20% participation rate can be changed.

## Scenarios (module 3)

**Stress scenarios** apply a price move to each holding. A move set for a specific instrument takes priority, then one for its country, then its sector, then its asset class. Bonds are always **repriced exactly** for the scenario's yield shift (rates, plus credit spread for non-government issuers) rather than estimated from duration, so large moves are handled correctly. Currency moves are then compounded: total = (1 + price move)(1 + FX move) − 1, applied only to holdings priced in a foreign currency.

The named scenarios are rounded approximations of past episodes, chosen to be illustrative. They are not calibrated replays.

**Factor scenarios** regress each holding on the factors and project its move as Σ βᵢ,f × shock_f. The results are only as reliable as each holding's R², which is reported alongside.

**Monte Carlo projection.** One-year paths are built by resampling blocks of five consecutive days from the portfolio's return history. Blocks preserve short-term momentum, volatility clustering and the relationships between assets, which independent daily draws would destroy. The default is 10,000 paths with a fixed seed, so results can be reproduced.

## Performance (module 4)

**Time-weighted return.** With external flows Fₜ arriving at the start of day t: rₜ = (Vₜ − Vₜ₋₁ − Fₜ) ÷ (Vₜ₋₁ + Fₜ). Daily returns are chained geometrically. This removes the effect of the investor's timing and is the GIPS standard for judging a manager.

**Money-weighted return.** The annual internal rate of return on the opening value, the flows and the closing value (XIRR, actual/365). It reflects the investor's own experience, including timing.

**Ratios.** Sharpe = mean excess daily return ÷ its standard deviation × √252. Sortino replaces the denominator with downside deviation √mean(min(excess, 0)²). Information ratio = mean active return ÷ tracking error, annualised. Calmar = annualised return ÷ maximum drawdown.

**Brinson-Fachler attribution** (single period, by asset class). For each segment, with portfolio and benchmark weights wp and wb, returns rp and rb, and total benchmark return Rb:

```
allocation  = (wp − wb) × (rb − Rb)
selection   = wb × (rp − rb)
interaction = (wp − wb) × (rp − rb)
```

The three effects sum exactly to Rp − Rb. A segment the portfolio does not hold is given the benchmark return, so it produces no selection effect. Portfolio weights are taken at the start of the period. Multi-period linking (for example the Carino method) is not yet implemented.

**Factor attribution** multiplies the fitted exposures by the realised factor returns over the period. The remainder is specific to the holdings chosen, whether through skill or luck.

## Compliance (module 5)

Each rule measures one quantity and compares it with its limit. A *max* rule warns at 90% of the limit and breaches above it. A *min* rule warns once the measure falls to within 10% of the limit and breaches below it. Rule types available: `max_position_weight`, `max_group_weight`, `min_group_weight` (by asset class, sector, country or currency, with look-through), `exclusion` (by instrument tag), `min_cash`, `max_var`, `max_duration`, `max_illiquid` and `min_holdings`.

**Pre-trade checking** applies the proposed trades to a copy of the portfolio, with cash moving by the trade value, and reports only breaches that the trades would *newly* cause. Existing breaches do not block an unrelated trade.

## Trading (module 6)

**Rebalancing.** The target quantity is target weight × NAV ÷ (price × FX), rounded to whole shares or to lots of 100 for bonds. Trades below a minimum value (default $1,000) are skipped. Turnover is half of Σ|trade value| ÷ NAV.

**Cost estimate.** Commission (5 bp) + half-spread (5 bp) + market impact. Impact follows the square-root law: k × σ_daily × √(quantity ÷ ADV), with k = 0.1. Impact therefore grows faster than trade size, which is why a large order in a thinly traded bond costs proportionally more.

**Optimisers** use sequential quadratic programming (SLSQP) with fully invested, long-only weights and per-holding bounds. Covariance is the annualised sample covariance. Expected returns are historical means **shrunk 50% towards their cross-sectional average**, because raw sample means are notoriously noisy and optimisers magnify that noise. The objectives:

| Method | Objective |
|---|---|
| Minimum variance | minimise wᵀΣw |
| Maximum Sharpe | maximise (wᵀμ − r_f) ÷ √(wᵀΣw) |
| Risk parity | minimise the dispersion of risk contributions wᵢ(Σw)ᵢ/σ |
| Mean-variance | maximise wᵀμ − ½λwᵀΣw, with λ = 4 |

## Private assets (module 7)

**Current value** is the latest reported NAV, plus calls and minus distributions since that statement (the standard roll-forward). **Paid-in** is the sum of calls, and **DPI**, **RVPI** and **TVPI** = DPI + RVPI follow their usual definitions. **IRR** is the XIRR of calls (negative), distributions (positive) and today's value.

**Kaplan–Schoar PME.** Each cash flow is carried forward to today at the benchmark's growth: FV = amount × Index_today ÷ Index_flow date. PME = (FV of distributions + current value) ÷ FV of calls. A value above 1 means the fund beat the public market.

**Forecasting** uses the Takahashi–Alexander (Yale) model. Calls each year are a rate (25%, 33%, then 50%) × the remaining unfunded commitment. Distributions are (age ÷ life)^bow × NAV after growth, with bow = 2.5 and a ten-year life. NAV grows at 10% a year. These are standard starting assumptions and should be tuned to the strategy.

## Real-time processing

See [realtime.md](realtime.md). In brief: the live NAV is maintained incrementally, tick volatility uses EWMA with λ = 0.97, jumps are flagged above six tick standard deviations, and live VaR is parametric, using live weights and the historical daily covariance.

## Known limitations

- Risk is measured from history. A risk that has not appeared in the data will not show up in VaR, which is why the scenario lab exists.
- The sample data is simulated. Its figures show the methods at work, not the behaviour of any real market.
- There is no treatment of derivatives (options, futures, swaps), short positions or leverage beyond what simple negative quantities imply.
- Attribution is single-period and covers asset classes only.
- Bond analytics cover plain fixed-coupon bonds.
