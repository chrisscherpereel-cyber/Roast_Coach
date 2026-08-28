"""What the app is willing to say about a roast, and how sure it is.

Roasting software has a habit of reading a curve and announcing a taste. This
module refuses to. Every finding is built in three levels, and each level is
allowed to claim less than the one before it:

    Observation    what was measured. "Rate of rise fell from 10.2 to 5.6 °C/min
                   between 8:16 and 8:49." Objective, reproducible, no opinion.

    Diagnosis      the name roasting practice gives that shape, under a stated
                   threshold. "Pronounced first-crack crash." True by definition
                   of the heuristic, which is why the heuristic is named.

    Cup risk       what practitioners associate with it — and cannot be
                   established from telemetry. "Associated with muted or baked
                   character. Confirm by cupping." Never stated as fact.

Every condition carries an evidence grade, and the grade decides the wording:

    A  experimental evidence          "Research has found…"
    B  strong practitioner consensus  "Roasting practice identifies this as…"
    C  useful heuristic               "Flagged by this app's threshold of…"
    D  sensory inference              "May increase the risk of…"

The grades are the point. A stall is grade A as a *thermal* observation — the
probe says the roast stopped climbing — while "baked" is grade D, because no
arrangement of thermocouples has ever tasted anything. The app can be confident
and humble in the same sentence as long as it says which part is which.

Sources are named in :mod:`roastcoach.evidence`.
"""

from __future__ import annotations

import numpy as np

from . import evidence, metrics as metric_rules

VERSION = 1

# How sure the app is allowed to sound, by grade.
CERTAINTY = {"A": "measured", "B": "recognised", "C": "this app's threshold",
             "D": "needs cupping"}

# Read through getattr so a deploy whose metrics.py is a build behind still gets
# findings rather than a broken page; app.py names the stale file on screen.
CRASH_BANDS = getattr(metric_rules, "CRASH_BANDS",
                      ((15.0, "minimal"), (30.0, "mild"), (45.0, "moderate"),
                       (float("inf"), "pronounced")))
CRASH_FLAG_FROM = getattr(metric_rules, "CRASH_FLAG_FROM", 30.0)
FLICK_TRANSIENT_SECONDS = getattr(metric_rules, "FLICK_TRANSIENT_SECONDS", 10.0)
FLICK_POSSIBLE_SECONDS = getattr(metric_rules, "FLICK_POSSIBLE_SECONDS", 20.0)
STALL_DEAD_BAND = getattr(metric_rules, "STALL_DEAD_BAND", 0.5)
STALL_SECONDS = getattr(metric_rules, "STALL_SECONDS", 15.0)
NEGATIVE_ROR_SECONDS = getattr(metric_rules, "NEGATIVE_ROR_SECONDS", 10.0)
OSCILLATION_TURNS = getattr(metric_rules, "OSCILLATION_TURNS", 4)
DEVELOPMENT_BAND = getattr(metric_rules, "DEVELOPMENT_BAND", (18.0, 25.0))
DRYING_BAND = getattr(metric_rules, "DRYING_BAND", (28.0, 40.0))
SHOWN_DECIMALS = getattr(metric_rules, "SHOWN_DECIMALS", 1)


def shares(total_minutes, yellow_minutes, crack_minutes) -> dict:
    """Phase shares from metrics.py, or worked out here if that file is older."""
    shared = getattr(metric_rules, "phase_shares", None)
    if shared is not None:
        return shared(total_minutes, yellow_minutes, crack_minutes)

    total, yellow, crack = (_number(total_minutes), _number(yellow_minutes),
                            _number(crack_minutes))
    if not np.isfinite(total) or total <= 0:
        return {}
    found = {}
    if np.isfinite(yellow):
        found["drying"] = yellow / total * 100.0
    if np.isfinite(yellow) and np.isfinite(crack):
        found["maillard"] = (crack - yellow) / total * 100.0
    if np.isfinite(crack):
        found["development"] = (total - crack) / total * 100.0
    return found


def band(percent) -> str | None:
    """Which crash band a relative fall lands in."""
    ranker = getattr(metric_rules, "severity", None)
    if ranker is not None:
        return ranker(percent)
    if percent is None or not np.isfinite(percent):
        return None
    for edge, name in CRASH_BANDS:
        if percent < edge:
            return name
    return CRASH_BANDS[-1][1]


def _number(value, default=np.nan) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if np.isfinite(value) else default


def _clock(minutes) -> str:
    minutes = _number(minutes)
    if not np.isfinite(minutes):
        return "—"
    return f"{int(minutes)}:{int(round((minutes % 1) * 60)):02d}"


