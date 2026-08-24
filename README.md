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

**Press Add roasts, pick the files.** In Chrome and Edge the dialog reopens in whatever
folder you used last, so after the first visit this is: click, select all, done. Files
already imported are skipped in the browser without being opened, so selecting the whole
folder every time costs nothing — and a file that turns up under a new name is recognised
by its contents, not added twice.

RoasTime keeps the originals here:

| System | Folder |
| --- | --- |
| macOS | `~/Library/Application Support/roast-time/roasts` |
| Windows | `%APPDATA%\roast-time\roasts` |

Picking **files** out of those folders works. Picking the **folder itself** does not:
Chrome refuses to share anything inside your system Library, answering *"this folder
contains system files"*. That is a rule about where the folder lives, not about your
files, and it is why the button asks for files. On macOS the dialog can jump straight
there — press **⌘⇧G** and paste the path, once.

Nothing is ever written back to RoasTime's folder. The app only reads.

Under **Other ways in** there is also drag-and-drop (any browser, or a zip of the whole
folder), whole-folder sync for folders Chrome will share, and a **demo roasting history**
— three coffees dialled in over a few months — so you can see how it all works before
connecting anything.

Both RoasTime formats are read: the per-roast JSON files, and the single-roast CSV
export. Every roast is identified by **date and coffee**; the coffee is read from the
roast name and you can correct it, along with origin, process, variety, weights, roast
level, rating, cupping score and notes.

---

## Sharing it with other people

Out of the box the app keeps everything in a SQLite file beside itself, which is fine for
one computer. For a group it needs two things, and **[SETUP.md](SETUP.md) walks through
both in about fifteen minutes**:

- **A shared database.** Point `[database] url` in secrets at a Postgres server — a free
  Supabase project does it — and every computer signed in sees the same roasts. This also
  fixes something easy to miss: Streamlit Community Cloud wipes its own disk on every
  restart, so without this, roasts imported today are gone tomorrow.
- **A sign-in.** `[passwords]` in secrets holds one PBKDF2 hash per person; `make_login.py`
  generates them. Nothing behind the sign-in renders for anyone who has not signed in, so
  the repository and the app URL can both stay public while the roasts stay yours.

Neither secret is in the repository, and the app tells you on the **Data** page which
database it is actually using — so a misconfigured deploy announces itself instead of
quietly losing data.

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

**Data** — add roasts, see where they are stored and who is signed in, load the demo,
manage what is kept.

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

With nothing configured, everything is stored in one SQLite file, `roast_coach.db`,
beside the app — `ROAST_COACH_DB` points it elsewhere, and `ROAST_COACH_DATABASE_URL`
(or `[database] url` in secrets) sends it to Postgres instead. The same SQL runs on
both. Three tables carry the data (`roasts`, `roast_curve`, `roast_notes`) and three
carry the coaching (`recommendations`, `effects`, `rule_stats`); `sources` remembers
which files have been read.

A roast's own fields are stored as JSON in one column, and its whole curve as a single
compressed row rather than fifteen hundred — which is the difference between an import
taking a second and taking a minute over a network. `load_curve()` still hands back the
per-sample table.

```bash
python3 test_roast_coach.py

# and against a real Postgres, which is what it runs on shared:
ROAST_COACH_TEST_POSTGRES="postgresql://…" python3 test_roast_coach.py
```

With a Postgres URL it also checks the thing that matters for a group: a second process
imports roasts, and this one sees them without restarting.

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
  db.py                     Postgres or SQLite, same SQL either way
  auth.py                   who is allowed in
  uploader.py, frontend/    the Add roasts button and whole-folder sync
  fields.py, csv_import.py  reading RoasTime's two formats
  origin.py                 the coffee's country, read from the roast name
  demo_data.py              a simulated roasting history
assets/                     the flame mark: logo-mark.svg (bare), icon.svg (tile),
                            logo-full.svg (lockup), icon-64/256.png
```

The mark is a flame with the roast curve cut out of it — a real mask, so the curve shows
whatever is behind it and the mark works on any ground.

Built on the Aillio Bullet's own recordings. Not affiliated with Aillio.
