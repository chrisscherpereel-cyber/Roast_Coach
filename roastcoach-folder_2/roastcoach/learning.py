"""
What Roast Coach learns from your roasting.

Generic roasting advice can tell you *which way* to turn a knob. Only your own
roasts can say *how far*: a power step is worth more on a 500 g batch than a
900 g one, and more on a hot drum than a cold one. This module measures that
from pairs of your roasts, and the coach uses the measured number instead of the
textbook one as soon as there is enough evidence.

The method
----------
Take roasts of the same coffee in time order. For each consecutive pair, compute
what changed: the difference in a control setting during one phase, and the
difference in the measure that control drives. Across many pairs, the ratio of
those differences is the effect size. The median ratio is used rather than the
mean, so one odd roast cannot drag the estimate around.

Every estimate starts at a textbook prior and moves toward the measured value as
pairs accumulate::

    slope = (prior_weight x prior + n x measured) / (prior_weight + n)

so the first suggestion after one roast is not built on one roast, and after
twenty pairs the prior has all but disappeared.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# How much a one-step control change is expected to move a measure, before this
# roaster's own data has a say. Signs matter more than magnitudes here.
PRIORS = {
    "power@Drying->avgRoRDrying": 2.0,          # °C/min per power step
    "power@Maillard->avgRoRMaillard": 1.2,
    "power@Development->avgRoRDevelopment": 0.9,
    "power@Maillard->firstCrackTime": -0.35,    # minutes per power step
    "power@Drying->yellowPointTime": -0.25,
    "fan@Maillard->avgRoRMaillard": -0.5,
    "fan@Development->avgRoRDevelopment": -0.6,
    "fan@Development->tempRiseAfterFirstCrack": -0.8,   # °C per fan step
    "power@Development->tempRiseAfterFirstCrack": 1.5,
    "power@Development->developmentTime": -0.15,
}

# Which roast column carries each control-and-phase pair.
CONTROL_COLUMNS = {
    "power@Charge": "powerCharge",
    "power@Drying": "powerDrying",
    "power@Maillard": "powerMaillard",
    "power@Development": "powerDevelopment",
    "fan@Charge": "fanCharge",
    "fan@Drying": "fanDrying",
    "fan@Maillard": "fanMaillard",
    "fan@Development": "fanDevelopment",
}

PRIOR_WEIGHT = 3.0        # pairs of evidence needed to half-outweigh the prior
MIN_CONTROL_CHANGE = 0.5  # ignore pairs where the control barely moved
MIN_PAIRS = 2             # below this, the prior stands alone

READABLE = {
    "avgRoRDrying": "rate of rise while drying",
    "avgRoRMaillard": "rate of rise through Maillard",
    "avgRoRDevelopment": "rate of rise after first crack",
    "firstCrackTime": "when first crack arrives",
    "yellowPointTime": "when yellowing arrives",
    "developmentTime": "development time",
    "tempRiseAfterFirstCrack": "temperature climb after first crack",
    "power": "power",
    "fan": "fan",
}


PHASE_WORDS = {"Charge": "charge", "Drying": "drying", "Maillard": "Maillard",
               "Development": "development"}


def describe(key: str) -> str:
    """'power@Maillard->avgRoRMaillard' as something a person would say."""
    control_phase, _, metric = key.partition("->")
    control, _, phase = control_phase.partition("@")
    return (f"{READABLE.get(control, control)} during {PHASE_WORDS.get(phase, phase.lower())}"
            f" → {READABLE.get(metric, metric)}")


def units(key: str) -> str:
    metric = key.partition("->")[2]
    if metric in ("firstCrackTime", "yellowPointTime", "developmentTime"):
        return "min"
    if metric == "tempRiseAfterFirstCrack":
        return "°C"
    return "°C/min"


def _pairs(roasts: pd.DataFrame, same_coffee: bool = True) -> list[tuple[pd.Series, pd.Series]]:
    """Consecutive roasts to compare: same coffee first, similar batch otherwise."""
    frame = roasts.dropna(subset=["roasted_at"]).sort_values("roasted_at")
    found: list[tuple[pd.Series, pd.Series]] = []

    if same_coffee:
        for _, group in frame.groupby("coffee"):
            rows = [row for _, row in group.iterrows()]
            found += list(zip(rows, rows[1:]))
        return found

    rows = [row for _, row in frame.iterrows()]
    for before, after in zip(rows, rows[1:]):
        green_before = before.get("greenWeight")
        green_after = after.get("greenWeight")
        if (pd.notna(green_before) and pd.notna(green_after) and green_before > 0
                and abs(green_after - green_before) / green_before > 0.15):
            continue
        found.append((before, after))
    return found


def measure(roasts: pd.DataFrame, key: str) -> dict:
    """Estimate one effect from the roaster's own history.

    Returns the blended slope, how many usable pairs it rests on, how much they
    disagreed, and the prior it started from.
    """
    prior = PRIORS.get(key, 0.0)
    control_phase, _, metric = key.partition("->")
    column = CONTROL_COLUMNS.get(control_phase)
    result = {"key": key, "slope": prior, "prior": prior, "observations": 0,
              "spread": np.nan, "measured": np.nan, "confidence": 0.0}

    if roasts.empty or column not in roasts.columns or metric not in roasts.columns:
        return result

    ratios = []
    for pairs in (_pairs(roasts, True), _pairs(roasts, False)):
        ratios = []
        for before, after in pairs:
            control_delta = after.get(column, np.nan) - before.get(column, np.nan)
            metric_delta = after.get(metric, np.nan) - before.get(metric, np.nan)
            if not np.isfinite(control_delta) or not np.isfinite(metric_delta):
                continue
            if abs(control_delta) < MIN_CONTROL_CHANGE:
                continue
            ratios.append(metric_delta / control_delta)
        if len(ratios) >= MIN_PAIRS:
            break

    if len(ratios) < MIN_PAIRS:
        return result

    measured = float(np.median(ratios))
    count = len(ratios)
    result["measured"] = measured
    result["observations"] = count
    result["spread"] = float(np.median(np.abs(np.array(ratios) - measured)))
    result["slope"] = (PRIOR_WEIGHT * prior + count * measured) / (PRIOR_WEIGHT + count)
    result["confidence"] = count / (count + PRIOR_WEIGHT)
    return result


def relearn(roasts: pd.DataFrame, path: str | None = None) -> pd.DataFrame:
    """Re-measure every effect and store it. Called after each import."""
    from . import store

    rows = []
    for key in PRIORS:
        estimate = measure(roasts, key)
        control_phase, _, metric = key.partition("->")
        control, _, phase = control_phase.partition("@")
        store.save_effect(key, control, phase, metric, estimate["slope"],
                          estimate["observations"], estimate["spread"], path)
        rows.append({**estimate, "control": control, "phase": phase, "metric": metric,
                     "description": describe(key), "units": units(key)})
    return pd.DataFrame(rows)


def slope_for(key: str, path: str | None = None) -> tuple[float, float, int]:
    """(slope, confidence, pairs) for one effect — the stored value if there is one."""
    from . import store

    stored = store.effect(key, path)
    if stored and stored.get("slope") is not None:
        count = int(stored.get("observations") or 0)
        return float(stored["slope"]), count / (count + PRIOR_WEIGHT), count
    return PRIORS.get(key, 0.0), 0.0, 0


def steps_to_move(key: str, wanted_change: float, path: str | None = None,
                  limit: float = 2.0) -> tuple[float, float, int]:
    """How many control steps to move a measure by ``wanted_change``.

    Rounded to a half step, capped at ``limit`` so the advice stays a nudge
    rather than a lurch, and returned with the confidence behind it.
    """
    slope, confidence, count = slope_for(key, path)
    if not slope:
        return 0.0, confidence, count
    steps = wanted_change / slope
    steps = max(-limit, min(limit, steps))
    steps = round(steps * 2) / 2
    return steps, confidence, count


def consistency(roasts: pd.DataFrame, coffee: str, columns: list[str] | None = None) -> pd.DataFrame:
    """How much each measure wanders between roasts of one coffee."""
    columns = columns or ["totalRoastMinutes", "firstCrackTime", "developmentTime",
                          "drumDropTemperature", "weightLossPercent", "peakROR"]
    subset = roasts[roasts["coffee"] == coffee]
    rows = []
    for column in columns:
        if column not in subset:
            continue
        values = pd.to_numeric(subset[column], errors="coerce").dropna()
        if len(values) < 2:
            continue
        rows.append({
            "measure": column,
            "roasts": len(values),
            "mean": values.mean(),
            "spread": values.std(),
            "range": values.max() - values.min(),
        })
    return pd.DataFrame(rows)
