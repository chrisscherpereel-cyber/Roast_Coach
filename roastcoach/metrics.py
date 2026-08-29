"""
Per-roast curve metrics: turning point, phases, peak rate of rise, and the
point-attribute columns from the *bullet_coffee_roasting* project.

These are computed once per roast at load time, straight from the sample arrays,
so the roast table can be filtered and correlated on them without rebuilding any
curves. Column names follow the original project exactly (``turningPointTime``,
``RoR-yellowing-est``, ``Drop-ChargeDeltaTemp`` …) so exports stay compatible
with anything already reading those files.

Three fixes relative to the original:

* ``totalRoastTime`` is seconds, but the original subtracted minutes from it when
  computing ``developmentTime`` and ``RoR-development-est`` (the source notes the
  latter "always = 1"). Times here are consistently minutes.
* The sample rate was hard-coded to 2 Hz; it is read from the roast instead, so
  CSV exports (usually 1 Hz) come out right.
* ``RoR-development-est`` is a rate: (drop temp − first crack temp) / development
  minutes. The original divided a time by a time.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# The original fixed the yellowing temperature at 165 °C and used the first
# sample past two minutes that reaches it when the roaster did not mark one.
YELLOW_POINT_TEMPERATURE = 165.0
YELLOW_POINT_MIN_SECONDS = 120.0

# Peak rate of rise is a centred rolling mean; the original used 20 samples at
# 2 Hz, i.e. a ten-second window.
PEAK_ROR_WINDOW_SECONDS = 10.0

# ---------------------------------------------------------------------------
# Thresholds
#
# Every number below is an *application heuristic*, not a published boundary,
# and the app says so wherever it uses one. They are gathered here so they can be
# argued with, and so nothing downstream invents its own.
#
# A crash is deliberately not "RoR fell N °C/min". That number depends on probe
# placement, sampling rate, smoothing, batch size and roaster design, so the same
# roast crosses it on one machine and not on another. What travels between
# machines is the *relative* fall from the roast's own pre-crack baseline.
# ---------------------------------------------------------------------------

CRASH_BASELINE_FROM = 45.0       # seconds before first crack where the baseline starts
CRASH_BASELINE_TO = 15.0         # …and ends: the settled run-in to the crack
CRASH_LOOK_AHEAD = 60.0          # seconds after first crack to look for the trough
CRASH_BANDS = ((15.0, "minimal"), (30.0, "mild"), (45.0, "moderate"),
               (float("inf"), "pronounced"))
CRASH_FLAG_FROM = 30.0           # the fall, in percent, at which the app raises it

# A flick is a *sustained* reversal. Below this it is noise, or one sample.
FLICK_TRANSIENT_SECONDS = 10.0
FLICK_POSSIBLE_SECONDS = 20.0    # 10–20 s: possible flick. Past this: a flick.
FLICK_PRONOUNCED_RISE = 1.5      # °C/min regained, on top of a sustained rise

# Stall and negative rate of rise are the most objective conditions here: they
# are read straight off the probe. The dead band is for probe noise, not taste.
STALL_DEAD_BAND = 0.5            # °C/min: below this the roast is not climbing
STALL_SECONDS = 15.0             # how long it has to sit there to be reported
NEGATIVE_ROR_SECONDS = 10.0      # sustained cooling during an active roast

OSCILLATION_SLOPE = 0.75         # °C/min per sample-window that counts as a turn
OSCILLATION_TURNS = 4            # reversals beyond that before it is called noisy

ABRUPT_CONTROL_STEP = 2.0        # power or fan steps in one move
CHASING_WINDOW_SECONDS = 90.0    # opposing moves inside this window are chasing
CHASING_MOVES = 3

LATE_HEAT_TAIL = 0.20            # a power increase in the last fifth of the roast

# What a roast is steered toward, as a share of the whole roast measured from
# charge — the way RoasTime shows it, and the way the readout and the phase bar
# show it. The flags and the coaching rules both read these, so a pattern check
# and a piece of advice can never disagree about where the line is.
DEVELOPMENT_BAND = (18.0, 25.0)
DRYING_BAND = (28.0, 40.0)
EXCESSIVE_DEVELOPMENT_PERCENT = DEVELOPMENT_BAND[1]
RAPID_DRYING_PERCENT = DRYING_BAND[0]

# Shares are compared at the precision they are shown at, so a roast displayed
# as 24.6% is never described as "past 25%".
SHOWN_DECIMALS = 1

# Bumped whenever a change here would give a stored roast different numbers.
# Metrics are worked out once, at import, and kept with the roast — so without a
# stamp, a correction like the phase-share fix would only reach roasts imported
# after it. store.remeasure() recomputes anything left behind.
#
# 1  the original
# 2  2026-08-26: phase shares measured from charge rather than the turning point;
#    thresholds judged at the precision shown; yellowing rate of rise uses the
#    temperature yellowing actually happened at.
# 3  2026-08-27: crash measured as a percentage fall from the roast's own
#    pre-crack baseline instead of a fixed °C/min; flick, stall and negative rate
#    of rise measured with durations; reversals counted.
# 4  2026-08-29: peak rate of rise measured from the turning point rather than
#    from charge. Charge is a discontinuity — cold beans on a hot drum — and
#    including it reported a peak of 169 °C/min for a roast that peaked at 19.
METRICS_VERSION = 4

# What this file can do — see the note in store.py. 2 adds phase_shares() and the
# shared bands; 3 measures crash, flick, stall and negative rate of rise
# relatively, and exposes the thresholds as named application heuristics.
VERSION = 3

FLAG_COLUMNS = [
    "flagRoRCrash", "flagRoRFlick", "flagStall", "flagNegativeRoR", "flagLateHeat",
    "flagExcessiveDevelopment", "flagRapidDrying", "flagCount", "flagSummary",
]

FLAG_LABELS = {
    "flagRoRCrash": "RoR crash",
    "flagRoRFlick": "RoR flick",
    "flagStall": "Stall",
    "flagNegativeRoR": "Bean temperature fell",
    "flagLateHeat": "Late heat",
    "flagExcessiveDevelopment": "Long development",
    "flagRapidDrying": "Fast drying",
}

FLAG_EXPLANATIONS = {
    "flagRoRCrash": f"Rate of rise fell at least {CRASH_FLAG_FROM:.0f}% from its own "
                    "settled level before first crack — measured against this roast, not "
                    "against a fixed °C/min, which does not travel between machines.",
    "flagRoRFlick": f"After that trough, rate of rise rose again for longer than "
                    f"{FLICK_POSSIBLE_SECONDS:.0f} s — long enough not to be noise.",
    "flagStall": f"Rate of rise sat inside ±{STALL_DEAD_BAND:.1f} °C/min for at least "
                 f"{STALL_SECONDS:.0f} s: the roast stopped climbing.",
    "flagNegativeRoR": f"Measured bean temperature went *down* during the roast for at "
                       f"least {NEGATIVE_ROR_SECONDS:.0f} s. Read straight off the probe — "
                       "worth explaining, whether it was energy, airflow or the probe itself.",
    "flagLateHeat": "Power was increased after first crack, or in the last fifth of the roast.",
    "flagExcessiveDevelopment": f"Development ran past {EXCESSIVE_DEVELOPMENT_PERCENT:.0f}% "
                                "of the roast.",
    "flagRapidDrying": f"Drying took less than {RAPID_DRYING_PERCENT:.0f}% of the roast.",
}

METRIC_COLUMNS = [
    "indexTurningPoint", "turningPointTime", "turningPointBeanTemp", "ibtsTurningPointTemp",
    "index165PT", "yellowPointTime", "yellowPointTemp165", "yellowPointTemp",
    "yellowPointSource",
    "firstCrackTime", "firstCrackTemp", "firstCrackBeanTemp",
    "peakROR", "peakRORTime", "peakIbtsROR", "peakIbtsRORTime",
    "totalRoastMinutes", "weightLostPercent",
    "time/temp", "temp/time", "Drop-ChargeDeltaTemp", "deltaIBTS-BT-atDrop",
    "yellowingPhaseTime", "browningPhaseTime", "developmentTime",
    "RoR-yellowing-est", "RoR-browning-est", "RoR-development-est", "RoR-fullRoast-est",
    "avgRoRDrying", "avgRoRMaillard", "avgRoRDevelopment",
    "rorAtFirstCrack", "rorAtDrop", "tempRiseAfterFirstCrack",
    "powerChanges", "fanChanges", "drumChanges", "lastPowerIncreaseTime",
    "crashBaselineROR", "crashTroughROR", "crashPercent", "crashSeconds", "crashSeverity",
    "flickSeconds", "flickRise", "flickSlope", "flickClass",
    "stallSeconds", "stallROR", "stallAt", "negativeSeconds", "negativeROR", "rorReversals",
    "powerCharge", "powerDrying", "powerMaillard", "powerDevelopment",
    "fanCharge", "fanDrying", "fanMaillard", "fanDevelopment", "drumMean",
    "powerAtFirstCrack", "fanAtFirstCrack",
] + FLAG_COLUMNS


def _as_shown(value):
    """The value as the roaster sees it — what every threshold is judged against."""
    if value is None:
        return np.nan
    try:
        value = float(value)
    except (TypeError, ValueError):
        return np.nan
    return round(value, SHOWN_DECIMALS) if np.isfinite(value) else np.nan


def phase_shares(total_minutes, yellow_minutes, crack_minutes) -> dict:
    """Drying, Maillard and development as shares of the roast, measured from charge.

    The single definition. RoasTime measures its phase percentages from charge —
    0:00 is the moment the beans go in — so drying is everything up to yellowing,
    including the dip to the turning point, and the three shares add to 100.
    """
    try:
        total = float(total_minutes)
    except (TypeError, ValueError):
        return {}
    if not np.isfinite(total) or total <= 0:
        return {}

    shares = {}
    yellow = float(yellow_minutes) if yellow_minutes is not None else np.nan
    crack = float(crack_minutes) if crack_minutes is not None else np.nan
    if np.isfinite(yellow):
        shares["drying"] = yellow / total * 100.0
    if np.isfinite(yellow) and np.isfinite(crack):
        shares["maillard"] = (crack - yellow) / total * 100.0
    if np.isfinite(crack):
        shares["development"] = (total - crack) / total * 100.0
    return shares


def _series(roast_json: dict, *names) -> pd.Series:
    """First present sample array, as a float Series."""
    for name in names:
        values = roast_json.get(name)
        if values:
            return pd.to_numeric(pd.Series(values), errors="coerce")
    return pd.Series(dtype="float64")


def _sample_rate(roast_json: dict) -> float:
    rate = roast_json.get("sampleRate")
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        return 1.0
    return rate if rate > 0 else 1.0


def _at(series: pd.Series, index) -> float:
    if series.empty or index is None or not np.isfinite(index):
        return np.nan
    position = int(round(index))
    if position < 0 or position >= len(series):
        return np.nan
    return float(series.iloc[position])


def _minutes(index, sample_rate: float) -> float:
    if index is None or not np.isfinite(index):
        return np.nan
    return float(index) / sample_rate / 60.0


def _peak(series: pd.Series, window: int, after=None) -> tuple[float, float]:
    """(peak value, index) of the centred rolling mean, after the turning point.

    Charge is a discontinuity, not a rate of rise. Cold beans hitting a hot drum
    send the IBTS through a transient of well over a hundred °C/min for a few
    seconds, and a peak taken over the whole series reports that — "peak rate of
    rise 169 °C/min" for a roast whose real peak was 19. The number a roaster
    means is the peak of the roast, which begins at the turning point, so that is
    where the search begins.
    """
    if series.dropna().empty:
        return np.nan, np.nan
    rolled = series.rolling(window=max(3, window), center=True,
                            min_periods=max(2, window // 2)).mean()
    if after is not None and np.isfinite(after):
        rolled = rolled.loc[int(after):]
    if rolled.dropna().empty:
        return np.nan, np.nan
    position = int(rolled.idxmax())
    return float(rolled.loc[position]), float(position)


def _smooth(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=max(3, window), center=True, min_periods=2).mean()


def _mean_between(series: pd.Series, start, end) -> float:
    """Mean of a series between two sample indices."""
    if series.dropna().empty or start is None or end is None:
        return np.nan
    if not (np.isfinite(start) and np.isfinite(end)) or end <= start:
        return np.nan
    window = series.iloc[int(max(0, start)):int(min(len(series), end))]
    return float(window.mean()) if not window.dropna().empty else np.nan


def _run_length(condition: pd.Series, sample_rate: float) -> tuple[float, int]:
    """Longest unbroken stretch where ``condition`` holds: seconds, and where it began."""
    longest = running = 0
    start = best_start = 0
    for position, value in enumerate(condition.to_numpy()):
        if value:
            if running == 0:
                start = position
            running += 1
            if running > longest:
                longest, best_start = running, start
        else:
            running = 0
    return longest / sample_rate if sample_rate else 0.0, best_start


def crash_measures(smooth: pd.Series, sample_rate: float, crack, end) -> dict:
    """How far the rate of rise fell after first crack, relative to its own baseline.

    The magnitude is a percentage of the roast's own settled pre-crack rate:

        crash % = 100 × (baseline − trough) / baseline

    which is comparable between machines in a way that "fell 3 °C/min" is not.
    The baseline is the median of the run-in to the crack rather than the value
    at the crack itself, so one noisy sample cannot set it.
    """
    blank = {"crashBaselineROR": np.nan, "crashTroughROR": np.nan, "crashPercent": np.nan,
             "crashSeconds": np.nan, "crashSeverity": None,
             "flickSeconds": np.nan, "flickRise": np.nan, "flickSlope": np.nan,
             "flickClass": None}
    if smooth.dropna().empty or not np.isfinite(crack or np.nan):
        return blank

    crack = int(crack)
    finish = int(end) if np.isfinite(end or np.nan) else len(smooth) - 1
    baseline_from = max(0, crack - int(CRASH_BASELINE_FROM * sample_rate))
    baseline_to = max(baseline_from + 1, crack - int(CRASH_BASELINE_TO * sample_rate))
    run_in = smooth.iloc[baseline_from:baseline_to].dropna()
    if run_in.empty:
        return blank

    baseline = float(run_in.median())
    ahead = smooth.iloc[crack:min(finish + 1, crack + int(CRASH_LOOK_AHEAD * sample_rate) + 1)]
    ahead = ahead.dropna()
    if ahead.empty or baseline <= 0:
        return blank

    trough_at = int(ahead.idxmin())
    trough = float(ahead.min())
    found = dict(blank)
    found["crashBaselineROR"] = baseline
    found["crashTroughROR"] = trough
    found["crashPercent"] = (baseline - trough) / baseline * 100.0
    found["crashSeconds"] = (trough_at - crack) / sample_rate if sample_rate else np.nan
    found["crashSeverity"] = severity(found["crashPercent"])

    # The flick: after that trough, does the rate of rise climb again, and for
    # long enough to be the roast rather than the probe?
    found.update(flick_measures(smooth, sample_rate, trough_at, finish))
    return found


def severity(percent) -> str | None:
    """Which band a relative fall lands in. Application heuristic, not a boundary."""
    if percent is None or not np.isfinite(percent):
        return None
    for edge, name in CRASH_BANDS:
        if percent < edge:
            return name
    return CRASH_BANDS[-1][1]


def flick_measures(smooth: pd.Series, sample_rate: float, trough_at: int, finish: int) -> dict:
    """The sustained rise after the post-crack trough, if there is one."""
    found = {"flickSeconds": np.nan, "flickRise": np.nan, "flickSlope": np.nan,
             "flickClass": None}
    after = smooth.iloc[trough_at:finish + 1].dropna()
    if len(after) < 3:
        return found

    floor = float(after.iloc[0])
    rising = after > (floor + 0.1)          # 0.1 °C/min of daylight, so noise is not a rise
    seconds, _ = _run_length(rising, sample_rate)
    rise = float(after.max() - floor)
    found["flickSeconds"] = seconds
    found["flickRise"] = rise
    found["flickSlope"] = (rise / (seconds / 60.0)) if seconds else np.nan

    if seconds < FLICK_TRANSIENT_SECONDS:
        found["flickClass"] = "none" if seconds <= 0 else "transient"
    elif seconds < FLICK_POSSIBLE_SECONDS:
        found["flickClass"] = "possible"
    elif rise >= FLICK_PRONOUNCED_RISE:
        found["flickClass"] = "pronounced"
    else:
        found["flickClass"] = "flick"
    return found


def quiet_measures(smooth: pd.Series, sample_rate: float, turning, crack, end) -> dict:
    """Stall and negative rate of rise: the roast not climbing, and the roast cooling.

    Both are read straight off the probe, which makes them the most objective
    conditions the app has. The dead band exists for probe noise, nothing else.
    """
    found = {"stallSeconds": 0.0, "stallROR": np.nan, "stallAt": np.nan,
             "negativeSeconds": 0.0, "negativeROR": np.nan}
    if smooth.dropna().empty:
        return found

    start = int(turning) + int(30 * sample_rate) if np.isfinite(turning or np.nan) else 0
    finish = int(end) if np.isfinite(end or np.nan) else len(smooth) - 1
    stretch = smooth.iloc[start:finish + 1]
    if stretch.dropna().empty:
        return found

    quiet = stretch.abs() < STALL_DEAD_BAND
    seconds, began = _run_length(quiet.fillna(False), sample_rate)
    found["stallSeconds"] = seconds
    if seconds:
        found["stallAt"] = _minutes(start + began, sample_rate)
        found["stallROR"] = float(stretch.iloc[began:began + int(seconds * sample_rate)].mean())

    cooling = stretch < -STALL_DEAD_BAND
    negative, at = _run_length(cooling.fillna(False), sample_rate)
    found["negativeSeconds"] = negative
    if negative:
        found["negativeROR"] = float(stretch.iloc[at:at + int(negative * sample_rate)].min())
    return found


def reversals(smooth: pd.Series, sample_rate: float) -> float:
    """How many times the rate of rise changed direction beyond the noise floor.

    Geometry, not judgement: a curve that turns repeatedly is described, not
    called defective, because probe noise and real control changes look alike
    until you know which happened.
    """
    if smooth.dropna().empty:
        return 0.0
    step = max(2, int(round(5 * sample_rate)))
    coarse = smooth.dropna().iloc[::step]
    if len(coarse) < 3:
        return 0.0
    slopes = coarse.diff().dropna()
    meaningful = slopes[abs(slopes) >= OSCILLATION_SLOPE * (step / max(sample_rate, 1e-9)) / 60.0]
    if meaningful.empty:
        return 0.0
    signs = np.sign(meaningful.to_numpy())
    return float((signs[1:] != signs[:-1]).sum())


def roast_dynamics(drum_ror: pd.Series, sample_rate: float, points: dict, metrics: dict,
                   actions: list) -> dict:
    """Phase-average rate of rise, control activity, and the pattern checks.

    The checks are heuristics with named thresholds, not verdicts: a crash on a
    light roast you dropped early may be exactly what you intended.
    """
    result: dict = {name: np.nan for name in
                    ("avgRoRDrying", "avgRoRMaillard", "avgRoRDevelopment",
                     "rorAtFirstCrack", "rorAtDrop", "tempRiseAfterFirstCrack",
                     "powerChanges", "fanChanges", "drumChanges", "lastPowerIncreaseTime")}
    for flag in FLAG_COLUMNS:
        result[flag] = False if flag.startswith("flag") and flag not in ("flagCount", "flagSummary") else None
    result["flagCount"] = 0
    result["flagSummary"] = ""

    window = max(3, int(round(PEAK_ROR_WINDOW_SECONDS * sample_rate)))
    smooth = _smooth(drum_ror, window) if not drum_ror.dropna().empty else pd.Series(dtype="float64")

    turning, yellow, crack, end = (points.get("turning"), points.get("yellow"),
                                   points.get("crack"), points.get("end"))

    result["avgRoRDrying"] = _mean_between(smooth, turning, yellow)
    result["avgRoRMaillard"] = _mean_between(smooth, yellow, crack)
    result["avgRoRDevelopment"] = _mean_between(smooth, crack, end)
    result["rorAtFirstCrack"] = _at(smooth, crack)
    result["rorAtDrop"] = _at(smooth, end)

    # --- control activity ---
    counts = {0: 0, 1: 0, 2: 0}
    last_increase = np.nan
    previous_power = None
    for action in actions or []:
        control = action.get("ctrlType")
        if control not in counts:
            continue
        counts[control] += 1
        if control == 0:
            value = action.get("value")
            if previous_power is not None and value is not None and value > previous_power:
                last_increase = float(action.get("index", 0))
            previous_power = value
    result["powerChanges"], result["fanChanges"], result["drumChanges"] = (
        counts[0], counts[1], counts[2])
    result["lastPowerIncreaseTime"] = _minutes(last_increase, sample_rate)

    # --- what the curve did, in numbers ---
    reasons = []

    result.update(crash_measures(smooth, sample_rate, crack, end))
    result.update(quiet_measures(smooth, sample_rate, turning, crack, end))
    result["rorReversals"] = reversals(smooth, sample_rate)

    if np.isfinite(result.get("crashPercent", np.nan)) \
            and result["crashPercent"] >= CRASH_FLAG_FROM:
        result["flagRoRCrash"] = True
        reasons.append(FLAG_LABELS["flagRoRCrash"])

    if result.get("flickSeconds", 0) and result["flickSeconds"] >= FLICK_POSSIBLE_SECONDS:
        result["flagRoRFlick"] = True
        reasons.append(FLAG_LABELS["flagRoRFlick"])

    if result.get("stallSeconds", 0) and result["stallSeconds"] >= STALL_SECONDS:
        result["flagStall"] = True
        reasons.append(FLAG_LABELS["flagStall"])

    if result.get("negativeSeconds", 0) and result["negativeSeconds"] >= NEGATIVE_ROR_SECONDS:
        result["flagNegativeRoR"] = True
        reasons.append(FLAG_LABELS["flagNegativeRoR"])

    if np.isfinite(last_increase):
        after_crack = np.isfinite(crack or np.nan) and last_increase > crack
        in_tail = np.isfinite(end or np.nan) and last_increase > end * (1 - LATE_HEAT_TAIL)
        if after_crack or in_tail:
            result["flagLateHeat"] = True
            reasons.append(FLAG_LABELS["flagLateHeat"])

    development_percent = _as_shown(metrics.get("_developmentPercent"))
    if development_percent is not None and np.isfinite(development_percent) \
            and development_percent > EXCESSIVE_DEVELOPMENT_PERCENT:
        result["flagExcessiveDevelopment"] = True
        reasons.append(FLAG_LABELS["flagExcessiveDevelopment"])

    drying_percent = _as_shown(metrics.get("_dryingPercent"))
    if drying_percent is not None and np.isfinite(drying_percent) \
            and drying_percent < RAPID_DRYING_PERCENT:
        result["flagRapidDrying"] = True
        reasons.append(FLAG_LABELS["flagRapidDrying"])

    result["flagCount"] = len(reasons)
    result["flagSummary"] = ", ".join(reasons)
    return result


def curve_metrics(roast_json: dict) -> dict:
    """Every curve-derived point attribute for one roast."""
    sample_rate = _sample_rate(roast_json)
    bean = _series(roast_json, "beanTemperature")
    drum = _series(roast_json, "drumTemperature")
    bean_ror = _series(roast_json, "beanDerivative")
    drum_ror = _series(roast_json, "ibtsDerivative", "drumDerivative")

    metrics: dict = {name: np.nan for name in METRIC_COLUMNS}
    metrics["yellowPointTemp165"] = YELLOW_POINT_TEMPERATURE
    metrics["yellowPointSource"] = None
    if bean.empty and drum.empty:
        return metrics

    # --- turning point: the coldest the beans get before they start roasting ---
    if not bean.dropna().empty:
        turning_index = float(bean.idxmin())
        metrics["indexTurningPoint"] = turning_index
        metrics["turningPointTime"] = _minutes(turning_index, sample_rate)
        metrics["turningPointBeanTemp"] = _at(bean, turning_index)
        metrics["ibtsTurningPointTemp"] = _at(drum, turning_index)

    # --- automatic yellowing: first sample past 2 min at or above 165 °C ---
    if not drum.dropna().empty:
        earliest = int(YELLOW_POINT_MIN_SECONDS * sample_rate)
        reached = drum.index[(drum.index >= earliest) & (drum >= YELLOW_POINT_TEMPERATURE)]
        if len(reached):
            metrics["index165PT"] = float(reached[0])

    marked_yellowing = roast_json.get("indexYellowingStart") or 0
    if marked_yellowing and marked_yellowing > 0:
        yellow_index = float(marked_yellowing)
        metrics["yellowPointSource"] = "marked"
    else:
        yellow_index = metrics["index165PT"]
        metrics["yellowPointSource"] = "auto-165" if np.isfinite(yellow_index) else None
    metrics["yellowPointTime"] = _minutes(yellow_index, sample_rate)
    # The drum temperature where yellowing actually was. When the roaster marked
    # it, that is rarely 165 °C, and the phase rate-of-rise estimates should use
    # the temperature the roast really passed through rather than the convention.
    metrics["yellowPointTemp"] = _at(drum, yellow_index)

    # --- first crack ---
    first_crack_index = roast_json.get("indexFirstCrackStart") or 0
    if first_crack_index and 0 < first_crack_index < 100000:
        metrics["firstCrackTime"] = _minutes(first_crack_index, sample_rate)
        metrics["firstCrackTemp"] = _at(drum, first_crack_index)
        metrics["firstCrackBeanTemp"] = _at(bean, first_crack_index)

    # --- peak rate of rise ---
    window = max(3, int(round(PEAK_ROR_WINDOW_SECONDS * sample_rate)))
    if bean_ror.dropna().empty and not bean.dropna().empty:
        bean_ror = bean.diff() * 60.0 * sample_rate
    if drum_ror.dropna().empty and not drum.dropna().empty:
        drum_ror = drum.diff() * 60.0 * sample_rate

    after_charge = metrics.get("indexTurningPoint")
    peak, position = _peak(bean_ror, window, after_charge)
    metrics["peakROR"] = peak
    metrics["peakRORTime"] = _minutes(position, sample_rate)
    peak, position = _peak(drum_ror, window, after_charge)
    metrics["peakIbtsROR"] = peak
    metrics["peakIbtsRORTime"] = _minutes(position, sample_rate)

    # --- totals, weights, temperature spreads ---
    end_index = roast_json.get("roastEndIndex")
    total_minutes = _minutes(end_index, sample_rate) if end_index else np.nan
    if not np.isfinite(total_minutes):
        total_seconds = roast_json.get("totalRoastTime")
        total_minutes = float(total_seconds) / 60.0 if total_seconds else np.nan
    metrics["totalRoastMinutes"] = total_minutes

    def number(field):
        try:
            value = float(roast_json.get(field))
        except (TypeError, ValueError):
            return np.nan
        return value

    drum_drop, drum_charge = number("drumDropTemperature"), number("drumChargeTemperature")
    bean_drop = number("beanDropTemperature")
    green, roasted = number("weightGreen"), number("weightRoasted")

    if green > 0 and roasted > 0:
        metrics["weightLostPercent"] = (green - roasted) / green * 100.0

    total_seconds = total_minutes * 60.0 if np.isfinite(total_minutes) else np.nan
    if np.isfinite(drum_drop) and drum_drop:
        metrics["time/temp"] = total_seconds / drum_drop
    if np.isfinite(total_seconds) and total_seconds:
        metrics["temp/time"] = drum_drop / total_seconds
    metrics["Drop-ChargeDeltaTemp"] = drum_drop - drum_charge
    metrics["deltaIBTS-BT-atDrop"] = drum_drop - bean_drop

    # --- phases, measured from the turning point ---
    turning = metrics["turningPointTime"]
    yellow = metrics["yellowPointTime"]
    crack = metrics["firstCrackTime"]

    if np.isfinite(turning) and np.isfinite(yellow):
        metrics["yellowingPhaseTime"] = yellow - turning
    if np.isfinite(yellow) and np.isfinite(crack) and crack > 0:
        metrics["browningPhaseTime"] = crack - yellow
    if np.isfinite(crack) and crack > 0 and np.isfinite(total_minutes):
        metrics["developmentTime"] = total_minutes - crack

    # --- straight-line rate of rise across each phase (°C/min) ---
    # The temperature yellowing was actually at, falling back to the 165 °C
    # convention only when nothing better is known.
    yellow_temp = metrics["yellowPointTemp"]
    if not np.isfinite(yellow_temp):
        yellow_temp = YELLOW_POINT_TEMPERATURE

    yellowing_phase = metrics["yellowingPhaseTime"]
    if np.isfinite(yellowing_phase) and yellowing_phase > 0 and np.isfinite(metrics["ibtsTurningPointTemp"]):
        metrics["RoR-yellowing-est"] = (
            yellow_temp - metrics["ibtsTurningPointTemp"]
        ) / yellowing_phase

    browning_phase = metrics["browningPhaseTime"]
    if np.isfinite(browning_phase) and browning_phase > 0 and np.isfinite(metrics["firstCrackTemp"]):
        metrics["RoR-browning-est"] = (metrics["firstCrackTemp"] - yellow_temp) / browning_phase

    development = metrics["developmentTime"]
    if np.isfinite(development) and development > 0 and np.isfinite(drum_drop) and np.isfinite(metrics["firstCrackTemp"]):
        metrics["RoR-development-est"] = (drum_drop - metrics["firstCrackTemp"]) / development

    if np.isfinite(turning) and np.isfinite(total_minutes) and total_minutes > turning:
        metrics["RoR-fullRoast-est"] = (drum_drop - metrics["ibtsTurningPointTemp"]) / (total_minutes - turning)

    # --- dynamics, control activity and the pattern checks ---
    # Phase shares are of the whole roast, from charge — not from the turning
    # point. Measuring from the turning point makes every share a percent or two
    # larger, which is how a roast shown as 24.6% development came to be flagged
    # for running past 25%.
    shares = phase_shares(total_minutes, yellow, crack)
    metrics["_dryingPercent"] = shares.get("drying", np.nan)
    metrics["_developmentPercent"] = shares.get("development", np.nan)

    points = {
        "turning": metrics["indexTurningPoint"],
        "yellow": (float(marked_yellowing) if marked_yellowing and marked_yellowing > 0
                   else metrics["index165PT"]),
        "crack": float(first_crack_index) if first_crack_index and 0 < first_crack_index < 100000 else np.nan,
        "end": float(end_index) if end_index else float(len(drum) - 1 if len(drum) else np.nan),
    }
    metrics.update(
        roast_dynamics(drum_ror, sample_rate, points, metrics,
                       (roast_json.get("actions") or {}).get("actionTimeList") or [])
    )
    metrics.update(control_means(roast_json, points))
    metrics["tempRiseAfterFirstCrack"] = drum_drop - metrics["firstCrackTemp"]
    metrics.pop("_dryingPercent", None)
    metrics.pop("_developmentPercent", None)

    return metrics


def control_means(roast_json: dict, points: dict) -> dict:
    """Average power, fan and drum over each phase of the roast.

    The learning engine works on these: to know what a power change does, you
    first have to know what power *was*, phase by phase.
    """
    result = {name: np.nan for name in
              ("powerCharge", "powerDrying", "powerMaillard", "powerDevelopment",
               "fanCharge", "fanDrying", "fanMaillard", "fanDevelopment", "drumMean",
               "powerAtFirstCrack", "fanAtFirstCrack")}

    length = max((len(roast_json.get(f) or []) for f in ("beanTemperature", "drumTemperature")),
                 default=0)
    if not length:
        return result

    series = {name: pd.Series(np.nan, index=range(length)) for name in ("power", "fan", "drum")}
    codes = {0: "power", 1: "fan", 2: "drum"}
    for action in (roast_json.get("actions") or {}).get("actionTimeList") or []:
        control = codes.get(action.get("ctrlType"))
        if control is None:
            continue
        position = int(action.get("index", 0))
        if 0 <= position < length:
            series[control].iloc[position] = action.get("value")
    for control in series:
        series[control] = series[control].ffill().bfill()

    turning = points.get("turning")
    yellow = points.get("yellow")
    crack = points.get("crack")
    end = points.get("end") or (length - 1)
    start = 0 if not np.isfinite(turning or np.nan) else int(turning)

    spans = {
        "Charge": (0, int(turning) if np.isfinite(turning or np.nan) else min(60, length)),
        "Drying": (start, int(yellow) if np.isfinite(yellow or np.nan) else None),
        "Maillard": (int(yellow) if np.isfinite(yellow or np.nan) else None,
                     int(crack) if np.isfinite(crack or np.nan) else None),
        "Development": (int(crack) if np.isfinite(crack or np.nan) else None, int(end)),
    }

    for phase, (begin, finish) in spans.items():
        if begin is None or finish is None or finish <= begin:
            continue
        for control in ("power", "fan"):
            window = series[control].iloc[begin:finish]
            if not window.dropna().empty:
                result[f"{control}{phase}"] = float(window.mean())

    if not series["drum"].dropna().empty:
        result["drumMean"] = float(series["drum"].mean())
    if np.isfinite(crack or np.nan) and int(crack) < length:
        result["powerAtFirstCrack"] = float(series["power"].iloc[int(crack)])
        result["fanAtFirstCrack"] = float(series["fan"].iloc[int(crack)])
    return result


def curve_frame(roast_json: dict, roast_name: str | None = None) -> pd.DataFrame:
    """One roast as the original project's long-format ``curve_df`` rows.

    Native sample rate, no smoothing: temperatures, both derivatives, the IBTS
    second derivative, and the control settings forward-filled from the action
    list.
    """
    bean = _series(roast_json, "beanTemperature")
    drum = _series(roast_json, "drumTemperature")
    if bean.empty and drum.empty:
        return pd.DataFrame()

    length = min(len(s) for s in (bean, drum) if len(s)) if (len(bean) and len(drum)) else max(len(bean), len(drum))
    bean_ror = _series(roast_json, "beanDerivative")
    drum_ror = _series(roast_json, "ibtsDerivative", "drumDerivative")
    sample_rate = _sample_rate(roast_json)

    frame = pd.DataFrame(
        {
            "roastName": roast_name or roast_json.get("roastName") or roast_json.get("uid"),
            "uid": roast_json.get("uid"),
            "indexTime": np.arange(length),
            "seconds": np.arange(length) / sample_rate,
            "beanTemperature": bean.reindex(range(length)).values if len(bean) else np.nan,
            "drumTemperature": drum.reindex(range(length)).values if len(drum) else np.nan,
        }
    )
    frame["beanDerivative"] = bean_ror.reindex(range(length)).values if len(bean_ror) else np.nan
    frame["ibtsDerivative"] = drum_ror.reindex(range(length)).values if len(drum_ror) else np.nan
    if frame["ibtsDerivative"].dropna().empty:
        frame["ibtsDerivative"] = frame["drumTemperature"].diff() * 60.0 * sample_rate
    frame["ibts2ndDerivative"] = frame["ibtsDerivative"].diff()

    for control in ("Power", "Fan", "Drum"):
        frame[control] = np.nan
    codes = {0: "Power", 1: "Fan", 2: "Drum"}
    for action in (roast_json.get("actions") or {}).get("actionTimeList") or []:
        control = codes.get(action.get("ctrlType"))
        if control is None:
            continue
        position = int(action.get("index", 0))
        if 0 <= position < length:
            frame.loc[position, control] = action.get("value")
    for control in ("Power", "Fan", "Drum"):
        frame[control] = frame[control].ffill().bfill()

    return frame[
        ["roastName", "uid", "indexTime", "seconds", "Power", "Fan", "Drum",
         "beanTemperature", "drumTemperature", "beanDerivative", "ibtsDerivative", "ibts2ndDerivative"]
    ]


# ---------------------------------------------------------------------------
# Phase conventions
# ---------------------------------------------------------------------------

PHASE_CONVENTIONS = {
    "markers": "RoasTime markers (from charge)",
    "turning-point": "Turning point (bullet_coffee_roasting)",
}


def phase_frame(roasts: pd.DataFrame, convention: str = "markers") -> pd.DataFrame:
    """Drying / Maillard / development for each roast, in minutes and percent.

    Two conventions, because the two source projects disagree:

    * ``markers`` measures from charge and uses the yellowing point the roaster
      marked in RoasTime.
    * ``turning-point`` measures from the turning point -- the moment the beans
      stop cooling and start roasting -- and falls back to the first crossing of
      165 °C when no yellowing point was marked, as *bullet_coffee_roasting* does.
    """
    frame = pd.DataFrame(index=roasts.index)
    frame["label"] = (
        roasts.get("date", pd.Series("", index=roasts.index)).astype(str)
        + "  " + roasts.get("roastName", pd.Series("", index=roasts.index)).fillna("").astype(str).str.slice(0, 28)
    )

    total = roasts.get("totalRoastMinutes")
    if convention == "turning-point":
        start = roasts.get("turningPointTime")
        yellow = roasts.get("yellowPointTime")
        crack = roasts.get("firstCrackTime")
    else:
        start = pd.Series(0.0, index=roasts.index)
        yellow = roasts.get("secondsYellowingStart") / 60.0
        crack = roasts.get("secondsFirstCrackStart") / 60.0
        yellow = yellow.where(yellow > 0)
        crack = crack.where(crack > 0)

    frame["start"] = start
    frame["drying"] = (yellow - start).where(lambda s: s > 0)
    frame["maillard"] = (crack - yellow).where(lambda s: s > 0)
    frame["development"] = (total - crack).where(lambda s: s > 0)

    span = frame[["drying", "maillard", "development"]].sum(axis=1, min_count=1)
    for phase in ("drying", "maillard", "development"):
        frame[f"{phase}Percent"] = np.where(span > 0, frame[phase] / span * 100, np.nan)
    frame["phaseSpan"] = span
    return frame
