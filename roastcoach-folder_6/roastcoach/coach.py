"""
The coach: what to change on the next roast, and whether it worked.

Each rule looks at one roast, decides whether something is worth saying, and
returns a recommendation with four parts:

* **finding** -- what it saw, in numbers
* **action** -- the specific change to make, with a magnitude taken from what
  that control has actually done on this machine (see ``learning``)
* **prediction** -- the value the target measure should reach if it works
* **confidence** -- how much evidence stands behind the magnitude, and how often
  this rule's predictions have come true before

The prediction is the important part. A suggestion nobody checks is an opinion;
a suggestion with a number attached can be marked right or wrong by the next
roast, and that is what lets the app get better at this.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

import numpy as np
import pandas as pd

from . import learning, metrics as metric_rules, store

# What a roast is being steered toward. These are starting points, not laws --
# every one of them can be overridden per coffee once a reference roast exists.
# The two phase bands are the same objects the pattern checks use, so a flag and
# a piece of advice cannot disagree about where the line is.
# getattr, not attribute access: a deploy whose metrics.py is older than this
# file still has to start. app.py notices and says which file is behind.
TARGETS = {
    "development_percent": getattr(metric_rules, "DEVELOPMENT_BAND", (18.0, 25.0)),
    "drying_percent": getattr(metric_rules, "DRYING_BAND", (28.0, 40.0)),
    "turning_point_minutes": (0.8, 1.8),
    "total_minutes": (8.0, 14.0),
    "ror_at_first_crack": (3.0, 12.0),
    "weight_loss_percent": (11.0, 17.0),
}

TOLERANCE = 0.35  # a prediction counts as met within this share of the intended move

# What this file can do — see the note in store.py. 2 reads the phase bands from
# metrics.py and judges them at the precision shown; 3 compares against the
# bean's own baseline where it exists and says which comparison it used;
# 4 adds review_all()/review_pass(), which reads the shared tables once.
VERSION = 4


def _value(row, name, default=np.nan):
    value = row.get(name, default)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return np.nan
    return value if np.isfinite(value) else np.nan


SHOWN_DECIMALS = getattr(metric_rules, "SHOWN_DECIMALS", 1)


def _clock(minutes) -> str:
    """m:ss — the only way a time is ever shown to somebody at the machine."""
    try:
        minutes = float(minutes)
    except (TypeError, ValueError):
        return "—"
    if not np.isfinite(minutes):
        return "—"
    return f"{int(minutes)}:{int(round((minutes % 1) * 60)):02d}"


def _shown(value, decimals: int = SHOWN_DECIMALS):
    """A measure at the precision the roaster sees it.

    Bands are judged on this rather than on the raw number, so a roast printed as
    24.6% development is never told it ran past 25%.
    """
    try:
        value = float(value)
    except (TypeError, ValueError):
        return np.nan
    return round(value, decimals) if np.isfinite(value) else np.nan


def _phase_percentages(row) -> dict:
    """Drying / Maillard / development as shares of the roast, from charge.

    One definition, in :mod:`roastcoach.metrics`, shared with the pattern checks
    and with what the roast readout shows. Rounded to the decimal the roaster is
    shown, so a card never argues with the number printed above it.
    """
    total = _value(row, "totalRoastMinutes")
    yellow = _value(row, "yellowPointTime")
    crack = _value(row, "firstCrackTime")

    shared = getattr(metric_rules, "phase_shares", None)
    if shared is not None:
        shares = shared(total, yellow, crack)
    elif np.isfinite(total) and total > 0:
        shares = {"drying": yellow / total * 100, "maillard": (crack - yellow) / total * 100,
                  "development": (total - crack) / total * 100}
        shares = {name: value for name, value in shares.items() if np.isfinite(value)}
    else:
        shares = {}
    return {name: round(value, SHOWN_DECIMALS) for name, value in shares.items()}


def metric_value(row, metric: str):
    """Read a target measure off a roast, computed ones included.

    Predictions are made against whatever measure the rule cares about, and some
    of those -- development share, a pattern flag -- are not columns.
    """
    if not metric:
        return np.nan
    if metric in ("development_percent", "drying_percent", "maillard_percent"):
        return _phase_percentages(row).get(metric.split("_")[0], np.nan)
    if metric.startswith("flag"):
        return 1.0 if row.get(metric) else 0.0
    return _value(row, metric)


# ---------------------------------------------------------------------------
# One review pass, one read of the small tables
#
# Every rule asks for the rule scoreboard, and several ask for a learned effect
# size. Reviewing forty coffees that way is some six hundred round trips, which
# on a database in another country is the difference between a second and a
# spinner that looks like a hang. Inside a pass the two small tables are read
# once and shared.
#
# Thread-local because Streamlit serves several people from one process.
# ---------------------------------------------------------------------------

_pass = threading.local()


@contextmanager
def review_pass(path: str | None = None):
    """Read the scoreboard and the effect sizes once for everything inside."""
    previous = getattr(_pass, "held", None)
    _pass.held = {"board": store.rule_scoreboard(path),
                  "effects": {row["key"]: dict(row)
                              for _, row in store.effects(path).iterrows()}}
    try:
        yield _pass.held
    finally:
        _pass.held = previous


def held(name):
    """What this pass already read, if a pass is open."""
    return (getattr(_pass, "held", None) or {}).get(name)


def _confidence(rule_id: str, evidence: float, path: str | None = None) -> float:
    """Blend the evidence behind the magnitude with the rule's own track record."""
    board = held("board")
    if board is None:
        board = store.rule_scoreboard(path)
    record = 0.5
    if not board.empty and rule_id in set(board["rule_id"]):
        row = board[board["rule_id"] == rule_id].iloc[0]
        if pd.notna(row.get("hit_rate")):
            tested = (row.get("achieved", 0) or 0) + (row.get("partial", 0) or 0) + (row.get("missed", 0) or 0)
            weight = min(1.0, tested / 5)
            record = (1 - weight) * 0.5 + weight * float(row["hit_rate"])
    return round(min(0.95, 0.35 + 0.35 * evidence + 0.3 * record), 2)


