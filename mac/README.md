# Getting roasts in without doing anything

> **macOS blocking these files?** Right-click → Open no longer works — Apple
> removed that in Sequoia. **[START-HERE.md](START-HERE.md)** has the way round
> it: open Terminal, type `bash ` and drag the file in. Thirty seconds.

Chrome will not accept a folder inside `~/Library` as an upload, and no web page
can reach in and copy that folder itself — a browser tab has no access to your
disk until you hand it files. So the way to stop repeating yourself is to have
something on **your Mac** do the work on a schedule, outside the browser.

**Nothing about your Mac stops this** — the restriction was always Chrome's. A
program running on your own computer reads `~/Library` like any other folder. So
the answer is to have a small program on this Mac do it, and the friendliest of
those is a page with a button.

Three ways, depending on how far you want to go.

---

## 0. A page with a Sync now button  ← start here

**`roast-sync-app.command`** — double-click it.

It opens a small page in your browser that is running *on this Mac*, so it can
read RoasTime's folder directly. The page shows:

- what this Mac has — how many roast, bean and recipe files, counted properly
  (RoasTime writes its files with no extension, which is what made an older copy
  script quietly copy nothing);
- where they are going, and whether that is a real shared database or just a
  file on this computer;
- a **Sync now** button, with a tick-box for *read every roast file again* when
  you have updated the app and want roasts imported by an older version to pick
  up fields it did not keep;
- what the database holds afterwards — roasts, beans, recipes, bags — and how
  many roasts actually found their coffee and their recipe;
- a switch to have macOS do it every fifteen minutes without the page open.

Leave the Terminal window it opens alone while you use the page; close it when
you are done. It is the same code as the command line below, so nothing is
different about the result.

---

## 1. Keep a copy where the browser will take it

**`roast-sync.command`** — double-click it.

It copies every roast out of RoasTime's folder into `~/Documents/RoastCoach`,
then offers to keep doing that every fifteen minutes and at login. Say yes and
you never run it again.

After that, importing is what you already wanted: press **Choose folder…** in
Roast Coach, pick **Documents → RoastCoach**, done. That folder is an ordinary
one, so Chrome has no objection to it, and it is always current.

Timestamps are preserved by the copy, which matters — they are how the app tells
a roast it has already read from one that has changed.

To stop the schedule, double-click the file again and answer `y`.

macOS may ask whether Terminal can use your Documents folder the first time. Say
yes; that is what lets the copy land.

---

## 2. Skip the browser entirely  ← the one to take

**`setup.command`** — double-click it, answer one question, done.

It checks Python is there, finds your roasts, asks once for the database address,
installs what it needs, sends everything across so you can see it worked, and
offers to keep doing that every fifteen minutes and at login. Say yes and that is
the end of it: roast on the Bullet, and the roast turns up in Roast Coach by
itself, for everybody signed in.

The address is saved to `~/.roastcoach/database`, readable only by your account —
never inside the scheduled job, where anything listing running processes could
read it.

**If macOS refuses to open it** — likely, since it came from a download — see
[START-HERE.md](START-HERE.md). Short version: open Terminal, type `bash ` with a
space, drag `setup.command` into the window, press Return. That route sidesteps
Gatekeeper completely, and works on every version of macOS.

### The same thing by hand

`setup.command` just runs **`sync_to_database.py`** for you. That script reads
RoasTime's folder and writes new and changed roasts *straight into the same
database the app reads*, so roasts appear on every computer signed in without
anybody pressing anything. Chrome is not involved, so its rules never come up.

To drive it yourself instead:

```bash
cd /path/to/roast-coach
pip3 install -r requirements.txt
export ROAST_COACH_DATABASE_URL="postgresql://…"     # the Session pooler URI
python3 mac/sync_to_database.py
```

It is a program in its own right — **the Roast Coach web app never calls it, and
it never calls the web app**. The two only ever meet in the database. That is
what lets it run on a schedule while nobody has the app open, and why it works
the same whether the app is on Streamlit Cloud or your own machine.

That does one pass and prints what it found:

```
14:32  reading 2 file(s) from roasts…
       1 new, 1 updated → Postgres — aws-1-us-east-2.pooler.supabase.com
```

To have macOS run it every fifteen minutes and at login:

```bash
python3 mac/sync_to_database.py --install
```

`--uninstall` stops it. `--every 15` runs it in a Terminal window instead, if you
would rather watch it work. Each scheduled run is logged to
`~/Library/Logs/roast-coach-sync.log`.

It also sends the sibling folders — `beans/`, `recipes/`, `containers/`,
`userProfiles/` — so origin, process, variety, recipe and machine fill in on
every roast without anybody typing them.

It re-learns the effect sizes and grades any outstanding advice after every
import, exactly as the app does — so the Coach page is current the moment you
open it.

---

## Which one

Take **2**. It removes the import step rather than making it easier, and it works
for everyone at once — one Mac syncing keeps everybody's view current. It needs
the shared database from `SETUP.md`, which is worth having anyway: without it,
Streamlit Community Cloud wipes your roasts every time the app restarts.

If you have not set up a database, or you would rather nothing ran unattended
against it, take **1**. It is a plain file copy with nothing to install.

Neither writes to RoasTime's folder. Both only read.

---

## Something is missing and you cannot see why

**`what_roastime_has.py`** — run it and paste the output back:

```bash
python3 mac/what_roastime_has.py
```

It prints every folder RoasTime has on this Mac with what is in it, the field
names in a sample of each kind of file, and then the part that matters: it takes
the bean and recipe ids your roasts actually carry and *goes looking for them* —
in the JSON folders, and inside RoasTime's own databases.

That last search is the point. RoasTime is an Electron app, and Electron apps
keep a great deal in IndexedDB, which on disk is a LevelDB store — `.log` and
`.ldb` files that look like nonsense in Finder but hold plain JSON inside. If
your recipe ids turn up there and in no folder, then no amount of folder copying
will ever bring recipe names across, and the answer is a different importer. If
they turn up nowhere at all, the names live in your Roast.World account and are
fetched when RoasTime is online.

It reads and prints. It changes nothing.
