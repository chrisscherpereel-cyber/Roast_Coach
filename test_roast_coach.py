"""
Tests for Roast Coach.

The interesting one is the learning test: the simulator generates roasts from
effect sizes it never reveals, and the learning engine has to recover them from
the roasts alone.

    python3 test_roast_coach.py
"""

import os
import tempfile

os.environ["ROAST_COACH_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

import pandas as pd  # noqa: E402

from roastcoach import coach, demo_data, learning, store  # noqa: E402
from roastcoach.fields import parse_roast_text  # noqa: E402

PASS, FAIL = "  ✓", "  ✗"


def check(condition, description, detail=""):
    print(f"{PASS if condition else FAIL} {description}{('  — ' + detail) if detail else ''}")
    assert condition, description


print("\nIMPORT")
store.clear()
roasts = demo_data.history()
report = store.add_roasts(demo_data.as_files(roasts))
check(report["added"] == len(roasts), f"imported all {len(roasts)} simulated roasts")
check(store.add_roasts(demo_data.as_files(roasts))["skipped"] == len(roasts),
      "a second import skips everything unchanged")

frame = store.load_roasts()
check(len(frame) == len(roasts), "every roast comes back out")
check(frame["coffee"].nunique() == 3, "roasts group into three coffees")
check(frame["label"].str.match(r"\d{4}-\d{2}-\d{2} · .+").all(),
      "every roast is identified by date and coffee")

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

print("\nTHE APP")
from streamlit.testing.v1 import AppTest  # noqa: E402

app = AppTest.from_file("app.py", default_timeout=300)
app.run()
check(not app.exception, "the app starts with roasts in the database")
check(any(m.label == "Roasts" for m in app.metric), "the coach page reports the roast count")

store.clear()
empty = AppTest.from_file("app.py", default_timeout=120)
empty.run()
check(not empty.exception, "the app starts with an empty database")

print("\nALL OK\n")