def _step_text(steps: float, control: str) -> str:
    if steps == 0:
        return f"hold {control} where it is"
    direction = "up" if steps > 0 else "down"
    amount = abs(steps)
    unit = "step" if amount == 1 else "steps"
    shown = f"{amount:g}"
    return f"{control} {direction} {shown} {unit}"


# ---------------------------------------------------------------------------
# Turning advice into something you can actually do
#
# The knobs move in whole steps. Nobody sets an average, and nobody sets 1.5.
# Every rule that used to say "power up 1.5 through Maillard" now names one
# control, one moment and one whole number, worked out against what that roast
# actually had set at that moment.
# ---------------------------------------------------------------------------

# Where in the roast each phase's change belongs, and what to call that moment.
PHASE_MOMENT = {
    "drying": ("turningPointTime", "just after the turn"),
    "Maillard": ("yellowPointTime", "at yellowing"),
    "development": ("firstCrackTime", "at first crack"),
    "charge": (None, "at charge"),
}


def moment_of(row, phase: str) -> tuple[float, str]:
    """The time a change for this phase should be made, and how to say it."""
    column, said = PHASE_MOMENT.get(phase, ("yellowPointTime", "at yellowing"))
    if column is None:
        return 0.0, said
    at = _value(row, column)
    return (at if np.isfinite(at) else 0.0), said


def control_moves(row, context, control: str, phase: str, steps: float,
                  why: str) -> list[dict]:
    """One concrete change: control, time, and the setting to use instead.

    Reads the setting that was in force at that moment from the roast's own
    control timeline, so the advice is "power 8 → 7", not "power down a bit".
    """
    from . import recipe

    moves = context.get("moves")
    at, _said = moment_of(row, phase)
    settings = recipe.settings_at(moves, at) if moves is not None else {}

    # The temperature at that moment comes from the curve, not from the nearest
    # control change — a recipe written in temperature has to name the right one.
    curve = context.get("curve")
    if curve is not None and not getattr(curve, "empty", True):
        settings["bt"], settings["ibts"] = recipe.temperatures_at(curve, at)
    current = settings.get(control, _value(row, f"{control}{phase.capitalize()}"))
    change = recipe.move(control, at, current, steps, why,
                         bean_temp=settings.get("bt"), drum_temp=settings.get("ibts"))
    if change:
        return [change]

    # The control is already at the end of its range. Saying "power up 1" to
    # somebody sitting at power 9 is worse than saying nothing, so the advice
    # moves to the other lever: on this machine, less air holds more heat in.
    if control == "power" and np.isfinite(_value({"v": current}, "v")):
        other = recipe.move(
            "fan", at, settings.get("fan"), -1 if steps > 0 else 1,
            f"{why} — power is already at {float(current):.0f}, so this has to come from "
            "airflow instead",
            bean_temp=settings.get("bt"), drum_temp=settings.get("ibts"))
        if other:
            return [other]
    return []


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


