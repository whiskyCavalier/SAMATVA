"""Fatigue mathematics for the Achchu algorithm.

Pure functions, borrowed from structural engineering and applied to investors:

* :func:`rainflow` breaks a value path into fall-and-recovery cycles, using the
  three-point method of ASTM E1049-85 (section 5.4.4), the standard used for
  aircraft, bridges and turbines. Each cycle records *when* it was completed,
  so strain can heal over time.
* :func:`fatigue_path` charges each cycle against the investor's capacity
  (Palmgren-Miner cumulative damage), ignores cycles below the endurance
  threshold, and lets damage heal exponentially in calm periods.
* :func:`calibrate` fits the exponent, endurance and healing rate to the moments
  an investor actually gave up in the past.

Cycle sizes are expressed as fractional falls (0.12 means a 12% fall from the
cycle's high to its low), so they compare directly with capacity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Cycle:
    size: float  # fractional fall from the cycle's high to its low (0.12 = 12%)
    count: float  # ASTM count: 1.0 for a full cycle, 0.5 for a half cycle
    start: int  # index where the cycle began
    end: int  # index where it was completed (or the series ends, for open falls)
    open_fall: bool = False  # True for a fall that has not yet recovered
    falls: int = 1  # falls the investor lived through in this cycle: 1, or 0 for a lone rise


def _reversals(x: np.ndarray) -> List[int]:
    """Indices of the turning points of a series (its local highs and lows), plus both ends."""
    n = len(x)
    if n < 2:
        return list(range(n))
    idx = [0]
    for i in range(1, n - 1):
        d1, d2 = x[i] - x[idx[-1]], x[i + 1] - x[i]
        if d1 == 0:
            continue
        if d1 * d2 < 0:
            idx.append(i)
    if x[n - 1] != x[idx[-1]]:
        idx.append(n - 1)
    return idx


def rainflow(values: Sequence[float], log_scale: bool = True, include_open_rises: bool = False) -> List[Cycle]:
    """Rainflow cycles of a path, in the order they were completed.

    With `log_scale` (the default for portfolio values) ranges are measured on
    log values, so a fall from 100 to 88 and from 1,000 to 880 count the same.
    Strain is counted per fall. A full cycle contains one fall and one
    recovery, so it counts as one fall. A half cycle counts as one fall if it
    goes down and as none if it goes up: gains cause no strain. A rise that
    has not been given back is left out unless `include_open_rises` is set
    (useful only for checking against other rainflow implementations). A fall
    that has not yet recovered is reported and marked ``open_fall``, because
    the investor is living through it now.
    """
    v = np.asarray(values, dtype=float)
    if len(v) < 2:
        return []
    x = np.log(v) if log_scale else v
    rev = _reversals(x)
    stack: List[int] = []
    cycles: List[Cycle] = []

    def size(a: int, b: int) -> float:
        r = abs(x[b] - x[a])
        return float(1 - np.exp(-r)) if log_scale else float(r)

    for k in rev:
        stack.append(k)
        while len(stack) >= 3:
            X = abs(x[stack[-1]] - x[stack[-2]])
            Y = abs(x[stack[-2]] - x[stack[-3]])
            if X < Y:
                break
            a, b = stack[-3], stack[-2]
            if len(stack) == 3:  # Y contains the starting point: a half cycle
                cycles.append(Cycle(size(a, b), 0.5, a, k, falls=int(x[b] < x[a])))
                stack.pop(0)
            else:
                cycles.append(Cycle(size(a, b), 1.0, a, k))
                last = stack.pop()
                stack.pop()
                stack.pop()
                stack.append(last)
    # Residual half cycles: keep falls (strain still felt), drop unreturned rises
    end = len(v) - 1
    for a, b in zip(stack[:-1], stack[1:]):
        if x[b] < x[a]:
            cycles.append(Cycle(size(a, b), 0.5, a, end, open_fall=True, falls=1))
        elif include_open_rises:
            cycles.append(Cycle(size(a, b), 0.5, a, end, falls=0))
    return cycles


def cycle_damage(c: Cycle, capacity: float, endurance: float, exponent: float) -> float:
    """Palmgren-Miner damage of one cycle: falls x (size / capacity) ^ exponent, if above endurance."""
    if capacity <= 0:
        raise ValueError("Capacity must be positive.")
    if c.size <= endurance or c.falls == 0:
        return 0.0
    return c.falls * (c.size / capacity) ** exponent


@dataclass
class FatigueResult:
    damage: pd.Series  # accumulated damage D through time (after healing)
    cycles: List[Cycle]
    charged: List[Tuple[Cycle, float]]  # cycles that caused damage, with their damage
    closed_damage_now: float  # healed damage from completed cycles at the last date
    open_damage: float  # damage from the fall the investor is still in
    capacity: float
    endurance: float
    exponent: float
    healing_rate: float

    @property
    def now(self) -> float:
        return float(self.closed_damage_now + self.open_damage)

    @property
    def life_left(self) -> float:
        return max(0.0, 1.0 - self.now)


def fatigue_path(values: Sequence[float], capacity: float, endurance: float = 0.03, exponent: float = 2.0,
                 healing_rate: float = float(np.log(2) / 126), index: Optional[Iterable] = None,
                 start_damage: float = 0.0) -> FatigueResult:
    """Accumulated damage along a value path, with healing between cycles.

    D(t) = D(t-1) x exp(-healing_rate) + damage of the cycles completed at t.
    The damage of a fall still in progress is added on top, without healing,
    because its strain is current.
    """
    v = np.asarray(values, dtype=float)
    n = len(v)
    cycles = rainflow(v)
    by_end: dict = {}
    charged: List[Tuple[Cycle, float]] = []
    open_damage = 0.0
    for c in cycles:
        d = cycle_damage(c, capacity, endurance, exponent)
        if d <= 0:
            continue
        charged.append((c, d))
        if c.open_fall:
            open_damage += d
        else:
            by_end[c.end] = by_end.get(c.end, 0.0) + d
    decay = float(np.exp(-healing_rate))
    D = np.empty(n)
    level = start_damage
    for t in range(n):
        level = (level * decay if t else level) + by_end.get(t, 0.0)
        D[t] = level
    idx = index if index is not None else range(n)
    series = pd.Series(D, index=list(idx) if not isinstance(idx, pd.Index) else idx, name="damage")
    # The open fall is felt now: show it in the last value of the series
    if n:
        series.iloc[-1] = D[-1] + open_damage
    return FatigueResult(series, cycles, charged, float(D[-1]) if n else 0.0, open_damage, capacity, endurance,
                         exponent, healing_rate)


def _peak_damage(x: np.ndarray, capacity: float, endurance: float, exponent: float, healing_rate: float,
                 start_damage: float) -> float:
    """Highest damage on one log-value path. Same result as fatigue_path, computed only at turning points.

    Between completed cycles damage only heals, so its highest values occur
    at the moments cycles complete, or at the end once the open fall is added.
    """
    d = np.diff(x)
    nz = np.flatnonzero(d)
    if len(nz) == 0:
        return start_damage
    s = np.sign(d[nz])
    turns = nz[1:][s[1:] != s[:-1]]  # index i where the move from x[i] to x[i+1] changes direction
    rev = [0, *turns.tolist(), len(x) - 1]
    if x[rev[-1]] == x[rev[-2]]:
        rev.pop()
    xs = x[rev]
    events: List[Tuple[int, float]] = []

    def dmg(r: float, n: int) -> float:
        size = 1 - np.exp(-r)
        return n * (size / capacity) ** exponent if size > endurance and n else 0.0

    stack: List[int] = []
    for j in range(len(rev)):
        stack.append(j)
        while len(stack) >= 3:
            X = abs(xs[stack[-1]] - xs[stack[-2]])
            Y = abs(xs[stack[-2]] - xs[stack[-3]])
            if X < Y:
                break
            a, b = stack[-3], stack[-2]
            if len(stack) == 3:
                dd = dmg(Y, int(xs[b] < xs[a]))
                stack.pop(0)
            else:
                dd = dmg(Y, 1)
                last = stack.pop()
                del stack[-2:]
                stack.append(last)
            if dd:
                events.append((rev[j], dd))
    open_d = sum(dmg(xs[a] - xs[b], 1) for a, b in zip(stack[:-1], stack[1:]) if xs[b] < xs[a])
    level, t_prev, peak = start_damage, 0, start_damage
    for t, dd in events:
        level = level * np.exp(-healing_rate * (t - t_prev)) + dd
        t_prev = t
        peak = max(peak, level)
    end_level = level * np.exp(-healing_rate * (len(x) - 1 - t_prev)) + open_d
    return float(max(peak, end_level))


def peak_damage_paths(paths: np.ndarray, capacity: float, endurance: float, exponent: float,
                      healing_rate: float, start_damage: float = 0.0) -> np.ndarray:
    """Highest damage reached along each simulated value path (rows = paths)."""
    if capacity <= 0:
        raise ValueError("Capacity must be positive.")
    logs = np.log(np.asarray(paths, dtype=float))
    return np.array([_peak_damage(p, capacity, endurance, exponent, healing_rate, start_damage) for p in logs])


# --------------------------------------------------------------------------- calibration
def _logistic_fit(x: np.ndarray, y: np.ndarray, iters: int = 50) -> Tuple[float, float, float]:
    """Fit P(y=1) = 1 / (1 + exp(-(a + b x))) by Newton's method. Returns a, b, log-likelihood."""
    X = np.column_stack([np.ones_like(x), x])
    beta = np.zeros(2)
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(X @ beta, -30, 30)))
        W = p * (1 - p) + 1e-9
        H = X.T @ (X * W[:, None]) + 1e-6 * np.eye(2)
        step = np.linalg.solve(H, X.T @ (y - p))
        beta += step
        if np.abs(step).max() < 1e-8:
            break
    p = np.clip(1 / (1 + np.exp(-(X @ beta))), 1e-12, 1 - 1e-12)
    ll = float((y * np.log(p) + (1 - y) * np.log(1 - p)).sum())
    return float(beta[0]), float(beta[1]), ll


