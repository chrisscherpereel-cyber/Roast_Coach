# Roast Coach

Analysis and coaching for **Aillio Bullet** roast profiles. It reads the roasts your
machine already recorded, tells you what to change on the next one, and then checks
whether the change did what it said it would.

That last part is the point. A suggestion nobody checks is an opinion. Every suggestion
here carries a number — *first crack should land at 9.0 min instead of 10.8* — and the
next roast of that coffee grades it. Over time the app learns two things: how much a
control change actually moves a measure **on your machine**, and which of its own
suggestions are worth listening to.

Runs entirely in the cloud. Nothing is installed on the machine that does the roasting.

---

## Getting your roasts in

**Connect a folder** — in Chrome or Edge, point the app at a folder of roasts once. The
browser holds that permission, so every later visit picks up whatever is new with a click.
The folder is read in your browser; only the roast files reach the app.

One catch, and it is Chrome's, not the app's: **Chrome refuses to share any folder inside
your system Library**, answering *"this folder contains system files"*. That is exactly
where RoasTime keeps its roasts —

| System | RoasTime's folder |
| --- | --- |
| macOS | `~/Library/Application Support/roast-time/roasts` |
| Windows | `%APPDATA%\roast-time\roasts` |

— so make a copy somewhere ordinary and connect that instead. On macOS:

```bash
mkdir -p ~/Documents/RoastCoach && cp -R ~/Library/Application\ Support/roast-time/roasts/. ~/Documents/RoastCoach/
```

Run it again whenever you want the newer roasts. Nothing ever writes back to RoasTime's
folder — the app only reads.

**Or upload** — drag in roast files or a zip of the folder. Works in every browser, Safari
included, from any folder, with no copying.

Both RoasTime formats are read: the per-roast JSON files, and the single-roast CSV
export. Every roast is identified by **date and coffee**; the coffee is read from the
roast name and you can correct it, along with origin, process, variety, weights, roast
level, rating, cupping score and notes.

There is also a **demo roasting history** — three coffees dialled in over a few months —
so you can see how it works before connecting anything.

---

## The five pages

**Coach** — what to change on your next roast of a given coffee, and the record of what
happened when you took earlier advice.

**Roasts** — every roast by date and coffee. Pick one for its profile chart, its numbers,
its pattern checks, everything you have written about it, and the coach's read on it.

**Coffees** — how a coffee is going: the trend of first crack, development and drop
temperature; how repeatable your roasts of it are; and every roast's rate of rise
overlaid, with your reference roast in front.

**Learning** — what the app has measured from your roasting, against what it assumed
before it had your data, and how often each kind of advice has worked.

**Data** — connect the folder, upload, load the demo, manage what is stored.

---

## How the coaching works

### It looks for things worth saying

Eight rules run over each roast: development too short or too long, a rate-of-rise crash
after first crack, a flick, a stall before first crack, drying that ran fast or dragged, a
late turning point, drift from your reference roast for that coffee, and weight loss
outside the band. A roast that lands inside every target draws no advice at all.

### It says how far, not just which way

Textbook advice says *add power*. Roast Coach says *power up 1.5 steps through Maillard*,
because it has measured what a power step does to the rate of rise on your machine, with
your batch sizes.

That measurement comes from your own roasts. Take two roasts of the same coffee in a row:
the difference in a control setting during one phase, against the difference in the
measure that control drives, is one observation of the effect size. The median across many
pairs is the estimate — the median, so one strange roast cannot drag it around. Every
estimate starts at a textbook value and moves toward yours as pairs accumulate:

```
slope = (3 × textbook + n × measured) / (3 + n)
```

so the first suggestion is never built on a single roast, and after twenty pairs the
textbook value has all but disappeared. The Learning page shows both numbers side by side.

### It grades itself

Mark a suggestion **I'll try this**, roast that coffee again, and the app compares what it
predicted against what happened:

- **worked** — the measure reached the predicted value, or moved past it
- **partly worked** — it moved the right way but fell short
- **did not work** — it did not move, or moved the wrong way

Those outcomes feed back twice. The effect sizes are re-measured with the new roast
included, and each rule keeps a hit rate that lowers the confidence shown on its future
suggestions. Advice that keeps missing on your machine gets quieter.

---

## Pattern checks

Six heuristics run over each roast's curve and become columns you can filter on. They are
conventions from roasting practice, not machine readings — a crash on a light roast you
dropped early may be exactly what you meant.

| Check | Fires when |
| --- | --- |
| RoR crash | rate of rise falls more than 3 °C/min within a minute after first crack, or goes negative |
| RoR flick | after a crash, rate of rise climbs back more than 1.5 °C/min before drop |
| Stall | rate of rise sits below 1.5 °C/min for over 45 s before first crack |
| Late heat | power increased after first crack, or in the last fifth of the roast |
| Long development | development ran past 25% of the roast |
| Fast drying | drying took less than 25% of the roast |

Thresholds are named constants at the top of `roastcoach/metrics.py`.

---

## Running it

```bash
pip install -r requirements.txt
streamlit run app.py
```

Everything is stored in one SQLite file, `roast_coach.db`, beside the app —
`ROAST_COACH_DB` points it elsewhere. Three tables carry the data (`roasts`,
`roast_curve`, `roast_notes`) and three carry the coaching (`recommendations`,
`effects`, `rule_stats`).

```bash
python3 test_roast_coach.py
```

The test suite ends with the one that matters: the simulator builds a roasting history
from effect sizes it never reveals, and the learning engine has to recover them from the
roasts alone. It does — first crack to within 0.00 min per power step, rate of rise to
within 0.13 °C/min.

---

## Layout

```
app.py                      the five pages
roastcoach/
  store.py                  the database: roasts, curves, notes, advice, outcomes
  coach.py                  the rules, the predictions, the grading
  learning.py               effect sizes measured from your own roasts
  metrics.py                turning point, phases, rate of rise, pattern checks
  curves.py                 smoothed series for the charts
  charts.py                 the four figures
  fields.py, csv_import.py  reading RoasTime's two formats
  origin.py                 the coffee's country, read from the roast name
  demo_data.py              a simulated roasting history
  folder.py, frontend/      browser folder access
assets/                     the flame mark: logo-mark.svg (bare), icon.svg (tile),
                            logo-full.svg (lockup), icon-64/256.png
```

The mark is a flame with the roast curve cut out of it — a real mask, so the curve shows
whatever is behind it and the mark works on any ground.

Built on the Aillio Bullet's own recordings. Not affiliated with Aillio.
