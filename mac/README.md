# Getting roasts in without doing anything

> **macOS blocking these files?** Right-click → Open no longer works — Apple
> removed that in Sequoia. **[START-HERE.md](START-HERE.md)** has the way round
> it: open Terminal, type `bash ` and drag the file in. Thirty seconds.

Chrome will not accept a folder inside `~/Library` as an upload, and no web page
can reach in and copy that folder itself — a browser tab has no access to your
disk until you hand it files. So the way to stop repeating yourself is to have
something on **your Mac** do the work on a schedule, outside the browser.

Two ways, depending on how far you want to go.

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
