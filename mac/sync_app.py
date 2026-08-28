"""
The Mac sync, as a page instead of a command.

Everything `sync_to_database.py` does, with buttons: find RoasTime's folder,
point at the database, send what is new, and say plainly what arrived. It runs
on the Mac that roasts — that is the whole point, because only this computer can
read RoasTime's folder — and it is the same code underneath, so the command line
still works for anyone who prefers it.

    ./mac/roast-sync-app.command          ← double-click this

or, by hand:

    python3 -m streamlit run mac/sync_app.py

Nothing is ever written back to RoasTime's folder. This only reads.
"""

from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

import streamlit as st

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import sync_to_database as sync                      # noqa: E402
from roastcoach import db, library, store            # noqa: E402

st.set_page_config(page_title="Roast Coach — Mac sync", page_icon="🔥",
                   layout="centered")

ASSETS = HERE.parent / "assets"


def say(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{seconds / 60:.0f} minutes ago"
    if seconds < 172800:
        return f"{seconds / 3600:.0f} hours ago"
    return f"{seconds / 86400:.0f} days ago"


def count_folder(folder: Path) -> int:
    """How many files RoasTime has in a folder — extensions and all.

    RoasTime writes its files with no extension at all, which is what made an
    earlier version of the copy script quietly copy nothing. Count what is
    actually there.
    """
    try:
        return sum(1 for path in folder.iterdir()
                   if path.is_file() and not path.name.startswith("."))
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# What this Mac has
# ---------------------------------------------------------------------------

if (ASSETS / "logo-full.svg").exists():
    st.image(str(ASSETS / "logo-full.svg"), width=190)
st.title("Mac sync")
st.caption("Reads RoasTime's folder on this computer and sends new roasts to "
           "Roast Coach's database. RoasTime's own files are never changed.")

st.markdown("### 1 · Where the roasts are")

guess = ""
for candidate in sync.DEFAULT_FOLDERS:
    if candidate.is_dir():
        guess = str(candidate)
        break

typed = st.text_input(
    "RoasTime's roasts folder", value=st.session_state.get("folder", guess),
    help="The default is RoasTime's own folder. Change it only if you keep a copy "
         "somewhere else.")
st.session_state["folder"] = typed
folder = Path(typed).expanduser() if typed else None

if not folder or not folder.is_dir():
    st.error("No folder there. RoasTime normally keeps roasts in "
             "`~/Library/Application Support/roast-time/roasts`.", icon=":material/error:")
    st.stop()

# The bean and recipe files live beside the roasts, not inside them. Without them
# every roast shows an empty Bean and Recipe, which is the single most common way
# for this to look broken while working perfectly.
beside = {name: folder.parent / name for name in
          ("beans", "recipes", "officialRecipes", "containers", "userProfiles")}
present = {name: count_folder(path) for name, path in beside.items() if path.is_dir()}

found = st.columns(3)
found[0].metric("Roast files", f"{count_folder(folder):,}")
found[1].metric("Bean files", f"{present.get('beans', 0):,}")
found[2].metric("Recipe files",
                f"{present.get('recipes', 0) + present.get('officialRecipes', 0):,}")

if not present:
    st.warning(
        "There is no `beans/` or `recipes/` folder beside this one, so roasts will "
        "arrive with no coffee and no recipe attached. That happens when the folder "
        "is a *copy* of `roasts/` rather than RoasTime's own — point this at "
        "`~/Library/Application Support/roast-time/roasts` instead.",
        icon=":material/folder_off:")

# ---------------------------------------------------------------------------
# Where it is going
# ---------------------------------------------------------------------------

st.markdown("### 2 · Where they are going")

# An environment that has already named a database wins over the one saved here:
# that is the same order `sync_to_database.py` uses, and it is what lets a test —
# or anyone running this against a second database for an afternoon — do so
# without quietly rewriting the setting this Mac normally uses.
decided = os.environ.get("ROAST_COACH_DATABASE_URL")
elsewhere = bool(decided or os.environ.get("ROAST_COACH_DB"))
stored_url = decided or ("" if elsewhere else (sync.stored_database() or ""))
url = st.text_input(
    "Database connection string", value=stored_url, type="password",
    placeholder="postgresql://…  — leave empty to use this computer only",
    help="The Session pooler URI from Supabase, or any Postgres server. It is kept "
         "in ~/.roastcoach/database, readable only by you, and never put in a "
         "launch agent where other processes could see it.")

if url.strip() and url.strip() != stored_url:
    sync.remember_database(url.strip())
    st.success("Saved. This computer will use that database from now on.",
               icon=":material/save:")

if url.strip():
    os.environ["ROAST_COACH_DATABASE_URL"] = url.strip()

try:
    where = db.describe()
    shared = db.is_shared()
except Exception as problem:                        # a bad URL must say so here
    st.error(f"That database cannot be reached: {problem}", icon=":material/error:")
    st.stop()

if shared:
    st.success(f"**{where}** — everyone signed in to the app sees these roasts.",
               icon=":material/cloud:")
else:
    st.warning(f"**{where}** — a file on this computer only. The web app will not "
               "see anything sent here. SETUP.md has the fifteen-minute fix.",
               icon=":material/warning:")

# ---------------------------------------------------------------------------
# Sending them
# ---------------------------------------------------------------------------

st.markdown("### 3 · Send them")

again = st.checkbox(
    "Read every roast file again, not only what has changed",
    help="Normally only new and changed files are read. Tick this after updating "
         "the app, so roasts imported by an older version pick up fields it did "
         "not keep — the link to the recipe, for one. Each roast is matched by its "
         "own id and updated in place: nothing is duplicated, and nothing you have "
         "typed in the app is touched.")

# A roast read by an older importer is missing whatever that version discarded,
# and only the file has it back. The sync does this by itself; saying so first
# explains why the next run reads five hundred files instead of none.
try:
    behind = store.unread()
except Exception:
    behind = 0
if behind and not again:
    st.info(f"**{behind} roast(s)** were read by an earlier version of this app, which "
            "kept fewer of RoasTime's fields — the link to the recipe among them. "
            "Pressing **Sync now** reads their files again by itself; you do not have "
            "to tick anything. Each is matched by its own id and updated in place.",
            icon=":material/refresh:")

if st.button("Sync now", type="primary", use_container_width=True):
    log = io.StringIO()
    with st.spinner("Reading RoasTime's folder…"):
        try:
            with redirect_stdout(log):
                report = sync.sync_once(folder, url.strip() or None, again=again)
        except Exception as problem:
            st.error(f"{type(problem).__name__}: {problem}", icon=":material/error:")
            report = None

    if report is not None:
        said = []
        if report.get("added"):
            said.append(f"**{report['added']}** new roast(s)")
        if report.get("updated"):
            said.append(f"**{report['updated']}** updated")
        if report.get("skipped"):
            said.append(f"{report['skipped']} unchanged")
        if report.get("failed"):
            said.append(f"{report['failed']} not roast files")
        st.success(" · ".join(said) or "Everything was already up to date.",
                   icon=":material/check_circle:")
        for problem in (report.get("problems") or [])[:5]:
            st.caption(f":orange[{problem}]")

    with st.expander("What it did, line by line"):
        st.code(log.getvalue() or "(nothing printed)", language=None)

# ---------------------------------------------------------------------------
# What the database holds now
# ---------------------------------------------------------------------------

st.markdown("### 4 · What the database holds now")

try:
    signature = store.fingerprint()
    total = int(signature[0]) if signature and signature[0] else 0
    last = signature[1] if len(signature) > 1 else None
except Exception:
    total, last = 0, None

held = library.counts()

held_columns = st.columns(4)
held_columns[0].metric("Roasts", f"{total:,}")
held_columns[1].metric("Beans", f"{held.get('bean', 0):,}")
held_columns[2].metric("Recipes", f"{held.get('recipe', 0):,}")
held_columns[3].metric("Bags", f"{held.get('container', 0):,}")
if last:
    st.caption(f"Last roast in the database: {str(last)[:16].replace('T', ' ')}")

if total and held.get("bean") and not held.get("recipe"):
    st.error(
        "Beans arrived and recipes did not, which means the `recipes/` folder was not "
        "read. It sits beside `roasts/`, not inside it — check that the folder above "
        "is RoasTime's own `roasts` folder and not a copy of it.",
        icon=":material/link_off:")

if total and not held:
    st.error(
        "There are roasts in the database and no bean, recipe or bag records at "
        "all — so every roast will show an empty **Bean** and **Recipe** in the app, "
        "however good its files are. Press **Sync now** above: the companion folders "
        "are sent with every sync.", icon=":material/link_off:")

# How many roasts actually came out with a coffee and a recipe on them. This is
# the question the app's Data page answers, asked here where it can be acted on.
if total:
    with st.expander("How many roasts found their bean and their recipe"):
        try:
            frame = store.load_roasts()
            rows = frame.to_dict("records")
            for item in library.coverage(rows):
                matched, missing = item["roasts matched"], item["file not here"]
                none = item["no id on the roast"]
                st.markdown(
                    f"**{item['what']}** — {matched} of {len(rows)} matched"
                    + (f" · {missing} point at a file that has not arrived" if missing else "")
                    + (f" · {none} carry no id at all" if none else ""))
                if item.get("how"):
                    st.caption("matched by: " + " · ".join(
                        f"{reason} ({count})" for reason, count in item["how"]))
        except Exception as problem:
            st.caption(f"Could not read the roasts: {problem}")

# ---------------------------------------------------------------------------
# Doing it without being asked
# ---------------------------------------------------------------------------

st.markdown("### 5 · Have macOS do it for you")

installed = sync.AGENT.exists()
if installed:
    st.info("A launch agent is installed: macOS runs the sync every fifteen minutes, "
            "whether or not this page is open.", icon=":material/schedule:")
else:
    st.caption("Nothing is scheduled. Roasts arrive only when you press **Sync now**.")

schedule = st.columns(2)
if schedule[0].button("Run it every 15 minutes", disabled=installed,
                      use_container_width=True):
    log = io.StringIO()
    with redirect_stdout(log):
        sync.install(folder, url.strip() or None)
    st.code(log.getvalue() or "(nothing printed)", language=None)
    st.rerun()

if schedule[1].button("Stop running it", disabled=not installed,
                      use_container_width=True):
    log = io.StringIO()
    with redirect_stdout(log):
        sync.uninstall()
    st.code(log.getvalue() or "(nothing printed)", language=None)
    st.rerun()

st.divider()
st.caption("This page reads RoasTime's folder and writes only to Roast Coach's "
           "database. It never changes anything RoasTime keeps.")
