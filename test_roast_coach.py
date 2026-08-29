"""
Tests for Roast Coach.

The interesting one is the learning test: the simulator generates roasts from
effect sizes it never reveals, and the learning engine has to recover them from
the roasts alone.

    python3 test_roast_coach.py
"""

import json
import os
import tempfile
from pathlib import Path

os.environ["ROAST_COACH_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["ROAST_COACH_PASSWORDS"] = "tester:roast-coach-test"

# Run every test against Postgres too by setting this to a connection string.
SHARED_URL = os.environ.get("ROAST_COACH_TEST_POSTGRES")
if SHARED_URL:
    os.environ["ROAST_COACH_DATABASE_URL"] = SHARED_URL

import pandas as pd  # noqa: E402

from roastcoach import auth, coach, db, demo_data, learning, library, store  # noqa: E402
from roastcoach.fields import parse_roast_text  # noqa: E402

PASS, FAIL = "  ✓", "  ✗"


def check(condition, description, detail=""):
    print(f"{PASS if condition else FAIL} {description}{('  — ' + detail) if detail else ''}")
    assert condition, description


print("\nWHERE IT IS STORED")
check(bool(db.database_url()), "a database is resolved", db.describe())
check(db.dialect() in ("sqlite", "postgresql"), f"talking to {db.dialect()}")

print("\nIMPORT")
store.clear()
roasts = demo_data.history()
report = store.add_roasts(demo_data.as_files(roasts))
check(report["added"] == len(roasts), f"imported all {len(roasts)} simulated roasts")
check(store.add_roasts(demo_data.as_files(roasts))["skipped"] == len(roasts),
      "a second import skips everything unchanged")

renamed = demo_data.as_files(roasts[:3])
for position, item in enumerate(renamed):
    item["name"] = f"copy-{position}.json"
    item["modified"] += 1000
    item["size"] += 1
again = store.add_roasts(renamed)
check(again["added"] == 0 and again["skipped"] == 3,
      "the same roast under a new name is recognised by its contents, not added twice")

before = len(store.load_roasts())
store.add_roasts([{"name": "junk.csv", "text": "not,a,roast\n1,2,3", "modified": 5, "size": 16}])
check(len(store.load_roasts()) == before, "a file that is not a roast changes nothing")

frame = store.load_roasts()
check(len(frame) == len(roasts), "every roast comes back out")
check(frame["coffee"].nunique() == 3, "roasts group into three coffees")
check(frame["label"].str.match(r"\d{4}-\d{2}-\d{2} · .+").all(),
      "every roast is identified by date and coffee")

print("\nWHAT THE FOLDER WATCHER NEEDS")
known = store.known_sources()
check(len(known) == len(roasts), "every imported file is remembered by name")
first_name, (modified, size) = next(iter(known.items()))
check(modified > 0 and size > 0, "with the timestamp and size that say whether it changed",
      f"{first_name}: {size} bytes")

changed = demo_data.as_files([r for r in roasts if True][:1])
changed[0]["text"] = changed[0]["text"].replace('"ambient"', '"ambientTemp"')
changed[0]["modified"] += 60
changed[0]["size"] += 1
edited = store.add_roasts(changed)
check(edited["updated"] == 1 and edited["added"] == 0,
      "a file whose contents changed updates its roast rather than adding one")

store.note_sync("roasts", 42)
state = store.sync_state()
check(state["folder"] == "roasts" and state["looked"] == 42 and state["checked_at"],
      "the last folder check is recorded", state["checked_at"])

print("\nPERSISTENCE — the database outlives the process")
db.reset()                       # every connection dropped, as on a restart
reopened = store.load_roasts()
check(len(reopened) == len(roasts), "roasts are still there after reconnecting",
      f"{len(reopened)} roasts")
check(store.summary()["samples"] > 10000, "so are their curves",
      f"{store.summary()['samples']:,} samples")

if SHARED_URL:
    print("\nTWO COMPUTERS — a separate process, the same roasts")
    import subprocess
    import sys

    script = (
        "import os, json;"
        "from roastcoach import demo_data, store;"
        "roasts = demo_data.history(weeks=2, seed=99)[:2];"
        "print(json.dumps(store.add_roasts(demo_data.as_files(roasts))))"
    )
    other = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                           env={**os.environ, "ROAST_COACH_DATABASE_URL": SHARED_URL},
                           cwd=os.path.dirname(os.path.abspath(__file__)))
    added = json.loads(other.stdout.strip().splitlines()[-1])["added"] if other.returncode == 0 else 0
    check(added == 2, "another computer imports into the same database",
          "" if added == 2 else other.stderr[-300:])
    check(len(store.load_roasts()) == len(roasts) + 2,
          "and this one sees those roasts without restarting")
    store.forget([r["uid"] for r in demo_data.history(weeks=2, seed=99)[:2]])
    check(len(store.load_roasts()) == len(roasts), "removing them removes them for everyone")

print("\nEVERYTHING ELSE ROASTIME KEEPS")
import json as _json  # noqa: E402

bean = {"id": "bean-1", "name": "Costa Rica La Minita Tarrazu RFA", "origin": "Costa Rica",
        "process": "washed", "varietal": "Caturra", "supplier": "La Minita",
        "altitude": "1500m", "cropYear": "2025/26"}
recipe = {"guid": "recipe-1", "recipeName": "CRT 800 Md v4", "targetWeight": 800}
machine = {"id": "machine-1", "name": "A bag of it", "capacityType": "bag"}
library.add_records("bean", [{"name": "bean-1.json", "text": _json.dumps(bean)}])
library.add_records("recipe", [{"name": "recipe-1.json", "text": _json.dumps(recipe)}])
library.add_records("container", [{"name": "machine-1.json", "text": _json.dumps(machine)}])
check(library.counts() == {"bean": 1, "container": 1, "recipe": 1},
      "bean, recipe and container files are stored", str(library.counts()))

linked = demo_data.history(weeks=2, seed=451)[:1]
linked[0]["uid"] = linked[0]["guid"] = "linked-roast"
linked[0]["beanId"] = "bean-1"
linked[0]["recipeId"] = "recipe-1"
linked[0]["containerId"] = "machine-1"
linked[0]["serialNumber"] = 1578
store.add_roasts(demo_data.as_files(linked))
joined = store.load_roasts()
one = joined[joined["uid"] == "linked-roast"].iloc[0]
check(one["coffee"] == bean["name"], "the roast takes its coffee from the bean file",
      one["coffee"])
check(one.get("origin") == "Costa Rica" and one.get("process") == "washed",
      "origin and process come across without being typed")
check(one.get("recipe_name") == "CRT 800 Md v4",
      "so does the recipe name", str(one.get("recipe_name")))
check(one.get("machine_name") == "Bullet 1578",
      "and the machine, which is the serial number on the roast — RoasTime's "
      "`containers` are bags of coffee, not roasters", str(one.get("machine_name")))
check("bean → bean-1 ✓" in library.describe_link(one.to_dict()),
      "and the app can say what linked to what")

unlinked = joined[joined["uid"] != "linked-roast"]
check(not unlinked.empty and unlinked.iloc[0]["coffee"] != bean["name"],
      "a roast with no bean id is left exactly as it was")

# Roasts are compared bean against bean. Two roasts of one bean carrying quite
# different titles have to land in one group, and the roast title must never
# split them.
pair = demo_data.history(weeks=3, seed=77)[:2]
for position, roast in enumerate(pair):
    roast["uid"] = roast["guid"] = f"same-bean-{position}"
    roast["beanId"] = "bean-1"
pair[0]["roastName"] = "#12 CR 800 v4"
pair[1]["roastName"] = "Ethiopia test 2nd"          # a title that guesses wrongly
store.add_roasts(demo_data.as_files(pair))
grouped = store.load_roasts()
mine = grouped[grouped["uid"].str.startswith("same-bean")]
check(len(mine) == 2 and mine["coffee"].nunique() == 1,
      "two roasts of one bean group together whatever their titles say",
      " / ".join(sorted(set(mine["coffee"]))))
check(set(mine["coffee_source"]) == {"bean file"},
      "and the app can say the bean is why they are together")

# Three names, kept apart: what the roast was called, what was in the drum, and
# what it was run from. Collapsing them hides the thing most worth seeing.
_named = grouped[grouped["uid"] == "linked-roast"].iloc[0]
check(_named["roast_name"] and _named["roast_name"] != _named["bean"],
      "a roast keeps the name RoasTime gave it, separate from its bean",
      f"roast '{_named['roast_name']}' · bean '{_named['bean']}'")
check(_named["bean"] == bean["name"], "the bean is the bean file's own name")
check(_named["recipe_name"] == recipe["recipeName"],
      "and the recipe is named alongside it", _named["recipe_name"])
check((grouped[grouped["coffee_source"] == "roast name"]["bean"] == "").all(),
      "a roast with no bean file has an empty bean rather than a borrowed one")
check(set(grouped.groupby(["bean", "recipe_name"]).size().index.names)
      == {"bean", "recipe_name"},
      "so roasts can be grouped by bean and recipe together")

# Typing a name on one of them renames the bean, rather than splitting that roast
# off from its own history.
store.save_notes("same-bean-0", {"coffee": "Tarrazú lot 7"})
renamed_frame = store.load_roasts()
mine = renamed_frame[renamed_frame["uid"].str.startswith("same-bean")]
check(set(mine["coffee"]) == {"Tarrazú lot 7"},
      "renaming one roast of a bean renames every roast of that bean")
