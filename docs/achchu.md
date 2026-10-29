# Achchu: the axle algorithm

*Achchu* (அச்சு) is Tamil for the axle of a cart. This module measures how close an investor is to breaking, which means abandoning their plan and selling at the worst moment. It then decides whether a proposed trade is safe to make now.

Most risk systems ask what the *portfolio* might lose. Achchu asks a different question: how much more can the *person* take? People seldom give up after one fall. They give up under the strain of many falls, none of which looked serious on its own. The Thirukkural said this two thousand years ago:

> பீலிபெய் சாகாடும் அச்சிறும் அப்பண்டம்
> சால மிகுத்துப் பெயின்
>
> Even a cart loaded only with peacock feathers will break its axle, if it is loaded too heavily.
> — Thirukkural 475

Engineers meet the same problem in bridges, aircraft wings and wind turbines. Metal rarely fails under one great load; it fails from *fatigue*, the accumulated damage of many smaller ones. They have counted fatigue reliably for decades. Achchu applies their method to the investor.

| | |
|---|---|
| **Command** | `samatva achchu --trade NWT:500` |
| **Dashboard** | Module 9, *Achchu (the axle)* |
| **API** | `GET /api/achchu`, `POST /api/achchu/gate`, `GET`/`PUT /api/achchu/profile` |
| **Code** | `samatva/analytics/fatigue.py` (the mathematics), `samatva/modules/achchu.py` (the module) |
| **Tests** | `tests/test_achchu.py` (24 tests) |

---

## The five steps at a glance

| Step | Question | Source | Method |
|---|---|---|---|
| 1. The axle | How large a single fall can this investor bear? | Kural 478, Gita 2.56 | The smaller of what their *means* and their *mind* allow |
| 2. The feathers | Which holdings put the most weight on the axle? | Kural 475 | Each holding's share of losses on falling days |
| 3. Fatigue | How much strength is left after the falls already lived through? | Kural 475, Gita 2.38, Kural 621, Gita 6.35 | Rainflow counting, Palmgren–Miner damage, an endurance threshold and healing |
| 4. Four strengths | Should this trade go ahead, and at what size? | Kural 471, 476, 467, Gita 18.63 | Weighs the deed, the investor, the opponent and the allies; never past the tip of the branch |
| 5. The crane | Is now the right moment? | Kural 481, 490, 495, Gita 2.47 | Each holding's *home ground*: the market states in which its record is good |

---

## Step 1. The axle: capacity

> ஆகாறு அளவிட்டி தாயினுங் கேடில்லை
> போகாறு அகலாக் கடை
>
> A narrow inflow does no harm, provided the outflow is not wider.
> — Thirukkural 478

> duḥkheṣv anudvigna-manāḥ sukheṣu vigata-spṛhaḥ … sthita-dhīr munir ucyate
>
> One whose mind is unshaken in sorrow and free of craving in joy is called steady in wisdom.
> — Bhagavad Gita 2.56

An axle has two limits, and the weaker one decides.

