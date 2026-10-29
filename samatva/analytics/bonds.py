"""Bond maths: price, yield, duration, convexity and DV01.

Prices are quoted per 100 of face value, as is market convention. The
functions use standard discounting of fixed coupons; no external library is
required, which keeps the results easy to audit.
"""

from __future__ import annotations

import math

from scipy.optimize import brentq


def _cashflows(coupon: float, maturity_years: float, freq: int = 2, face: float = 100.0):
    n = max(1, int(round(maturity_years * freq)))
    c = face * coupon / freq
    for k in range(1, n + 1):
        t = k / freq
        yield t, c + (face if k == n else 0.0)


def bond_price(ytm: float, coupon: float, maturity_years: float, freq: int = 2, face: float = 100.0) -> float:
    """Present value of the bond's cash flows at a given yield to maturity."""
    return sum(cf / (1 + ytm / freq) ** (t * freq) for t, cf in _cashflows(coupon, maturity_years, freq, face))


def bond_yield(price: float, coupon: float, maturity_years: float, freq: int = 2, face: float = 100.0) -> float:
    """Yield to maturity that reproduces the given price (solved numerically)."""
    f = lambda y: bond_price(y, coupon, maturity_years, freq, face) - price  # noqa: E731
    return brentq(f, -0.05, 1.0, xtol=1e-12)


def macaulay_duration(ytm: float, coupon: float, maturity_years: float, freq: int = 2) -> float:
    p = bond_price(ytm, coupon, maturity_years, freq)
    weighted = sum(t * cf / (1 + ytm / freq) ** (t * freq) for t, cf in _cashflows(coupon, maturity_years, freq))
    return weighted / p


def modified_duration(ytm: float, coupon: float, maturity_years: float, freq: int = 2) -> float:
    """Approximate % price fall for a 1 percentage-point rise in yield."""
    return macaulay_duration(ytm, coupon, maturity_years, freq) / (1 + ytm / freq)


def convexity(ytm: float, coupon: float, maturity_years: float, freq: int = 2) -> float:
    p = bond_price(ytm, coupon, maturity_years, freq)
    total = 0.0
    for t, cf in _cashflows(coupon, maturity_years, freq):
        n = t * freq
        total += cf * n * (n + 1) / (1 + ytm / freq) ** (n + 2)
    return total / (p * freq ** 2)


def dv01(ytm: float, coupon: float, maturity_years: float, freq: int = 2, face: float = 100.0) -> float:
    """Price change per 100 face for a one basis-point (0.01%) rise in yield."""
    up = bond_price(ytm + 0.0001, coupon, maturity_years, freq, face)
    down = bond_price(ytm - 0.0001, coupon, maturity_years, freq, face)
    return (down - up) / 2


def price_change_for_yield_shift(ytm: float, coupon: float, maturity_years: float, shift: float, freq: int = 2) -> float:
    """Exact repricing for a parallel yield shift, returned as a fractional change."""
    p0 = bond_price(ytm, coupon, maturity_years, freq)
    p1 = bond_price(ytm + shift, coupon, maturity_years, freq)
    return p1 / p0 - 1 if math.isfinite(p1) else float("nan")
