# User guide

This guide walks through the platform screen by screen. Each module opens with the question it answers. Each screen gives the answer in a sentence first and the supporting detail underneath, so you can stop reading as soon as you have what you need.

The figures quoted here come from the sample portfolio, the *Global Balanced Fund*, a $10 million fund of eleven holdings, as at 23 October 2026.

---

## A note on reading the numbers

Every figure on every screen carries three layers:

1. **The value**, such as *1.05%*.
2. **What it means**, in one line: *on 19 days out of 20, the portfolio should not lose more than this.*
3. **How it is calculated**, behind the small *ƒ How it is calculated* link: *5th percentile of daily returns (historical simulation).*

These words live in a single file (`samatva/glossary.py`), so the dashboard, the reports, the API and the explainer always describe a number the same way.

Two conventions are worth knowing:

- **Losses are shown as positive amounts** in risk measures. A Value at Risk of $104,946 means a possible *loss* of that size.
- **Risk figures describe what you own today.** They are worked out as though today's holdings had been held throughout the history, which is the standard way to measure the risk of a current portfolio.

---

## 1. Portfolio hub: *what do I own, and what is it worth now?*

The hub opens with a single sentence that sums up the portfolio:

> Global Balanced Fund is worth $10.00m across 11 holdings, split Fixed Income 45%, Equity 44%, Real Estate 4%, Commodity 4%.

