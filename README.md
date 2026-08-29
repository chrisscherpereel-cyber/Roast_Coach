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

### A page with a Sync now button

On the Mac that roasts, double-click **`mac/roast-sync-app.command`**. It opens a
small page — running on that Mac, not in the cloud, so it reads RoasTime's folder
directly — which counts what is there, sends it, says what the database holds
afterwards, and can hand the job to macOS on a fifteen-minute schedule. Chrome's
rules about `~/Library` never come into it, because Chrome is not doing the
reading.

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

Three things about that folder are worth knowing, because each of them cost a round of
"why is nothing showing up":

- **The files have no extension.** RoasTime writes `beans/a1b2c3…` with no `.json` on the
  end, and a browser's file picker filtered on type quietly drops every one of them. The
  uploader now decides what a file is by reading it, not by its name, and anything that
  parses as JSON is kept — recognised or not.
- **A roast's `beanId` may be a bean or a bag.** RoasTime keeps *containers* — bags of a
  coffee — as well as beans, and a roast can point at either. Both are followed, and a bag
  resolves to the bean in it, so two bags of one coffee stay one coffee. The bag keeps its
  own name beside the bean rather than instead of it.
- **Not every id on a record belongs to it.** Twenty-six recipes on the machine this was
  built against carry the same `guid` — it is the *device's*, not the recipe's — while each
  has its own `uid`. So the app works out which field identifies a record by looking at the
  batch: the one that is present on every record and different for every record. Filing
  them under a fixed field stored twenty-six recipes on top of one another.
- **A field name is not case-sensitive here.** These roasts spell the link to their recipe
  `recipeID`; older files spell it `recipeId`. One capital is not a different field.
- **Recipes are a file, not a field.** `recipes/` and `officialRecipes/` hold the recipe as
  written — its name, its roast degree, and its steps with the temperature or the time each
  one fires at. Each roast shows that recipe beside what actually happened, under **The
  recipe you actually ran**, with the raw record one click away.

Recipes are set by temperature far more often than by the clock, so every step is shown
both ways: the temperature it was written for, and the moment on this roast's curve where
that temperature arrived.

Both RoasTime formats are read: the per-roast JSON files, and the single-roast CSV export.

**Three names, kept apart.** Every table and every grouping carries the **roast name** you
typed at the machine, the **bean** RoasTime linked to the roast, and the **recipe** it was
run from — as three separate columns, because the thing you most often want to see is two
roasts of one bean that ran from different recipes. Grouping works on any of them, or on
combinations: bean × recipe, bean × batch size, recipe × month.

**Roasts are compared bean against bean.** The coffee on a roast is the bean RoasTime linked
to it; only where no bean file matched does the app fall back to what you typed, and then to
a name read out of the roast title. So "#12 CR 800 v4" and "Costa test 2nd" are one coffee
if they were one bean, and trends, repeatability, effect sizes and the coach's next-roast
advice all follow the bean. Type a different name on any roast of a bean and every roast of
that bean takes it — a rename, not a split. The **Data** page says how many roasts matched a
bean file and names the ids whose file has not arrived. Origin, process, variety, weights,
roast level, rating, cupping score and notes are all yours to correct.

There is also a **demo roasting history** so you can look around before importing anything.

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

A roast's numbers are worked out once, at import, and kept with it — which is what makes the
pages quick, and what means a corrected calculation would otherwise only reach roasts imported
after the correction. Each roast carries the version of the metrics that measured it; when
that falls behind, the Data page offers to **measure them again** from the curves already
stored — nothing is re-imported, and nothing you typed is touched. The Mac sync does it by
itself, before anyone is looking.

Roasts do not only arrive through the browser: the Mac sync writes straight into the
database, and so does anybody else signed in. Every page checks a one-query signature of
what is stored — how many roasts, when the last one landed — and reads the table again by
itself when that moves. So a roast finished on the Bullet turns up in an app that is
already open, without a restart and without pressing anything.

**The sidebar keeps the answer in sight**: how many roasts are stored, when the last one
arrived — *"42 roasts · last added 12 min ago"* — and one **Update** button. It does not
only re-read: it puts right everything that can have fallen behind since the last look —
roasts written by the Mac sync or by anybody else signed in, measurements left by an older
version of the metrics, the links from roasts to beans and recipes, and the coach's own
learning — then says what changed (*"3 new roasts · 41 re-measured"*). If nothing has
arrived for a day and a half it says so there too, because a sync that has quietly stopped
looks exactly like a week without roasting.

