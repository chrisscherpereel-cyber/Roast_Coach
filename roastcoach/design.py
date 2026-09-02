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

from . import knowledge, learning

# What this file can do — see the note in store.py.
#   1  plans from a roast or from nothing; roast level and batch size transforms;
#      RoasTime export
#   2  the plan as a curve you can pull about, and the recipe that follows from it
VERSION = 3

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


# ---------------------------------------------------------------------------
# The same page, printed
# ---------------------------------------------------------------------------

# The one-pager exists to be carried to the roaster and written on, so it has to
# print the same everywhere — which markdown does not. This is the same content
# laid out on a real page. Nothing is computed here that :func:`as_sheet` does
# not compute; if the two ever disagree, the plan is the thing to trust.

PAGE_INK = "#1B1613"
PAGE_QUIET = "#6B6259"
PAGE_RULE = "#D8D0C7"
PAGE_ORANGE = "#E8622A"
PAGE_BAND = "#F4EFE9"


def _escape(value) -> str:
    """Text safe to hand a reportlab Paragraph, which reads a little XML."""
    return (str("" if value is None else value)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def as_pdf(plan: dict) -> bytes:
    """The one-pager as a PDF, ready to print and write on."""
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    ink, quiet = colors.HexColor(PAGE_INK), colors.HexColor(PAGE_QUIET)
    rule, band = colors.HexColor(PAGE_RULE), colors.HexColor(PAGE_BAND)

    def style(name, size, leading, colour=ink, font="Helvetica", space=0, after=0):
        return ParagraphStyle(name, fontName=font, fontSize=size, leading=leading,
                              textColor=colour, spaceBefore=space, spaceAfter=after,
                              alignment=TA_LEFT)

    title = style("title", 19, 22, font="Helvetica-Bold", after=2)
    sub = style("sub", 9.5, 13, quiet, after=0)
    heading = style("heading", 11, 13, font="Helvetica-Bold", space=14, after=5)
    body = style("body", 9, 12)
    small = style("small", 8, 10.5, quiet)
    cell = style("cell", 8.5, 11)
    cell_bold = style("cellbold", 8.5, 11, font="Helvetica-Bold")
    head_cell = style("head", 7.5, 10, quiet, font="Helvetica-Bold")

    charge = plan.get("charge") or {}
    drop = plan.get("drop") or {}
    weight = f"{float(plan['weight']):.0f} g" if plan.get("weight") else "—"
    crack = f"{float(drop['first_crack']):.1f} min" if drop.get("first_crack") else "—"
    total = f"{float(drop['expect_minutes']):.1f} min" if drop.get("expect_minutes") else "—"

    flow = [
        Paragraph(_escape(_name(plan)), title),
        Paragraph(
            f"{_escape(plan.get('level') or '—')} &nbsp;·&nbsp; {weight} &nbsp;·&nbsp; "
            f"built from {_escape(plan.get('from_label') or 'the library, not a roast')}",
            sub),
        Spacer(1, 9),
        Paragraph(
            "Every temperature below is an <b>IBTS</b> reading. This plan is only as "
            f"good as its weakest part, which is: <b>{_escape(confidence(plan))}</b>.",
            body),
    ]

    # The roast itself.
    steps = [[Paragraph(text, head_cell)
              for text in ("IBTS", "DO", "WHY", "RESTING ON")]]
    for _, row in as_table(plan).iterrows():
        doing = (f"{row['control']} {row['set to']}".strip()
                 if row["control"] not in ("charge", "drop")
                 else (row["set to"] or row["control"]))
        was = f"  {row['was']}" if row["was"] else ""
        steps.append([
            Paragraph(_escape(row["IBTS"]), cell_bold),
            Paragraph(_escape(doing + was), cell),
            Paragraph(_escape(row["why"]), cell),
            Paragraph(_escape(row["from"]), small),
        ])

    plan_table = Table(steps, colWidths=[0.72 * inch, 1.85 * inch, 3.05 * inch, 0.88 * inch],
                       repeatRows=1, hAlign="LEFT")
    plan_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, ink),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, rule),
        ("BACKGROUND", (0, 1), (-1, 1), band),
        ("BACKGROUND", (0, -1), (-1, -1), band),
        ("LINEABOVE", (0, -1), (-1, -1), 0.9, colors.HexColor(PAGE_ORANGE)),
    ]))
    flow += [Paragraph("The roast", heading), plan_table]

    # Where each number comes from.
    provenance = plan.get("provenance") or []
    if provenance:
        items = []
        for item in provenance:
            items.append(Paragraph(
                f"<b>{_escape(item.get('what'))}</b> — <i>{_escape(item.get('basis'))}</i>. "
                f"{_escape(item.get('detail'))}", body))
            items.append(Spacer(1, 3))
        flow += [Paragraph("Where each number comes from", heading)] + items

    # Room to write.
    blanks = [[Paragraph(text, head_cell) for text in ("", "PLANNED", "ACTUAL")]]
    for label, planned in (("Charge IBTS", _degrees(charge.get("ibts"))),
                           ("First crack", crack),
                           ("Drop IBTS", _degrees(drop.get("ibts"))),
                           ("Total time", total),
                           ("Weight out", ""),
                           ("Colour", ""),
                           ("Notes", "")):
        blanks.append([Paragraph(_escape(label), cell_bold),
                       Paragraph(_escape(planned), cell), ""])

    written = Table(blanks, colWidths=[1.25 * inch, 1.25 * inch, 4.0 * inch],
                    rowHeights=[14] + [22] * 6 + [46], hAlign="LEFT")
    written.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, ink),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, rule),
        ("BACKGROUND", (2, 1), (2, -1), band),
    ]))
    flow += [KeepTogether([Paragraph("What actually happened", heading), written])]

    flow += [
        Spacer(1, 12),
        Paragraph(
            "Built by Roast Coach. A plan is a hypothesis: roast it, record what "
            "happened, and the app will tell you whether it held.", small),
    ]

    def furniture(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(rule)
        canvas.setLineWidth(0.4)
        canvas.line(0.7 * inch, 0.62 * inch, LETTER[0] - 0.7 * inch, 0.62 * inch)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(quiet)
        canvas.drawString(0.7 * inch, 0.45 * inch, "Roast Coach")
        canvas.drawRightString(LETTER[0] - 0.7 * inch, 0.45 * inch,
                               f"page {document.page}")
        canvas.restoreState()

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=LETTER,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.68 * inch, bottomMargin=0.78 * inch,
        title=_name(plan), author="Roast Coach", subject="Roast plan")
    document.build(flow, onFirstPage=furniture, onLaterPages=furniture)
    return buffer.getvalue()


