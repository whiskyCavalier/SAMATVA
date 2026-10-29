"""Plain-English definitions for every number the platform shows.

Principle: no number appears on screen without its meaning beside it and its
formula one click away. This module is the single source of those words, so
the dashboard, the reports, the API and the explainer all say the same thing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class Definition:
    label: str
    meaning: str
    formula: str
    fmt: str = "pct"  # pct | money | ratio | days | number


GLOSSARY = {
    "nav": Definition("Net asset value", "What the whole portfolio is worth today, including cash.",
                      "Σ quantity × price × FX rate + cash", "money"),
    "volatility": Definition("Volatility (annual)",
                             "How much the portfolio typically swings in a year. Higher means a bumpier ride.",
                             "standard deviation of daily returns × √252"),
    "ewma_volatility": Definition("Recent volatility (EWMA)",
                                  "Volatility with recent days weighted more heavily, so it reacts faster to change.",
                                  "σ²ₜ = λσ²ₜ₋₁ + (1−λ)r²ₜ, λ = 0.94, then × √252"),
    "var_95": Definition("Value at Risk (95%, 1 day)",
                         "On 19 days out of 20, the portfolio should not lose more than this.",
                         "5th percentile of daily returns (historical simulation)"),
    "var_99": Definition("Value at Risk (99%, 1 day)",
                         "On 99 days out of 100, the portfolio should not lose more than this.",
                         "1st percentile of daily returns (historical simulation)"),
    "es_95": Definition("Expected Shortfall (95%, 1 day)",
                        "On the worst 1-in-20 days, this is the average loss. It describes how bad bad days get.",
                        "mean of returns below the 95% VaR threshold"),
    "max_drawdown": Definition("Maximum drawdown",
                               "The largest fall from a high point before the portfolio recovered.",
                               "min over time of (value ÷ running peak − 1)"),
    "beta": Definition("Beta to benchmark",
                       "If the benchmark moves 1%, the portfolio has tended to move this much.",
                       "covariance(portfolio, benchmark) ÷ variance(benchmark)", "ratio"),
    "tracking_error": Definition("Tracking error",
                                 "How far, in a typical year, the portfolio's return drifts from the benchmark's.",
                                 "standard deviation of (portfolio − benchmark) daily returns × √252"),
    "sharpe": Definition("Sharpe ratio", "Return earned for each unit of risk taken. Above 1 is generally good.",
                         "(annual return − risk-free rate) ÷ annual volatility", "ratio"),
    "sortino": Definition("Sortino ratio", "Like Sharpe, but only counts the bad swings as risk.",
                          "(return − risk-free) ÷ downside deviation", "ratio"),
    "information_ratio": Definition("Information ratio",
                                    "How consistently the portfolio beats its benchmark. Above 0.5 is respectable.",
                                    "mean active return ÷ tracking error (annualised)", "ratio"),
    "diversification_ratio": Definition("Diversification ratio",
                                        "How much holdings offset one another. 1.0 means no benefit; higher is better.",
                                        "Σ |wᵢ| σᵢ ÷ σ portfolio", "ratio"),
    "effective_holdings": Definition("Effective number of holdings",
                                     "How many equally sized holdings would give the same concentration.",
                                     "1 ÷ Σ wᵢ² (inverse Herfindahl)", "number"),
    "duration": Definition("Portfolio duration",
                           "If interest rates rise by 1 percentage point, the bond holdings lose roughly this "
                           "share of the whole portfolio.",
                           "Σ weightᵢ × modified durationᵢ", "number"),
    "dv01": Definition("DV01", "Money lost if all interest rates rise by 0.01 percentage points.",
                       "Σ position face ÷ 100 × price change per 1bp", "money"),
    "liquidity_days": Definition("Days to liquidate (90%)",
                                 "Trading days needed to sell 90% of the portfolio without moving prices, "
                                 "trading no more than 20% of daily volume.",
                                 "quantity ÷ (20% × average daily volume), value-weighted", "days"),
    "twr": Definition("Time-weighted return",
                      "The return the investment decisions earned, ignoring when money came in or out.",
                      "Π(1 + daily return) − 1", "pct"),
    "mwr": Definition("Money-weighted return",
                      "The investor's own annual return, including the timing of their deposits.",
                      "internal rate of return of all cash flows", "pct"),
    "r_squared": Definition("R-squared", "Share of the portfolio's movement explained by the factors.",
                            "1 − residual variance ÷ total variance", "pct"),
    "irr": Definition("IRR", "The annual return earned on the cash actually invested in a private fund.",
                      "rate that sets the net present value of all cash flows to zero"),
    "tvpi": Definition("TVPI", "Total value for every dollar paid in, counting both cash returned and what remains.",
                       "(distributions + remaining value) ÷ paid-in capital", "ratio"),
    "dpi": Definition("DPI", "Cash actually returned for every dollar paid in.",
                      "distributions ÷ paid-in capital", "ratio"),
    "rvpi": Definition("RVPI", "Value still held in the fund for every dollar paid in.",
                       "remaining value ÷ paid-in capital", "ratio"),
}


@dataclass
class Metric:
    key: str
    value: Optional[float]
    label: str
    meaning: str
    formula: str
    fmt: str

    def as_dict(self) -> dict:
        return asdict(self)

    def display(self) -> str:
        return format_value(self.value, self.fmt)


def metric(key: str, value: Optional[float]) -> Metric:
    d = GLOSSARY[key]
    v = None if value is None else float(value)
    return Metric(key, v, d.label, d.meaning, d.formula, d.fmt)


def format_value(value: Optional[float], fmt: str, currency: str = "$") -> str:
    if value is None:
        return "n/a"
    if fmt == "pct":
        return f"{value:.2%}"
    if fmt == "money":
        sign = "-" if value < 0 else ""
        v = abs(value)
        if v >= 1e9:
            return f"{sign}{currency}{v / 1e9:.2f}bn"
        if v >= 1e6:
            return f"{sign}{currency}{v / 1e6:.2f}m"
        if v >= 1e3:
            return f"{sign}{currency}{v:,.0f}"
        return f"{sign}{currency}{v:.2f}"
    if fmt == "days":
        return "more than a year" if value > 252 else f"{value:.1f} days"
    if fmt == "ratio":
        return f"{value:.2f}"
    return f"{value:,.2f}"