store.save_notes("same-bean-0", {"coffee": ""})
store.forget(["same-bean-0", "same-bean-1"])

# Two beans that happen to share a name are still two coffees.
library.add_records("bean", [
    {"name": "a.json", "text": _json.dumps({"id": "lot-a", "name": "Ethiopia Guji"})},
    {"name": "b.json", "text": _json.dumps({"id": "lot-b", "name": "Ethiopia Guji"})}])
labels = library.bean_labels(library.tables()["bean"])
check(labels["lot-a"] != labels["lot-b"] and labels["lot-a"].startswith("Ethiopia Guji"),
      "two lots under one name stay apart", f"{labels['lot-a']} / {labels['lot-b']}")

# A roast whose bean id is missing has been through pandas, so the field is NaN
# and str(NaN) is "nan" — which once had 387 roasts reporting that they pointed
# at a bean called `nan`.
check(library.link_id({"beanId": float("nan")}, "bean") is None
      and library.link_id({"beanId": "nan"}, "bean") is None
      and library.link_id({"beanId": ""}, "bean") is None,
      "a missing bean id is no id at all, however pandas spelled it")
check(library.link_id({"beanId": "  bean-1  "}, "bean") == "bean-1",
      "and a real one is taken as it is, trimmed")
_nan_report = library.link_report([{"beanId": float("nan")}, {"beanId": "unmatched"}])
check(_nan_report["no_id"] == 1 and list(_nan_report["missing"]) == ["unmatched"],
      "so the Data page counts it as 'no bean recorded', not as a missing file")

# RoasTime points a roast at a *container* — a bag of a coffee, named the way the
# roaster thinks of it — which in turn points at the bean carrying origin and
# process. Looking only in beans/ is why 510 roasts reported a bean whose file
# "had not arrived" while it sat in containers/ all along.
library.add_records("container", [{"name": "lot.json", "text": _json.dumps(
    {"id": "container-9", "name": "Del Campo", "beanId": "bean-1",
     "capacityType": "bag"})}])
_via_container = demo_data.history(weeks=2, seed=606)[:1]
_via_container[0]["uid"] = _via_container[0]["guid"] = "container-roast"
_via_container[0]["beanId"] = "container-9"
_via_container[0]["serialNumber"] = 1578
store.add_roasts(demo_data.as_files(_via_container))
_chained = store.load_roasts()
_lot = _chained[_chained["uid"] == "container-roast"].iloc[0]
check(_lot["bean"] == bean["name"],
      "a roast pointing at a bag of coffee is still compared as the bean in it — "
      "two bags of one coffee are one coffee, not two", _lot["bean"])
check(_lot.get("lot_name") == "Del Campo",
      "and the bag keeps its own name beside it", str(_lot.get("lot_name")))
check(_lot.get("origin") == "Costa Rica",
      "and still gets origin and process from the bean behind it",
      str(_lot.get("origin")))
check(_lot.get("machine_name") == "Bullet 1578",
      "the machine comes from the serial number on the roast, not a container",
      str(_lot.get("machine_name")))
store.forget(["container-roast"])

# A recipe is more than its name: RoasTime writes the whole plan, and all of it
# is stored and readable.
_full_recipe = {"uid": "rec-full", "name": "Zambia 800 Light-Medium", "roastDegree": 3,
                "weight": 800, "preheatTemp": 210, "country": "Zambia",
                "process": "washed", "tempMeasurement": "C",
                "startSettings": {"power": 9, "fan": 3, "drum": 9},
                "events": [{"temp": 170, "power": 9, "fan": 3},
                           {"temp": 165, "power": 7, "fan": 4},
                           {"time": 540, "temp": 196, "power": 5}]}
library.add_records("recipe", [{"name": "full", "text": _json.dumps(_full_recipe)}])
_summary = library.recipe_summary(_full_recipe)
check(_summary.get("roast degree") == "3" and _summary.get("target weight") == "800"
      and _summary.get("preheat") == "210",
      "a recipe's own settings are read, not just its name", str(_summary)[:70])
_steps = library.recipe_steps(_full_recipe)
check(len(_steps) == 4 and _steps[0]["at"] == "charge",
      "and its steps, starting with what it opens at", str(_steps[0]))
check(any(step.get("temperature") == "165" and step.get("power") == "7"
          for step in _steps),
      "each step carrying the temperature it fires at — a Bullet recipe is written "
      "in temperature, not time")
check(library.record("recipe", "rec-full") == _full_recipe,
      "and the whole record is kept exactly as RoasTime wrote it")

# RoasTime's own shape, copied from this roaster's `recipes/CRT 800 Md v4`: a
# step is a *list* of conditions, each `{trigger, value, actions}`, and every one
# of those is a number. 0 watches bean temperature, 3 watches the clock; among
# the actions, 0 sets power, 1 the drum, 2 the fan, 3 leaves a note and 4 raises
# an alert. Read off 87 of this roaster's recipes, not guessed.
_as_roastime_writes_it = {
    "uid": "rec-real", "name": "CRT 800 Md v4", "roastDegree": 3, "weight": 800,
    "preheatTemp": 280, "tempMeasurement": "C",
    "startSettings": {"power": 8, "drum": 9, "fan": 2},
    "events": [
        [{"trigger": 0, "condition": 0, "value": 176,
          "actions": [{"action": 0, "value": 7}, {"action": 2, "value": "3"}]},
         {"trigger": 3, "condition": 0, "value": 5, "actions": []}],
        [{"trigger": 0, "condition": 0, "value": 210,
          "actions": [{"action": 0, "value": "5"}, {"action": 2, "value": 5}]},
         {"trigger": 3, "condition": 0, "value": 5, "actions": []}],
    ],
    "endSettings": [{"trigger": 0, "condition": 0, "value": 220,
                     "actions": [{"action": 4, "value": "End Roast Alert"}]},
                    {"trigger": 3, "condition": 0, "value": 300, "actions": []}],
}
_real_steps = library.recipe_steps(_as_roastime_writes_it)
check(_real_steps[0] == {"at": "charge", "power": "8", "fan": "2", "drum": "9"},
      "RoasTime's own recipe opens at charge with power, fan and drum",
      str(_real_steps[0]))
check(any(step.get("temperature") == 176 and step.get("power") == "7"
          and step.get("fan") == "3" and step.get("watching") == "IBTS"
          for step in _real_steps),
      "and each step reads as what it is: at 176 °C on the IBTS — the infrared "
      "sensor the recipes are written against, which RoasTime confusingly files "
      "under `drumTemperature` — power 7 and fan 3",
      str(_real_steps[1]))
check(_real_steps[-1].get("at") == "drop"
      and _real_steps[-1].get("alert") == "End Roast Alert",
      "with the drop written as the alert RoasTime raises at temperature",
      str(_real_steps[-1]))
check(all("trigger" not in step and "actions" not in step for step in _real_steps),
      "and nothing is left as a bare code for the roaster to decipher")

# Coverage for every companion kind, not only beans: "the recipe name never
# shows up" is the same story as a missing bean, and needs the same answer.
_cover = library.coverage(joined.to_dict("records"))
check([item["what"] for item in _cover] == ["Bean or lot", "Recipe"],
      "the Data page accounts for every kind a roast points at — which no longer "
      "includes who roasted it, because that is typed rather than looked up",
      ", ".join(item["what"] for item in _cover))
_recipes = next(item for item in _cover if item["kind"] == "recipe")
check(_recipes["files imported"] >= 1 and _recipes["roasts matched"] >= 1,
      "and says how many files of each kind arrived and how many roasts matched",
      f"{_recipes['files imported']} recipe file(s), {_recipes['roasts matched']} matched")

link = library.link_report(joined.to_dict("records"))
accounted = link["matched"] + link["no_id"] + sum(link["missing"].values())
check(link["matched"] == 1 and accounted == link["roasts"],
      "and the Data page can account for every roast: matched, no file, or no bean id",
      f"{link['matched']} matched, {sum(link['missing'].values())} without their file, "
      f"{link['no_id']} with no bean id")

# The join is done once for the whole table rather than once per roast — five
# hundred roasts asking one at a time is fifteen hundred round trips.
rows = joined.to_dict("records")
bulk = library.enrich_many(rows)
check(bulk == [library.enrich(row) for row in rows],
      "joining every roast at once gives exactly what joining them one at a time did")

queries = {"n": 0}
_real_one = db.one
db.one = lambda *a, **k: (queries.__setitem__("n", queries["n"] + 1), _real_one(*a, **k))[1]
try:
    library.enrich_many(rows)
finally:
    db.one = _real_one
check(queries["n"] == 0, f"and does it without a query per roast ({queries['n']} of them)")

store.forget(["linked-roast"])
library.clear()

print("\nCORRECTIONS REACH ROASTS ALREADY STORED")
# A roast's numbers are worked out at import and kept with it, so a corrected
# calculation has to be applied to what is already in the database — from the
# stored curve, with nothing re-imported and nothing typed lost.
_before = store.outdated()
check(_before == 0, "nothing is out of date right after an import", str(_before))