Below it are four headline figures (net asset value, the latest day's change, unrealised gain and cash) and a short *Today in brief* commentary. It reads as a portfolio manager would brief a colleague: what moved, why, whether risk is behaving normally, and whether any rule needs attention. Any item that needs a second look appears under the commentary as a flag, with a red stripe for a rule breach.

**Where the money is** groups the portfolio by asset class, sector, country or currency. Funds are *looked through*: the Emerging Markets ETF is split into China, India, Taiwan and so on, because those are the exposures you actually carry.

**Holdings** lists each position with its quantity, local price, value in the base currency, weight and unrealised gain or loss.

---

## 2. Risk: *how much could I lose, and where is the risk coming from?*

The headline sentence turns Value at Risk into money:

> On a typical bad day (1 in 20) the portfolio could lose about $104,946; on the very worst days the average loss is closer to $134,427.

**The metric cards** cover the standard measures: volatility, recent (EWMA) volatility, VaR at 95% and 99%, Expected Shortfall, maximum drawdown, beta, tracking error, diversification and concentration. If recent volatility runs well above long-run volatility, markets have become rougher lately.

**Where the risk comes from** is often the most useful chart on the screen. For each holding it sets the share of the *money* (grey) against the share of the *risk* (green). Northwind Technologies is 10% of the money but 23% of the risk, because it is both volatile and moves with the rest of the portfolio. The US Treasury is 20% of the money but only 7% of the risk. If you want to reduce risk efficiently, start with holdings whose green bar is much longer than their grey one.

**Value at Risk, four ways** computes VaR by historical simulation, by a normal-distribution formula and by two Monte Carlo methods. When the four agree, as they do here (1.05% to 1.08%), bad days behave much as a bell curve predicts. When the historical and bootstrap figures sit clearly above the others, the portfolio's tails are fatter than normal and the simple formula understates the danger.

**Exposure to market drivers** shows how strongly the portfolio responds to broad market moves, to company size, value and momentum styles, and to interest rates. Here the drivers explain 88% of the portfolio's daily movement, and a one-point rise in rates would cost about 3.4%.

**Interest-rate risk** covers each bond's yield, duration and DV01 (the money lost if rates rise by 0.01 percentage points), and states the total plainly: *if all interest rates rose by 1 percentage point, the bonds would lose about $300,841.*

**How quickly could we sell?** estimates what share of the portfolio could be turned into cash within one, five and twenty days without trading more than 20% of any instrument's normal daily volume. The thinly traded Atlas Corp bond would take about nine days on its own.

---

## 3. Scenario lab: *what happens if the market moves?*

**Stress scenarios** applies seven named shocks: a 2008-style banking crisis, a 2020-style pandemic crash, a 2022-style inflation shock, a technology sell-off, a one-point rate rise, a stronger dollar and an emerging-markets crisis. Each is reported as a percentage, in money, and in a sentence:

> Under 'Inflation and rate shock (2022-style)', the portfolio would lose about $1.88m (−18.8%).

Select a scenario to see each holding's contribution. The 2022 case is instructive. It hurts this portfolio most because bonds and shares fall together, so the bonds give no cushion. In the 2008 case, government bonds rally and soften the blow.

The scenario moves are rounded approximations of past episodes, chosen for illustration. They are not forecasts.

**Build your own scenario** gives you six sliders: equities, interest rates, credit spreads, property, commodities and the dollar. Results update as you move them. Bonds are repriced exactly from their yields rather than estimated from duration, so large rate moves are handled correctly.

**A year from now** shows the spread of 10,000 simulated one-year outcomes, built by replaying five-day blocks of the holdings' own history. It answers questions such as *how likely is a loss?* (25% here) and *how bad could a poor year be?* (one year in twenty ends below $9.08m).

---

## 4. Performance: *am I beating my benchmark, and why?*

The headline compares the annual return with the benchmark's and gives the Sharpe ratio. The **growth of 100** chart sets the portfolio against a 60% equity, 40% bond benchmark.

The sample's track record is a *back-cast*: today's holdings valued through history. To measure a real track record, pass in your actual daily values and any deposits or withdrawals, and the time-weighted and money-weighted returns will reflect them.

**Returns by period** covers month-to-date, quarter-to-date, year-to-date, one year, three years (annualised) and since inception, each against the benchmark.

**What explains the difference** uses Brinson-Fachler attribution to split the gap into three parts:

- **Allocation**: were we in the right asset classes?
- **Selection**: did we pick better holdings within each class?
- **Interaction**: the combination of the two.

The three add up exactly to the difference from the benchmark.

**Recent contributors** lists the holdings that added or removed the most value over the last month. **Return from market drivers** separates the part of the return that came from broad factors from the part that came from the specific holdings chosen.

---

## 5. Compliance: *am I breaking any rules?*

Each rule appears with a status:

| Status | Meaning |
|---|---|
| Pass | Comfortably inside the limit |
| Near limit | Inside the limit, but within 10% of it |
| Breach | Outside the limit |

The sample rule book has ten rules, including a single-holding cap, equity and bond ranges, a tobacco exclusion, an energy cap, minimum cash, a VaR limit, a duration limit, a liquidity limit and a minimum number of holdings. It currently shows one breach: 13% of the portfolio would take more than five days to sell, against a 10% limit.

Rules are plain data (see `examples/rules.json`), so a compliance officer can read and change them without touching code. The same rules run in two places: on today's holdings, and on every proposed trade *before* it is placed.

---

## 6. Trading: *what should I trade to reach my target?*

**Suggest a target mix** offers four well-established ways to build a portfolio:

| Choice | What it does | Suits |
|---|---|---|
| Lowest risk | The mix with the least volatility | Capital preservation |
| Equal risk | Every holding contributes the same share of risk (risk parity) | Balanced, diversified portfolios |
| Best return per risk | The highest expected return per unit of risk (maximum Sharpe) | Efficiency-minded investors |
| Return-seeking | Expected return minus a penalty for risk | Growth with a risk budget |

Expected returns are estimates and the screen says so. They are deliberately pulled halfway towards the average, because raw historical returns tempt an optimiser to bet heavily on whatever happened to do well.

**Trades to get there** converts the target into exact quantities. Bonds are rounded to lots of 100 and trades under $1,000 are skipped. Each trade comes with an estimated cost covering commission, half the bid-ask spread and market impact. The whole plan then passes through the pre-trade compliance check. In the sample, *Best return per risk* is blocked because it would cut bonds below the 30% minimum. That is the check doing its job.

**Order ticket** places a single order and checks it first. Try buying Ember Tobacco: the exclusion rule rejects it, and the blotter records the rejection along with its reason.

---

## 7. Private assets: *how do my private investments fit in?*

Private funds work differently from listed shares. You commit a sum, the manager *calls* it over several years, and money comes back as *distributions* long before the fund closes. For each fund the screen shows:

- how much of the commitment has been called;
- the value today, rolled forward from the latest statement for any calls and distributions since;
- **TVPI** (total value per dollar paid in), **DPI** (cash returned per dollar) and **IRR**;
- the **public market equivalent**: how the fund compares with putting the same cash, on the same dates, into the benchmark (above 1.0 means the fund did better);
- a six-year forecast of calls, distributions and value.

The top of the screen combines public and private holdings into one allocation and sets outstanding commitments against cash. In the sample, $320,000 could be called at short notice against $300,000 of cash. That is worth knowing before it happens.

---

## 8. Live stream: *what is happening right now, tick by tick?*

This is the real-time showcase. Prices tick continuously, and the portfolio's value, today's profit or loss, live VaR and a gauge of *VaR budget used today* update four times a second.

Use the event buttons to see the platform react:

- **Equities −3%** or **Everything −5%** drops prices in a single tick. Within that tick the processor flags each abnormal jump. Once the day's loss exceeds the morning's 95% VaR, a critical alert announces *a 1-in-20 day is under way*.
- **Northwind +8%** shows a single-stock jump being singled out.

The **Engine** panel shows ticks processed, throughput and latency per tick in microseconds, together with a speed test of the processor running flat out. [Real-time processing](realtime.md) explains how it works.

---

## 9. Achchu (the axle): *how close is the investor to breaking, and what can they safely do now?*

Portfolios do not abandon plans; people do. This screen measures the strain the falls of the past have put on the investor, and checks trades before they are placed. [Achchu](achchu.md) explains the method in full.

- **The four figures at the top.** *Strength left* is what remains of the investor's axle: 100% is fresh, 0% is breaking point. *Capacity* is the largest single fall they can bear, set by the weaker of their means and their steadiness of mind. *Damage now* splits into past falls (healed over time) and the fall still in progress. *Chance of breaking within a year* comes from 600 simulated years.
- **Strain over time.** Damage jumps when a fall completes and fades in calm periods. The line at 1.0 is breaking point. In the sample, three falls of 11–13% between 2022 and 2026 did most of the damage, and the current 11.6% fall is still open.
- **The falls that strained most** and **which feathers weigh most** show where the strain came from. A holding whose load is far above its weight is a heavy feather: Northwind is 10% of the money but a quarter of the load.
- **The axle.** Change the investor's stated tolerance, equanimity, reserves and monthly spending, and every figure is recalculated.
- **Four strengths.** Choose an instrument and a quantity (negative to sell) and press *Weigh it*. The verdict is one of *Allow*, *Wait*, *Hold and reflect* or *Block*, with the three tests, the four strengths in money, and a safe size where a smaller trade would pass.
- **Home ground** shows, for each holding, the market states in which its record is good, and whether today's market is one of them.

The same check runs inside the Trading screen's order ticket. An order that Achchu *blocks* is rejected; an order it advises to *hold* or *wait* is recorded with that advice attached.

---

## Ask: *can you explain this to me?*

Type a question in plain words or choose a suggestion. The explainer recognises questions about value, losses, risk sources, performance, today's move, liquidity, interest rates, concentration, compliance, private funds, definitions and the investor's strain (*How close am I to breaking?*). It also turns *what if* questions with numbers into scenarios:

> **What if stocks drop 15% and rates rise 50 bps?**
> Under 'Equities −15%, rates +0.50 pp', the portfolio would lose about $807,377 (−8.1%). The biggest hit comes from Northwind Technologies (−$150,019).

Every answer lists the figures it used, so it can be checked. With an API key configured, questions the built-in engine does not recognise go to a language model. The model receives a snapshot of the portfolio's facts and is told to answer only from them and never to give investment advice.

---

## Reports

From the API or `samatva report`, the platform writes:

- a **PDF** of four or five pages: summary and commentary, allocation and scenario charts, key risk measures with their meanings, performance, stress tests, compliance, private assets and holdings;
- an **Excel workbook** with one sheet per module, for anyone who wants to work with the numbers.