def sheet_name(plan: dict, extension: str = "pdf") -> str:
    """A file name for the one-pager that reads as what it is."""
    stem = "".join(character if character.isalnum() or character in " -_" else ""
                   for character in _name(plan)).strip()
    stem = "-".join(part for part in stem.split() if part).lower() or "roast-plan"
    return f"{stem}.{extension}"


# ---------------------------------------------------------------------------
# The plan as a curve
# ---------------------------------------------------------------------------

# Where the roast is divided for the purpose of "how much power through here".
# The same four phases the rest of the app uses, so a change drawn on the curve
# lands on an effect size the app has actually fitted.
PHASES = ("Drying", "Maillard", "Development")

# How many points the roaster gets to pull. Enough to shape a curve, few enough
# that each one means something — a control point every thirty seconds would be
# drawing, not roasting.
ANCHORS = 9


def projection(plan: dict, curve=None, points: int = 90, after=None) -> pd.DataFrame:
    """The roast this plan describes, as a curve: minutes, IBTS, rate of rise.

    Where the plan came from a roast, that roast's own smoothed IBTS is the
    shape — this is a projection of something real, not a drawing. Where it came
    from the library, the shape is synthesised: a rate of rise that peaks after
    the turning point and declines to nearly nothing at the drop, which is the
    one thing every source agrees a roast should do.

    Temperature is the integral of the rate of rise, so the two panels cannot
    disagree with each other. Pull the rate of rise up in the middle and the
    temperature curve steepens and the drop arrives sooner — which is what
    actually happens, and is the whole reason this is worth dragging.
    """
    drop = plan.get("drop") or {}
    charge = plan.get("charge") or {}
    total = _number(drop.get("expect_minutes")) or 11.0
    end = _number(drop.get("ibts")) or 215.0

    if curve is not None and not getattr(curve, "empty", True):
        found = _from_curve(curve, points, after)
        if found is not None and not found.empty:
            return found

    # No roast to project from: a declining rate of rise, anchored at the two
    # ends the plan does state — where it charges and where it drops.
    start = _number(charge.get("ibts"))
    turning = 1.2
    minutes = np.linspace(0, total, points)
    peak = 45.0
    ror = np.where(
        minutes < turning,
        peak * (minutes / max(turning, 0.01)),
        peak * np.exp(-1.15 * (minutes - turning) / max(total - turning, 0.01)))

    # Scale the rate of rise so the integral actually lands on the drop
    # temperature. A curve that ends somewhere other than where the plan says it
    # drops is not a projection of that plan.
    bottom = 90.0 if start is None else min(start, 95.0)
    climb = np.concatenate([[0], np.cumsum(np.diff(minutes) * ror[:-1])])
    if climb[-1] > 0:
        ror = ror * ((end - bottom) / climb[-1])
        climb = np.concatenate([[0], np.cumsum(np.diff(minutes) * ror[:-1])])
    return pd.DataFrame({"minutes": minutes, "ibts": bottom + climb, "ror": ror,
                         "measured": False})