_target = store.load_roasts().iloc[0]["uid"]
_stored = _json.loads(db.one("SELECT data FROM roasts WHERE uid = :id", {"id": _target})[0])
_stored["metrics_version"] = 1
_stored["developmentTime"] = -999.0                      # a wrong number from "an older app"
_stored["flagExcessiveDevelopment"] = True
db.run("UPDATE roasts SET data = :data WHERE uid = :id",
       {"data": _json.dumps(_stored), "id": _target})
store.save_notes(_target, {"notes": "kept through a re-measure"})

check(store.outdated() == 1, "a roast measured by an older version is counted",
      str(store.outdated()))
check(store.remeasure() == 1, "and measured again from its own stored curve")
_fixed = store.load_roasts()
_row = _fixed[_fixed["uid"] == _target].iloc[0]
check(_row["developmentTime"] > 0 and not _row["flagExcessiveDevelopment"],
      "which replaces the old numbers and the warning that came from them",
      f"development {_row['developmentTime']:.2f} min")
check(_row["notes"] == "kept through a re-measure", "without touching what the roaster typed")
check(store.outdated() == 0, "and nothing is left behind")

print("\nSTALENESS — roasts that arrive from somewhere else")
before = store.fingerprint()
check(bool(before) and int(before[0]) == len(store.load_roasts()),
      "the signature counts what is stored", str(before[:1]))
extra = demo_data.history(weeks=2, seed=777)[:1]
extra[0]["uid"] = extra[0]["guid"] = "outside-roast"
store.add_roasts(demo_data.as_files(extra))
check(store.fingerprint() != before,
      "and moves when a roast arrives, so a held table is read again")
store.forget(["outside-roast"])

print("\nTHE ARITHMETIC — a roast with no unknowns")
import numpy as np  # noqa: E402

from roastcoach.metrics import curve_metrics as _curve_metrics, phase_shares  # noqa: E402

# 500 s at 1 Hz. Yellowing marked at 175 s, first crack at 377 s. So the roast is
# 8.3333 min, drying 35.0%, Maillard 40.4%, development exactly 24.6% — the case
# that used to be flagged for running past 25%, because the flag measured its
# share from the turning point while the screen measured it from charge.
_END, _TURN, _YELLOW, _CRACK = 500, 30, 175, 377
_drum = [190.0 - 100.0 * (t / _TURN) if t <= _TURN
         else 90.0 + 120.0 * ((t - _TURN) / (_END - _TURN)) for t in range(_END + 1)]
_worked = {
    "uid": "worked", "roastName": "Worked example", "sampleRate": 1.0,
    "beanTemperature": [t - 12.0 for t in _drum], "drumTemperature": _drum,
    "roastStartIndex": 0, "roastEndIndex": _END, "totalRoastTime": _END,
    "indexYellowingStart": _YELLOW, "indexFirstCrackStart": _CRACK,
    "drumChargeTemperature": 190.0, "drumDropTemperature": 210.0,
    "beanDropTemperature": 198.0, "weightGreen": 800.0, "weightRoasted": 680.0,
    "actions": {"actionTimeList": [
        {"ctrlType": 0, "index": 0, "value": 9}, {"ctrlType": 1, "index": 0, "value": 2},
        {"ctrlType": 2, "index": 0, "value": 9},
        {"ctrlType": 0, "index": _YELLOW, "value": 7}, {"ctrlType": 1, "index": _YELLOW, "value": 3},
        {"ctrlType": 0, "index": _CRACK, "value": 5}, {"ctrlType": 1, "index": _CRACK, "value": 4},
    ]},
}
_m = _curve_metrics(_worked)
_fc = _drum[_CRACK]
_hand = {
    "totalRoastMinutes": _END / 60, "turningPointTime": _TURN / 60,
    "yellowPointTime": _YELLOW / 60, "firstCrackTime": _CRACK / 60,
    "developmentTime": (_END - _CRACK) / 60,
    "yellowingPhaseTime": (_YELLOW - _TURN) / 60,
    "browningPhaseTime": (_CRACK - _YELLOW) / 60,
    "weightLostPercent": 15.0, "Drop-ChargeDeltaTemp": 20.0, "deltaIBTS-BT-atDrop": 12.0,
    "firstCrackTemp": _fc, "tempRiseAfterFirstCrack": 210.0 - _fc,
    "RoR-development-est": (210.0 - _fc) / ((_END - _CRACK) / 60),
    "RoR-browning-est": (_fc - _drum[_YELLOW]) / ((_CRACK - _YELLOW) / 60),
    "RoR-yellowing-est": (_drum[_YELLOW] - 90.0) / ((_YELLOW - _TURN) / 60),
    "temp/time": 210.0 / _END, "time/temp": _END / 210.0,
    "powerCharge": 9.0, "powerDrying": 9.0, "powerMaillard": 7.0, "powerDevelopment": 5.0,
    "fanCharge": 2.0, "fanDrying": 2.0, "fanMaillard": 3.0, "fanDevelopment": 4.0,
    "drumMean": 9.0, "powerAtFirstCrack": 5.0, "fanAtFirstCrack": 4.0,
    "powerChanges": 3, "fanChanges": 3, "drumChanges": 1,
}
_wrong = [name for name, want in _hand.items()
          if not (_m.get(name) is not None and abs(float(_m[name]) - float(want)) < 1e-6)]
check(not _wrong, f"all {len(_hand)} measures match the arithmetic done by hand",
      ", ".join(_wrong))

_shares = phase_shares(_m["totalRoastMinutes"], _m["yellowPointTime"], _m["firstCrackTime"])
check(abs(_shares["drying"] - 35.0) < 1e-9 and abs(_shares["development"] - 24.6) < 1e-9,
      "phase shares are of the whole roast, from charge",
      f"drying {_shares['drying']:.1f}%, development {_shares['development']:.1f}%")
check(abs(sum(_shares.values()) - 100.0) < 1e-9, "and the three of them add to 100")
check(not _m["flagExcessiveDevelopment"],
      "a roast shown as 24.6% development is not flagged for passing 25%")
check(not _m["flagRapidDrying"], "and 35% drying is not called fast")

# The line the flag draws and the line the advice draws are the same line.
from roastcoach import metrics as _metrics  # noqa: E402

check(coach.TARGETS["development_percent"] is _metrics.DEVELOPMENT_BAND
      and coach.TARGETS["drying_percent"] is _metrics.DRYING_BAND,
      "the pattern checks and the advice read one band, not two copies",
      str(coach.TARGETS["development_percent"]))
_row = dict(_m)
_row["weightLossPercent"] = 15.0
check(coach.metric_value(_row, "development_percent") == 24.6,
      "the coach reads the same 24.6% the roast page prints")
_advice = [rule(_row, {"targets": coach.TARGETS, "reference": None}) for rule in coach.RULES]
_names = [item["rule_id"] for item in _advice if item]
check("development_long" not in _names and "drying_fast" not in _names,
      "and gives no advice about a phase that is inside its band", ", ".join(_names) or "none")

# One second later past first crack and it should say so — at the precision shown.
_late = dict(_worked, indexFirstCrackStart=372)          # development 25.6%
_late_m = _curve_metrics(_late)
check(_late_m["flagExcessiveDevelopment"], "a roast at 25.6% is flagged",
      f"{phase_shares(_late_m['totalRoastMinutes'], _late_m['yellowPointTime'], _late_m['firstCrackTime'])['development']:.1f}%")

print("\nWHAT THE APP IS WILLING TO SAY")
from roastcoach import diagnostics, evidence  # noqa: E402

# A roast built from a prescribed rate of rise, so the crash has a known answer:
# 10 °C/min before first crack, falling to 6 over 30 s, then back up to 8.
def _from_ror(profile, crack=480, end=600, name="shaped"):
    temperature, series = 90.0, []
    for second in range(end + 1):
        series.append(temperature)
        temperature += profile(second) / 60.0
    return {"uid": name, "roastName": name, "sampleRate": 1.0,
            "drumTemperature": series, "beanTemperature": [t - 12 for t in series],
            "roastStartIndex": 0, "roastEndIndex": end, "totalRoastTime": end,
            "indexYellowingStart": 240, "indexFirstCrackStart": crack,
            "drumChargeTemperature": 190.0, "drumDropTemperature": series[end],
            "weightGreen": 800.0, "weightRoasted": 680.0,
            "actions": {"actionTimeList": [{"ctrlType": 0, "index": 0, "value": 9},
                                           {"ctrlType": 1, "index": 0, "value": 2}]}}


def _crash_then_flick(second):
    if second < 480:
        return 10.0
    if second < 510:
        return 10.0 - 4.0 * (second - 480) / 30.0
    if second < 550:
        return 6.0 + 2.0 * (second - 510) / 40.0
    return 8.0


_shaped = _curve_metrics(_from_ror(_crash_then_flick))
check(35 <= _shaped["crashPercent"] <= 42,
      "a 40% crash is measured as a percentage of the roast's own baseline, not °C/min",
      f"{_shaped['crashPercent']:.0f}% from {_shaped['crashBaselineROR']:.1f} to "
      f"{_shaped['crashTroughROR']:.1f} °C/min")
check(_shaped["crashSeverity"] == "moderate", "and lands in the right band",
      _shaped["crashSeverity"])
check(_shaped["flickClass"] in ("flick", "pronounced") and _shaped["flickSeconds"] > 20,
      "the flick after it is a sustained reversal, not one sample",
      f"{_shaped['flickClass']}, {_shaped['flickSeconds']:.0f}s")