def rule_development(row, context, path=None):
    phases = _phase_percentages(row)
    development = phases.get("development")
    if development is None or not np.isfinite(development):
        return None

    # Against this bean's own history where there is one; against the configured
    # band otherwise. Which of the two was used is said out loud, because they
    # deserve different amounts of trust.
    baseline = (context.get("baseline") or {}).get("development_ratio")
    if baseline is not None and np.isfinite(_value({"b": baseline}, "b")):
        baseline = _shown(baseline)
        if abs(development - baseline) < 2.5:
            return None
        low = high = baseline
        basis = f"your own {int(context['baseline']['roasts'])} roasts of this coffee"
    else:
        low, high = context["targets"]["development_percent"]
        basis = f"this app's configured band of {low:.0f}–{high:.0f}%"
        if low <= development <= high:
            return None

    total = _value(row, "totalRoastMinutes")
    crack = _value(row, "firstCrackTime")
    target = low if development < low else high
    wanted_minutes = crack * (target / (100 - target)) - (total - crack)

    if development < low:
        rule_id = "development_short"
        headline = "Development is short"
        finding = (f"First crack to drop is {development:.1f}% of the roast "
                   f"({total - crack:.1f} of {total:.1f} min), against {basis}. "
                   "Short development is associated with sharp, cereal-like cups — "
                   "association, not diagnosis: cup it before believing it.")
        action = (f"Carry this roast {wanted_minutes:+.1f} min further before dropping — "
                  f"about {(total + wanted_minutes):.1f} min total. Take the heat down a "
                  "step at first crack so the extra time is gentle rather than hot.")
    else:
        rule_id = "development_long"
        headline = "Development is running long"
        finding = (f"First crack to drop is {development:.1f}% of the roast "
                   f"({total - crack:.1f} of {total:.1f} min), against {basis}. "
                   "Long development is associated with flatter, more muted cups — "
                   "association, not diagnosis: cup it before believing it.")
        action = (f"Drop {abs(wanted_minutes):.1f} min earlier — about "
                  f"{(total + wanted_minutes):.1f} min total. If first crack keeps arriving "
                  "late, the fix belongs earlier in the roast rather than at the drop.")

    # Development is a *when*, not a knob: the change is where the drop lands.
    # The heat move that goes with it is a whole step at first crack.
    drop_at = total + wanted_minutes
    moves = [{"control": "drop", "at": float(drop_at), "clock": _clock(drop_at),
              "from": float(total), "to": float(drop_at), "step": wanted_minutes,
              "why": ("drop later, to lengthen development" if wanted_minutes > 0
                      else "drop earlier, to shorten development")}]
    if wanted_minutes > 0:
        moves += control_moves(row, context, "power", "development", -1,
                               "so the extra time is gentle rather than hot")

    return {
        "rule_id": rule_id, "headline": headline, "finding": finding, "action": action,
        "moves": moves,
        "reason": ("Development share sets how far the roast travels after first crack. "
                   f"Compared against {basis}."),
        "target_metric": "development_percent",
        "current_value": development, "predicted_value": float(target),
        "direction": "up" if development < low else "down",
        "confidence": _confidence(rule_id, 0.6, path),
        "basis": "phase targets",
    }


def rule_crash(row, context, path=None):
    if not bool(row.get("flagRoRCrash")):
        return None

    current = _value(row, "rorAtFirstCrack")
    key = "power@Maillard->avgRoRMaillard"
    wanted = 1.5
    steps, evidence, pairs = learning.steps_to_move(key, wanted, path)

    return {
        "rule_id": "ror_crash",
        "headline": "Rate of rise crashed after first crack",
        "finding": (
            (f"Rate of rise fell {_value(row, 'crashPercent'):.0f}% from its settled "
             f"pre-crack level"
             if np.isfinite(_value(row, "crashPercent")) else
             "Rate of rise fell away once first crack started")
            + (f", to {_value(row, 'crashTroughROR'):.1f} °C/min"
               if np.isfinite(_value(row, "crashTroughROR")) else "")
            + ". Practitioners associate a pronounced crash there with hollow or baked "
              "character; the curve cannot establish that on its own."),
        "action": ("The cure is earlier, not at the crack: more heat *before* first crack "
                   "prevents a crash, more heat after it causes a flick."),
        "moves": control_moves(row, context, "power", "Maillard", abs(steps) or 1,
                               "carry more momentum into first crack"),
        "reason": ("A crash means the bean's exothermic phase outran the heat still going in. "
                   "More heat applied after the crack causes a flick; more heat applied before "
                   "it prevents the crash."),
        "target_metric": "rorAtFirstCrack",
        "current_value": current,
        "predicted_value": (current + wanted) if np.isfinite(current) else np.nan,
        "direction": "up",
        "confidence": _confidence("ror_crash", evidence, path),
        "basis": f"{pairs} paired roast(s)" if pairs else "textbook effect size",
    }