def _from_curve(curve, points: int, after=None) -> pd.DataFrame | None:
    """A roast that happened, resampled onto an even grid.

    Starting after the turning point, and after the sensor has settled. For the
    first minute the IBTS is recovering onto the bean mass and reads a rate of
    rise near 100 °C/min, which is the sensor catching up rather than the roast
    heating. Handing somebody that as the first handle to drag would be handing
    them an artefact.
    """
    frame = curve
    time_column = ("time_minutes" if "time_minutes" in frame
                   else ("seconds" if "seconds" in frame else None))
    if time_column is None:
        return None
    minutes = (pd.to_numeric(frame[time_column], errors="coerce")
               / (60.0 if time_column == "seconds" else 1.0))

    warm = "smoothDrumTemperature" if "smoothDrumTemperature" in frame else "ibts_temp"
    rate = "smoothDrumDerivative" if "smoothDrumDerivative" in frame else "ibts_ror"
    if warm not in frame:
        return None

    held = pd.DataFrame({
        "minutes": minutes,
        "ibts": pd.to_numeric(frame[warm], errors="coerce"),
        "ror": pd.to_numeric(frame.get(rate), errors="coerce"),
    }).dropna(subset=["minutes", "ibts"]).sort_values("minutes")
    if held.empty:
        return None

    # From the turning point — the roast's own, where it is known — and past the
    # sensor's recovery, which is whichever comes later.
    turning = float(after) if after else (
        float(held.loc[held["ibts"].idxmin(), "minutes"]) if len(held) > 3 else 0.0)
    # Settled means climbing, and climbing at a rate a roast can actually climb
    # at. The plunge as cold beans fill the sensor's view reads far negative; the
    # recovery afterwards reads near 100. Neither is the roast.
    settled = held[(held["minutes"] > turning)
                   & (held["ror"] > -2) & (held["ror"] < 60)]
    start = float(settled["minutes"].min()) if not settled.empty else turning
    held = held[held["minutes"] >= start]
    if len(held) < 5:
        return None

    grid = np.linspace(float(held["minutes"].min()), float(held["minutes"].max()), points)
    out = pd.DataFrame({
        "minutes": grid,
        "ibts": np.interp(grid, held["minutes"], held["ibts"]),
    })
    if held["ror"].notna().any():
        out["ror"] = np.interp(grid, held["minutes"], held["ror"].ffill().bfill())
    else:
        out["ror"] = np.gradient(out["ibts"], out["minutes"])
    out["measured"] = True
    return out


def anchors(curve: pd.DataFrame, how_many: int = ANCHORS) -> list[dict]:
    """The handles: a few points on the rate of rise, evenly spread.

    Returned as `[{minutes, ror, ibts}]`, which is what the editor draws and
    hands back when somebody has pulled them about.
    """
    if curve is None or curve.empty:
        return []
    at = np.linspace(float(curve["minutes"].min()), float(curve["minutes"].max()), how_many)
    return [{"minutes": round(float(moment), 2),
             "ror": round(float(np.interp(moment, curve["minutes"], curve["ror"])), 2),
             "ibts": round(float(np.interp(moment, curve["minutes"], curve["ibts"])), 1)}
            for moment in at]


def calibration(held: list[dict], start: float, ends_at: float) -> float:
    """What to multiply the integral by so an untouched curve lands where it did.

    Nine handles cannot carry every wiggle of a real roast, so integrating them
    lands a few degrees off. Measuring that error once, on the curve *before*
    anybody drags anything, means the untouched case is exact and every edit
    reads as a change from the truth rather than from an approximation.
    """
    if not held or not np.isfinite(ends_at):
        return 1.0
    drawn = redraw(held, start)
    climb = float(drawn["ibts"].iloc[-1]) - float(start)
    if abs(climb) < 1:
        return 1.0
    return float(np.clip((float(ends_at) - float(start)) / climb, 0.5, 2.0))


def redraw(held: list[dict], start: float, points: int = 90,
           gain: float = 1.0) -> pd.DataFrame:
    """The curve those handles describe, with temperature as the integral.

    This is the half that makes dragging honest. A roaster pulling the rate of
    rise up through Maillard is saying *put more heat in here*, and the
    temperature curve has to answer — arriving at first crack sooner, and at a
    higher temperature by the drop. Drawing the two independently would let
    somebody draw a roast that cannot exist.
    """
    if not held:
        return pd.DataFrame(columns=["minutes", "ibts", "ror"])
    at = np.array([float(item["minutes"]) for item in held])
    rate = np.array([float(item["ror"]) for item in held])
    grid = np.linspace(at.min(), at.max(), points)
    drawn = np.interp(grid, at, rate)
    # The same rounding of the joins the editor does in the browser. Straight
    # lines between handles put corners in a curve, and a drum has too much mass
    # to turn a corner.
    window = max(3, int(round(points / 20)) | 1)
    smooth = pd.Series(drawn).rolling(window, center=True, min_periods=1).mean().to_numpy()
    climb = np.concatenate([[0.0], np.cumsum(np.diff(grid) * smooth[:-1])]) * float(gain)
    return pd.DataFrame({"minutes": grid, "ror": smooth,
                         "ibts": float(start) + climb, "measured": False})