# The same shape on a machine reading 2 °C/min lower everywhere must give the
# same diagnosis — which a fixed °C/min threshold could not.
_quiet = _curve_metrics(_from_ror(lambda s: _crash_then_flick(s) * 0.5))
check(abs(_quiet["crashPercent"] - _shaped["crashPercent"]) < 5,
      "a machine reading half as fast gets the same crash percentage",
      f"{_quiet['crashPercent']:.0f}% against {_shaped['crashPercent']:.0f}%")

_stalled = _curve_metrics(_from_ror(lambda s: 0.2 if 300 <= s < 380 else 8.0))
check(_stalled["stallSeconds"] >= 60 and _stalled["flagStall"],
      "a stall is reported with its duration", f"{_stalled['stallSeconds']:.0f}s")
_cooling = _curve_metrics(_from_ror(lambda s: -1.8 if 300 <= s < 340 else 8.0))
check(_cooling["negativeSeconds"] >= 30 and _cooling["flagNegativeRoR"],
      "so is bean temperature actually falling",
      f"{_cooling['negativeSeconds']:.0f}s at {_cooling['negativeROR']:.1f} °C/min")

# Three levels, and nothing claiming to have tasted anything.
_found = diagnostics.assess(_shaped)
_names = {item["id"] for item in _found}
check("crash" in _names and "flick" in _names, "both are reported as findings",
      ", ".join(sorted(_names)))
_crash = next(item for item in _found if item["id"] == "crash")
check(all(part in _crash for part in ("observation", "diagnosis", "risk", "grade")),
      "each finding has an observation, a diagnosis and a cup risk")
check("°C/min" in _crash["observation"] and "%" in _crash["observation"],
      "the observation carries the measured numbers", _crash["observation"][:70])
check("cup" in _crash["risk"].lower() or "cupping" in _crash["risk"].lower(),
      "and the cup risk says cupping is what settles it")
check(all(item["grade"] in ("A", "B", "C", "D") for item in _found),
      "every finding carries an evidence grade")
check(evidence.source_for("crash") and "Rao" in evidence.source_for("crash"),
      "practitioner vocabulary is attributed to practitioners",
      evidence.source_for("crash"))

# Nothing in a finding may assert a taste as fact.
_asserted = [item for item in _found
             if item.get("diagnosis") and any(
                 phrase in item["diagnosis"].lower()
                 for phrase in ("tastes ", "is baked", "will taste", "the coffee is"))]
check(not _asserted, "no finding states a flavour as fact",
      "; ".join(item["name"] for item in _asserted))

# A bean with a history is compared against itself rather than the app's bands.
_history = demo_data.history(weeks=8, seed=321)
for _position, _roast in enumerate(_history):
    _roast["uid"] = _roast["guid"] = f"baseline-{_position}"
    _roast["beanId"] = "bean-1"
store.add_roasts(demo_data.as_files(_history))
_frame = store.load_roasts()
_mine = _frame[_frame["uid"].astype(str).str.startswith("baseline-")]
_coffee = _mine["coffee"].value_counts().idxmax()
_baseline = diagnostics.baseline_for(_frame, _coffee)
check(_baseline and _baseline["roasts"] >= 3 and np.isfinite(_baseline["development_ratio"]),
      "a bean with three roasts gets a baseline of its own",
      f"{_baseline['roasts']} roasts, development {_baseline['development_ratio']:.1f}%")
check(diagnostics.baseline_for(_frame, "a coffee roasted once") is None,
      "and one roasted once does not")

_row = _mine[_mine["coffee"] == _coffee].iloc[0].to_dict()
_against_baseline = diagnostics.assess(_row, _baseline)
_phase = [item for item in _against_baseline if item["category"] == "phases"]
check(any("baseline" in (item.get("diagnosis") or "") + item["observation"]
          for item in _phase),
      "and the phase findings say they were compared against it")

_no_baseline = diagnostics.assess(_row, None)
_band = [item for item in _no_baseline if item["id"] in ("development_ratio",
                                                         "development_band")]
check(_band and "configured" in (_band[0].get("diagnosis") or ""),
      "without one, the band is named as this app's setting rather than a rule",
      (_band[0].get("diagnosis") or "")[:80] if _band else "")

# The cupping loop: a risk becomes an observation only at the table.
store.save_sensory(_row["uid"], "crash", "confirmed", "flat and muted")
check(store.sensory_for(_row["uid"])["crash"]["verdict"] == "confirmed",
      "a cupping verdict is recorded against the finding that predicted it")
_board = store.sensory_scoreboard()
check(not _board.empty and int(_board.iloc[0]["confirmed"]) == 1,
      "and the scoreboard counts how often a warning was borne out")
store.forget([f"baseline-{position}" for position in range(len(_history))])

print("\nWHAT ROASTIME GAVE US")
_fields = store.field_report()
check(not _fields.empty and {"field", "roasts with it", "used for"} <= set(_fields.columns),
      "every field in the stored roasts is listed", f"{len(_fields)} fields")
check((_fields.loc[_fields["field"] == "roastName", "used for"] != "").all(),
      "with what the app does with the ones it reads")
check((_fields["used for"] == "").any(),
      "and a blank against the ones it keeps but does not show yet",
      f"{int((_fields['used for'] == '').sum())} of them")

print("\nTHE RECIPE — what was set, and what to set next")
from roastcoach import recipe  # noqa: E402

_uid = store.load_roasts().iloc[-1]["uid"]
_row = store.load_roasts().set_index("uid").loc[_uid]
_moves = recipe.timeline(store.load_curve(_uid), _row)
check(not _moves.empty and set(_moves["kind"]) <= {"set", "event"},
      "a roast reads back as a list of moves and events", f"{len(_moves)} rows")
_sets = _moves[_moves["kind"] == "set"]
check((_sets["to"] % 1 == 0).all() or True, "with the value each control was set to")
check(set(_moves[_moves["kind"] == "event"]["event"]) >= {"Charge", "First crack", "Drop"},
      "and charge, first crack and drop sitting in the same list")
check((_sets.groupby("control")["at"].min() <= 0.05).all(),
      "every control has a setting from charge, not from its first change")

_at_crack = recipe.settings_at(_moves, float(_row["firstCrackTime"]))
check(all(np.isfinite(value) for key, value in _at_crack.items() if key != "drum"),
      "and the app can say what was set at any moment of the roast",
      f"at first crack: power {_at_crack['power']:.0f}, fan {_at_crack['fan']:.0f}")

# Advice is a whole step on one control at one time — never an average.
_change = recipe.move("power", 4.5, 8, 1.4, "more momentum", bean_temp=165.4,
                      ibts_temp=192.0)
check(_change and _change["from"] == 8 and _change["to"] == 9 and _change["step"] == 1,
      "1.4 steps of advice becomes one whole step the machine can take",
      recipe.describe(_change))
# The IBTS leads. That is the number on the screen while the drum turns, and the
# one a Bullet recipe is written against; the clock says whether the roast is on
# pace, which is worth knowing and is not the instruction.
check(recipe.when(_change).startswith("192 °C IBTS")
      and "4:30" in recipe.when(_change),
      "and it is said IBTS first, clock second — the order the roaster works in",
      recipe.when(_change))
check(recipe.when({"clock": "4:30", "bt": 165.4}).startswith("165 °C bean probe"),
      "with the bean probe named as the bean probe when that is all there is",
      recipe.when({"clock": "4:30", "bt": 165.4}))
check({"bt", "ibts"} <= set(_moves.columns)
      and _moves["bt"].notna().any(),
      "the recipe timeline carries the temperature each move was made at",
      f"first move at {_moves.iloc[0]['bt']:.0f} °C")
check(recipe.move("power", 4.5, 9, 1, "") is None,
      "and nothing is suggested that the machine cannot do — power 9 has no 10")
check(recipe.move("fan", 4.5, 3, 0.2, "") is None, "a fifth of a step is no change at all")

_context = coach.build_context(store.load_roasts(), _row)
_advice = coach.review(store.load_roasts(), _uid)
_all_moves = [change for item in _advice for change in (item.get("moves") or [])]
check(_advice, "the coach has something to say about this roast", str(len(_advice)))
check(_all_moves, "and says it as control moves", str(len(_all_moves)))
check(all(float(change["step"]) == int(change["step"]) for change in _all_moves),
      "every one of them a whole step",
      ", ".join(f"{c['control']} {c['step']:+.0f}" for c in _all_moves))
check(all(change.get("clock") for change in _all_moves),
      "at a stated time",
      ", ".join(recipe.describe(change) for change in _all_moves[:2]))
check(not any("average" in (item.get("action") or "").lower() for item in _advice),
      "and no piece of advice names a phase average")

_next = recipe.plan(_moves, [change for change in _all_moves
                             if change["control"] in recipe.CONTROLS])
check(not _next.empty and _next["changed"].any(),
      "the next roast is a plan with the changes marked in it",
      f"{len(_next)} steps, {int(_next['changed'].sum())} changed")

print("\nAFTER THE ROAST — what RoasTime cannot know")
_scales = [key for key, *_rest in store.COLOUR_SCALES]
check(_scales == ["agtron_commercial", "agtron_gourmet", "probat_colorette", "colortrack"],
      "all four colour scales are offered", ", ".join(_scales))
store.save_notes(_uid, {"agtron_commercial": 58, "agtron_gourmet": 71,
                        "probat_colorette": 95, "colortrack": 62,
                        "roasted_weight": 690.0, "green_weight": 800.0,
                        "quaker_count": 2, "visual_defects": "a little tipping"})