**Every warning carries the button that fixes it.** A caution the app raises is a job it
knows how to do — roasts measured by an older version, beans whose file has not arrived,
learning that has not been run — so each one is shown with the button that does it rather
than with instructions for finding the right page. Where the fix is genuinely yours to make
(a folder to point at, a file to copy), the button opens the exact steps instead.

---

## The four pages, in the order a roast lives through them

**Coach** — what to change on your next roast, as **the next roast written out step by
step**: what to set at charge, and every change after it *at the IBTS temperature where it
belongs*, with the clock time beside it. The Bullet's recipes are written against the IBTS
and that is the number on screen while the drum turns, so it leads; the time tells you
whether the roast is on pace.

**Roasts** — every roast by date and coffee, with three names kept apart (roast name, bean,
recipe). Open one and it has two tabs: **What happened** — the profile, the readout, the
recipe as written against what you actually ran, and what the roast did — and **After the
roast**, where colour, weights, defects, rating and tasting notes are entered. Those were
two pages until they were not: reading a roast and writing down how it tasted are the same
roast, and finding it twice was silly.

**Compare** — two things, in that order. **Particular roasts you pick**, overlaid on one
pair of axes (IBTS and its rate of rise), optionally lined up at first crack so development
compares like with like. And **groups**: bean × recipe, bean × batch size, recipe × month —
every measure worked out inside each group, with the spread, which is usually the more
interesting number. At the foot of it, what the coach has learned from all your roasts: how
far each control actually moves each measure on your machine, and which of its own rules
have earned their place.

**Setup** — two tabs, neither of them in the way: **Data and sync** (where the roasts are
kept, what arrived from RoasTime, what is behind and the button that fixes it) and **How it
decides** (the three levels of a finding, the evidence grades, and every source).

### Three roasts overlaid, and not four

Overlaid curves cross, so every pair of colours is adjacent somewhere on the chart. Three
hues clear the colour-blind separation floors on that test in both light and dark themes; no
fourth one tried does. So the overlay holds three — each also dash-patterned and named at
the end of its line — and a fourth roast gets its own panel on the same axes instead, with
the first roast you picked drawn faintly behind each of the others. That is a real
comparison rather than a knot of lines.

### The two probes, named for what they are

The Bullet has an **IBTS** — an infrared sensor reading the bean mass — and a **bean
probe** buried in the beans. RoasTime stores the IBTS in a field called `drumTemperature`,
which invites exactly the wrong reading, and this app used to repeat it. The IBTS is not the
drum: it is what the recipes are written against and what the roaster watches, so it is
named IBTS everywhere it appears, and the physical probe is called the bean probe.

---

## What the app is willing to say

Roasting software has a habit of reading a curve and announcing a taste. This one will not.
Every finding is built in three levels, and each claims less than the one before it:

| Level | Example | How sure |
| --- | --- | --- |
| **Observation** | *Rate of rise fell from 10.2 °C/min — its settled level in the 45 s before first crack — to 5.6 °C/min, a 45% decline reached 33 s after the crack at 8:21.* | Measured. Reproducible. No opinion in it. |
| **Diagnosis** | *A pronounced first-crack crash, by this app's bands.* | True by definition of the threshold — which is why the threshold is named. |
| **Possible cup effect** | *Pronounced crashes are associated by practitioners with muted or baked character. The curve cannot establish that.* | A hypothesis. Only cupping settles it, and the app gives you the buttons to record what you found. |

Each one carries an evidence grade, and the grade fixes the wording:

| Grade | Means | Said as |
| --- | --- | --- |
| **A** | Experimental, or read straight off the probe | "Research has found…" |
| **B** | Strong practitioner consensus | "Roasting practice identifies this as…" |
| **C** | This app's heuristic | "Flagged by this app's threshold of…" |
| **D** | Sensory inference | "May increase the risk of…" |

A stall is grade A *as a thermal reading* — the probe recorded the roast not climbing —
while "baked" is grade D, because no arrangement of thermocouples has tasted anything.

### A crash is not a number of °C/min

