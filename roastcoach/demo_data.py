"""
A simulated roasting history, for trying Roast Coach without a Bullet.

The roasts are generated from a small physical model rather than from noise: the
power you set through Maillard genuinely moves first crack earlier and lifts the
rate of rise, a late power increase genuinely produces a flick, and dropping too
soon after first crack genuinely leaves development short. That matters for two
reasons -- the demo behaves like real roasting, and the learning engine can be
tested against effect sizes whose true values are known.
"""

from __future__ import annotations

import math
import random

import numpy as np

# The truth the simulator runs on. The learning engine should recover these from
# the roasts alone, without being told.
TRUE_EFFECTS = {
    "power@Maillard->firstCrackTime": -0.30,      # minutes per power step
    "power@Maillard->avgRoRMaillard": 1.40,       # °C/min per power step
    "power@Drying->yellowPointTime": -0.22,
    "fan@Development->avgRoRDevelopment": -0.55,
}

COFFEES = [
    {"coffee": "Ethiopia Yirgacheffe Kochere", "origin": "Ethiopia", "process": "washed",
     "charge": 205, "green": 600, "drop_temp": 198},
    {"coffee": "Costa Rica La Minita Tarrazu", "origin": "Costa Rica", "process": "washed",
     "charge": 210, "green": 650, "drop_temp": 203},
    {"coffee": "Sumatra Gayo Mandheling", "origin": "Indonesia", "process": "wet hulled",
     "charge": 215, "green": 700, "drop_temp": 209},
]


def _milestones(power_drying, power_maillard, power_development, fan_development, drop_delay):
    """When the roast's landmarks fall, given how it was driven."""
    turning = 1.15 - 0.03 * (power_drying - 9)
    yellowing = 4.60 + TRUE_EFFECTS["power@Drying->yellowPointTime"] * (power_drying - 9)
    first_crack = (9.40
                   + TRUE_EFFECTS["power@Maillard->firstCrackTime"] * (power_maillard - 7)
                   - 0.10 * (power_drying - 9))
    total = first_crack + drop_delay + 0.06 * (fan_development - 4)
    return turning, yellowing, first_crack, total


def _temperature_curve(seconds, turning, yellowing, first_crack, total, charge, drop_temp):
    """A monotone drum curve through the roast's landmarks."""
    from scipy.interpolate import PchipInterpolator

    knots_x = [0.0, turning, (turning + yellowing) / 2, yellowing,
               (yellowing + first_crack) / 2, first_crack, total]
    knots_y = [charge, 92.0, 132.0, 165.0,
               (165 + 196) / 2, 196.0 + 0.35 * (drop_temp - 200), drop_temp]

    order = np.argsort(knots_x)
    curve = PchipInterpolator(np.array(knots_x)[order], np.array(knots_y)[order])
    return curve(np.clip(seconds / 60.0, 0, total))