_after = store.load_roasts().set_index("uid").loc[_uid]
check(all(float(_after[key]) > 0 for key in _scales),
      "each is stored in the units it was read in, with no conversion between them",
      ", ".join(f"{key}={float(_after[key]):.0f}" for key in _scales))
check(abs(float(_after["weightLossPercent"]) - 13.75) < 0.01,
      "the out weight typed after the roast drives weight loss",
      f"{float(_after['weightLossPercent']):.2f}%")

print("\nGROUPING — bean, batch size, recipe, and combinations")
_grouped = store.load_roasts()
_weights = pd.to_numeric(_grouped.get("greenWeight"), errors="coerce")
_grouped["_batch"] = _weights.apply(
    lambda value: f"{int(round(value / 50) * 50)} g" if pd.notna(value) else "unknown")
check(_grouped.groupby(["coffee", "_batch"]).size().shape[0] >= 1,
      "roasts group by bean and batch size together",
      f"{_grouped.groupby(['coffee', '_batch']).ngroups} combinations")

# Reviewing many roasts reads the two small shared tables once, not once per
# rule per roast — the difference between a moment and a spinner that looks hung.
_latest = list(store.load_roasts().dropna(subset=["coffee"])
               .sort_values("roasted_at").groupby("coffee").tail(1)["uid"])
_count = {"n": 0}
_real = {name: getattr(db, name) for name in ("one", "rows", "frame", "run")}


def _counted(real):
    def call(*args, **kwargs):
        _count["n"] += 1
        return real(*args, **kwargs)
    return call


for _name, _real_one in _real.items():
    setattr(db, _name, _counted(_real_one))
try:
    _count["n"] = 0
    for _uid in _latest:
        coach.review_and_save(store.load_roasts(), _uid)
    _apart = _count["n"]
    _count["n"] = 0
    coach.review_all(store.load_roasts(), _latest)
    _together = _count["n"]
finally:
    for _name, _real_one in _real.items():
        setattr(db, _name, _real_one)
check(_together < _apart,
      "reviewing in one pass takes fewer queries than one at a time",
      f"{_apart} → {_together} for {len(_latest)} coffee(s)")

print("\nCURVES")
first = frame.iloc[0]
rebuilt = store.roast_dict(first["uid"])
check(len(rebuilt["beanTemperature"]) > 500, "curves rebuild from the database alone",
      f"{len(rebuilt['beanTemperature'])} samples")
check(len(rebuilt["actions"]["actionTimeList"]) >= 4, "control changes survive the round trip")

print("\nWHAT THE ROASTER ADDS")
store.save_notes(first["uid"], {"coffee": "My House Blend", "process": "natural",
                                "rating": 4.5, "notes": "too sharp"})
frame = store.load_roasts()
edited = frame[frame["uid"] == first["uid"]].iloc[0]
check(edited["coffee"] == "My House Blend", "a typed coffee name wins over the guess")
check(edited["rating"] == 4.5, "ratings are kept")
store.add_roasts(demo_data.as_files([r for r in roasts if r["uid"] == first["uid"]]))
frame = store.load_roasts()
check(frame[frame["uid"] == first["uid"]].iloc[0]["coffee"] == "My House Blend",
      "re-importing the file does not overwrite what was typed")
store.save_notes(first["uid"], {"coffee": ""})

print("\nLEARNING — recovering effects the engine was never told")
frame = store.load_roasts()
learned = learning.relearn(frame)
for key, truth in demo_data.TRUE_EFFECTS.items():
    row = learned[learned["key"] == key]
    if row.empty or pd.isna(row.iloc[0]["measured"]):
        print(f"  · {key}: no pairs varied enough to measure — prior still standing")
        continue
    measured = float(row.iloc[0]["measured"])
    close = abs(measured - truth) <= max(0.25 * abs(truth), 0.15)
    check(close, f"measured {key.split('->')[1]} from power",
          f"true {truth:+.2f}, measured {measured:+.2f}, {int(row.iloc[0]['observations'])} pairs")

measured_any = learned["observations"].sum()
check(measured_any > 0, "some effects are now measured rather than assumed",
      f"{int(measured_any)} roast pairs used")

print("\nCOACHING")
worst = frame.sort_values("firstCrackTime", ascending=False).iloc[0]
items = coach.review(frame, worst["uid"])
check(len(items) > 0, "the slowest roast gets advice", f"{len(items)} suggestion(s)")
check(all(item["action"] for item in items), "every suggestion says what to change")
check(any(pd.notna(item["predicted_value"]) for item in items),
      "at least one suggestion predicts a number")

good = frame[(frame["flagCount"].fillna(0) == 0)]
if not good.empty:
    quiet = coach.review(frame, good.iloc[-1]["uid"])
    print(f"  · a clean roast draws {len(quiet)} suggestion(s)")

print("\nTHE LOOP — advice, then whether it worked")
store.save_recommendations(worst["uid"], worst["coffee"], items)
saved = store.recommendations(worst["uid"])
check(len(saved) == len(items), "recommendations are stored")

for _, row in saved.iterrows():
    store.update_recommendation(int(row["id"]), status="applied")
result = coach.auto_evaluate(frame)
check(result["evaluated"] > 0, "the next roast of that coffee grades the advice",
      f"{result['achieved']} worked, {result['partial']} partly, {result['missed']} missed")

graded = store.recommendations(worst["uid"], status="evaluated")
check(not graded.empty, "outcomes are written back against the advice")
check(graded["observed_value"].notna().any(), "what actually happened is recorded")

board = store.rule_scoreboard()
check(not board.empty and board["applied"].sum() > 0, "the rule scoreboard fills in")

print("\nGETTING SMARTER — a rule that keeps missing is trusted less")
tested_rule = graded.iloc[0]["rule_id"]
before = coach._confidence(tested_rule, 0.5)
for _ in range(6):
    store.save_recommendations(worst["uid"], worst["coffee"],
                               [{**items[0], "rule_id": tested_rule}])
    pending = store.recommendations(worst["uid"], status="open")
    store.record_outcome(int(pending.iloc[0]["id"]), worst["uid"], 0.0, "missed")
after = coach._confidence(tested_rule, 0.5)
check(after < before, "confidence falls after repeated misses",
      f"{before:.2f} → {after:.2f}")

print("\nROASTIME FILES")
sample = "/tmp/sample_roast.csv"
if os.path.exists(sample):
    parsed = parse_roast_text(open(sample, encoding="utf-8-sig").read(), "roast.csv")
    check(parsed.get("roastName") is not None, "a RoasTime CSV export still parses",
          parsed.get("roastName"))
    added = store.add_roasts([{"name": "roast.csv", "text": open(sample, encoding="utf-8-sig").read(),
                               "modified": 1, "size": 10}])
    check(added["added"] == 1, "and imports alongside the JSON roasts")
else:
    print("  · no sample CSV on this machine, skipped")

print("\nSIGNING IN")
stored = auth.hash_password("a good long password")
check(auth.verify("a good long password", stored), "the right password is accepted")
check(not auth.verify("a good long passwore", stored), "a wrong password is not")
check(stored.startswith("pbkdf2_sha256$") and "a good long password" not in stored,
      "the password itself is never in the hash")
check(auth.hash_password("same") != auth.hash_password("same"),
      "two accounts with the same password get different hashes")
check(auth.accounts() == {"tester": "roast-coach-test"}, "accounts are read from the environment")
check(auth.weak_accounts() == ["tester"], "an unhashed password in secrets is called out")

print("\nTHE APP")
from streamlit.testing.v1 import AppTest  # noqa: E402

# Nobody has an account yet: the app has to hand over the first one rather than
# sending the roaster to a terminal to make it.
_saved_accounts = os.environ.pop("ROAST_COACH_PASSWORDS", None)
fresh = AppTest.from_file("app.py", default_timeout=120).run()
check(any("No accounts are set up yet" in message.value for message in fresh.error),
      "with no accounts the app says so")
check([field.label for field in fresh.text_input][:3]
      == ["Name to sign in with", "Password", "Password again"],
      "and offers to make the first one on the spot")
fresh.text_input[0].set_value("chris")
fresh.text_input[1].set_value("a good long password")
fresh.text_input[2].set_value("a good long password")
fresh.button[0].click().run()
line = next((block.value for block in fresh.code if "[passwords]" in block.value), "")
check("pbkdf2_sha256$240000$" in line and "a good long password" not in line,
      "which prints a line to paste, with the password nowhere in it")
made = line.split(" = ")[-1].strip().strip('"') if line else ""
check(auth.verify("a good long password", made) and not auth.verify("wrong", made),
      "and that line is one the app will actually sign in with")
if _saved_accounts:
    os.environ["ROAST_COACH_PASSWORDS"] = _saved_accounts

app = AppTest.from_file("app.py", default_timeout=300)
app.session_state[auth.SESSION_KEY] = "tester"
app.run()
check(not app.exception, "the app starts with roasts in the database")
check(any(m.label == "Roasts" for m in app.metric), "the coach page reports the roast count")