def finding(condition_id, name, category, grade, observation,
            diagnosis=None, risk=None, source=None, measurements=None) -> dict:
    return {
        "id": condition_id, "name": name, "category": category, "grade": grade,
        "certainty": CERTAINTY.get(grade, ""),
        "observation": observation, "diagnosis": diagnosis, "risk": risk,
        "source": source or evidence.source_for(condition_id),
        "measurements": measurements or {},
    }


# ---------------------------------------------------------------------------
# A. Rate of rise and curve shape
# ---------------------------------------------------------------------------


def crash(row) -> dict | None:
    percent = _number(row.get("crashPercent"))
    baseline = _number(row.get("crashBaselineROR"))
    trough = _number(row.get("crashTroughROR"))
    seconds = _number(row.get("crashSeconds"))
    if not np.isfinite(percent) or percent < CRASH_BANDS[0][0]:
        return None

    severity = row.get("crashSeverity") or band(percent)
    crack = _clock(row.get("firstCrackTime"))
    observation = (
        f"Rate of rise fell from {baseline:.1f} °C/min — its settled level in the "
        f"45 s before first crack — to {trough:.1f} °C/min, a {percent:.0f}% decline "
        f"reached {seconds:.0f} s after the crack at {crack}.")

    if percent < CRASH_FLAG_FROM:
        diagnosis = (f"A {severity} decline. Some fall after first crack is normal: the "
                     "bean's own exothermic phase changes what the heat is doing.")
        return finding("crash", "Rate of rise fell after first crack", "curve", "C",
                       observation, diagnosis,
                       measurements={"percent": percent, "seconds": seconds})

    diagnosis = (
        f"A {severity} first-crack crash, by this app's bands "
        f"(<15% minimal · 15–30% mild · 30–45% moderate · >45% pronounced). Those "
        "bands are an application setting, not a published boundary — the percentage "
        "above is the finding; the word is the label.")
    risk = ("Pronounced crashes are associated by roasting practitioners with muted or "
            "baked character in the cup. The curve alone cannot establish that. Cup it "
            "and record what you find.")
    return finding("crash", "First-crack crash", "curve", "B", observation, diagnosis, risk,
                   measurements={"percent": percent, "seconds": seconds,
                                 "baseline": baseline, "trough": trough})


def flick(row) -> dict | None:
    kind = row.get("flickClass")
    seconds = _number(row.get("flickSeconds"))
    rise = _number(row.get("flickRise"))
    if kind in (None, "none") or not np.isfinite(seconds) or seconds <= 0:
        return None

    observation = (f"After the post-crack trough, rate of rise climbed back "
                   f"{rise:.1f} °C/min over {seconds:.0f} s.")
    if kind == "transient":
        return finding("flick_transient", "Brief rise after the trough", "curve", "C",
                       observation,
                       f"Under {FLICK_TRANSIENT_SECONDS:.0f} s. Reported, not "
                       "named: at this length a probe cannot be told from a roast.",
                       measurements={"seconds": seconds, "rise": rise})

    label = {"possible": "Possible flick", "flick": "Flick",
             "pronounced": "Pronounced flick"}.get(kind, "Flick")
    crashed = bool(row.get("flagRoRCrash"))
    diagnosis = (
        f"{label} — a sustained reversal of a declining rate of rise. "
        + ("Following the crash above, this is the crash-and-flick sequence."
           if crashed else "No crash preceded it.")
        + f" Sustained longer than {FLICK_POSSIBLE_SECONDS:.0f} s, which is "
          "this app's line between a reversal and noise.")
    risk = ("A rising rate of rise through development is associated by practitioners with "
            "harsher, drier cups. Cupping is what would settle it.")
    return finding("flick", label, "curve", "B", observation, diagnosis, risk,
                   measurements={"seconds": seconds, "rise": rise})


def stall(row) -> dict | None:
    seconds = _number(row.get("stallSeconds"))
    if not np.isfinite(seconds) or seconds < STALL_SECONDS:
        return None

    at = _number(row.get("stallAt"))
    crack = _number(row.get("firstCrackTime"))
    after_crack = np.isfinite(at) and np.isfinite(crack) and at >= crack
    observation = (
        f"Rate of rise stayed inside ±{STALL_DEAD_BAND:.1f} °C/min for "
        f"{seconds:.0f} s from {_clock(at)} — the roast was not climbing.")

    if after_crack:
        return finding("development_stall", "Development stall", "curve", "B", observation,
                       "A stall after first crack, when the roast should still be moving. "
                       "This is a thermal reading, not an inference: the probe recorded it.",
                       "Practitioners associate stalled development with flat, muted cups. "
                       "Confirm by cupping.",
                       measurements={"seconds": seconds, "at": at})

    return finding("stall", "Stall before first crack", "curve", "A", observation,
                   "The roast stopped gaining temperature. Objective: the only judgement in "
                   f"it is the ±{STALL_DEAD_BAND:.1f} °C/min dead band, which is "
                   "there for probe noise.",
                   "Sugars sitting at temperature without progressing is associated with "
                   "baked character. Cupping is the test.",
                   measurements={"seconds": seconds, "at": at})


