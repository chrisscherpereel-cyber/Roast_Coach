"""
Building a roast, rather than reading one.

Everything else in this app looks backwards: here is what happened, here is what
it means, here is one thing to change next time. This module looks forwards. It
takes a roast that worked — or nothing at all — and writes the recipe for a roast
that has not happened yet, at a different roast level, or a different batch size,
or both.

Two rules run through it.

**Every number says where it came from.** A step is `measured` when it comes from
this roaster's own roasts, `learned` when it comes from an effect size the app
has fitted to their machine, `library` when it comes from a book, and `assumed`
when it is the best available reasoning and nobody has checked. The difference
between the first and the last is the difference between advice and invention,
and a roaster standing at a machine deserves to know which they are holding.

**The plan is written against the IBTS**, because that is the number on the
Bullet's screen and the one its recipes are written against. Times are carried
along because they say whether a roast is on pace, but nothing here asks anybody
to make a change at a moment on a clock.

A plan is a plain dict, so it stores as JSON and survives this file changing::

    {"bean": …, "level": "Full City", "weight": 800,
     "charge": {"ibts": 210, "power": 8, "fan": 2, "drum": 9},
     "steps": [{"ibts": 176, "control": "power", "to": 7, "why": …, "from": …}],
     "drop": {"ibts": 220, "expect_minutes": 11.2},
     "provenance": [{"what": …, "basis": "measured", "detail": …}]}
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import knowledge

# What this file can do — see the note in store.py.
#   1  plans from a roast or from nothing; roast level and batch size transforms;
#      RoasTime export
VERSION = 1

CONTROLS = ("power", "fan", "drum")

# The Bullet's settings are whole numbers in these ranges.
LIMITS = {"power": (0, 9), "fan": (0, 9), "drum": (1, 9)}

# How sure a number is, worst to best. The app shows this on every step.
BASIS = ("assumed", "library", "learned", "measured")


# ---------------------------------------------------------------------------
# Roast level
# ---------------------------------------------------------------------------

# Brault's ladder, converted to °C and stated as the IBTS temperature to drop at.
# His own caveats are carried with it: the temperatures are relative to one
# another, they were taken on a ten-pound drum roaster, and probe placement moves
# any of them by several degrees. They are a starting point for a level this
# roaster has never taken a coffee to — not a target to trust over their own
# record of a level they have.
#
# `confidence` is Brault's, not ours: "Arabic" is not in his book at all, and
# New England and American are a naming aside rather than defined levels.
LEVELS = {
    "Arabic":      {"f": None,        "confidence": "none",
                    "note": "Not in any source in the library. Placed below Cinnamon "
                            "because that is where the name is used, and nothing here "
                            "supports a temperature for it."},
    "Cinnamon":    {"f": (395, 410),  "confidence": "high",
                    "note": "The colour entering first crack."},
    "New England": {"f": (410, 421),  "confidence": "low",
                    "note": "Brault mentions it once, as a name some roasters use for "
                            "the lighter side of Light. No temperature of its own."},
    "American":    {"f": (414, 425),  "confidence": "low",
                    "note": "Same status as New England, and he does not distinguish "
                            "the two."},
    "City":        {"f": (425, 435),  "confidence": "high",
                    "note": "About a minute after the tail of first crack."},
    "Full City":   {"f": (435, 445),  "confidence": "high",
                    "note": "Two minutes or more past first crack."},
    "Vienna":      {"f": (445, 455),  "confidence": "medium",
                    "note": "Two to three minutes after second crack starts."},
    "French":      {"f": (460, 465),  "confidence": "high",
                    "note": "Brown-black but not black; the toasty taste is the test."},
    "Italian":     {"f": (470, 480),  "confidence": "high",
                    "note": "Black and carbonised. Brault calls it a fire risk."},
}

ORDER = list(LEVELS)


# The Bullet's IBTS is an infrared sensor reading the bean surface inside a 1 kg
# drum. Brault's ladder is bean-probe temperature on a ten-pound drum roaster.
# They are different instruments on different machines, and converting his
# Fahrenheit to Celsius does **not** produce a number to aim this machine at:
# his French roast comes out at 239 °C, and this roaster's IBTS drops sit between
# 207 and 218 °C. Telling somebody to take a Bullet to 239 °C IBTS is not a dark
# roast, it is a fire.
#
# So the ladder is used only for the *gaps between its rungs* — how much further
# one level is than another — applied as an offset to a temperature this machine
# has actually reached. And the result is capped, because an extrapolation that
# runs off the end of the roaster's own experience should stop at the edge of it
# and say so.
CEILING = 232.0

# Where second crack tends to sit on a Bullet's IBTS. Past this the roast is in
# the region Brault calls a fire risk, and nothing in the library establishes a
# safe number for this machine.
SECOND_CRACK = 225.0


def _c(fahrenheit) -> float:
    return (float(fahrenheit) - 32.0) * 5.0 / 9.0


def level_temperature(level: str) -> tuple:
    """Where the library puts a level, **on its own scale**, and how sure it is.

    This is a bean-probe temperature on a drum roaster. It is not a number to aim
    a Bullet's IBTS at — see the note on :data:`CEILING`. Use :func:`level_gap`
    to get the distance between two levels, which is the part that travels.
    """
    found = LEVELS.get(str(level).strip())
    if not found or not found.get("f"):
        return (np.nan, "none")
    low, high = found["f"]
    return (round((_c(low) + _c(high)) / 2.0, 1), found["confidence"])


def level_gap(from_level: str, to_level: str) -> float:
    """How much further one level is than another, in °C, on the library's scale.

    A difference between two rungs of one ladder survives being carried to
    another instrument far better than either rung's absolute value does. It is
    still an approximation, and the app says so.
    """
    was, _ = level_temperature(from_level)
    now, _ = level_temperature(to_level)
    if not (np.isfinite(was) and np.isfinite(now)):
        return np.nan
    return round(float(now - was), 1)


def level_from_history(level: str, history) -> tuple:
    """What *this* roaster's own roasts of that level dropped at.

    Their record beats any book: a level is a colour, colour is what the roaster
    is actually judging, and their machine's IBTS reads what it reads. Two roasts
    is not many, and it is enough to prefer over a number taken on somebody
    else's ten-pound drum.
    """
    if history is None or getattr(history, "empty", True):
        return (np.nan, 0)
    if "roast_level" not in history or "drumDropTemperature" not in history:
        return (np.nan, 0)
    theirs = history[history["roast_level"].astype(str).str.strip().str.lower()
                     == str(level).strip().lower()]
    drops = pd.to_numeric(theirs.get("drumDropTemperature"), errors="coerce").dropna()
    if drops.empty:
        return (np.nan, 0)
    return (round(float(drops.median()), 1), int(len(drops)))


def darker(from_level: str, to_level: str) -> int:
    """+1 if the target is darker, -1 lighter, 0 the same or unknown."""
    try:
        return int(np.sign(ORDER.index(to_level) - ORDER.index(from_level)))
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Making a plan
# ---------------------------------------------------------------------------


def _whole(value, control: str):
    low, high = LIMITS.get(control, (0, 9))
    try:
        return int(min(high, max(low, round(float(value)))))
    except (TypeError, ValueError):
        return None


def from_roast(row, moves) -> dict:
    """The recipe that would reproduce a roast that has already happened.

    `moves` is :func:`recipe.timeline` — every control change with the IBTS
    temperature it was made at. This is the strongest starting point there is:
    not a profile somebody wrote, but one this machine has actually run.
    """
    row = dict(row)
    plan = {
        "bean": _text(row.get("bean")) or _text(row.get("coffee")),
        "from_roast": str(row.get("uid") or ""),
        "from_label": str(row.get("label") or ""),
        "level": _text(row.get("roast_level")) or None,
        "weight": _number(row.get("greenWeight")),
        "charge": {}, "steps": [], "drop": {}, "provenance": [],
    }

    opening = {}
    if moves is not None and not getattr(moves, "empty", True):
        first = moves[moves["kind"] == "set"] if "kind" in moves else moves
        for _, item in first.iterrows():
            at = float(item.get("at") or 0)
            control = str(item.get("control") or "")
            value = _whole(item.get("to"), control)
            if control not in CONTROLS or value is None:
                continue
            if at <= 0.2:                      # charge, not a step
                opening[control] = value
                continue
            ibts = _number(item.get("ibts"))
            plan["steps"].append({
                "ibts": ibts, "at": round(at, 2), "control": control, "to": value,
                "from": None, "why": "", "basis": "measured" if ibts else "assumed",
            })

    plan["charge"] = {
        "ibts": _number(row.get("drumChargeTemperature")) or _number(row.get("preheatTemperature")),
        **{control: opening.get(control) for control in CONTROLS if opening.get(control)},
    }
    plan["drop"] = {
        "ibts": _number(row.get("drumDropTemperature")),
        "expect_minutes": _number(row.get("totalRoastMinutes")),
        "first_crack": _number(row.get("firstCrackTime")),
    }
    plan["provenance"].append({
        "what": "the whole plan",
        "basis": "measured",
        "detail": f"Read back off {plan['from_label'] or 'a roast you have run'} — every "
                  "setting at the IBTS temperature it was actually made at.",
    })
    # Steps before the turning point carry no IBTS: the sensor is looking at an
    # empty hot drum, not at beans.
    plan["steps"] = [step for step in plan["steps"] if step.get("ibts")]
    return plan


def blank(bean: str = "", weight: float = 800, level: str = "City") -> dict:
    """A plan for a coffee nobody here has roasted, from the library alone.

    Deliberately plain: a declining rate of rise, one power step down before
    first crack and another into it, dropped at the temperature the library puts
    that level at. It is a place to start and it says so — every line of it is
    `library` or `assumed`, and the first roast run from it replaces the lot.
    """
    drop, sure = level_temperature(level)
    plan = {
        "bean": str(bean or ""), "level": level, "weight": _number(weight) or 800,
        "from_roast": "", "from_label": "",
        "charge": {"ibts": None, "power": 8, "fan": 2, "drum": 9},
        "steps": [
            {"ibts": 150.0, "control": "power", "to": 7, "from": 8,
             "why": "ease off through drying so the rate of rise starts declining",
             "basis": "assumed"},
            {"ibts": 175.0, "control": "fan", "to": 3, "from": 2,
             "why": "more airflow as the bean starts to give off chaff and steam",
             "basis": "assumed"},
            {"ibts": 190.0, "control": "power", "to": 6, "from": 7,
             "why": "take heat out before first crack so it does not race",
             "basis": "assumed"},
        ],
        "drop": {"ibts": drop if np.isfinite(drop) else 215.0,
                 "expect_minutes": None, "first_crack": None},
        "provenance": [
            {"what": "the shape of the roast", "basis": "assumed",
             "detail": "A declining rate of rise with the power eased off twice. This is "
                       "convention, not a measurement — no source in the library "
                       "prescribes a curve shape."},
            {"what": f"dropping at {drop:.0f} °C for {level}" if np.isfinite(drop)
                     else f"the drop temperature for {level}",
             "basis": "library" if np.isfinite(drop) else "assumed",
             "detail": _level_detail(level, sure)},
        ],
    }
    return plan


def _level_detail(level: str, sure: str) -> str:
    found = LEVELS.get(level, {})
    where = knowledge.cite("brault") or "Brault, The Coffee Roaster's Handbook"
    if sure == "none":
        return found.get("note", "Nothing in the library gives a temperature for this level.")
    return (f"{where}, and his caveats come with it: the temperatures are relative to "
            f"one another, taken on a ten-pound drum, and probe placement moves any of "
            f"them by several degrees. His confidence in this one: {sure}. "
            + found.get("note", ""))


def _text(value) -> str:
    """A string, where a pandas NaN is nothing rather than the word "nan"."""
    if value is None:
        return ""
    try:
        if value != value:                       # NaN is not equal to itself
            return ""
    except Exception:
        pass
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none", "<na>") else text


def _number(value):
    try:
        found = float(value)
    except (TypeError, ValueError):
        return None
    return round(found, 1) if np.isfinite(found) else None


# ---------------------------------------------------------------------------
# Changing the roast level
# ---------------------------------------------------------------------------


def to_level(plan: dict, target: str, history=None) -> dict:
    """The same roast taken to a different level — New England to French, say.

    Darker is not simply *later*. Carrying a roast further means holding heat
    into and past first crack, and the further it goes the more the last power
    step matters: too little and it stalls in development, too much and second
    crack arrives before the colour does. So the drop moves to the target
    temperature, the last power step before first crack is eased in the
    direction of travel, and the expected time moves with it.

    Where this roaster has taken *anything* to the target level, that is what
    sets the temperature. Where they have not, the library does, and the plan
    says so on its face.
    """
    made = _copy(plan)
    was = plan.get("level") or "the level you had"
    made["level"] = target

    theirs, count = level_from_history(target, history)
    had = _number(plan.get("drop", {}).get("ibts"))
    capped = False

    if count >= 2 and np.isfinite(theirs):
        drop, basis = theirs, "measured"
        detail = (f"The median IBTS you have actually dropped {count} {target} roast(s) "
                  "at. Your own record beats any book here — it is your machine's "
                  "sensor and your judgement of the colour.")
    elif count == 1 and np.isfinite(theirs):
        drop, basis = theirs, "measured"
        detail = (f"The one {target} roast you have logged dropped at {theirs:.0f} °C. "
                  "One roast is not a pattern; it is still yours.")
    else:
        # Nothing of theirs at this level, so the library sets the *distance*,
        # not the destination. Brault's numbers are bean-probe readings on a
        # ten-pound drum; carrying his absolute values onto a Bullet's IBTS would
        # put French at 239 °C, which is a fire rather than a roast.
        gap = level_gap(was, target)
        _, sure = level_temperature(target)
        if was not in LEVELS:
            # A distance can only be measured from somewhere. Without a level on
            # the roast this plan came from, the ladder has no rung to start at,
            # and guessing one would compound the error rather than hide it.
            drop, basis = had, "assumed"
            detail = (
                f"The roast this came from has no roast level recorded, so there is no "
                f"rung to measure {target} from — the drop is left where it was. Put a "
                "level on that roast in **After the roast** and this becomes an answer "
                "rather than a shrug.")
        elif had is not None and np.isfinite(gap):
            drop, basis = round(had + gap, 1), "library"
            detail = (
                f"You have no {target} roast logged, so this moves the drop by the "
                f"distance between {was} and {target} on Brault's ladder — "
                f"{gap:+.0f} °C — from where this roast actually dropped. His absolute "
                "temperatures are bean-probe readings on a ten-pound drum roaster and "
                "are not comparable with a Bullet's IBTS; the gap between two of his "
                "rungs travels better than either rung does. Judge the colour and the "
                "sound, not this number. " + _level_detail(target, sure))
        else:
            drop, basis = had, "assumed"
            detail = ("Nothing in the library gives a usable temperature for this level "
                      "and you have not roasted one. The drop is left where it was.")

    # An extrapolation that runs off the end of the roaster's own experience
    # should stop at the edge of it. Past second crack on this machine there is
    # nothing in the library that establishes a safe number.
    if drop is not None and float(drop) > CEILING:
        capped, drop = True, CEILING
        detail += (f" Capped at {CEILING:.0f} °C: the ladder wanted to go further, and "
                   "past that this app has nothing that establishes a safe drop for a "
                   "Bullet. Take it further only by watching and listening, a few "
                   "degrees at a time.")

    step = darker(was, target)
    made["drop"] = {**plan.get("drop", {}), "ibts": _number(drop)}
    if capped or (drop is not None and float(drop) >= SECOND_CRACK):
        made["warning"] = (
            f"This plan drops at {float(drop):.0f} °C IBTS, which is at or past where "
            "second crack tends to arrive on a Bullet. Dark roasts are where drum fires "
            "start. Stay with the machine, judge it by colour and sound rather than by "
            "this number, and stop early if the smoke changes.")
    made["provenance"] = list(plan.get("provenance", [])) + [{
        "what": f"{was} → {target}: dropping at "
                + (f"{float(drop):.0f} °C IBTS" if drop else "the same temperature"),
        "basis": basis, "detail": detail,
    }]

    # Where the roast is going darker, the last power move before first crack is
    # eased so there is heat left to carry it; going lighter, it comes off
    # earlier and harder.
    powers = [item for item in made["steps"] if item["control"] == "power"]
    if step and powers:
        last = powers[-1]
        moved = _whole((last["to"] or 0) + step, "power")
        if moved is not None and moved != last["to"]:
            last["from"], last["to"] = last["to"], moved
            last["why"] = ("carry more heat into first crack, since the roast has "
                           "further to go" if step > 0 else
                           "take the heat off sooner, since the roast is stopping earlier")
            last["basis"] = "assumed"
            made["provenance"].append({
                "what": f"power {last['from']} → {last['to']} at "
                        f"{last.get('ibts') or '—'} °C",
                "basis": "assumed",
                "detail": "One step, in the direction of travel. Nothing in the library "
                          "says how much power a level change is worth; this is the "
                          "smallest move the machine can make, and the roast that "
                          "follows will say whether it was enough.",
            })

    # The time moves with the temperature, roughly a minute per ten degrees of
    # IBTS in development. Stated as an expectation, not an instruction.
    old_drop = _number(plan.get("drop", {}).get("ibts"))
    if old_drop and drop and plan.get("drop", {}).get("expect_minutes"):
        shift = (float(drop) - old_drop) / 10.0
        made["drop"]["expect_minutes"] = round(
            float(plan["drop"]["expect_minutes"]) + shift, 1)
        made["provenance"].append({
            "what": f"expect about {made['drop']['expect_minutes']:.1f} min",
            "basis": "assumed",
            "detail": f"Roughly a minute per 10 °C of IBTS at the end of the roast, "
                      f"from the {abs(shift):.1f} min this level change implies. It is "
                      "an expectation for judging pace, not a target to roast to.",
        })
    return made


# ---------------------------------------------------------------------------
# Changing the batch size
# ---------------------------------------------------------------------------


def batch_ratio_from_history(history, bean: str | None = None) -> tuple:
    """How this machine has actually behaved across batch sizes.

    The honest way to answer "what is this recipe at 500 g" is to look at what
    the roaster did the last time they changed batch size. Where there are roasts
    at two sizes, the power settings at the same point of the roast give the
    exponent directly: if halving the charge went with 0.8 of the power, the
    relationship is milder than proportional, and by how much.

    Returns `(exponent, how many roasts it rests on)`. An exponent of 1 would be
    strictly proportional; 0 would be no change at all.
    """
    if history is None or getattr(history, "empty", True):
        return (np.nan, 0)
    frame = history
    if bean and "bean" in frame:
        same = frame[frame["bean"].astype(str) == str(bean)]
        if len(same) >= 4:
            frame = same

    weights = pd.to_numeric(frame.get("greenWeight"), errors="coerce")
    power = pd.to_numeric(frame.get("avgPowerMaillard", frame.get("avgPower")),
                          errors="coerce")
    usable = pd.DataFrame({"weight": weights, "power": power}).dropna()
    usable = usable[(usable["weight"] > 50) & (usable["power"] > 0)]
    if usable["weight"].nunique() < 2 or len(usable) < 4:
        return (np.nan, 0)

    # A power law through the log of both — the exponent is the slope.
    slope = np.polyfit(np.log(usable["weight"]), np.log(usable["power"]), 1)[0]
    if not np.isfinite(slope):
        return (np.nan, 0)
    return (round(float(min(1.2, max(0.0, slope))), 2), int(len(usable)))


def to_batch(plan: dict, weight: float, history=None, exponent: float | None = None) -> dict:
    """The same roast at a different batch size.

    The physics is one sentence: the drum puts in about the same heat at a given
    power setting, and there is less coffee to absorb it, so the same setting
    heats a smaller batch faster. Holding the curve means taking power out.

    How much is the question, and it is not answerable from first principles,
    because the heat that goes into the drum and out of the exhaust does not
    scale with the charge. Proportional is too much; nothing is too little. So:

    * where this roaster has roasts at two batch sizes, the exponent is fitted
      from what they actually did, and the answer is `measured`;
    * otherwise it falls back to 0.7 — nearer proportional than not — and says
      plainly that it is `assumed`.

    Charge temperature moves too: less mass takes less heat out of the drum, so
    the same charge temperature overshoots. And the roast shortens, because a
    smaller charge reaches every milestone sooner.
    """
    made = _copy(plan)
    was = _number(plan.get("weight")) or 0
    now = _number(weight) or 0
    if not was or not now or was == now:
        made["weight"] = now or was
        return made

    made["weight"] = now
    ratio = now / was

    fitted, roasts = batch_ratio_from_history(history, plan.get("bean"))
    if exponent is not None:
        power_exponent, basis, detail = (
            float(exponent), "assumed",
            f"You set the exponent to {float(exponent):.2f}. At 1.00 the power moves in "
            "proportion to the charge; at 0 it does not move at all.")
    elif np.isfinite(fitted) and roasts >= 4:
        power_exponent, basis = fitted, "measured"
        detail = (f"Fitted to {roasts} of your own roasts across more than one batch "
                  f"size: on this machine, power has gone as charge^{fitted:.2f}. "
                  "Proportional would be 1.00.")
    else:
        power_exponent, basis = 0.7, "assumed"
        detail = ("No roasts of yours at two batch sizes to fit against, so this uses "
                  "charge^0.70 — less than proportional, because the heat lost to the "
                  "drum and the exhaust does not shrink with the charge. Roast this "
                  "once at the new size and the app will fit the real number.")

    scale = ratio ** power_exponent
    made["provenance"] = list(plan.get("provenance", [])) + [{
        "what": f"{was:.0f} g → {now:.0f} g: power × {scale:.2f}",
        "basis": basis, "detail": detail,
    }]

    for step in made["steps"]:
        if step["control"] != "power":
            continue
        moved = _whole(round(float(step["to"]) * scale), "power")
        if moved is not None and moved != step["to"]:
            step["from"], step["to"] = step["to"], moved
            step["basis"] = basis
    if made["charge"].get("power"):
        made["charge"]["power"] = _whole(
            round(float(made["charge"]["power"]) * scale), "power")

    # A smaller charge takes less heat out of the drum at charge, so the same
    # charge temperature runs hotter. About five degrees per hundred grams.
    if made["charge"].get("ibts"):
        shift = round((now - was) / 100.0 * 5.0, 1)
        made["charge"]["ibts"] = round(float(made["charge"]["ibts"]) + shift, 1)
        made["provenance"].append({
            "what": f"charge at {made['charge']['ibts']:.0f} °C "
                    f"({'+' if shift >= 0 else ''}{shift:.0f} °C)",
            "basis": "assumed",
            "detail": "About 5 °C per 100 g. Less coffee takes less heat out of the "
                      "drum, so the same charge temperature runs hotter than it did.",
        })

    if made["drop"].get("expect_minutes"):
        made["drop"]["expect_minutes"] = round(
            float(made["drop"]["expect_minutes"]) * (ratio ** 0.25), 1)
        made["provenance"].append({
            "what": f"expect about {made['drop']['expect_minutes']:.1f} min",
            "basis": "assumed",
            "detail": "A smaller charge reaches every milestone sooner. This is a mild "
                      "shortening, not a proportional one — judge it on the IBTS, not "
                      "the clock.",
        })
    return made


def _copy(plan: dict) -> dict:
    made = dict(plan)
    made["charge"] = dict(plan.get("charge") or {})
    made["drop"] = dict(plan.get("drop") or {})
    made["steps"] = [dict(step) for step in plan.get("steps") or []]
    made["provenance"] = [dict(item) for item in plan.get("provenance") or []]
    return made


def confidence(plan: dict) -> str:
    """The weakest thing the plan rests on, which is what it rests on."""
    found = [item.get("basis", "assumed") for item in plan.get("provenance") or []]
    found += [step.get("basis", "assumed") for step in plan.get("steps") or []]
    if not found:
        return "assumed"
    return min(found, key=lambda basis: BASIS.index(basis) if basis in BASIS else 0)


# ---------------------------------------------------------------------------
# Getting it out of the app
# ---------------------------------------------------------------------------


def as_table(plan: dict) -> pd.DataFrame:
    """The plan as a list to read at the machine, IBTS first."""
    rows = [{
        "IBTS": _degrees(plan.get("charge", {}).get("ibts")),
        "control": "charge",
        "set to": " · ".join(f"{control} {plan['charge'][control]}"
                             for control in CONTROLS if plan.get("charge", {}).get(control)),
        "was": "", "why": "the settings the drum is turning at when the beans go in",
        "from": "measured" if plan.get("from_roast") else "assumed",
    }]
    for step in sorted(plan.get("steps") or [], key=lambda item: item.get("ibts") or 0):
        rows.append({
            "IBTS": _degrees(step.get("ibts")),
            "control": step.get("control", ""),
            "set to": step.get("to"),
            "was": "" if step.get("from") in (None, step.get("to")) else f"was {step['from']}",
            "why": step.get("why", ""),
            "from": step.get("basis", "assumed"),
        })
    drop = plan.get("drop") or {}
    rows.append({
        "IBTS": _degrees(drop.get("ibts")), "control": "drop", "set to": "",
        "was": "", "from": "",
        "why": (f"expect about {float(drop['expect_minutes']):.1f} min"
                if drop.get("expect_minutes") else "drop here"),
    })
    # Every column a string: this table mixes a settings summary with plain
    # numbers, and Arrow — which is what draws it — will not take a column that
    # is sometimes text and sometimes an integer.
    return pd.DataFrame(rows).astype(str).replace({"None": "", "nan": ""})


def _degrees(value):
    number = _number(value)
    return f"{number:.0f} °C" if number is not None else "—"


# RoasTime writes a recipe as numbers: what to watch (`trigger`), the number to
# watch for (`value`), and what to do (`actions`, each its own code). These are
# those numbers, read off this roaster's own recipes — 0 watches the IBTS, and
# among the actions 0 sets power, 1 the drum, 2 the fan, 4 raises an alert.
ROASTIME_ACTION = {"power": 0, "drum": 1, "fan": 2}
ROASTIME_IBTS_TRIGGER = 0


def as_roastime(plan: dict, name: str = "") -> dict:
    """The plan as a recipe RoasTime can read.

    Written in RoasTime's own shape so it can be imported on the machine. This
    app never writes into RoasTime's folder — the file goes wherever the browser
    puts downloads, and what happens to it after that is the roaster's business.
    """
    steps = []
    for step in sorted(plan.get("steps") or [], key=lambda item: item.get("ibts") or 0):
        action = ROASTIME_ACTION.get(step.get("control"))
        if action is None or step.get("ibts") is None:
            continue
        steps.append([{
            "trigger": ROASTIME_IBTS_TRIGGER, "condition": 0,
            "value": round(float(step["ibts"])),
            "actions": [{"action": action, "value": step.get("to")}],
        }])

    drop = plan.get("drop") or {}
    ending = []
    if drop.get("ibts") is not None:
        ending.append({"trigger": ROASTIME_IBTS_TRIGGER, "condition": 0,
                       "value": round(float(drop["ibts"])),
                       "actions": [{"action": 4, "value": "End Roast Alert"}]})

    charge = plan.get("charge") or {}
    return {
        "name": name or _name(plan),
        "tempMeasurement": "C",
        "weight": plan.get("weight"),
        "preheatTemp": round(float(charge["ibts"])) if charge.get("ibts") else None,
        "startSettings": {control: charge.get(control) for control in CONTROLS
                          if charge.get(control) is not None},
        "events": steps,
        "endSettings": ending,
        "roastCoach": {
            "built_from": plan.get("from_label") or "no roast — built from the library",
            "level": plan.get("level"), "confidence": confidence(plan),
            "note": "Written by Roast Coach. Check it before you roast from it.",
        },
    }


def _name(plan: dict) -> str:
    parts = [plan.get("bean") or "Roast", plan.get("level") or "",
             f"{int(plan['weight'])}g" if plan.get("weight") else ""]
    return " ".join(part for part in parts if part).strip()


def as_sheet(plan: dict) -> str:
    """A page to have beside the roaster, with room to write what happened."""
    charge = plan.get("charge") or {}
    drop = plan.get("drop") or {}
    weight = f"{float(plan['weight']):.0f} g" if plan.get("weight") else "—"
    crack = (f"{float(drop['first_crack']):.1f} min" if drop.get("first_crack") else "—")
    total = (f"{float(drop['expect_minutes']):.1f} min" if drop.get("expect_minutes") else "—")
    lines = [
        f"# {_name(plan)}",
        "",
        f"**Level** {plan.get('level') or '—'}  ·  **Batch** {weight}  ·  "
        f"**Built from** {plan.get('from_label') or 'the library, not a roast'}",
        "",
        f"Everything below is an **IBTS** temperature. This plan is only as good as its "
        f"weakest part, which is: **{confidence(plan)}**.",
        "",
        "## The roast",
        "",
        "| IBTS | do | why | resting on |",
        "| --- | --- | --- | --- |",
    ]
    table = as_table(plan)
    for _, row in table.iterrows():
        doing = (f"{row['control']} {row['set to']}".strip()
                 if row["control"] not in ("charge", "drop")
                 else (row["set to"] or row["control"]))
        lines.append(f"| {row['IBTS']} | {doing} {row['was']} | {row['why']} | {row['from']} |")

    lines += ["", "## Where each number comes from", ""]
    for item in plan.get("provenance") or []:
        lines.append(f"- **{item['what']}** — *{item['basis']}*. {item['detail']}")

    lines += [
        "", "## What actually happened", "",
        "| | planned | actual |",
        "| --- | --- | --- |",
        f"| Charge IBTS | {_degrees(charge.get('ibts'))} | |",
        f"| First crack | {crack} | |",
        f"| Drop IBTS | {_degrees(drop.get('ibts'))} | |",
        f"| Total | {total} | |",
        "| Weight out | | |",
        "| Colour | | |",
        "| Notes | | |",
        "",
        "---",
        "",
        "*Built by Roast Coach. A plan is a hypothesis: roast it, record what happened, "
        "and the app will tell you whether it held.*",
    ]
    return "\n".join(lines)