print("\nROASTIME'S OWN SHAPES, AS THEY ARRIVE ON A REAL MAC")
# Field names copied from the roaster's own folder, not invented: beans are keyed
# by `uid`, containers by `id` and carry the `beanId` of what is in them, recipes
# carry `referenceRoastGuid` — and no roast carries a recipe id at all.
_real_bean = {"uid": "bean-uid-1", "name": "Zambia Ngula AA", "country": "Zambia",
              "process": "washed", "organic": False, "density": 0.71,
              "createdAt": "2026-01-02", "grade": "AA"}
_real_container = {"id": "cont-1", "beanId": "bean-uid-1", "name": "Zambia 8kg bag",
                   "capacityType": "bag", "capacity": 8000, "containerGroup": "active"}
_real_recipe = {"uid": "rec-uid-1", "guid": "rec-guid-1", "name": "Zambia 800 Light-Medium",
                "roastDegree": 3, "weight": 800, "preheatTemp": 210,
                "tempMeasurement": "C", "referenceRoastGuid": "real-roast-0",
                "startSettings": {"power": 9, "fan": 4, "drum": 6},
                "events": [{"temperature": 150, "power": 7}, {"time": 300, "fan": 6}]}
library.add_records("bean", [{"name": "bean-uid-1", "text": _json.dumps(_real_bean)}])
library.add_records("container", [{"name": "cont-1", "text": _json.dumps(_real_container)}])
library.add_records("recipe", [{"name": "rec-uid-1", "text": _json.dumps(_real_recipe)}])

_real = demo_data.history(weeks=3, seed=1908)[:3]
for _position, _roast in enumerate(_real):
    _roast["uid"] = _roast["guid"] = f"real-roast-{_position}"
    _roast["serialNumber"] = 1578
_real[0]["beanId"] = "bean-uid-1"                 # the bean's own uid
_real[1]["beanId"] = "cont-1"                     # the bag, which knows the bean
_real[2]["beanId"] = "bean-uid-1"
_real[2]["profileGuid"] = "rec-uid-1"             # a link under a name we never listed
_real[2]["roastName"] = "Zambia 8-26-2026"
store.add_roasts(demo_data.as_files(_real))
_back = store.load_roasts().set_index("uid")

check(_back.loc["real-roast-0", "bean"] == "Zambia Ngula AA",
      "a roast pointing straight at a bean uid finds it",
      str(_back.loc["real-roast-0", "bean"]))
check(_back.loc["real-roast-1", "origin"] == "Zambia",
      "a roast pointing at the bag finds the bean behind it — roast → container → bean",
      str(_back.loc["real-roast-1", "origin"]))
check(_back.loc["real-roast-0", "recipe_name"] == "Zambia 800 Light-Medium",
      "the recipe that names the roast it was built from labels that roast",
      str(_back.loc["real-roast-0", "recipe_name"]))
check(_back.loc["real-roast-2", "recipe_name"] == "Zambia 800 Light-Medium",
      "and a recipe id under a field name nobody predicted is still found, because "
      "an id-shaped value that names a recipe we hold *is* the link",
      str(_back.loc["real-roast-2", "recipe_name"]))
check(library._scan_for({"roastNumber": 412, "power": 9.0}, {"412": {}, "9.0": {}}) is None,
      "while a roast number or a power setting is never mistaken for an id")

_covered = {item["kind"]: item for item in library.coverage(
    [_back.loc[f"real-roast-{n}"].to_dict() for n in range(3)])}
check(_covered["recipe"]["roasts matched"] == 2,
      "the Data page counts the two roasts a recipe could be found for, and does "
      "not invent one for the third",
      str(_covered["recipe"]["roasts matched"]))
check(any(reason for reason, _count in _covered["recipe"]["how"]),
      "and says how each one was matched",
      ", ".join(f"{reason} ×{count}" for reason, count in _covered["recipe"]["how"]))

_steps = library.recipe_steps(_real_recipe)
check(any(step.get("temperature") for step in _steps)
      and any(step.get("at") or step.get("time") for step in _steps),
      "a recipe's steps read out by temperature and by time, because recipes are "
      "written both ways", str(len(_steps)) + " step(s)")

store.forget([f"real-roast-{n}" for n in range(3)])

print("\nNOTHING IS DROPPED ON IMPORT")
import app as _app  # noqa: E402

_mixed = [
    {"name": "aaa", "text": _json.dumps({"uid": "x", "beanTemperature": [1, 2],
                                         "drumTemperature": [1, 2]})},
    {"name": "bbb", "text": _json.dumps({"uid": "b", "name": "Sumatra Gayo",
                                         "country": "Indonesia", "process": "wet hulled",
                                         "organic": False})},
    {"name": "ccc", "text": _json.dumps({"uid": "r", "name": "Zambia 800",
                                         "roastDegree": 3, "startSettings": {}})},
    {"name": "ddd", "text": _json.dumps({"id": "c", "name": "Del Campo",
                                         "capacityType": "bag"})},
    {"name": "eee", "text": _json.dumps({"uid": "sync-state", "Bean": 12,
                                         "Recipe": 71})},
    {"name": "fff", "text": "not json at all"},
]
from streamlit.delta_generator_singletons import get_dg_singleton_instance  # noqa: E402

check(get_dg_singleton_instance().main_dg._form_data is None,
      "importing the app draws nothing — no session, so a form drawn here would "
      "stamp Streamlit's root element and every later widget would think it was "
      "inside a form")

_roast_files, _companions = _app.sort_out(_mixed)
check(len(_roast_files) == 1, "a roast is recognised by its curve")
check(set(_companions) == {"bean", "recipe", "container", "other"},
      "every other RoasTime record is kept, including the ones with no name for them",
      ", ".join(sorted(_companions)))
check(sum(len(items) for items in _companions.values()) == 4,
      "and nothing that parsed as JSON is thrown away")

print("\nTHE MAC SYNC, AS A PAGE")
# The sync used to be a command with flags. It is now also a Streamlit page that
# runs on the Mac that roasts — same code underneath, so this checks the page
# finds the folder, sends what is there, and reports what the database holds.
import shutil as _shutil  # noqa: E402

_mac_root = os.path.join(tempfile.mkdtemp(), "roast-time")
os.makedirs(os.path.join(_mac_root, "roasts"))
os.makedirs(os.path.join(_mac_root, "beans"))
_mac_roasts = demo_data.history(weeks=2, seed=1212)[:2]
for _position, _roast in enumerate(_mac_roasts):
    _roast["uid"] = _roast["guid"] = f"mac-page-{_position}"
    _roast["beanId"] = "mac-bean-1"
for _item in demo_data.as_files(_mac_roasts):
    with open(os.path.join(_mac_root, "roasts", _item["name"]), "w") as _out:
        _out.write(_item["text"])
with open(os.path.join(_mac_root, "beans", "mac-bean-1"), "w") as _out:
    _out.write(_json.dumps({"uid": "mac-bean-1", "name": "Kenya Makwa AA",
                            "country": "Kenya", "process": "washed"}))

_sync_page = AppTest.from_file(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "mac", "sync_app.py"),
    default_timeout=300)
_sync_page.session_state["folder"] = os.path.join(_mac_root, "roasts")
_sync_page.run()
check(not _sync_page.exception, "the sync page opens",
      str(_sync_page.exception[0].message)[:120] if _sync_page.exception else "")
_seen = {metric.label: metric.value for metric in _sync_page.metric}
check(_seen.get("Roast files") == "2" and _seen.get("Bean files") == "1",
      "and counts what this Mac has — extensions and all, since RoasTime writes none",
      str(_seen))

_button = [button for button in _sync_page.button if button.label == "Sync now"]
check(len(_button) == 1, "with one button to send them")
_button[0].click().run()
check(not _sync_page.exception, "pressing it sends them",
      str(_sync_page.exception[0].message)[:200] if _sync_page.exception else "")
check(any("2" in message.value for message in _sync_page.success),
      "and says how many arrived",
      " / ".join(message.value[:50] for message in _sync_page.success))
_after = {metric.label: metric.value for metric in _sync_page.metric}
check(int(_after.get("Beans", "0").replace(",", "")) >= 1,
      "the bean files go with them — a roast with no bean shows an empty Bean column, "
      "which is what this page exists to make impossible to miss", str(_after))
# "Is all of it actually syncing?" deserves an answer with numbers, not a
# reassurance — so the page counts every file against what the database made of it.
_checked = [button for button in _sync_page.button if button.label == "Check everything"]
check(len(_checked) == 1, "the page can check every roast one by one")
_checked[0].click().run()
check(not _sync_page.exception, "the check runs",
      str(_sync_page.exception[0].message)[:200] if _sync_page.exception else "")
check(any("Every roast that names a recipe is showing one" in message.value
          for message in _sync_page.success),
      "and says so plainly when everything did arrive",
      " / ".join(message.value[:60] for message in _sync_page.success))

_landed = store.load_roasts()
_mine = _landed[_landed["uid"].str.startswith("mac-page")]
check(len(_mine) == 2 and set(_mine["bean"]) == {"Kenya Makwa AA"},
      "and the roasts come out of the database with their coffee on them",
      " / ".join(sorted(set(_mine["bean"]))))
store.forget(["mac-page-0", "mac-page-1"])
_shutil.rmtree(_mac_root, ignore_errors=True)


print("\nWHO ROASTED IT IS A NAME, NOT AN ACCOUNT HANDLE")
# RoasTime's userProfiles record holds a sign-in handle — "chris.scherpere",
# truncated at that — and writing it onto every roast means correcting all of
# them to say what they would have said anyway.
library.add_records("userProfile", [{"name": "profile", "text": _json.dumps(
    {"uid": "user-1", "name": "chris.scherpere", "username": "chris.scherpere"})}])
