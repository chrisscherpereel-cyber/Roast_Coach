"""What was actually set, when — and what to set next time.

Nobody turns a knob to an average. A roast is a short list of moves: at 0:00 the
drum goes to 9 and power to 9; at 4:12 power comes down to 7; at 8:20 the fan
goes up. That list *is* the recipe, and it is the only form advice can take if
somebody is going to stand at the machine and follow it.

So this module does two things:

* :func:`timeline` — the moves this roast actually made, read back out of the
  stored curve, with the roast's own events (charge, yellowing, first crack,
  drop) sitting in the same list at the times they happened.
* :func:`plan` — the same list for the *next* roast, with the coach's changes
  applied to it. Each change is a whole number on one control at one time,
  because that is what the Bullet accepts: power, fan and drum move in steps of
  one.

Everything here is integers on purpose. "Power up 1.5 steps through Maillard"
cannot be done. "At 4:12, power 7 instead of 8" can.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

VERSION = 2

CONTROLS = ("power", "fan", "drum")

# The Bullet's own ranges. Advice is clamped to them so nothing suggests a
# setting the machine does not have.
LIMITS = {"power": (0, 9), "fan": (0, 12), "drum": (0, 9)}

# The Bullet is being set up in the first seconds; those are not moves in the
# roast. Whatever it settles on by here is the setting the roast starts with.
SETTLE_SECONDS = 3.0


def clock(minutes) -> str:
    try:
        minutes = float(minutes)
    except (TypeError, ValueError):
        return "—"
    if not np.isfinite(minutes) or minutes < 0:
        return "—"
    return f"{int(minutes)}:{int(round((minutes % 1) * 60)):02d}"


def _events(row) -> list[dict]:
    """Charge, yellowing, first crack and drop, as rows in the same list."""
    marks = (("Charge", 0.0), ("Yellowing", row.get("yellowPointTime")),
             ("First crack", row.get("firstCrackTime")),
             ("Drop", row.get("totalRoastMinutes")))
    out = []
    for name, minutes in marks:
        try:
            minutes = float(minutes)
        except (TypeError, ValueError):
            continue
        if np.isfinite(minutes):
            out.append({"at": minutes, "kind": "event", "event": name})
    return out


def _temperature_at(curve: pd.DataFrame, seconds: pd.Series, column: str, at_seconds: float):
    """The temperature the roast was at, at a given second."""
    if column not in curve:
        return np.nan
    values = pd.to_numeric(curve[column], errors="coerce")
    position = int((seconds - at_seconds).abs().idxmin()) if len(seconds) else None
    if position is None or position >= len(values):
        return np.nan
    value = values.iloc[position]
    return float(value) if pd.notna(value) else np.nan


def timeline(curve: pd.DataFrame, row=None) -> pd.DataFrame:
    """Every control change in one roast: when, at what temperature, and to what.

    Read from the stored per-sample curve rather than from anything the roaster
    typed, so it is what the machine did.

    Both time and temperature, because a Bullet recipe is written in temperature
    — "power 7 at 165 °C" — while the roast is watched on a clock. Neither one
    alone lets you follow a roast you did not run yourself.
    """
    columns = ["at", "clock", "bt", "ibts", "kind", "event", "control", "from", "to", "step"]
    if curve is None or curve.empty or "seconds" not in curve:
        return pd.DataFrame(columns=columns)

    seconds = pd.to_numeric(curve["seconds"], errors="coerce")
    moves = []
    for control in CONTROLS:
        if control not in curve:
            continue
        values = pd.to_numeric(curve[control], errors="coerce").ffill()
        previous = np.nan
        for position, value in enumerate(values):
            if pd.isna(value):
                continue
            # Whatever happens in the first few seconds is the machine being set
            # up, not a move during the roast: keep the setting it settled on.
            if float(seconds.iloc[position]) <= SETTLE_SECONDS:
                previous = value
                continue
            if pd.isna(previous) or abs(value - previous) >= 0.5:
                moves.append({
                    "at": float(seconds.iloc[position]) / 60.0,
                    "kind": "set",
                    "event": "",
                    "control": control,
                    "from": (None if pd.isna(previous) else float(previous)),
                    "to": float(value),
                    "step": (None if pd.isna(previous) else float(value - previous)),
                })
                previous = value

    # The settings the roast started with, as one row at charge.
    opening = {}
    for control in CONTROLS:
        if control in curve:
            values = pd.to_numeric(curve[control], errors="coerce").ffill()
            settled = values[pd.to_numeric(curve["seconds"], errors="coerce") <= SETTLE_SECONDS]
            settled = settled.dropna()
            if not settled.empty:
                opening[control] = float(settled.iloc[-1])
    charge = [{"at": 0.0, "kind": "set", "event": "", "control": control,
               "from": None, "to": value, "step": None}
              for control, value in opening.items()]

    rows = charge + moves + (_events(row) if row is not None else [])
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=columns)

    frame = frame.sort_values(["at", "kind"], ascending=[True, False]).reset_index(drop=True)
    frame["clock"] = frame["at"].map(clock)

    # The temperature each move was made at — the way the recipe was written.
    frame["bt"] = [_temperature_at(curve, seconds, "bean_temp", at * 60.0)
                   for at in frame["at"]]
    frame["ibts"] = [_temperature_at(curve, seconds, "ibts_temp", at * 60.0)
                     for at in frame["at"]]

    for column in columns:
        if column not in frame:
            frame[column] = None
    return frame[columns]


def temperatures_at(curve: pd.DataFrame, minutes: float) -> tuple:
    """Bean and drum temperature at a moment, read from the curve itself.

    Not from the nearest control change: a move made at 1:12 is not at the
    charge temperature, and saying so would send somebody to the wrong place in
    the roast.
    """
    if curve is None or curve.empty or "seconds" not in curve:
        return (np.nan, np.nan)
    seconds = pd.to_numeric(curve["seconds"], errors="coerce")
    at = float(minutes) * 60.0
    return (_temperature_at(curve, seconds, "bean_temp", at),
            _temperature_at(curve, seconds, "ibts_temp", at))


def settings_at(moves: pd.DataFrame, minutes: float) -> dict:
    """What each control was set to at a given moment, and the temperature there."""
    found = {control: np.nan for control in CONTROLS}
    found["bt"] = found["ibts"] = np.nan
    if moves is None or moves.empty:
        return found

    sets = moves[(moves["kind"] == "set") & (moves["at"] <= minutes + 1e-9)]
    for control in CONTROLS:
        mine = sets[sets["control"] == control]
        if not mine.empty:
            found[control] = float(mine.iloc[-1]["to"])

    # The temperature at that moment, from whichever row of the timeline is
    # nearest — so advice can be given the way a recipe is written.
    if "bt" in moves:
        nearest = (moves["at"] - minutes).abs().idxmin()
        for column in ("bt", "ibts"):
            value = moves.loc[nearest, column] if column in moves else np.nan
            found[column] = float(value) if pd.notna(value) else np.nan
    return found


def move(control: str, at_minutes: float, current, steps: float, why: str = "",
         bean_temp=None, drum_temp=None) -> dict | None:
    """One change the roaster can actually make: a whole step, on one control, at a time.

    Anything under half a step is no change at all and is dropped rather than
    dressed up — the machine has no such setting.
    """
    try:
        current = float(current)
    except (TypeError, ValueError):
        current = np.nan
    # The machine only has whole settings. A stored 8.5 is a smoothed reading of
    # one, so the advice is worked out from the setting the roaster can see.
    if np.isfinite(current):
        current = float(round(current))
    whole = int(np.sign(steps) * max(1, round(abs(steps)))) if abs(steps) >= 0.5 else 0
    if whole == 0:
        return None

    low, high = LIMITS.get(control, (0, 9))
    target = np.nan
    if np.isfinite(current):
        target = float(min(high, max(low, current + whole)))
        if target == current:
            return None
        whole = int(target - current)

    def _temp(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return round(value, 1) if np.isfinite(value) else None

    return {"control": control, "at": float(at_minutes), "clock": clock(at_minutes),
            "bt": _temp(bean_temp), "ibts": _temp(drum_temp),
            "from": current if np.isfinite(current) else None,
            "to": target if np.isfinite(target) else None,
            "step": whole, "why": why}


def when(change: dict) -> str:
    """When to make a change, said both ways: on the clock and at a temperature."""
    at = change.get("clock", "—")
    temperature = change.get("bt")
    return f"{at} · {float(temperature):.0f} °C BT" if temperature is not None else at


def describe(change: dict) -> str:
    """One line a person can follow standing at the machine."""
    if not change:
        return ""
    control = change["control"]
    if change.get("to") is not None and change.get("from") is not None:
        return (f"At {when(change)}, {control} {change['from']:.0f} → "
                f"{change['to']:.0f}")
    direction = "up" if change["step"] > 0 else "down"
    return (f"At {when(change)}, {control} {direction} "
            f"{abs(change['step']):.0f} step{'s' if abs(change['step']) != 1 else ''}")


def plan(moves: pd.DataFrame, changes: list[dict]) -> pd.DataFrame:
    """The next roast's control list: last time's moves with the changes folded in.

    Shown as a plan rather than a diff, because that is how it gets used — read
    down the list while the drum is turning. Anything altered says what it was.
    """
    columns = ["clock", "at", "bt", "control", "set to", "last time", "why"]
    rows = []

    if moves is not None and not moves.empty:
        for _, item in moves[moves["kind"] == "set"].iterrows():
            rows.append({"clock": item["clock"], "at": float(item["at"]),
                         "bt": item.get("bt"),
                         "control": item["control"], "set to": item["to"],
                         "last time": item["to"], "why": ""})

    for change in changes or []:
        if not change:
            continue
        at = float(change["at"])
        control = change["control"]
        existing = [row for row in rows
                    if row["control"] == control and abs(row["at"] - at) < 0.25]
        if existing:
            row = existing[0]
            row["set to"] = change.get("to")
            row["why"] = change.get("why", "")
        else:
            rows.append({"clock": change["clock"], "at": at, "bt": change.get("bt"),
                         "control": control, "set to": change.get("to"),
                         "last time": change.get("from"),
                         "why": change.get("why", "")})

    if not rows:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame(rows).sort_values(["at", "control"]).reset_index(drop=True)
    frame["changed"] = frame["why"].astype(str).str.len() > 0
    return frame[columns + ["changed"]]