def rule_flick(row, context, path=None):
    if not bool(row.get("flagRoRFlick")) or bool(row.get("flagRoRCrash")):
        return None
    last_increase = _value(row, "lastPowerIncreaseTime")
    return {
        "rule_id": "ror_flick",
        "headline": "Rate of rise flicked back up late in the roast",
        "finding": ("Rate of rise turned upward again near the end"
                    + (f", after the power increase at {last_increase:.1f} min" if np.isfinite(last_increase) else "")
                    + ". A rising rate through development bakes in harshness."),
        "action": ("Leave the heat alone after first crack. If the roast feels like it is "
                   "stalling there, take the fan down a step instead — it holds momentum "
                   "without pushing the curve back up."),
        "moves": control_moves(row, context, "fan", "development", -1,
                               "hold momentum without pushing the curve back up"),
        "reason": "Fan changes move heat transfer gently; power changes late are blunt.",
        "target_metric": "flagRoRFlick", "current_value": 1.0, "predicted_value": 0.0,
        "direction": "down",
        "confidence": _confidence("ror_flick", 0.4, path),
        "basis": "roasting practice",
    }


def rule_stall(row, context, path=None):
    if not bool(row.get("flagStall")):
        return None
    current = _value(row, "avgRoRMaillard")
    key = "power@Maillard->avgRoRMaillard"
    wanted = 2.0
    steps, evidence, pairs = learning.steps_to_move(key, wanted, path)
    return {
        "rule_id": "stall",
        "headline": "The roast stalled before first crack",
        "finding": ("Rate of rise sat near zero before first crack"
                    + (f" — {current:.1f} °C/min through Maillard" if np.isfinite(current) else "")
                    + ". A stall there bakes the sugars rather than developing them."),
        "action": ("Apply it by yellowing rather than after the curve has already "
                   "flattened — heat added to a stall arrives too late to recover it."),
        "moves": control_moves(row, context, "power", "Maillard", abs(steps) or 1,
                               "keep the curve moving through Maillard"),
        "reason": "Heat added after a stall arrives too late to recover the phase.",
        "target_metric": "avgRoRMaillard",
        "current_value": current,
        "predicted_value": (current + wanted) if np.isfinite(current) else np.nan,
        "direction": "up",
        "confidence": _confidence("stall", evidence, path),
        "basis": f"{pairs} paired roast(s)" if pairs else "textbook effect size",
    }


def rule_drying(row, context, path=None):
    phases = _phase_percentages(row)
    drying = phases.get("drying")
    if drying is None or not np.isfinite(drying):
        return None
    low, high = context["targets"]["drying_percent"]
    if low <= drying <= high:
        return None

    yellow = _value(row, "yellowPointTime")
    total = _value(row, "totalRoastMinutes")
    target = low if drying < low else high
    wanted_minutes = total * target / 100 - yellow
    key = "power@Drying->yellowPointTime"
    steps, evidence, pairs = learning.steps_to_move(key, wanted_minutes, path)

    fast = drying < low
    return {
        "rule_id": "drying_fast" if fast else "drying_slow",
        "headline": "Drying went through too quickly" if fast else "Drying is dragging",
        "finding": (f"Yellowing at {yellow:.1f} min is {drying:.1f}% of a {total:.1f} min roast. "
                    + ("Rushing the free moisture out tends to leave grassy, underbaked notes."
                       if fast else
                       "A long drying phase flattens acidity and dulls the cup.")),
        # A learned effect size can round to nothing; the roaster still has to
        # turn something. One whole step in the direction that helps is the floor.
        "moves": control_moves(row, context, "power", "drying",
                               steps if abs(steps) >= 0.5 else (-1 if fast else 1),
                               "bring yellowing back where you want it"),
        "action": (f"Move yellowing to about {_clock(yellow + wanted_minutes)}."
                   + (" Charging a little cooler has the same effect if power is already low."
                      if fast else "")),
        "reason": "Drying share sets how much of the roast is spent driving off water.",
        "target_metric": "yellowPointTime",
        "current_value": yellow, "predicted_value": yellow + wanted_minutes,
        "direction": "up" if fast else "down",
        "confidence": _confidence("drying_fast" if fast else "drying_slow", evidence, path),
        "basis": f"{pairs} paired roast(s)" if pairs else "textbook effect size",
    }