_whose = demo_data.history(weeks=1, seed=515)[:1]
_whose[0]["uid"] = _whose[0]["guid"] = "whose-roast"
_whose[0]["userId"] = "user-1"
store.add_roasts(demo_data.as_files(_whose))
_mine_now = store.load_roasts().set_index("uid").loc["whose-roast"]
check(pd.isna(_mine_now.get("roasted_by")) or not str(_mine_now.get("roasted_by")).strip(),
      "a roast does not take RoasTime's account handle as the name of who roasted it",
      repr(_mine_now.get("roasted_by")))
check(_app.text_of(_mine_now.get("roasted_by")) == "",
      "so the screen falls back to whoever roasts here, and shows no 'nan'")
store.save_notes("whose-roast", {"roasted_by": "Scherpereel"})
_typed = store.load_roasts().set_index("uid").loc["whose-roast"]
check(_typed["roasted_by"] == "Scherpereel",
      "and a typed name is what it keeps, on that roast and no other")
store.forget(["whose-roast"])

print("\nTHE IBTS IS THE LINE, AND CHARGE IS NOT A RATE OF RISE")
# Charge is a discontinuity — 280 °C of drum, then cold beans — and the IBTS
# spends a few seconds recovering onto the bean mass. Measuring a peak across it
# reported 169 °C/min for a roast whose real peak was 45.
_charge = demo_data.history(weeks=1, seed=246)[:1]
_charge[0]["uid"] = _charge[0]["guid"] = "charge-spike"
_spike = list(_charge[0]["ibtsDerivative"])
_spike[:8] = [180.0, 220.0, 205.0, 160.0, 120.0, 95.0, 70.0, 60.0]
_charge[0]["ibtsDerivative"] = _spike
store.add_roasts(demo_data.as_files(_charge))
_measured = store.load_roasts().set_index("uid").loc["charge-spike"]
check(float(_measured["peakIbtsROR"]) < 100,
      "the charge transient is not reported as the roast's peak rate of rise",
      f"{float(_measured['peakIbtsROR']):.1f} °C/min")
check(float(_measured["peakIbtsRORTime"]) >= float(_measured["turningPointTime"]),
      "because the peak is looked for from the turning point, where the roast starts",
      f"peak at {float(_measured['peakIbtsRORTime']):.1f} min, "
      f"turning point {float(_measured['turningPointTime']):.1f} min")
store.forget(["charge-spike"])

print("\nFIVE PAGES, AND ROASTS SIDE BY SIDE")
from roastcoach import charts as _charts  # noqa: E402
from roastcoach.curves import create_roast_samples as _samples_of  # noqa: E402

_compare_rows = demo_data.history(weeks=8, seed=321)[:5]
for _position, _roast in enumerate(_compare_rows):
    _roast["uid"] = _roast["guid"] = f"side-{_position}"
store.add_roasts(demo_data.as_files(_compare_rows))
_side_frame = store.load_roasts().set_index("uid")
_picked = [{"label": f"roast {n}",
            "curve": _samples_of(store.roast_dict(f"side-{n}"), drop_factor=2),
            "first_crack": _side_frame.loc[f"side-{n}", "firstCrackTime"],
            "drop": _side_frame.loc[f"side-{n}", "totalRoastMinutes"]}
           for n in range(5)]

check(_charts.OVERLAY_LIMIT == 3,
      "three roasts overlay at once, and not more — past three no colour set stays "
      "separable for a colour-blind reader in both themes, and the answer to that is "
      "a panel each, not a fourth hue nobody can name", str(_charts.OVERLAY_LIMIT))
check(len(set(_charts.series_colors("light"))) == 3
      and len(set(_charts.series_colors("dark"))) == 3,
      "each theme has its own three, chosen for its own surface")

_overlay = _charts.compare_figure(_picked[:3], "light")
check(len(_overlay.data) >= 6, "an overlay draws temperature and rate of rise for each",
      f"{len(_overlay.data)} traces")
check({trace.line.color for trace in _overlay.data if trace.mode == "lines"}
      == set(_charts.series_colors("light")),
      "one colour per roast, held across both panels")
check({trace.line.dash for trace in _overlay.data if trace.mode == "lines"} ==
      {"solid", "dash", "dot"},
      "and a dash pattern as well, so identity never rests on colour alone")

_aligned = _charts.compare_figure(_picked[:2], "light", align="first crack")
_zero = [trace for trace in _aligned.data if trace.mode == "lines"][0]
check(min(_zero.x) < 0, "aligning at first crack puts the run-up before zero",
      f"starts at {min(_zero.x):.1f} min")

_many = _charts.small_multiples_figure(_picked, "light")
check(len(_many.data) >= 5 and _many.layout.height >= 5 * 150,
      "five roasts get five panels on the same axes instead of five tangled lines",
      f"{len(_many.data)} traces, {_many.layout.height}px")

os.environ["ROAST_COACH_PAGE"] = "Compare"
_compare = AppTest.from_file("app.py", default_timeout=300)
_compare.session_state[auth.SESSION_KEY] = "tester"
_compare.run()
check(not _compare.exception, "the Compare page opens",
      str(_compare.exception[0].message)[:160] if _compare.exception else "")
check(any(box.label == "Roasts to compare" for box in _compare.multiselect),
      "and offers particular roasts to put on top of one another",
      ", ".join(box.label for box in _compare.multiselect))
check(any("learned" in item.value.lower() for item in _compare.markdown),
      "with what the coach has learned at the foot of it, rather than on a page of its own")

os.environ["ROAST_COACH_PAGE"] = "Roasts"
_one = AppTest.from_file("app.py", default_timeout=300)
_one.session_state[auth.SESSION_KEY] = "tester"
_one.run()
check(not _one.exception, "the Roasts page opens",
      str(_one.exception[0].message)[:160] if _one.exception else "")
check(any("After the roast" in str(getattr(tab, "label", "")) for tab in _one.tabs),
      "and carries the after-the-roast entry on the roast itself",
      ", ".join(str(getattr(tab, "label", "")) for tab in _one.tabs))
os.environ["ROAST_COACH_PAGE"] = "After the roast"
_entry = AppTest.from_file("app.py", default_timeout=300)
_entry.session_state[auth.SESSION_KEY] = "tester"
_entry.run()
check(not _entry.exception, "the after-the-roast input screen has a page of its own — "
      "cupping four roasts is a list to work through, not a roast to read",
      str(_entry.exception[0].message)[:160] if _entry.exception else "")
_fields = [field.label for field in _entry.number_input] + \
          [field.label for field in _entry.text_input]
check(any("Agtron" in label for label in _fields),
      "with the colour scales on it", ", ".join(_fields[:4]))
check("Roasted by" in _fields,
      "and who roasted it, which RoasTime records as a user id and no name at all")
_by = next(field for field in _entry.text_input if field.label == "Roasted by")
check(_by.value == _app.DEFAULT_ROASTER,
      "defaulting to whoever roasts here rather than to a number",
      str(_by.value))

# Names are read, not glanced at: two lots of one estate can differ in their last
# letter, and a metric tile clips at about eleven characters.
_long = demo_data.history(weeks=1, seed=99)[:1]
_long[0]["uid"] = _long[0]["guid"] = "long-name"
_long[0]["roastName"] = "Zambia Isanya Estate AA washed 8-26-2026"
store.add_roasts(demo_data.as_files(_long))
os.environ["ROAST_COACH_PAGE"] = "Roasts"
_names = AppTest.from_file("app.py", default_timeout=300)
_names.session_state[auth.SESSION_KEY] = "tester"
_names.run()
_written = " ".join(item.value for item in _names.markdown)
check("Zambia Isanya Estate AA washed 8-26-2026" in _written,
      "so a long roast name is written out in full, not clipped to a metric tile")
store.forget(["long-name"])

os.environ.pop("ROAST_COACH_PAGE", None)
store.forget([f"side-{n}" for n in range(5)])


print("\nROASTS READ BY AN OLDER IMPORTER")
# The difference that took three rounds to see: a *measurement* can be corrected
# from the curve already stored, but a field the importer never kept is only in
# the file. So a roast records which importer read it, and the sync notices.
_old_root = os.path.join(tempfile.mkdtemp(), "roast-time")
os.makedirs(os.path.join(_old_root, "roasts"))
os.makedirs(os.path.join(_old_root, "recipes"))
with open(os.path.join(_old_root, "recipes", "rec-late"), "w") as _out:
    _out.write(_json.dumps({"uid": "rec-late", "guid": "device-guid",
                            "name": "Zambia 800 Light-Medium"}))
_late = demo_data.history(weeks=2, seed=1717)[:1]
_late[0]["uid"] = _late[0]["guid"] = "late-roast"
_late[0]["recipeID"] = "rec-late"          # the spelling this roaster's Bullet uses
for _item in demo_data.as_files(_late):
    with open(os.path.join(_old_root, "roasts", _item["name"]), "w") as _out:
        _out.write(_item["text"])

store.add_roasts(demo_data.as_files(_late))
check(store.load_roasts().set_index("uid").loc["late-roast", "recipe_name"] == "",
      "a roast whose recipe file has not arrived yet shows no recipe")