def negative_ror(row) -> dict | None:
    seconds = _number(row.get("negativeSeconds"))
    if not np.isfinite(seconds) or seconds < NEGATIVE_ROR_SECONDS:
        return None
    worst = _number(row.get("negativeROR"))
    return finding(
        "negative_ror", "Bean temperature fell during the roast", "curve", "A",
        f"Measured rate of rise went negative for {seconds:.0f} s, reaching "
        f"{worst:.1f} °C/min: the bean probe read *cooler*, not warmer.",
        "A factual reading rather than a verdict. Worth explaining: an energy deficit, a "
        "large airflow change, a door opened, or the probe itself.",
        measurements={"seconds": seconds, "worst": worst})


def oscillation(row) -> dict | None:
    turns = _number(row.get("rorReversals"))
    if not np.isfinite(turns) or turns < OSCILLATION_TURNS:
        return None
    return finding(
        "oscillation", "Rate of rise turned repeatedly", "curve", "C",
        f"The smoothed rate of rise changed direction {turns:.0f} times beyond the noise "
        "floor.",
        "Described, not judged. Repeated turns are either real responses to control changes "
        "or measurement noise, and the curve cannot say which — compare against the control "
        "moves below.",
        measurements={"turns": turns})


# ---------------------------------------------------------------------------
# B. First crack, development and phases — against this bean's own history
# ---------------------------------------------------------------------------


def development(row, baseline=None) -> dict | None:
    ratio = shares(row.get("totalRoastMinutes"), row.get("yellowPointTime"),
                   row.get("firstCrackTime")).get("development")
    if ratio is None or not np.isfinite(ratio):
        return None
    ratio = round(ratio, SHOWN_DECIMALS)
    minutes = _number(row.get("developmentTime"))
    total = _number(row.get("totalRoastMinutes"))

    observation = (f"Development ratio {ratio:.1f}% — {minutes:.1f} min of a {total:.1f} min "
                   "roast, measured from charge.")

    if baseline is not None and np.isfinite(_number(baseline.get("development_ratio"))):
        theirs = _number(baseline.get("development_ratio"))
        difference = ratio - theirs
        if abs(difference) < 2.0:
            return finding("development_ratio", "Development ratio", "phases", "C",
                           observation,
                           f"Within 2 points of your baseline for this bean ({theirs:.1f}%).",
                           measurements={"ratio": ratio, "baseline": theirs})
        return finding(
            "development_divergence", "Development differs from your baseline", "phases", "C",
            observation + f" Your baseline for this bean is {theirs:.1f}%.",
            f"{abs(difference):.1f} points {'longer' if difference > 0 else 'shorter'} than "
            f"the roasts of this bean you have kept. A divergence from your own record, "
            "which is a stronger comparison than any universal figure.",
            measurements={"ratio": ratio, "baseline": theirs, "difference": difference})

    low, high = DEVELOPMENT_BAND
    if low <= ratio <= high:
        return finding("development_ratio", "Development ratio", "phases", "C", observation,
                       f"Inside this app's configured band of {low:.0f}–{high:.0f}%. "
                       "No baseline for this bean yet — three roasts of it and the "
                       "comparison becomes your own.",
                       measurements={"ratio": ratio})

    return finding(
        "development_band", "Development ratio outside the configured band", "phases", "C",
        observation,
        f"Outside this app's configured band of {low:.0f}–{high:.0f}%. That band is a "
        "setting, not a rule: excellent roasts are made below it, and the figure only "
        "becomes meaningful next to a target you have set or a history for this bean.",
        measurements={"ratio": ratio, "low": low, "high": high})