def rule_turning_point(row, context, path=None):
    turning = _shown(_value(row, "turningPointTime"))
    if not np.isfinite(turning):
        return None
    low, high = context["targets"]["turning_point_minutes"]
    if low <= turning <= high:
        return None
    charge = _value(row, "drumChargeTemperature")
    late = turning > high
    return {
        "rule_id": "turning_point_late" if late else "turning_point_early",
        "headline": "Turning point is late" if late else "Turning point is very early",
        "finding": (f"The beans stopped cooling at {turning:.1f} min"
                    + (f", with the drum charged at {charge:.0f} °C" if np.isfinite(charge) else "")
                    + (". A late turn means the charge could not carry the batch."
                       if late else
                       ". A very early turn usually means a small batch or a hot charge.")),
        "action": (f"Charge {'hotter' if late else 'cooler'} by 8–10 °C"
                   + (f" (you charged at {charge:.0f} °C)" if np.isfinite(charge) else "")
                   + (", or take a little weight out of the batch." if late else ".")),
        "moves": [{"control": "charge temperature", "at": 0.0, "clock": "0:00",
                   "from": (float(charge) if np.isfinite(charge) else None),
                   "to": (float(charge + (9 if late else -9)) if np.isfinite(charge) else None),
                   "step": (9 if late else -9),
                   "why": "so the batch turns where you want it"}],
        "reason": "The turning point is where the drum's stored heat and the batch balance out.",
        "target_metric": "turningPointTime",
        "current_value": turning,
        "predicted_value": float(high if late else low),
        "direction": "down" if late else "up",
        "confidence": _confidence("turning_point_late" if late else "turning_point_early", 0.3, path),
        "basis": "charge behaviour",
    }


def rule_drift(row, context, path=None):
    """How this roast differs from the roaster's own reference for that coffee."""
    reference = context.get("reference")
    if reference is None or reference.get("uid") == row.get("uid"):
        return None

    comparisons = [
        ("firstCrackTime", "first crack", "min", 0.4),
        ("totalRoastMinutes", "total time", "min", 0.5),
        ("drumDropTemperature", "drop temperature", "°C", 3.0),
        ("developmentTime", "development", "min", 0.3),
    ]
    worst = None
    for column, label, unit, tolerance in comparisons:
        current = _value(row, column)
        target = _value(reference, column)
        if not np.isfinite(current) or not np.isfinite(target):
            continue
        difference = current - target
        if abs(difference) <= tolerance:
            continue
        size = abs(difference) / tolerance
        if worst is None or size > worst["size"]:
            worst = {"column": column, "label": label, "unit": unit,
                     "difference": difference, "current": current, "target": target, "size": size}

    if worst is None:
        return None

    control_hint = ""
    control_fix = ""
    for column, control_label, unit in (("powerMaillard", "power through Maillard", ""),
                                        ("powerDrying", "power through drying", ""),
                                        ("fanDevelopment", "fan after first crack", ""),
                                        ("drumChargeTemperature", "charge temperature", " °C")):
        current = _value(row, column)
        target = _value(reference, column)
        threshold = 4.0 if column == "drumChargeTemperature" else 0.5
        if np.isfinite(current) and np.isfinite(target) and abs(current - target) >= threshold:
            control_hint = (f" You ran {control_label} at {current:.1f}{unit} against "
                            f"{target:.1f}{unit} on the reference — the likeliest cause.")
            control_fix = (f" Start by matching the reference's {control_label}: "
                           f"{target:.1f}{unit} rather than {current:.1f}{unit}.")
            break

    return {
        "rule_id": "drift_from_reference",
        "headline": f"Drifted from your reference roast: {worst['label']}",
        "finding": (f"{worst['label'].capitalize()} came in at {worst['current']:.1f} {worst['unit']} "
                    f"against {worst['target']:.1f} on your reference roast for this coffee, "
                    f"a difference of {worst['difference']:+.1f} {worst['unit']}.{control_hint}"),
        "action": (f"Bring {worst['label']} back toward {worst['target']:.1f} {worst['unit']}."
                   + control_fix),
        "reason": "Repeatability is measured against the roast you decided was right.",
        "target_metric": worst["column"],
        "current_value": worst["current"], "predicted_value": worst["target"],
        "direction": "down" if worst["difference"] > 0 else "up",
        "confidence": _confidence("drift_from_reference", 0.5, path),
        "basis": "your reference roast",
    }