@dataclass
class Calibration:
    exponent: float
    endurance: float
    healing_half_life: float
    threshold: float  # the damage level at which this investor tends to give up
    match: float  # F1 score: how well crossings of the threshold line up with the real events (1.0 = perfectly)
    log_likelihood: float
    damage_at_events: List[float]
    damage_elsewhere: float
    grid: List[dict] = field(default_factory=list)


def _crossings(D: np.ndarray, theta: float) -> np.ndarray:
    """Indices where damage rises through `theta`: the moments the model says the investor would give up."""
    return np.flatnonzero((D[1:] >= theta) & (D[:-1] < theta)) + 1


def _match(pred: np.ndarray, actual: np.ndarray, window: int) -> float:
    """F1 score of predicted against actual event days, counting a prediction within `window` days as a hit."""
    if len(pred) == 0 or len(actual) == 0:
        return 0.0
    hits_a = sum(bool(np.any(np.abs(pred - a) <= window)) for a in actual)
    hits_p = sum(bool(np.any(np.abs(actual - q) <= window)) for q in pred)
    recall, precision = hits_a / len(actual), hits_p / len(pred)
    return 0.0 if recall + precision == 0 else 2 * recall * precision / (recall + precision)


def calibrate(values: pd.Series, events: Sequence, capacity: float,
              exponents: Sequence[float] = (1.5, 2.0, 2.5, 3.0),
              endurances: Sequence[float] = (0.02, 0.03, 0.04, 0.06),
              half_lives: Sequence[float] = (63, 126, 252), window: int = 5) -> Calibration:
    """Choose the settings under which damage best explains when the investor gave up.

    `values` is the investor's portfolio value through time and `events` the
    dates (index labels) on which they abandoned their plan. For each setting
    on the grid the damage path is computed, and the model predicts giving up
    whenever damage rises through a threshold. The threshold is tried at the
    damage seen on each real event. The setting and threshold whose
    predictions best match the real events (F1 score, allowing `window` days
    either side) win; ties go to the better logistic fit of "gave up today"
    on damage.
    """
    events = list(events)
    if not events:
        raise ValueError("Calibration needs at least one date on which the investor gave up.")
    y = values.index.isin(events).astype(float)
    actual = np.flatnonzero(y)
    best, grid = None, []
    for m in exponents:
        for e in endurances:
            for hl in half_lives:
                D = fatigue_path(values.values, capacity, e, m, np.log(2) / hl, index=values.index).damage.values
                _, b, ll = _logistic_fit(D, y)
                score, theta = 0.0, float(np.median(D[actual]))
                for cand in np.unique(np.round(D[actual], 6)):
                    if cand <= 0:
                        continue
                    sc = _match(_crossings(D, cand * (1 - 1e-9)), actual, window)
                    if sc > score:
                        score, theta = sc, float(cand)
                row = {"exponent": m, "endurance": e, "half_life": hl, "threshold": theta, "match": score,
                       "log_likelihood": ll, "slope": b}
                grid.append(row)
                key = (score, ll)
                if best is None or key > best[0]:
                    best = (key, row, D)
    _, row, D = best
    at = [float(D[i]) for i in actual]
    else_ = float(D[y == 0].mean()) if (y == 0).any() else 0.0
    return Calibration(row["exponent"], row["endurance"], row["half_life"], row["threshold"], row["match"],
                       row["log_likelihood"], at, else_, grid)