def drying(row, baseline=None) -> dict | None:
    ratio = shares(row.get("totalRoastMinutes"), row.get("yellowPointTime"),
                   row.get("firstCrackTime")).get("drying")
    if ratio is None or not np.isfinite(ratio):
        return None
    ratio = round(ratio, SHOWN_DECIMALS)
    observation = (f"Drying ran to yellowing at {_clock(row.get('yellowPointTime'))}, "
                   f"{ratio:.1f}% of the roast.")

    if baseline is not None and np.isfinite(_number(baseline.get("drying_ratio"))):
        theirs = _number(baseline.get("drying_ratio"))
        if abs(ratio - theirs) < 3.0:
            return None
        return finding(
            "drying_divergence", "Drying differs from your baseline", "phases", "C",
            observation + f" Your baseline for this bean is {theirs:.1f}%.",
            f"{abs(ratio - theirs):.1f} points "
            f"{'longer' if ratio > theirs else 'shorter'} than your own roasts of it.",
            measurements={"ratio": ratio, "baseline": theirs})

    low, high = DRYING_BAND
    if low <= ratio <= high:
        return None
    return finding(
        "drying_band", "Drying outside the configured band", "phases", "C", observation,
        f"Outside this app's configured band of {low:.0f}–{high:.0f}%, which is a setting "
        "rather than a published requirement.",
        measurements={"ratio": ratio, "low": low, "high": high})


def event_divergence(row, baseline=None) -> list[dict]:
    """First crack, total time and drop temperature against this bean's own roasts."""
    if not baseline:
        return []
    found = []
    for key, column, label, unit, tolerance in (
            ("first_crack", "firstCrackTime", "First crack", "min", 0.4),
            ("total_time", "totalRoastMinutes", "Total time", "min", 0.5),
            ("drop_temp", "drumDropTemperature", "Drop temperature", "°C", 3.0)):
        theirs = _number(baseline.get(key))
        mine = _number(row.get(column))
        if not (np.isfinite(theirs) and np.isfinite(mine)):
            continue
        difference = mine - theirs
        if abs(difference) <= tolerance:
            continue
        found.append(finding(
            f"{key}_divergence", f"{label} differs from your baseline", "phases", "C",
            f"{label} {mine:.1f} {unit} against {theirs:.1f} {unit} across the "
            f"{int(baseline.get('roasts', 0))} roasts of this bean you have kept "
            f"({difference:+.1f} {unit}).",
            "A divergence from your own record for this coffee, which is the comparison "
            "worth making — not a universal target.",
            measurements={"value": mine, "baseline": theirs, "difference": difference}))
    return found


# ---------------------------------------------------------------------------
# C. What the roaster did — control moves, not flavour
# ---------------------------------------------------------------------------


def late_heat(row) -> dict | None:
    if not row.get("flagLateHeat"):
        return None
    at = _number(row.get("lastPowerIncreaseTime"))
    crack = _number(row.get("firstCrackTime"))
    when = (f"at {_clock(at)}" if np.isfinite(at) else "late in the roast")
    after = np.isfinite(at) and np.isfinite(crack) and at > crack
    return finding(
        "late_heat", "Heat added late", "controls", "A",
        f"Power was increased {when}"
        + (f", after first crack at {_clock(crack)}." if after else ", in the last fifth of "
           "the roast."),
        "A record of what was done, not a defect. It is the move most often behind a rising "
        "rate of rise through development — see whether a flick was measured above.",
        measurements={"at": at})


# ---------------------------------------------------------------------------
# D–G. What the curve cannot see: colour, uniformity, surface damage, quakers
# ---------------------------------------------------------------------------


def colour(row) -> list[dict]:
    """Roast colour, when it has been measured. Stronger evidence than drop temperature."""
    found = []
    whole = _number(row.get("colour_whole"))
    ground = _number(row.get("colour_ground"))
    spread = _number(row.get("colour_sd"))

    if np.isfinite(whole) and np.isfinite(ground):
        gap = ground - whole
        found.append(finding(
            "colour_gap", "Whole-bean against ground colour", "colour", "A",
            f"Whole bean {whole:.0f}, ground {ground:.0f} — a gap of {gap:+.0f}.",
            "The gap between exterior and interior colour is the standard read on whether "
            "the inside of the bean kept up with the outside. Research finds roast colour "
            "carries more sensory weight than roast time alone, which is why it is worth "
            "measuring rather than inferring from drop temperature.",
            measurements={"whole": whole, "ground": ground, "gap": gap}))
    elif np.isfinite(whole):
        found.append(finding(
            "colour", "Roast colour", "colour", "A",
            f"Whole-bean colour {whole:.0f}.",
            "Recorded. Ground colour as well would give the interior–exterior gap.",
            measurements={"whole": whole}))

    if np.isfinite(spread) and spread > 0:
        found.append(finding(
            "colour_variance", "Batch colour spread", "colour", "A",
            f"Bean-to-bean colour spread {spread:.1f} across the batch.",
            "Colour variance is directly quantifiable, which makes it a far better statement "
            "than \"uneven roast\". Compare it against your own batches rather than a fixed "
            "figure.",
            measurements={"sd": spread}))
    return found