def rule_weight_loss(row, context, path=None):
    loss = _shown(_value(row, "weightLossPercent"))
    if not np.isfinite(loss):
        return None
    low, high = context["targets"]["weight_loss_percent"]
    if low <= loss <= high:
        return None
    development = _phase_percentages(row).get("development")
    heavy = loss > high
    return {
        "rule_id": "weight_loss_high" if heavy else "weight_loss_low",
        "headline": "Weight loss is high" if heavy else "Weight loss is low",
        "finding": (f"This roast lost {loss:.1f}% of its weight"
                    + (f", with {development:.1f}% of the roast after first crack"
                       if development else "")
                    + (". That is dark territory for a filter roast." if heavy
                       else ". The beans may not be fully developed inside.")),
        "action": ("Drop earlier or cooler." if heavy else
                   "Carry the roast a little further, or drop 3–4 °C hotter."),
        "reason": "Weight loss is the most direct proxy for how far a roast went.",
        "target_metric": "weightLossPercent",
        "current_value": loss, "predicted_value": float(high if heavy else low),
        "direction": "down" if heavy else "up",
        "confidence": _confidence("weight_loss_high" if heavy else "weight_loss_low", 0.4, path),
        "basis": "roast level bands",
    }


RULES = [rule_crash, rule_stall, rule_flick, rule_development, rule_drying,
         rule_turning_point, rule_drift, rule_weight_loss]


# ---------------------------------------------------------------------------
# Running the coach
# ---------------------------------------------------------------------------


def build_context(roasts: pd.DataFrame, row, path: str | None = None) -> dict:
    """Everything a rule needs beyond the roast itself."""
    coffee = row.get("coffee")
    same = roasts[roasts["coffee"] == coffee] if coffee else roasts.iloc[0:0]

    reference = None
    marked = same[same.get("is_reference", 0) == 1] if not same.empty else same
    if not marked.empty:
        reference = marked.iloc[-1]
    elif len(same) > 1:
        # No reference chosen yet: the best-rated roast, else the most recent other one.
        rated = same[same["rating"].notna()] if "rating" in same else same.iloc[0:0]
        pool = rated if not rated.empty else same
        pool = pool[pool["uid"] != row.get("uid")]
        if not pool.empty:
            reference = pool.sort_values("rating" if not rated.empty else "roasted_at").iloc[-1]

    # What this bean usually does on this machine. Where it exists, it beats every
    # universal target the app carries — and the rules say which they used.
    from . import diagnostics

    baseline = diagnostics.baseline_for(roasts, coffee, exclude=row.get("uid"))

    # The roast's own control moves, so advice can say "power 8 → 7 at 4:12"
    # instead of naming a phase average nobody ever set.
    moves = curve = None
    try:
        from . import recipe, store

        curve = store.load_curve(row.get("uid"), path)
        moves = recipe.timeline(curve, row)
    except Exception:
        moves = curve = None

    return {"targets": TARGETS, "reference": reference, "coffee": coffee,
            "history": same, "baseline": baseline, "moves": moves, "curve": curve,
            "path": path}


def review(roasts: pd.DataFrame, roast_id: str, path: str | None = None) -> list[dict]:
    """Every recommendation for one roast, most confident first."""
    matches = roasts[roasts["uid"] == roast_id]
    if matches.empty:
        return []
    row = matches.iloc[0]
    context = build_context(roasts, row, path)

    found = []
    for rule in RULES:
        try:
            item = rule(row, context, path)
        except Exception:
            item = None
        if item:
            found.append(item)
    return sorted(found, key=lambda item: -(item.get("confidence") or 0))