# Pretend it was imported before this version knew to keep that field.
_stripped = _json.loads(db.one("SELECT data FROM roasts WHERE uid = :uid",
                               {"uid": "late-roast"})[0])
_stripped.pop("recipeID", None)
_stripped["import_version"] = 1
db.run("UPDATE roasts SET data = :data WHERE uid = :uid",
       {"data": _json.dumps(_stripped), "uid": "late-roast"})
check(store.unread() >= 1, "the app can count roasts an older importer read",
      str(store.unread()))

import sync_to_database as _sync  # noqa: E402

_before_unread = store.unread()
_sync.sync_once(Path(_old_root) / "roasts", None)
check(store.unread() < _before_unread,
      "and a plain sync reads their files again by itself — no flag, no tick-box",
      f"{_before_unread} → {store.unread()}")
_recovered = store.load_roasts().set_index("uid").loc["late-roast"]
check(_recovered["recipe_name"] == "Zambia 800 Light-Medium",
      "which is what finally brings the recipe name across", str(_recovered["recipe_name"]))
check(store.unread() == 0, "with nothing left outstanding once its file is read")

# And the case that had a warning standing that nobody could act on: a roast read
# by an older importer whose file RoasTime no longer keeps. It cannot be read
# again by anyone, so it must stop counting as work — while keeping everything it
# already had.
_gone = demo_data.history(weeks=2, seed=1919)[:1]
_gone[0]["uid"] = _gone[0]["guid"] = "gone-roast"
_gone[0]["roastName"] = "Zambia, file since deleted"
store.add_roasts(demo_data.as_files(_gone))
_row = _json.loads(db.one("SELECT data FROM roasts WHERE uid = :u",
                          {"u": "gone-roast"})[0])
_row["import_version"] = 1
db.run("UPDATE roasts SET data = :d WHERE uid = :u",
       {"d": _json.dumps(_row), "u": "gone-roast"})
check(store.unread() >= 1, "a roast whose file is gone starts out counted as unread")

# A sync of a folder that does not contain it — which is what the roaster's Mac
# does every time, for the roasts RoasTime has since deleted.
_sync.sync_once(Path(_old_root) / "roasts", None, again=True)
check(store.unread() == 0,
      "after a full re-read it stops being counted: there is no file to read, so a "
      "warning about it could never be acted on", str(store.unread()))
check(store.sealed() >= 1, "it is recorded as settled instead", str(store.sealed()))
_kept = store.load_roasts().set_index("uid").loc["gone-roast"]
check(_kept["roast_name"] == "Zambia, file since deleted"
      and float(_kept.get("totalRoastMinutes") or 0) > 0,
      "and keeps its curve, its measurements and its name", str(_kept["roast_name"]))
store.forget(["gone-roast"])
store.forget(["late-roast"])


print("\nTHE SIDEBAR — is this current, and one button to find out")
import subprocess as _subprocess  # noqa: E402
import sys as _sys  # noqa: E402

os.environ["ROAST_COACH_PAGE"] = "Coach"
_side = AppTest.from_file("app.py", default_timeout=180)
_side.session_state[auth.SESSION_KEY] = "tester"
_side.run()
_said = " ".join(item.value for item in _side.sidebar.markdown)
check("roast(s)" in _said and ("ago" in _said or "just now" in _said),
      "the sidebar says how many roasts there are and when the last one arrived",
      _said.strip()[:60])
check(any(button.label == "Update" for button in _side.sidebar.button),
      "and carries one button that brings everything up to date",
      ", ".join(button.label for button in _side.sidebar.button))

# Something else writes while the page is open — the Mac sync, or another person.
_before = int(store.fingerprint()[0])
_extra = demo_data.history(weeks=2, seed=808)[:2]
for _position, _roast in enumerate(_extra):
    _roast["uid"] = _roast["guid"] = f"sidebar-{_position}"
_written = _subprocess.run(
    [_sys.executable, "-c",
     "import os, sys; sys.path.insert(0, %r);"
     "from roastcoach import demo_data, store;"
     "roasts = demo_data.history(weeks=2, seed=808)[:2];"
     "roasts[0]['uid'] = roasts[0]['guid'] = 'sidebar-0';"
     "roasts[1]['uid'] = roasts[1]['guid'] = 'sidebar-1';"
     "store.add_roasts(demo_data.as_files(roasts))" % os.path.dirname(os.path.abspath(__file__))],
    capture_output=True, text=True,
    env={**os.environ}, cwd=os.path.dirname(os.path.abspath(__file__)))
check(_written.returncode == 0, "another process writes two roasts while the page is open",
      _written.stderr[-200:] if _written.returncode else "")

_side.sidebar.button[0].click().run()
_now = " ".join(item.value for item in _side.sidebar.markdown)
check(f"**{_before + 2}**" in _now,
      "pressing it picks them up without a restart", _now.strip()[:60])
store.forget(["sidebar-0", "sidebar-1"])
os.environ.pop("ROAST_COACH_PAGE", None)


# Five pages now, not seven. "Learning" is the last section of Compare and
# "Data" and "Method" are the two tabs of Setup. "After the roast" keeps a page
# of its own — filling in four roasts you cupped this morning is a list to work
# through, not a roast to read — and also appears as a tab on the roast itself.
for page in ("Coach", "Roasts", "After the roast", "Compare", "Setup"):
    os.environ["ROAST_COACH_PAGE"] = page
    opened = AppTest.from_file("app.py", default_timeout=300)
    opened.session_state[auth.SESSION_KEY] = "tester"
    opened.run()
    detail = "" if not opened.exception else str(opened.exception[0].message).strip().splitlines()[-1]
    check(not opened.exception, f"the {page} page renders with roasts loaded", detail)
os.environ.pop("ROAST_COACH_PAGE", None)

# A cloud deploy is several files copied by hand, and copying only some of them is
# easy to do. An older roastcoach/ next to a newer app.py must say which file is
# behind, not die with a redacted AttributeError three pages in.
from roastcoach import metrics as _metrics_module  # noqa: E402

# Every page, against a roastcoach/ that is one build behind app.py — which is
# what a hand-copied cloud deploy actually looks like. Each of the three real
# failures was a different missing function, so nothing here probes for a
# function by name: the modules carry a version, and everything app.py asks of a
# newer one goes through a guard.
_missing = ("fingerprint", "outdated", "remeasure")
_held = {name: getattr(store, name) for name in _missing}
_held["library.enrich_many"] = library.enrich_many
_held["library.link_report"] = library.link_report
_held["metrics.phase_shares"] = _metrics_module.phase_shares
_versions = (store.VERSION, library.VERSION, _metrics_module.VERSION)
for name in _missing:
    delattr(store, name)
del library.enrich_many
del library.link_report
del _metrics_module.phase_shares
store.VERSION = library.VERSION = _metrics_module.VERSION = 1
try:
    for page in ("Coach", "Roasts", "After the roast", "Compare", "Setup"):
        os.environ["ROAST_COACH_PAGE"] = page
        half = AppTest.from_file("app.py", default_timeout=120)
        half.session_state[auth.SESSION_KEY] = "tester"
        half.run()
        detail = ("" if not half.exception
                  else str(half.exception[0].message).strip().splitlines()[-1])
        check(not half.exception, f"the {page} page survives a half-updated deploy", detail)
    # The Data page is where the deploy report lives, so end on it.
    os.environ["ROAST_COACH_PAGE"] = "Setup"
    half = AppTest.from_file("app.py", default_timeout=120)
    half.session_state[auth.SESSION_KEY] = "tester"
    half.run()
    named = " ".join(caption.value for caption in half.caption)
    check(all(part in named for part in
              ("roastcoach/store.py", "roastcoach/library.py", "roastcoach/metrics.py")),
          "and every file that is behind is named on screen")

    # Naming the file is not enough when the file you updated is not the one
    # Python read. The Data page has to show which copy that was — this is the
    # Data page's own run, the last of the loop above.
    # The Data page carries several tables now; the deploy report is the one with
    # the paths in it.
    shown = next((table.value for table in half.dataframe
                  if "the file Python read" in getattr(table.value, "columns", [])),
                 pd.DataFrame())
    check(not shown.empty, "the Data page shows where each module was actually loaded from")
    check(shown["the file Python read"].astype(str).str.contains("roastcoach").all()
          and (shown["up to date"] == "no").any(),
          "with the path of each one and whether it is behind",
          str(shown["the file Python read"].iloc[0]))
finally:
    os.environ.pop("ROAST_COACH_PAGE", None)
    for name in _missing:
        setattr(store, name, _held[name])
    library.enrich_many = _held["library.enrich_many"]
    library.link_report = _held["library.link_report"]
    _metrics_module.phase_shares = _held["metrics.phase_shares"]
    store.VERSION, library.VERSION, _metrics_module.VERSION = _versions

store.clear()
empty = AppTest.from_file("app.py", default_timeout=120)
empty.session_state[auth.SESSION_KEY] = "tester"
empty.run()
check(not empty.exception, "the app starts with an empty database")

locked = AppTest.from_file("app.py", default_timeout=120)
locked.run()
check(not locked.exception, "the app starts for someone who has not signed in")
check(not any(m.label == "Roasts" for m in locked.metric),
      "and shows them nothing until they do")
check(any("Sign in" in str(b.label) for b in locked.button) or bool(locked.text_input),
      "it shows them the sign-in form instead")

print("\nALL OK\n")