def quakers(row) -> dict | None:
    count = _number(row.get("quaker_count"))
    if not np.isfinite(count) or count <= 0:
        return None
    return finding(
        "quakers", "Quakers in the batch", "green", "A",
        f"{count:.0f} quaker(s) picked out after roasting.",
        "A green-coffee defect made visible by roasting, not caused by it: immature or "
        "compositionally deficient beans never develop colour whatever the profile does. "
        "Nothing here is a mark against the roast — take it up with the lot.",
        measurements={"count": count})


def surface_damage(row) -> list[dict]:
    """Scorching, tipping, facing — recorded by eye, since no probe sees them."""
    seen = str(row.get("visual_defects") or "").strip()
    if not seen:
        return []
    known = {
        "scorching": ("Scorching", "A",
                      "Localised burning on the broad faces of the bean, associated with "
                      "excessive contact heat or a drum hot spot. Experimentally produced "
                      "scorched roasts differ measurably in volatile chemistry from "
                      "standard ones."),
        "tipping": ("Tipping", "B",
                    "Burning concentrated at the bean tips, where local heat load exceeded "
                    "what the tissue tolerates — usually charge temperature or early "
                    "energy."),
        "facing": ("Facing", "B",
                   "One face darkened well beyond the other, from conductive or radiant "
                   "exposure on that side."),
        "charring": ("Charring", "A",
                     "Extensive blackening: thermal degradation well past the intended "
                     "roast."),
    }
    found = []
    for key, (label, grade, meaning) in known.items():
        if key in seen.lower():
            found.append(finding(
                f"surface_{key}", label, "surface", grade,
                f"Recorded by eye on this batch: {label.lower()}.", meaning,
                measurements={}))
    if not found:
        found.append(finding("surface_other", "Visual observation", "surface", "C",
                             f"Recorded by eye: {seen}", None))
    return found


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------


def assess(row, baseline: dict | None = None) -> list[dict]:
    """Every finding for one roast, most objective first.

    ``baseline`` is what this bean usually does — see :func:`baseline_for`. With
    one, phase and event comparisons are made against the roaster's own history;
    without one they fall back to the configured bands, saying so.
    """
    row = dict(row)
    found = [item for item in (
        negative_ror(row), stall(row), crash(row), flick(row), oscillation(row),
        development(row, baseline), drying(row, baseline), late_heat(row), quakers(row),
    ) if item]
    found.extend(event_divergence(row, baseline))
    found.extend(colour(row))
    found.extend(surface_damage(row))

    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    found.sort(key=lambda item: order.get(item["grade"], 9))
    return found


def baseline_for(frame, coffee: str, exclude: str | None = None) -> dict | None:
    """What this bean usually does: the reference roast, or the median of its history.

    The dictionary's central argument is that a matched baseline beats a
    universal target — comparing a roast against other roasts of the same bean,
    on the same machine, at the same batch size. This is that baseline. It needs
    three roasts before it will say anything, so one odd roast cannot become the
    standard.
    """
    if frame is None or getattr(frame, "empty", True) or not coffee:
        return None
    same = frame[frame["coffee"] == coffee]
    if exclude is not None:
        same = same[same["uid"] != exclude]
    if len(same) < 3:
        return None

    reference = same[same.get("is_reference", 0) == 1]
    source = "your reference roast" if not reference.empty else f"{len(same)} roasts"
    pick = reference if not reference.empty else same

    def middle(column):
        if column not in pick:
            return np.nan
        values = pick[column].astype("float64").dropna()
        return float(values.median()) if not values.empty else np.nan

    each = [shares(r.get("totalRoastMinutes"), r.get("yellowPointTime"),
                   r.get("firstCrackTime")) for _, r in pick.iterrows()]
    development_ratios = [one["development"] for one in each if "development" in one]
    drying_ratios = [one["drying"] for one in each if "drying" in one]

    return {
        "coffee": coffee,
        "roasts": len(same),
        "from": source,
        "first_crack": middle("firstCrackTime"),
        "total_time": middle("totalRoastMinutes"),
        "drop_temp": middle("drumDropTemperature"),
        "development_ratio": float(np.median(development_ratios)) if development_ratios else np.nan,
        "drying_ratio": float(np.median(drying_ratios)) if drying_ratios else np.nan,
    }
