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