def simulate_roast(profile: dict, when, power=(9, 7, 5), fan=(2, 3, 4), drum=9,
                   drop_delay=2.1, sample_rate=2, seed=0, late_power_bump=False,
                   stall=False) -> dict:
    """One roast, in the same shape as a RoasTime roast file."""
    rng = random.Random(seed)
    noise = np.random.default_rng(seed)

    power_drying, power_maillard, power_development = power
    fan_drying, fan_maillard, fan_development = fan

    turning, yellowing, first_crack, total = _milestones(
        power_drying, power_maillard, power_development, fan_development, drop_delay)
    if stall:
        first_crack += 1.1
        total += 1.1

    count = int(total * 60 * sample_rate)
    seconds = np.arange(count) / sample_rate
    drum_temperature = _temperature_curve(seconds, turning, yellowing, first_crack,
                                          total, profile["charge"], profile["drop_temp"])

    # Rate of rise follows from the curve, then the roast's character is applied.
    rate = np.gradient(drum_temperature, seconds) * 60
    minutes = seconds / 60
    rate += TRUE_EFFECTS["power@Maillard->avgRoRMaillard"] * (power_maillard - 7) * \
        np.exp(-((minutes - (yellowing + first_crack) / 2) ** 2) / 6)
    rate += TRUE_EFFECTS["fan@Development->avgRoRDevelopment"] * (fan_development - 4) * \
        (minutes > first_crack)

    if stall:
        rate -= 6.5 * np.exp(-((minutes - (first_crack - 1.4)) ** 2) / 0.6)
    if power_development <= 4 and not late_power_bump:
        rate -= 5.0 * (minutes > first_crack) * np.clip(minutes - first_crack, 0, None)
    if late_power_bump:
        rate += 4.0 * np.exp(-((minutes - (first_crack + 1.0)) ** 2) / 0.15)

    rate += noise.normal(0, 0.25, count)
    bean = drum_temperature - (14 + 9 * np.exp(-minutes / 1.6)) + noise.normal(0, 0.3, count)
    bean[0] = 190 + rng.random() * 6          # the probe starts hot from preheat

    actions = []
    schedule = [
        (0, 0, power_drying), (1, 0, fan_drying), (2, 0, drum),
        (int(yellowing * 60 * sample_rate), 0, power_maillard),
        (int(yellowing * 60 * sample_rate) + 2, 1, fan_maillard),
        (int(first_crack * 60 * sample_rate), 0, power_development),
        (int(first_crack * 60 * sample_rate) + 2, 1, fan_development),
    ]
    if late_power_bump:
        schedule.append((int((first_crack + 0.7) * 60 * sample_rate), 0, power_development + 2))
    for index, control, value in schedule:
        actions.append({"ctrlType": control if control != 2 else 2,
                        "index": max(0, min(index, count - 1)), "value": value})
    actions = [{"ctrlType": (0 if position == 0 else control), **rest}
               for position, (control, rest) in enumerate([])] or actions

    weight_loss = 0.118 + 0.010 * (total - first_crack) + noise.normal(0, 0.004)
    identifier = f"demo-{profile['coffee'][:3].lower()}-{when.strftime('%Y%m%d%H%M')}"

    return {
        "uid": identifier, "guid": identifier,
        "dateTime": int(when.timestamp() * 1000),
        "roastName": f"{profile['coffee']}",
        "beanId": profile["coffee"],
        "serialNumber": 1578, "firmware": 555, "hardware": 50462976,
        "ambient": round(19 + noise.normal(0, 1.5), 1),
        "roomHumidity": str(round(42 + noise.normal(0, 6))),
        "weightGreen": profile["green"],
        "weightRoasted": round(profile["green"] * (1 - weight_loss)),
        "preheatTemperature": profile["charge"] + 45,
        "drumChargeTemperature": round(float(drum_temperature[0]), 1),
        "beanChargeTemperature": round(float(bean[0]), 1),
        "drumDropTemperature": round(float(drum_temperature[-1]), 1),
        "beanDropTemperature": round(float(bean[-1]), 1),
        "totalRoastTime": total * 60, "sampleRate": sample_rate,
        "roastStartIndex": 0, "roastEndIndex": count - 1,
        "indexYellowingStart": int(yellowing * 60 * sample_rate),
        "indexFirstCrackStart": int(first_crack * 60 * sample_rate),
        "indexFirstCrackEnd": 0, "indexSecondCrackStart": 0, "indexSecondCrackEnd": 0,
        "beanTemperature": [round(float(v), 2) for v in bean],
        "drumTemperature": [round(float(v), 2) for v in drum_temperature],
        "beanDerivative": [round(float(v), 2) for v in (rate - 1.5 + noise.normal(0, 0.2, count))],
        "ibtsDerivative": [round(float(v), 2) for v in rate],
        "actions": {"actionTimeList": sorted(actions, key=lambda a: a["index"])},
    }


def history(weeks: int = 12, seed: int = 11) -> list[dict]:
    """A plausible few months of roasting: three coffees, dialled in over time."""
    import datetime as dt

    rng = random.Random(seed)
    start = dt.datetime.now() - dt.timedelta(weeks=weeks)
    roasts = []
    counter = 0

    for index, profile in enumerate(COFFEES):
        # Each coffee starts rough and is worked toward a good profile.
        power_maillard = 6.0 + index * 0.5
        power_drying = 9.0
        fan_development = 5.0
        drop_delay = 1.5

        for session in range(6 + index):
            counter += 1
            when = start + dt.timedelta(days=session * 7 + index * 2,
                                        hours=9 + rng.random() * 3)
            stall = session == 0 and index == 0
            bump = session == 1 and index == 0

            roasts.append(simulate_roast(
                profile, when,
                power=(power_drying, power_maillard, max(3.5, power_maillard - 1.5)),
                fan=(2, 3, fan_development),
                drop_delay=drop_delay,
                seed=seed + counter,
                late_power_bump=bump, stall=stall,
            ))

            # The roaster reacts: more heat in the middle, longer development.
            power_maillard = min(8.5, power_maillard + rng.choice([0.5, 1.0, 0.5, 0]))
            drop_delay = min(2.6, drop_delay + rng.choice([0.2, 0.3, 0.1]))
            fan_development = max(3.0, fan_development - rng.choice([0, 0.5, 0.5]))
            power_drying = min(9.5, max(8.0, power_drying + rng.choice([-0.5, 0, 0.5])))

    return roasts


def as_files(roasts: list[dict]) -> list[dict]:
    """The demo roasts packaged the way the importer expects."""
    import json

    files = []
    for roast in roasts:
        text = json.dumps(roast)
        files.append({"name": f"{roast['uid']}.json", "text": text,
                      "modified": roast["dateTime"] / 1000, "size": len(text)})
    return files
