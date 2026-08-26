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
machine = {"id": "machine-1", "name": "Bullet R1 V2", "serialNumber": 1578}
library.add_records("bean", [{"name": "bean-1.json", "text": _json.dumps(bean)}])
library.add_records("recipe", [{"name": "recipe-1.json", "text": _json.dumps(recipe)}])
library.add_records("container", [{"name": "machine-1.json", "text": _json.dumps(machine)}])
check(library.counts() == {"bean": 1, "container": 1, "recipe": 1},
      "bean, recipe and machine files are stored", str(library.counts()))

linked = demo_data.history(weeks=2, seed=451)[:1]
linked[0]["uid"] = linked[0]["guid"] = "linked-roast"
linked[0]["beanId"] = "bean-1"
linked[0]["recipeId"] = "recipe-1"
linked[0]["containerId"] = "machine-1"
store.add_roasts(demo_data.as_files(linked))
joined = store.load_roasts()
one = joined[joined["uid"] == "linked-roast"].iloc[0]
check(one["coffee"] == bean["name"], "the roast takes its coffee from the bean file",
      one["coffee"])
check(one.get("origin") == "Costa Rica" and one.get("process") == "washed",
      "origin and process come across without being typed")
check(one.get("recipe_name") == "CRT 800 Md v4" and one.get("machine_name") == "Bullet R1 V2",
      "so do the recipe and the machine")
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

for page in ("Coach", "Roasts", "Coffees", "Learning", "Data"):
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

_held = {"store.fingerprint": store.fingerprint,
         "library.enrich_many": library.enrich_many,
         "metrics.phase_shares": _metrics_module.phase_shares}
del store.fingerprint
del library.enrich_many
del _metrics_module.phase_shares
try:
    half = AppTest.from_file("app.py", default_timeout=120)
    half.session_state[auth.SESSION_KEY] = "tester"
    half.run()
    detail = "" if not half.exception else str(half.exception[0].message).strip().splitlines()[-1]
    check(not half.exception, "a half-updated deploy still runs", detail)
    named = " ".join(caption.value for caption in half.caption)
    check(all(part in named for part in
              ("roastcoach/store.py", "roastcoach/library.py", "roastcoach/metrics.py")),
          "and names every file that is behind")
finally:
    store.fingerprint = _held["store.fingerprint"]
    library.enrich_many = _held["library.enrich_many"]
    _metrics_module.phase_shares = _held["metrics.phase_shares"]

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
