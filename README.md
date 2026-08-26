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

**Choose folder…** on the Data page opens a file dialog. Pick the folder, and every roast
file inside it goes in at once. Do that whenever you have roasted — anything already
imported is skipped in the browser without being opened, so only new and changed roasts
are actually read, and a file that reappears under another name is recognised by its
contents rather than added twice.

### The one exception, and it is Chrome's

RoasTime writes to `~/Library/Application Support/roast-time/roasts`, and **Chrome will
not accept any folder inside your Library as a folder upload** — it answers *"this folder
contains system files"*. That is Chrome's rule and no web app can override it. macOS also
hides the Library, so it will not show up in the dialog until you go there directly.

Three ways in, in order of least trouble. All three end up in the same place:

1. **Choose files…** — in the dialog press **⌘⇧G**, paste the path, Return, then **⌘A** to
   select every file and Open. Picking files is allowed where picking the folder is not.
2. **Drag it** — in Finder press ⌘⇧G, paste the path, and drag the folder onto the box.
   Dragging is not the picker and has no such rule. While you are there, drag the folder
   into Finder's **sidebar** — one click away from then on.
3. **Zip it** — right-click the folder in Finder, **Compress**, and upload the zip. A zip
   is one ordinary file, so nothing objects to it.

On Windows the folder is `%APPDATA%\roast-time\roasts` and **Choose folder…** takes it
directly.

### Doing none of that, ever again

A browser tab cannot reach into your Library to copy or zip anything for you — it has no
access to your disk until you hand it files. Something on **your Mac** has to do it, on a
schedule. Two ways in `mac/`, both set up once:

- **`mac/roast-sync.command`** — double-click. Copies your roasts to
  `~/Documents/RoastCoach` and offers to keep doing it every fifteen minutes and at login.
  After that **Choose folder…** → Documents → RoastCoach works forever, because that folder
  is an ordinary one. Nothing to install.
- **`mac/sync_to_database.py`** — removes the import step rather than easing it. Writes new
  and changed roasts straight into the shared database, so they appear in the app on their
  own, on every computer. `python3 mac/sync_to_database.py --install` and it runs itself.

`mac/README.md` has both in full. Or do the copy by hand whenever you like:

```bash
mkdir -p ~/Documents/RoastCoach && cp -R ~/Library/Application\ Support/roast-time/roasts/. ~/Documents/RoastCoach/
```

Nothing is ever written back to RoasTime's folder. The app only reads.

### Everything else RoasTime keeps

A roast file is only part of the story. RoasTime stores the coffee, the recipe you roasted
from, and the machine in sibling folders — `beans/`, `recipes/`, `officialRecipes/`,
`containers/`, `userProfiles/` — and the roast points at them by id.

All of it is synced and stored, exactly as it arrived, so a RoasTime update that adds a
field loses nothing. Each roast then shows **origin, process, variety, farm, altitude,
harvest, recipe name, machine and who roasted it** without anyone typing them, and the
Roasts page carries the same readout RoasTime shows beside its graph: preheat, charge,
turning point, yellowing, first crack, development with its temperature rise, end
temperature, every rate-of-rise figure, yield and weight loss, the power and fan settings
per phase, ambient, humidity and energy.

Linking is done by trying the id fields RoasTime has actually used rather than assuming
one name, and the app can say what matched — so a version that names things differently
is a fixable observation rather than a silent blank.

Both RoasTime formats are read: the per-roast JSON files, and the single-roast CSV export.
Every roast is identified by **date and coffee**; the coffee is read from the roast name and
you can correct it, along with origin, process, variety, weights, roast level, rating,
cupping score and notes. There is also a **demo roasting history** so you can look around
before importing anything.

---

## Sharing it with other people

Out of the box the app keeps everything in a SQLite file beside itself, which is fine for
one computer. For a group it needs two things, and **[SETUP.md](SETUP.md) walks through
both in about fifteen minutes**:

- **A shared database.** Point `[database] url` in secrets at a Postgres server — a free
  Supabase project does it — and every computer signed in sees the same roasts. This also
  fixes something easy to miss: Streamlit Community Cloud wipes its own disk on every
  restart, so without this, roasts imported today are gone tomorrow.
- **A sign-in.** `[passwords]` in secrets holds one PBKDF2 hash per person. With no accounts
  set up, the app makes the first one for you: it asks for a name and password and prints
  the two lines to paste into secrets. `password_tool.html` in any browser and
  `make_login.py` in a terminal do the same job. Nothing behind the sign-in renders for
  anyone who has not signed in, so the repository and the app URL can both stay public while
  the roasts stay yours.

Neither secret is in the repository, and the app tells you on the **Data** page which
database it is actually using — so a misconfigured deploy announces itself instead of
quietly losing data.

Roasts do not only arrive through the browser: the Mac sync writes straight into the
database, and so does anybody else signed in. Every page checks a one-query signature of
what is stored — how many roasts, when the last one landed — and reads the table again by
itself when that moves. So a roast finished on the Bullet turns up in an app that is
already open, without a restart and without pressing anything. **Re-read** on the Data
page forces it, if you want to watch it happen.

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

**Data** — folder upload, where the roasts are stored, who is signed in, the demo, and
what to delete.

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

The bean, recipe and machine attached to each roast are joined in one pass over the whole
table rather than a query per roast: five hundred roasts asking one at a time is fifteen
hundred round trips — 7.8 s against a database on the same machine, and far worse across
the Atlantic. Read together it is 0.2 s.

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
  library.py                beans, recipes, machines — and joining them to roasts
  auth.py                   who is allowed in
  uploader.py, frontend/    the drop box, and an optional watched folder
  fields.py, csv_import.py  reading RoasTime's two formats
  origin.py                 the coffee's country, read from the roast name
  demo_data.py              a simulated roasting history
password_tool.html          makes account lines for secrets, entirely in the browser
mac/                        Mac-side sync: a folder copy, or straight to the database
SETUP.md                    the shared-database and sign-in walkthrough
assets/                     the flame mark: logo-mark.svg (bare), icon.svg (tile),
                            logo-full.svg (lockup), icon-64/256.png
```

The mark is a flame with the roast curve cut out of it — a real mask, so the curve shows
whatever is behind it and the mark works on any ground.

Built on the Aillio Bullet's own recordings. Not affiliated with Aillio.