`RoR fell 3 °C/min` is not portable. It depends on probe placement, sampling rate,
smoothing, batch size and roaster design, so the same roast crosses it on one machine and
not on another. What travels is the fall relative to the roast's own settled rate before
first crack:

```
crash % = 100 × (baseline − trough) / baseline
```

with the baseline taken as the median of 45 s to 15 s before the crack, and the trough as
the minimum in the 60 s after it. The bands — under 15% minimal, 15–30% mild, 30–45%
moderate, over 45% pronounced — are **this app's settings, not published boundaries**, and
the app says so every time it uses one. A machine reading half as fast gets the same
percentage, which is the point.

A flick is a *sustained* reversal after that trough: under 10 s it is called a transient and
named as noise; past 20 s it is a flick. A stall is rate of rise inside ±0.5 °C/min for at
least 15 s, and bean temperature actually falling is reported with its duration and its
worst rate, because that is a fact about the probe rather than an interpretation.

### Measured against your own roasts, not a universal target

Once a bean has three roasts, development ratio, drying, first crack, total time and drop
temperature are compared against **your** record of that bean on that machine, and the
finding says so. Until then the configured bands apply and are named as settings — a
development ratio outside 18–25% is reported as a number outside a preference, never as a
defect. Excellent roasts are made outside them.

### What the curve cannot see

Roast colour carries more sensory weight than drop temperature, so it is a first-class
input rather than something inferred: whole-bean and ground colour, the gap between them,
and batch colour spread. Quakers are recorded too — and treated as a **green-coffee defect
made visible by roasting, never blamed on the profile**. Scorching, tipping, facing and
charring are recorded by eye and explained back with their likely mechanism.

Then the loop closes: every cup risk carries **Tasted it — confirmed** / **Cupped, not
present** buttons, and the Method page keeps the scoreboard. That is the honest measure of a
heuristic — not how often it fires, but how often somebody tasted what it warned about.

Every threshold in the app is listed on the **Method** page with its value and its
justification, alongside the sources: Yang et al. (2016) on experimentally generated roast
defects, Münchow et al. (2020) on roast colour against roast time, Alstrup et al. (2020) on
development-time modulation, Masi et al. (2013), Hu et al. (2020) — and Scott Rao, Barista
Hustle, Rob Hoos and Loring, listed as the practitioner and educational sources they are.
The vocabulary of crash, flick and development ratio comes from practice; that is a
different thing from experimental validation, and the app does not blur them.

Every phase share is of the whole roast measured from charge, the way RoasTime shows it:
drying is everything up to yellowing, and the three shares add to 100. Shares are also
compared at the precision they are displayed at, so a roast shown as 24.6% development is
never told it ran past 25%.

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
app.py                      the four pages
roastcoach/
  store.py                  the database: roasts, curves, notes, advice, outcomes
  coach.py                  the rules, the predictions, the grading
  learning.py               effect sizes measured from your own roasts
  metrics.py                turning point, phases, rate of rise, pattern checks
  curves.py                 smoothed series for the charts
  charts.py                 the four figures
  db.py                     Postgres or SQLite, same SQL either way
  diagnostics.py            the condition dictionary: observation, diagnosis, cup risk
  evidence.py               the grades A–D, and who each claim rests on
  library.py                beans, recipes, machines — and joining them to roasts
  auth.py                   who is allowed in
  uploader.py, frontend/    the drop box, and an optional watched folder
  fields.py, csv_import.py  reading RoasTime's two formats
  origin.py                 the coffee's country, read from the roast name
  demo_data.py              a simulated roasting history
password_tool.html          makes account lines for secrets, entirely in the browser
mac/                        everything that runs on the Mac that roasts
  sync_app.py               the sync as a page: a Sync now button, and what arrived
  roast-sync-app.command    double-click to open that page
  sync_to_database.py       the same sync as a command, with --every and --install
  roast-sync.command        the older route: copy the folder somewhere Chrome accepts
  what_roastime_has.py      a read-only inspector: what RoasTime keeps, and what links
SETUP.md                    the shared-database and sign-in walkthrough
assets/                     the flame mark: logo-mark.svg (bare), icon.svg (tile),
                            logo-full.svg (lockup), icon-64/256.png
```

The mark is a flame with the roast curve cut out of it — a real mask, so the curve shows
whatever is behind it and the mark works on any ground.

Built on the Aillio Bullet's own recordings. Not affiliated with Aillio.