def review_all(roasts: pd.DataFrame, roast_ids, path: str | None = None, progress=None):
    """Review several roasts in one pass, reading the shared tables once.

    Forty coffees reviewed one at a time is some six hundred small queries; in a
    pass it is two. On a database in another country that is the difference
    between a moment and a spinner nobody trusts.
    """
    ids = list(roast_ids)
    done = []
    with review_pass(path):
        for position, roast_id in enumerate(ids):
            done.append(review_and_save(roasts, roast_id, path))
            if progress:
                progress(position + 1, len(ids))
    return done


def review_and_save(roasts: pd.DataFrame, roast_id: str, path: str | None = None) -> list[dict]:
    items = review(roasts, roast_id, path)
    row = roasts[roasts["uid"] == roast_id].iloc[0]
    store.save_recommendations(roast_id, row.get("coffee"), items, path)
    return items


def evaluate(recommendation: pd.Series, follow_up: pd.Series) -> dict:
    """Did the change do what the coach said it would?

    Achieved means the measure reached the predicted value or moved past it.
    Partial means it moved the right way but fell short. Missed means it did not
    move, or went the other way.
    """
    metric = recommendation.get("target_metric")
    predicted = recommendation.get("predicted_value")
    before = recommendation.get("current_value")
    observed = metric_value(follow_up, metric)

    try:
        observed = float(observed)
        predicted = float(predicted)
        before = float(before)
    except (TypeError, ValueError):
        return {"outcome": "unknown", "observed": None, "moved": None, "intended": None}

    intended = predicted - before
    moved = observed - before
    if intended == 0:
        return {"outcome": "unknown", "observed": observed, "moved": moved, "intended": intended}

    share = moved / intended
    if share >= 1 - TOLERANCE:
        outcome = "achieved"
    elif share > 0.15:
        outcome = "partial"
    else:
        outcome = "missed"
    return {"outcome": outcome, "observed": observed, "moved": moved,
            "intended": intended, "share": share}


def follow_up_for(roasts: pd.DataFrame, recommendation: pd.Series) -> pd.Series | None:
    """The roast that would have tested a recommendation: the next of that coffee."""
    coffee = recommendation.get("coffee")
    created = pd.to_datetime(recommendation.get("created_at"), errors="coerce", utc=True)
    source = roasts[roasts["uid"] == recommendation.get("roast_id")]
    if source.empty:
        return None
    after = source.iloc[0]["roasted_at"]

    candidates = roasts[(roasts["coffee"] == coffee) & (roasts["roasted_at"] > after)]
    candidates = candidates.sort_values("roasted_at")
    if candidates.empty:
        return None
    if pd.notna(created):
        later = candidates[candidates["roasted_at"].dt.tz_localize("UTC", nonexistent="shift_forward",
                                                                   ambiguous=False)
                           >= created] if candidates["roasted_at"].dt.tz is None else \
            candidates[candidates["roasted_at"] >= created]
        if not later.empty:
            return later.iloc[0]
    return candidates.iloc[0]


def auto_evaluate(roasts: pd.DataFrame, path: str | None = None) -> dict:
    """Grade every applied recommendation that now has a roast to test it."""
    pending = store.recommendations(status="applied", path=path)
    report = {"evaluated": 0, "achieved": 0, "partial": 0, "missed": 0}

    for _, recommendation in pending.iterrows():
        follow_up = None
        if recommendation.get("applied_roast_id"):
            matches = roasts[roasts["uid"] == recommendation["applied_roast_id"]]
            follow_up = matches.iloc[0] if not matches.empty else None
        if follow_up is None:
            follow_up = follow_up_for(roasts, recommendation)
        if follow_up is None:
            continue

        result = evaluate(recommendation, follow_up)
        if result["outcome"] == "unknown":
            continue
        store.record_outcome(int(recommendation["id"]), follow_up["uid"],
                             result["observed"], result["outcome"], path)
        report["evaluated"] += 1
        report[result["outcome"]] += 1

    return report


def plan_for_coffee(roasts: pd.DataFrame, coffee: str, path: str | None = None) -> dict:
    """What to do on the next roast of this coffee."""
    same = roasts[roasts["coffee"] == coffee].sort_values("roasted_at")
    if same.empty:
        return {"coffee": coffee, "items": [], "based_on": None}
    latest = same.iloc[-1]
    items = review(roasts, latest["uid"], path)
    return {"coffee": coffee, "items": items[:3], "based_on": latest,
            "roasts": len(same)}