**Means.** Money the investor will need over the horizon, beyond what income and reserves cover, must come out of the portfolio. If that need is *F* and the portfolio is worth *V*, a fall larger than 1 − *F*/*V* leaves them unable to meet it.

```
F       = max(0, (monthly outflow − monthly inflow) × horizon months − reserves)
C_means = max(0, 1 − F / V)
```

**Mind.** The largest fall the investor can live through without abandoning the plan. If they have actually sat through a fall, that *observed* tolerance is used. Otherwise their *stated* tolerance is used, reduced by a safety margin (0.8 by default), because people overestimate their own calm.

```
C_mind = observed tolerance, or stated tolerance × safety margin
C      = min(C_means, C_mind)
```

For the sample fund, with the default profile (stated tolerance 20%, no spending needs), *C* = 20% × 0.8 = **16%**, set by the mind.

---

## Step 2. The feathers: load shares

A holding's weight says how much money is in it. Its *load* says how much of the strain it causes. On days the portfolio falls, each holding's share of the loss is:

```
load_i = w_i × E[r_i | r_p < 0] / E[r_p | r_p < 0]
```

Loads add up to one. A holding whose load is much larger than its weight is a heavy feather. In the sample fund, Northwind Technologies is 10% of the money but **24.5%** of the load on falling days.

---

## Step 3. Fatigue: how much strength is left

### Counting the falls: rainflow

The portfolio's value path is broken into fall-and-recovery cycles with the *rainflow* method of ASTM E1049-85 (section 5.4.4). This is the standard that aircraft and bridge engineers use, and the code is checked against an independent implementation (the `rainflow` package) on hundreds of random paths.

Three choices adapt it to people:

- **Sizes are fractional falls.** A cycle's size is the fall from its high to its low, as a fraction (0.12 is a 12% fall). It is measured on log values, so a fall from 100 to 88 counts the same as one from 1,000 to 880.
- **Strain comes from falls, not rises.** A full cycle (one fall, one recovery) counts once. A half cycle counts once if it goes down and not at all if it goes up. Gains cause no strain.
- **A fall still in progress is counted now.** The investor is living through it, so its damage is added to today's total and marked *still open*.

### Charging each fall: Palmgren–Miner damage

Each fall uses up a part of the axle's life:

```
damage of one fall = (size / C) ^ m        if size > e, otherwise 0
```

- **m**, the exponent (2 by default): larger falls strain disproportionately more. A fall twice as large does four times the damage.
- **e**, the endurance threshold. Falls smaller than this cause no strain at all, as metal has an endurance limit below which it can be loaded forever.

> sukha-duḥkhe same kṛtvā lābhālābhau jayājayau
>
> Treating alike pleasure and pain, gain and loss, victory and defeat…
> — Bhagavad Gita 2.38

The more even-minded the investor, the higher their endurance. With *equanimity* q between 0 (very reactive) and 1 (perfectly even):

```
e = e0 × (0.5 + q)          e0 = 4% by default
```

Equanimity can be measured from behaviour with `equanimity_from_trades`. It is one minus the rank correlation between how much the investor traded each day and how large the market's recent move was. Someone who trades most after big moves scores near 0.

### Healing

> இடுக்கண் வருங்கால் நகுக அதனை
> அடுத்தூர்வது அஃதொப்ப தில்
>
> When trouble comes, smile; nothing overcomes trouble so well.
> — Thirukkural 621

> abhyāsena tu kaunteya vairāgyeṇa ca gṛhyate
>
> The mind is held steady by practice and detachment.
> — Bhagavad Gita 6.35

Unlike metal, people recover. Damage fades exponentially in calm periods, and faster for an investor with practised discipline (d, the share of past decisions that followed their own rules):

```
h    = ln 2 / half-life × (1 + d)          half-life 126 trading days (about six months) by default
D(t) = D(t−1) × e^(−h) + damage of the falls completed at t
D_now = D(today) + damage of the fall still open
Life left L = 1 − D_now
```

The axle breaks when *D* reaches 1.

### A worked example

Capacity 15%, endurance 3%, exponent 2, no healing. These figures are tested exactly in `test_damage_worked_examples`.

| Path | Falls counted | Damage | Strength left |
|---|---|---|---|
| A: 100 → 88 → 100 | one 12% fall | (0.12 / 0.15)² = **0.64** | 36% |
| B: six separate falls of 7%, each recovered | six 7% falls | 6 × (0.07 / 0.15)² = **1.31** | 0%: broken |
| C: 100 → 90, not yet recovered | one open 10% fall | (0.10 / 0.15)² = **0.44** | 56% |
| D: 100 → 110 → 100 | one 9.1% fall (110 to 100) | (0.0909 / 0.15)² = **0.37** | 63% |
| E: fifty separate 2% falls | none; all below endurance | **0** | 100% |

Path B is the peacock feathers. No single fall in it came near the investor's capacity, and a conventional drawdown measure would report only 7%. Yet together the falls break the axle. Path E shows the other side: small, ordinary swings cost an even-minded investor nothing.

### The chance of breaking

Achchu simulates the next year (252 trading days) 600 times. It draws five-day blocks of the holdings' own history, which keeps their volatility and correlation. Each path begins with the portfolio's journey since its last high, so a fall now in progress continues rather than being forgotten. The damage already done is carried in. The **chance of breaking** is the share of paths on which *D* reaches 1.

For the sample fund with the default profile:

| | |
|---|---|
| Damage now | 0.72 (0.20 from past falls, after healing, plus 0.53 from the 11.6% fall still open) |
| Strength left | **28%**: near breaking |
| Chance of breaking within a year | **30%** |
| Heaviest feather | Northwind Technologies, 24.5% of the load |

---

## Step 4. The four strengths: the trade gate

> வினைவலியும் தன்வலியும் மாற்றான் வலியும்
> துணைவலியும் தூக்கிச் செயல்
>
> Act after weighing the strength of the deed, your own strength, your opponent's and your allies'.
> — Thirukkural 471

Every proposed trade is weighed four ways, in money over a year:

| Strength | Meaning in Achchu | Calculation |
|---|---|---|
| **The deed** | What the trade is expected to earn | value × expected return. Set to zero outside the holding's home ground (step 5) |
| **The opponent** | What resists it | trading cost (commission, spread, market impact) + λ × the trade's stand-alone monthly tail loss |
| **The allies** | What helps it | λ × (stand-alone tail loss − the change in the portfolio's tail loss): the diversification the trade brings |
| **The investor's own strength** | What it costs the axle | the change in expected peak damage over the year, from the same simulated paths with and without the trade |

Tail losses are monthly 95% Expected Shortfall. λ (risk aversion) is 0.5 by default. The simulations with and without the trade use the same random draws, so the difference between them is the trade's effect and not simulation noise.

The trade must pass three tests.

1. **Net strength.** Deed + allies − opponent > 0, unless the trade reduces risk.
2. **Spare strength.** The trade may use at most a quarter (κ = 0.25) of the strength left.
3. **The tip of the branch.**

   > நுனிக்கொம்பர் ஏறினார் அஃதிறந் தூக்கின்
   > உயிர்க்கிறுதி ஆகி விடும்
   >
   > Whoever climbs beyond the tip of the branch falls to their end.
   > — Thirukkural 476

   After the trade, the chance of breaking must stay under the ceiling (20% by default), unless the trade reduces risk. An investor already past the ceiling may still make trades that move the odds by less than one percentage point.

The decision follows from the tests:

| Decision | When | What it means |
|---|---|---|
| **Allow** | All three tests pass | Go ahead |
| **Wait** | A buy outside the holding's home ground that fails on net strength | The moment is wrong; wait like the crane (step 5) |
| **Hold** | Net or spare strength fails | Write down the reason and confirm after a pause |
| **Block** | The tip of the branch is crossed | Do not trade at this size |

> எண்ணித் துணிக கருமம் துணிந்தபின்
> எண்ணுவம் என்பது இழுக்கு
>
> Think, then act; to think only after acting is a fault.
> — Thirukkural 467

> vimṛśyaitad aśeṣeṇa yathecchasi tathā kuru
>
> Reflect on this fully, then do as you choose.
> — Bhagavad Gita 18.63

Achchu advises; the investor decides. A *hold* or *wait* order is still recorded, with the advice attached. Only a *block* is rejected, because crossing the tip of the branch is the one mistake that cannot be undone.

For a buy that is held or blocked, Achchu searches by bisection for the largest **safe size** that would pass all three tests.

Examples on the sample fund:

| Trade | Decision | Why |
|---|---|---|
| Sell 3,000 Emerging Markets ETF | Allow | Removes strain; the chance of breaking falls from 30% to 24% |
| Buy 300 Gold ETF | Allow | Diversifies and barely moves the odds |
| Buy 20 Northwind | Wait | Today's market (rising and turbulent) is outside Northwind's home ground |
| Buy 3,000 Gold ETF | Block, safe size about 980 | The odds would rise by two and a half points while the investor is already past the ceiling |
| Buy 5,000 Northwind | Block | The chance of breaking would double, to 61% |

---

## Step 5. The crane: home ground

> கொக்கொக்க கூம்பும் பருவத்து மற்றதன்
> குத்தொக்க சீர்த்த இடத்து
>
> When the time is not right, stay still like the crane; when it comes, strike as surely as the crane strikes.
> — Thirukkural 490

> நெடும்புனலுள் வெல்லும் முதலை அடும்புனலின்
> நீங்கின் அதனைப் பிற
>
> In deep water the crocodile overcomes all; out of the water, others overcome it.
> — Thirukkural 495

Each day is placed in one of four market states, using the benchmark (or the portfolio, if there is none):

| | Calm (21-day volatility at or below its one-year median) | Turbulent (above it) |
|---|---|---|
| **Rising** (63-day return ≥ 0) | Rising and calm | Rising and turbulent |
| **Falling** (63-day return < 0) | Falling and calm | Falling and turbulent |

A holding's **home ground** is the set of states in which its *next-day* returns have been positive with a t-statistic of at least 1, over at least 20 days. Using the next day avoids looking ahead: the state is known before the return is earned. Outside its home ground, a holding's expected gain is not counted in the trade gate.

> karmaṇy evādhikāras te mā phaleṣu kadācana
>
> Your right is to the action alone, never to its fruits.
> — Bhagavad Gita 2.47

Waiting for home ground does not promise a good result. It is a discipline about when to act, kept regardless of the outcome.

---

## Calibrating to a real investor

The default settings are sensible starting points, not facts about any particular person. With a record of when an investor actually gave up, `calibrate_profile` fits the settings to them:

```python
from samatva.modules.achchu import calibrate_profile, AxleProfile

profile = calibrate_profile(values, events, AxleProfile(stated_tolerance=0.25))
```

`values` is their portfolio's value through time and `events` the dates they abandoned their plan. For every combination of exponent (1.5–3), endurance (2–6%) and healing half-life (63, 126 or 252 days), the damage path is computed. The model then predicts giving up whenever damage rises through a threshold, and that threshold is tried at the damage seen on each real event. The setting whose predictions best match the real events (F1 score, within five days either side) wins.

On synthetic investors who give up whenever damage reaches 0.9, calibration matches every event and recovers the healing half-life exactly (`test_calibration_recovers_healing_and_matches_events`). The exponent and endurance are only partly identifiable from a handful of events. Several combinations can explain the same history equally well. Treat them as a range, and recalibrate as more history arrives.

---

## Configuration

Every setting lives in `AxleProfile`. It can be changed through `PUT /api/achchu/profile` or the form on the dashboard.

| Field | Default | Meaning |
|---|---|---|
| `reserves` | 0 | Cash outside the portfolio that can meet needs |
| `monthly_inflow`, `monthly_outflow` | 0 | Income and committed spending |
| `horizon_months` | 12 | How far ahead needs are counted |
| `stated_tolerance` | 0.20 | Largest fall the investor says they can bear |
| `observed_tolerance` | none | Largest fall they actually sat through; replaces the stated figure |
| `safety_margin` | 0.80 | Applied to a stated tolerance |
| `exponent` | 2.0 | m |
| `endurance_base` | 0.04 | e0 |
| `equanimity` | 0.5 | q, from 0 to 1 |
| `healing_half_life_days` | 126 | Days for damage to halve in calm |
| `discipline` | 0 | d, from 0 to 1 |
| `spare_share` | 0.25 | κ, the most of the remaining strength one trade may use |
| `max_break_probability` | 0.20 | The tip of the branch |
| `tip_tolerance` | 0.01 | When already past the ceiling, the most a trade may add to the odds |
| `horizon_days` | 252 | Simulation horizon |
| `risk_aversion` | 0.5 | λ |

---

## Limits and honest caveats

- **It models a person with a few numbers.** Real people are more complicated. Achchu is a structured way to talk about strain and timing, not a diagnosis.
- **The history is the portfolio's, not the investor's.** Unless calibrated, the damage path shows the strain the *current holdings* would have caused. It does not show what the investor actually lived through.
- **The simulations resample the past.** A crisis unlike anything in the history will be underestimated. Use the Scenario lab alongside it.
- **Home ground is statistical, and weak.** A t-statistic of 1 is a low bar, chosen to inform timing, not to forecast. With few years of data, many holdings have no home ground at all, and then the deed is counted as normal.
- **Originality.** Rainflow counting and Palmgren–Miner damage are long-established in engineering. Applying them to an investor's tolerance, with endurance set by equanimity, healing by discipline, and the Thirukkural's tests of strength and timing, is new as far as our searches of the literature and patent databases found. Searches cannot prove that nobody has done something similar, so the method is published openly, as prior art, rather than claimed.
- **Translations** of the verses are our own short renderings for clarity. For study, consult an authoritative edition of the Thirukkural and the Bhagavad Gita.

Achchu is analytics, not advice. It describes strain and estimates odds from history and stated assumptions.
