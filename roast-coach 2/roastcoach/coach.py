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

import numpy as np
import pandas as pd

from . import learning, store

# What a roast is being steered toward. These are starting points, not laws --
# every one of them can be overridden per coffee once a reference roast exists.
TARGETS = {
    "development_percent": (18.0, 25.0),
    "drying_percent": (28.0, 40.0),
    "turning_point_minutes": (0.8, 1.8),
    "total_minutes": (8.0, 14.0),
    "ror_at_first_crack": (3.0, 12.0),
    "weight_loss_percent": (11.0, 17.0),
}

TOLERANCE = 0.35  # a prediction counts as met within this share of the intended move


def _value(row, name, default=np.nan):
    value = row.get(name, default)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return np.nan
    return value if np.isfinite(value) else np.nan


def _phase_percentages(row) -> dict:
    """Drying / Maillard / development as shares of the roast, from charge."""
    total = _value(row, "totalRoastMinutes")
    yellow = _value(row, "yellowPointTime")
    crack = _value(row, "firstCrackTime")
    if not np.isfinite(total) or total <= 0:
        return {}
    result = {}
    if np.isfinite(yellow):
        result["drying"] = yellow / total * 100
    if np.isfinite(yellow) and np.isfinite(crack):
        result["maillard"] = (crack - yellow) / total * 100
    if np.isfinite(crack):
        result["development"] = (total - crack) / total * 100
    return result


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


def _confidence(rule_id: str, evidence: float, path: str | None = None) -> float:
    """Blend the evidence behind the magnitude with the rule's own track record."""
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
# The rules
# ---------------------------------------------------------------------------


def rule_development(row, context, path=None):
    phases = _phase_percentages(row)
    development = phases.get("development")
    if development is None or not np.isfinite(development):
        return None
    low, high = context["targets"]["development_percent"]
    if low <= development <= high:
        return None

    total = _value(row, "totalRoastMinutes")
    crack = _value(row, "firstCrackTime")
    target = low if development < low else high
    wanted_minutes = crack * (target / (100 - target)) - (total - crack)

    if development < low:
        rule_id = "development_short"
        headline = "Development is short"
        finding = (f"First crack to drop is {development:.0f}% of the roast "
                   f"({total - crack:.1f} of {total:.1f} min). Below {low:.0f}% the cup "
                   "tends to read sharp and underdeveloped.")
        action = (f"Carry this roast {wanted_minutes:+.1f} min further before dropping — "
                  f"about {(total + wanted_minutes):.1f} min total. Take the heat down a "
                  "step at first crack so the extra time is gentle rather than hot.")
    else:
        rule_id = "development_long"
        headline = "Development is running long"
        finding = (f"First crack to drop is {development:.0f}% of the roast "
                   f"({total - crack:.1f} of {total:.1f} min). Past {high:.0f}% the "
                   "origin character starts to flatten out.")
        action = (f"Drop {abs(wanted_minutes):.1f} min earlier — about "
                  f"{(total + wanted_minutes):.1f} min total. If first crack keeps arriving "
                  "late, the fix belongs earlier in the roast rather than at the drop.")

    return {
        "rule_id": rule_id, "headline": headline, "finding": finding, "action": action,
        "reason": "Development share sets how far the roast travels after first crack.",
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
    power = _value(row, "powerMaillard")

    return {
        "rule_id": "ror_crash",
        "headline": "Rate of rise crashed after first crack",
        "finding": ("Rate of rise fell away once first crack started"
                    + (f", reaching {current:.1f} °C/min at the crack itself" if np.isfinite(current) else "")
                    + ". A crash there is the roast running out of momentum, and it usually "
                      "shows up as a hollow, papery cup."),
        "action": (f"The cure is earlier, not at the crack. Add heat through the Maillard "
                   f"phase — {_step_text(abs(steps) or 1, 'power')} from the "
                   f"{power:.1f} average you used — and then reduce it *before* first crack "
                   "rather than during it, so the curve is already easing down when the crack "
                   "arrives."),
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
    power = _value(row, "powerMaillard")
    return {
        "rule_id": "stall",
        "headline": "The roast stalled before first crack",
        "finding": ("Rate of rise sat near zero before first crack"
                    + (f" — {current:.1f} °C/min on average through Maillard" if np.isfinite(current) else "")
                    + ". A stall there bakes the sugars rather than developing them."),
        "action": (f"{_step_text(abs(steps) or 1, 'power').capitalize()} through the Maillard "
                   f"phase (you averaged {power:.1f}), applied by yellowing rather than after "
                   "the curve has already flattened."),
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
    power = _value(row, "powerDrying")

    fast = drying < low
    return {
        "rule_id": "drying_fast" if fast else "drying_slow",
        "headline": "Drying went through too quickly" if fast else "Drying is dragging",
        "finding": (f"Yellowing at {yellow:.1f} min is {drying:.0f}% of a {total:.1f} min roast. "
                    + ("Rushing the free moisture out tends to leave grassy, underbaked notes."
                       if fast else
                       "A long drying phase flattens acidity and dulls the cup.")),
        "action": (f"{_step_text(steps, 'power').capitalize()} through drying "
                   f"(you averaged {power:.1f}) to move yellowing to about "
                   f"{yellow + wanted_minutes:.1f} min."
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
    turning = _value(row, "turningPointTime")
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
    loss = _value(row, "weightLossPercent")
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
                    + (f", with {development:.0f}% of the roast after first crack"
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

    return {"targets": TARGETS, "reference": reference, "coffee": coffee,
            "history": same, "path": path}


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