def phase_of(minutes: float, first_crack: float | None, yellowing: float | None) -> str:
    """Which phase a moment belongs to, for looking up what a control is worth."""
    crack = float(first_crack) if first_crack else None
    yellow = float(yellowing) if yellowing else (crack * 0.55 if crack else None)
    if crack and minutes >= crack:
        return "Development"
    if yellow and minutes >= yellow:
        return "Maillard"
    return "Drying"


def from_curve(plan: dict, held: list[dict], was: list[dict], first_crack=None,
               yellowing=None) -> dict:
    """The recipe that follows from a curve somebody has pulled about.

    A rate of rise is not a thing you can set on a Bullet. Power is. So the
    change is read phase by phase — *this roaster wants 3 °C/min more through
    Maillard* — and turned into control steps by the effect sizes the app has
    fitted to **this machine**: how much one step of power has actually moved the
    rate of rise, in their own roasts. Where there is no effect size yet, a prior
    stands in and the step says `assumed` rather than `learned`.

    Nothing here invents a step the machine cannot make. Power is whole numbers
    between 0 and 9, and a change smaller than one of them is not a change.
    """
    made = _copy(plan)
    if not held or not was:
        return made

    now = {item["minutes"]: float(item["ror"]) for item in held}
    before = {item["minutes"]: float(item["ror"]) for item in was}
    shared = sorted(set(now) & set(before))
    if not shared:
        return made

    # What was asked for, phase by phase.
    wanted: dict[str, list] = {}
    for moment in shared:
        wanted.setdefault(phase_of(moment, first_crack, yellowing), []).append(
            now[moment] - before[moment])

    for phase, changes in wanted.items():
        change = float(np.mean(changes))
        if abs(change) < 0.75:                # smaller than the curve's own wobble
            continue

        key = f"power@{phase}->avgRoR{phase}"
        steps, sure, seen = learning.steps_to_move(key, change)
        basis = "learned" if seen >= 2 else "assumed"
        whole = int(np.sign(steps) * max(1, round(abs(steps)))) if abs(steps) >= 0.5 else 0
        if not whole:
            continue

        where = _phase_window(made, phase, first_crack, yellowing)
        touched = False
        for step in made["steps"]:
            if step["control"] != "power" or step.get("ibts") is None:
                continue
            if not (where[0] <= step["ibts"] <= where[1]):
                continue
            moved = _whole((step["to"] or 0) + whole, "power")
            if moved is not None and moved != step["to"]:
                step["from"], step["to"] = step["to"], moved
                step["why"] = (f"{abs(change):.1f} °C/min "
                               f"{'more' if change > 0 else 'less'} through {phase.lower()}")
                step["basis"] = basis
                touched = True

        made["provenance"].append({
            "what": f"{phase}: {change:+.1f} °C/min → power {whole:+d}",
            "basis": basis,
            "detail": (
                f"You pulled the rate of rise {abs(change):.1f} °C/min "
                f"{'up' if change > 0 else 'down'} through {phase.lower()}. On this "
                f"machine one step of power has moved it by about "
                f"{abs(change / whole):.1f} °C/min, fitted from {seen} pair(s) of your "
                f"own roasts — so that is {abs(whole)} step(s)."
                if basis == "learned" else
                f"You pulled the rate of rise {abs(change):.1f} °C/min "
                f"{'up' if change > 0 else 'down'} through {phase.lower()}. The app has "
                f"not seen enough of your roasts to know what a power step is worth "
                f"there yet, so this uses a starting figure. Roast it and it will learn "
                f"the real one.")
            + ("" if touched else
               f" There was no power step in {phase.lower()} to move, so this is a "
               "change to make and nothing in the list has moved — add one at the "
               "temperature you want it."),
        })

    made["curve"] = [dict(item) for item in held]
    return made


def _phase_window(plan: dict, phase: str, first_crack=None, yellowing=None) -> tuple:
    """The IBTS temperatures a phase covers, so a step can be found inside it."""
    steps = sorted((step.get("ibts") or 0) for step in plan.get("steps") or [])
    drop = _number((plan.get("drop") or {}).get("ibts")) or 215.0
    lowest = steps[0] if steps else 120.0
    if phase == "Development":
        return (drop - 25.0, drop + 5.0)
    if phase == "Maillard":
        return (lowest + 15.0, drop - 25.0)
    return (0.0, lowest + 15.0)
