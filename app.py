"""
Roast Coach — read your Aillio Bullet roasts, understand them, improve them.

    streamlit run app.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from roastcoach import (auth, charts, coach, db, demo_data, diagnostics, evidence,
                        learning, library, recipe, store)
from roastcoach.curves import create_roast_samples, roast_events
from roastcoach import metrics as metric_rules
from roastcoach.naming import label_for

ASSETS = Path(__file__).parent / "assets"

st.set_page_config(page_title="Roast Coach", page_icon=str(ASSETS / "icon-64.png"),
                   layout="wide", initial_sidebar_state="expanded")

# Whoever is at the machine unless a roast says otherwise. RoasTime records a
# user id, not a name, and one roastery is usually one roaster.
DEFAULT_ROASTER = os.environ.get("ROAST_COACH_ROASTER", "Scherpereel")

# Streamlit sets a metric's value in 2.25rem, which is a headline size. It suits
# a number and defeats a name: at that size "Zambia Isanya Estate AA" is clipped
# to "Zambia Isan…" in a third of a screen. Numbers here are short, names are
# long, and the page reads better with both a size smaller.
st.markdown("""
<style>
  [data-testid="stMetricValue"] { font-size: 1.45rem; line-height: 1.25; }
  [data-testid="stMetricLabel"] p { font-size: .82rem; opacity: .72; }
  [data-testid="stMetricValue"] div { white-space: normal; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False)
def load(token) -> pd.DataFrame:
    return store.load_roasts()


# Updating a cloud app means copying several files, and copying only some of them
# is easy to do — it happened three times in one afternoon, each time as a
# redacted AttributeError pointing at whichever function had not arrived yet.
#
# So each of Roast Coach's own modules carries a VERSION, this is the version
# app.py needs, and anything older is named on screen and worked around. Probing
# for one function per file was the earlier attempt, and it only ever caught the
# function I happened to think of.
NEEDS = (("roastcoach/store.py", store, 12),
         ("roastcoach/library.py", library, 9),
         ("roastcoach/metrics.py", metric_rules, 3),
         ("roastcoach/coach.py", coach, 4),
         ("roastcoach/diagnostics.py", diagnostics, 1),
         ("roastcoach/evidence.py", evidence, 1),
         ("roastcoach/recipe.py", recipe, 4))

STALE = [name for name, module, wanted in NEEDS
         if getattr(module, "VERSION", 0) < wanted]


def deploy_report() -> list[dict]:
    """Which copy of each module actually got imported, and how old it is.

    "Update these files" is useless advice if the files you updated are not the
    ones Python is reading. This says the path, so a second copy of the package
    further up sys.path — or an unzipped folder inside the repo — shows itself.
    """
    import roastcoach

    beside = Path(__file__).resolve().parent
    package = Path(getattr(roastcoach, "__file__", "") or ".").resolve().parent
    rows = []
    for name, module, wanted in NEEDS:
        found = getattr(module, "VERSION", 0)
        where = Path(getattr(module, "__file__", "") or "?").resolve()
        rows.append({
            "file": name,
            "needs": wanted,
            "found": found or "before versions",
            "up to date": "yes" if found >= wanted else "no",
            "the file Python read": str(where),
        })
    return rows, package, beside


def optional(module, name, *args, default=None, **kwargs):
    """Call something a newer version of that module has, if this copy has it.

    Everything app.py asks of a module that older copies lack goes through here,
    so a half-updated deploy loses that one feature rather than the page.
    """
    function = getattr(module, name, None)
    if function is None:
        return default
    return function(*args, **kwargs)


def phase_shares(total_minutes, yellow_minutes, crack_minutes) -> dict:
    """Drying, Maillard and development as shares of the roast, measured from charge.

    Normally metrics.py's, so the flags, the rules and the screen cannot drift
    apart. Worked out here only when this deploy's metrics.py is older than
    app.py — the sidebar says so, and the numbers stay right meanwhile.
    """
    shared = getattr(metric_rules, "phase_shares", None)
    if shared is not None:
        return shared(total_minutes, yellow_minutes, crack_minutes)

    try:
        total = float(total_minutes)
        yellow = float(yellow_minutes)
        crack = float(crack_minutes)
    except (TypeError, ValueError):
        return {}
    if not (total > 0) or pd.isna(yellow) or pd.isna(crack):
        return {}
    return {"drying": yellow / total * 100.0,
            "maillard": (crack - yellow) / total * 100.0,
            "development": (total - crack) / total * 100.0}


def signature() -> tuple:
    """How much is stored — from store.py, or worked out here if that file is old."""
    if hasattr(store, "fingerprint"):
        return store.fingerprint()
    try:
        row = db.one("SELECT COUNT(*), MAX(imported_at) FROM roasts")
        return tuple("" if value is None else str(value) for value in (row or ()))
    except Exception:
        return ()


def token():
    """What the cached table was read at.

    Roasts arrive from three directions: this browser, somebody else's, and the
    Mac sync writing straight into the database with no browser at all. Only the
    first of those can clear a cache. So the key is a one-query signature of what
    is actually stored — when that moves, the table is read again by itself.
    """
    return (signature(), st.session_state.get("data_token", 0))


def refresh():
    st.session_state["data_token"] = st.session_state.get("data_token", 0) + 1
    load.clear()
    samples_for.clear()


def roasts() -> pd.DataFrame:
    return load(token())


@st.cache_data(show_spinner=False, max_entries=128)
def samples_for(roast_id: str, token):
    roast = store.roast_dict(roast_id)
    if not roast:
        return pd.DataFrame(), []
    return create_roast_samples(roast, drop_factor=2), roast_events(roast)


def curve_of(roast_id: str):
    return samples_for(roast_id, token())


def theme() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


def number(value, suffix="", decimals=1, dash="—"):
    if value is None or pd.isna(value):
        return dash
    return f"{float(value):.{decimals}f}{suffix}"


# What each kind of RoasTime record looks like from the inside. Checked in this
# order, most specific first — a recipe carries roast settings and events, a bean
# carries where the coffee came from, a container is a bag of one of them.
COMPANION_HINTS = {
    "recipe": ('"startSettings"', '"roastDegree"', '"recipeName"', '"preheatTemp"',
               '"referenceRoastGuid"', '"officialDetails"'),
    "container": ('"capacityType"', '"containerGroup"', '"startOnHand"', '"defaultCost"'),
    "userProfile": ('"roastCount"', '"favoriteBean"', '"experience"', '"aboutMe"'),
    "bean": ('"country"', '"process"', '"varietal"', '"beanName"', '"organic"',
             '"density"', '"vendorIds"'),
}


def sort_out(files: list[dict]) -> tuple[list[dict], dict]:
    """Roast files in one pile, and everything that names them in the others.

    A whole RoasTime folder can be dropped in at once. RoasTime writes every
    record with **no file extension**, so what a file is has to be read out of
    its contents — a bean is a bean because it says `country` and `process`, not
    because it is called `bean.json`.
    """
    roast_files, companions = [], {}
    for item in files:
        text = item.get("text") or ""
        head = text[:4000]
        if any(hint in head for hint in ("beanTemperature", "drumTemperature",
                                         "beanDerivative", "Timeline")):
            roast_files.append(item)
            continue

        placed = False
        for kind, hints in COMPANION_HINTS.items():
            if any(hint in head for hint in hints):
                companions.setdefault(kind, []).append(item)
                placed = True
                break

        # Everything else RoasTime writes — sync state, config, container groups,
        # whatever a future version adds — is kept rather than dropped. Storing a
        # record nobody reads yet costs nothing; throwing it away is how 42 bean
        # files went missing.
        if not placed and head.lstrip()[:1] in ("{", "["):
            companions.setdefault("other", []).append(item)
    return roast_files, companions


def import_files(files: list[dict]) -> dict:
    """Bring roasts in, then re-learn and re-grade — the whole cycle."""
    files, companions = sort_out(files)
    for kind, records in companions.items():
        library.add_records(kind, records)
    report = store.add_roasts(files)
    report["companions"] = sum(len(v) for v in companions.values())
    if report["added"] or report["updated"]:
        refresh()
        frame = roasts()
        learning.relearn(frame)
        coach.auto_evaluate(frame)
    return report


@st.cache_data
def _mark() -> str:
    """The logo as a data URI.

    Not inline SVG: Streamlit sanitises the HTML it renders and strips the mask
    that cuts the roast curve out of the flame, which leaves a plain blob.
    """
    import base64

    svg = (ASSETS / "logo-mark.svg").read_bytes()
    return "data:image/svg+xml;base64," + base64.b64encode(svg).decode()


def text_of(value) -> str:
    """Form fields should be empty when there is nothing there, not 'nan'."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value)
    return "" if text.lower() in ("nan", "none", "<na>") else text


def brand_header(subtitle: str):
    st.markdown(
        f"""<div style="display:flex;align-items:center;gap:14px;margin:-8px 0 22px">
              <img src="{_mark()}" alt="" width="42" height="42" style="flex:0 0 42px">
              <div>
                <div style="font-size:1.45rem;font-weight:650;letter-spacing:-.02em;line-height:1.1">
                  Roast <span style="color:#E8622A">Coach</span>
                </div>
                <div style="opacity:.65;font-size:.9rem">{subtitle}</div>
              </div>
            </div>""",
        unsafe_allow_html=True,
    )


def ago(stamp) -> str:
    """How long since something happened, said the way a person would."""
    if not stamp:
        return "never"
    try:
        when = pd.to_datetime(str(stamp))
        seconds = (pd.Timestamp.now(tz=when.tz) - when).total_seconds()
    except Exception:
        return str(stamp)[:16].replace("T", " ")
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min ago"
    if seconds < 172800:
        return f"{seconds / 3600:.0f} h ago"
    return f"{seconds / 86400:.0f} days ago"


def sidebar_sync():
    """When roasts last arrived, and one button to go and look again.

    Roasts land in the database from the Mac sync and from other people's
    browsers, so "is this current?" is a real question with a cheap answer — the
    same one-query signature every page already asks for. The button re-reads and
    then says what turned up, which is the difference between a refresh you
    trust and one you press twice.
    """
    stored = signature()
    count = int(stored[0]) if stored and stored[0] else 0
    last = stored[1] if len(stored) > 1 else None

    previous = st.session_state.get("_last_seen_count")
    st.session_state["_last_seen_count"] = count

    st.markdown(f"**{count}** roast(s) · last added **{ago(last)}**")
    if last:
        st.caption(str(last)[:16].replace("T", " "))

    # What this button can and cannot do. It reads the *database* again — which is
    # everything, when somebody else has synced — but this app runs in the cloud
    # and cannot reach into the Mac where RoasTime keeps its files. A roast made
    # this morning is not here until the Mac sends it, and saying "everything was
    # already up to date" while today's roasts sit on a laptop is a lie of
    # omission that costs an afternoon.
    if st.button("Update", use_container_width=True, type="primary",
                 help="Reads the database again and puts right anything that has fallen "
                      "behind: roasts sent by the Mac sync or by anybody else signed in, "
                      "measurements from an older version, the links to beans and "
                      "recipes, and the coach's own learning. It cannot reach your Mac — "
                      "new roasts get here by the Mac sync."):
        with st.spinner("Updating…"):
            done = bring_up_to_date()
        fresh = signature()
        now = int(fresh[0]) if fresh and fresh[0] else 0
        arrived = now - (previous if previous is not None else count)

        said = []
        if arrived > 0:
            said.append(f"{arrived} new roast(s)")
        if done["remeasured"]:
            said.append(f"{done['remeasured']} re-measured")
        if done["graded"]:
            said.append(f"{done['graded']} suggestion(s) graded")
        st.session_state["_update_found_nothing"] = not said
        st.toast(" · ".join(said) if said
                 else "Nothing new in the database — see the sidebar.")
        st.rerun()

    if st.session_state.pop("_update_found_nothing", False):
        st.caption(":orange[Nothing new was in the database. This app reads the "
                   "database, not your Mac — a roast is here once the Mac has sent it. "
                   "**Roasted today?** Open **`mac/roast-sync-app.command`** on the "
                   "roasting Mac and press *Sync now*, then press **Update** here.]")

    behind = optional(store, "outdated", default=0) or 0
    if behind:
        st.caption(f":orange[{behind} roast(s) were measured by an older version. "
                   "**Update** puts them right.]")

    # Nothing for a day and a half, from any direction, means the Mac sync has
    # stopped rather than that nobody has roasted.
    try:
        quiet = (pd.Timestamp.now(tz=pd.to_datetime(str(last)).tz)
                 - pd.to_datetime(str(last))).total_seconds() / 3600 if last else None
    except Exception:
        quiet = None
    # Half a day of quiet is worth a word — a roasting day is a few hours, so a
    # sync that stopped this morning should be noticed this afternoon, not in
    # two days' time.
    if quiet is not None and quiet > 12:
        gone = (f"{quiet:.0f} hours" if quiet < 48 else f"{quiet / 24:.0f} days")
        st.caption(f":orange[Nothing has arrived for {gone}. If you have roasted since, "
                   "the roasts are still on the Mac: open "
                   "**`mac/roast-sync-app.command`** there and press *Sync now*. To stop "
                   "having to — the same page has a switch that lets macOS do it every "
                   "fifteen minutes.]")


def account_strip(user: str):
    """Who is signed in, where the roasts are kept, and the way out."""
    with st.sidebar:
        st.divider()
        sidebar_sync()
        st.divider()
        st.caption(f"Signed in as **{user}**")
        st.caption(db.describe())
        if not db.is_shared():
            st.caption(":orange[This computer only — see the README to share a database.]")
        if STALE:
            st.caption(":orange[Update " + ", ".join(STALE) +
                       " — this deploy has an older copy than app.py expects. "
                       "The **Data** page says which file Python actually read.]")
        if st.button("Sign out", use_container_width=True):
            auth.sign_out()
            st.rerun()


def caution(message: str, fix_label: str = "", fix=None, key: str = "",
            icon: str = ":material/warning:", steps: str = ""):
    """A warning that comes with the button that settles it.

    A warning with nothing to press is a chore handed to the reader. Where the
    app can put something right itself, the button does it; where it cannot —
    because the fix is on somebody's Mac — the button shows the exact steps
    rather than pretending.
    """
    st.warning(message, icon=icon)
    if fix_label and fix is not None:
        if st.button(fix_label, key=f"fix_{key}", type="primary"):
            fix()
    elif fix_label and steps:
        with st.expander(fix_label):
            st.markdown(steps)


def bring_up_to_date(relearn: bool = True) -> dict:
    """Everything the app itself can put right, in one pass.

    Re-reads what is stored, re-measures roasts left behind by an older version,
    joins them to their beans and recipes again, re-learns the effect sizes and
    grades any advice a later roast has since tested. This is what the sidebar
    button runs, and what every "fix it" button on a data warning calls into.
    """
    done = {"remeasured": 0, "graded": 0, "roasts": 0}

    behind = optional(store, "outdated", default=0) or 0
    if behind:
        done["remeasured"] = optional(store, "remeasure", default=0) or 0

    refresh()
    frame = roasts()
    done["roasts"] = len(frame)
    if relearn and not frame.empty:
        learning.relearn(frame)
        result = coach.auto_evaluate(frame)
        done["graded"] = result.get("evaluated", 0)
    refresh()
    return done


def empty_state(message: str):
    # A page with nothing on it while the database is full of roasts is the most
    # confusing thing the app can do. Count the rows before saying there are none.
    try:
        stored = signature()
        held = int(stored[0]) if stored and stored[0] else 0
    except Exception:
        held = 0

    if held:
        brand_header("Nothing to work with yet")
        st.warning(
            f"The database holds **{held} roast(s)**, but this page could not read them. "
            "That is a bug rather than a missing import — press **Read them again** and "
            "tell me what happens.")
        if st.button("Read them again", type="primary"):
            refresh()
            st.rerun()
        st.stop()

    brand_header("Nothing to work with yet")
    st.info(message + "  \n\nGo to **Data** in the sidebar to connect your roasts, "
            "or load the demo history to look around first.")
    st.stop()


# ---------------------------------------------------------------------------
# Recommendation cards
# ---------------------------------------------------------------------------

OUTCOME_STYLE = {"achieved": "✅ worked", "partial": "🟡 partly worked",
                 "missed": "❌ did not work", "unknown": "— not measurable"}


def next_roast_plan(row, items):
    """Last roast's control list with the coach's changes folded in.

    This is the thing to have open while the drum turns: every setting, the time
    to make it, and what it was last time where it differs. A plan, not a diff.
    """
    changes = [change for _, item in items.iterrows() for change in moves_of(item)]
    control_changes = [change for change in changes
                       if change.get("control") in recipe.CONTROLS]
    other = [change for change in changes if change.get("control") not in recipe.CONTROLS]

    plan = optional(recipe, "plan", moves_for(row["uid"], token()), control_changes)
    if plan is None or plan.empty:
        return

    st.markdown("##### The next roast, step by step")
    shown = plan.copy()
    shown["set to"] = shown["set to"].map(lambda v: "" if pd.isna(v) else f"{float(v):.0f}")
    shown["last time"] = [
        "" if (pd.isna(was) or not changed) else f"was {float(was):.0f}"
        for was, changed in zip(plan["last time"], plan["changed"])]
    for column in ("ibts", "bt"):
        shown[column] = shown.get(column, pd.Series(index=shown.index, dtype=float)).map(
            lambda v: "" if pd.isna(v) else f"{float(v):.0f} °C")

    # IBTS first: it is the number on the machine's screen when the change is
    # made. The clock says whether the roast is on pace, and the bean probe is
    # there because RoasTime records it — nothing is aimed at it.
    order = ["ibts", "clock", "control", "set to", "last time", "why"]
    if shown["bt"].astype(str).str.strip().any():
        order.insert(2, "bt")
    st.dataframe(shown[order].rename(
        columns={"clock": "time", "ibts": "IBTS", "bt": "bean probe",
                 "why": "why it changed"}),
        width="stretch", hide_index=True,
        height=min(420, 40 + 35 * len(shown)))
    st.caption("Make each change when the **IBTS** reaches that temperature; the time "
               "says whether the roast is on pace. The charge settings have no IBTS "
               "temperature against them because the sensor is looking at an empty hot "
               "drum until the beans settle — charge is a moment, not a number to wait "
               "for.")

    for change in other:
        if change.get("control") == "drop" and change.get("to") is not None:
            st.markdown(f"**Drop at {recipe.clock(change['to'])}** — "
                        f"{change.get('why', '')} (last time {recipe.clock(change.get('from'))}).")
        elif change.get("to") is not None:
            st.markdown(f"**{str(change['control']).capitalize()}: "
                        f"{float(change['from']):.0f} → {float(change['to']):.0f}** — "
                        f"{change.get('why', '')}")

    st.caption("Everything not marked is what you did last time. Power, fan and drum move in "
               "whole steps, so every change here is a whole step.")


def moves_of(item) -> list[dict]:
    """The control changes attached to a piece of advice, however it was stored."""
    raw = item.get("moves")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except Exception:
            return []
    return [change for change in (raw or []) if isinstance(change, dict)]


def recommendation_card(item: dict | pd.Series, stored: bool = False, key: str = ""):
    """One piece of advice, with what it predicts and what became of it."""
    confidence = float(item.get("confidence") or 0)
    outcome = item.get("outcome")

    with st.container(border=True):
        top = st.columns([5, 1])
        top[0].markdown(f"**{item['headline']}**")
        top[1].markdown(
            f"<div style='text-align:right;opacity:.7;font-size:.85rem'>"
            f"confidence {confidence:.0%}</div>", unsafe_allow_html=True)

        st.write(item["finding"])

        # What to actually do, on the machine, in whole steps at a stated time.
        # Nobody sets an average, so no piece of advice here names one.
        for change in moves_of(item):
            control = str(change.get("control", ""))
            # The IBTS temperature first, the clock second. That is the order the
            # roaster works in: a Bullet recipe is written against the IBTS, and
            # the change is made when that number arrives — early or late. The
            # time is how you know whether the roast is on pace, which is worth
            # knowing and is not the instruction.
            at = optional(recipe, "when", change, default=change.get("clock", "—"))

            if change.get("from") is not None and change.get("to") is not None:
                if control == "drop":
                    headline = (f"Drop at **{recipe.clock(change['to'])}** "
                                f"instead of {recipe.clock(change['from'])}")
                else:
                    headline = (f"At **{at}** — {control} "
                                f"**{float(change['from']):.0f} → {float(change['to']):.0f}**")
            else:
                headline = f"At **{at}** — {control} "
                headline += f"{'up' if (change.get('step') or 0) > 0 else 'down'} " \
                            f"{abs(float(change.get('step') or 0)):.0f}"
            st.markdown(
                f"<div style='background:rgba(232,98,42,.10);border-left:3px solid #E8622A;"
                f"padding:8px 12px;border-radius:0 6px 6px 0;margin:6px 0'>{headline}"
                + (f"<div style='opacity:.7;font-size:.86rem'>{change.get('why','')}</div>"
                   if change.get("why") else "")
                + "</div>", unsafe_allow_html=True)

        st.markdown(f"👉 {item['action']}")

        target = item.get("target_metric")
        current, predicted = item.get("current_value"), item.get("predicted_value")
        if target and pd.notna(current) and pd.notna(predicted):
            line = (f"Prediction · **{label_for(target)}** {float(current):.1f} → **{float(predicted):.1f}**"
                    f" · based on {item.get('basis', 'roasting practice')}")
            if outcome:
                observed = item.get("observed_value")
                line += (f" · came out at **{float(observed):.1f}** "
                         f"→ {OUTCOME_STYLE.get(outcome, outcome)}")
            st.caption(line)

        if item.get("reason"):
            with st.expander("Why"):
                st.write(item["reason"])

        if stored and item.get("status") == "open":
            buttons = st.columns([1, 1, 4])
            if buttons[0].button("I'll try this", key=f"apply_{key}_{item['id']}"):
                store.update_recommendation(int(item["id"]), status="applied")
                st.toast("Marked to try. The next roast of this coffee will test it.")
                refresh()
                st.rerun()
            if buttons[1].button("Not for me", key=f"skip_{key}_{item['id']}"):
                store.update_recommendation(int(item["id"]), status="dismissed")
                refresh()
                st.rerun()
        elif stored and item.get("status") == "applied":
            st.caption("⏳ Waiting for the next roast of this coffee to test it.")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def page_coach():
    frame = roasts()
    if frame.empty:
        empty_state("Roast Coach has no roasts yet.")

    brand_header("What to change on your next roast")

    graded = store.recommendations(status="evaluated")
    tested = len(graded)
    hits = int((graded["outcome"] == "achieved").sum()) if tested else 0

    top = st.columns(4)
    top[0].metric("Roasts", len(frame))
    top[1].metric("Coffees", frame["coffee"].nunique())
    top[2].metric("Suggestions to try", len(store.recommendations(status="open")))
    top[3].metric("Predictions that came true", f"{hits}/{tested}" if tested else "—",
                  help="How often a suggestion you tried moved the measure as far as the coach said it would.")

    if st.button("Review my latest roasts", type="primary"):
        latest = (frame.dropna(subset=["coffee"]).sort_values("roasted_at")
                  .groupby("coffee").tail(1))
        bar = st.progress(0.0, text=f"Reading the most recent roast of "
                                    f"{len(latest)} coffee(s)…")

        def step(done, total):
            bar.progress(done / max(total, 1), text=f"Reviewed {done} of {total}")

        reviewed = optional(coach, "review_all", frame, list(latest["uid"]), progress=step)
        if reviewed is None:                       # an older coach.py, one at a time
            for uid in latest["uid"]:
                coach.review_and_save(frame, uid)
        bar.progress(1.0, text="Re-measuring what your roasts have taught it…")
        learning.relearn(frame)
        result = coach.auto_evaluate(frame)
        bar.empty()
        st.success(f"Reviewed the latest roast of {len(latest)} coffee(s); "
                   f"graded {result['evaluated']} earlier suggestion(s).")
        refresh()
        st.rerun()

    st.divider()

    recent = frame.sort_values("roasted_at", ascending=False)
    coffees = list(dict.fromkeys(recent["coffee"].dropna()))
    if not coffees:
        st.info("No coffees identified yet — add a coffee name on the Roasts page.")
        return

    chosen = st.selectbox("Next roast of", coffees, key="coach_coffee")
    plan = coach.plan_for_coffee(frame, chosen)
    latest = plan["based_on"]

    if latest is None:
        st.info("No roasts of that coffee yet.")
        return

    st.markdown(f"#### Based on **{latest['label']}** · {plan['roasts']} roast(s) of this coffee")
    said = [("Roast name", text_of(latest.get("roast_name"))),
            ("Bean", text_of(latest.get("bean"))),
            ("Recipe", text_of(latest.get("recipe_name"))),
            ("Batch", number(latest.get("greenWeight"), " g", 0))]
    st.caption(" · ".join(f"**{name}:** {value}" for name, value in said if value and value != "—"))

    saved = store.recommendations(latest["uid"])
    if saved.empty and plan["items"]:
        coach.review_and_save(frame, latest["uid"])
        saved = store.recommendations(latest["uid"])

    open_items = saved[saved["status"].isin(["open", "applied"])] if not saved.empty else saved
    if open_items.empty:
        st.success("Nothing to change — that roast landed inside every target the coach checks.")
    else:
        next_roast_plan(latest, open_items)
        st.markdown("##### Why each change")
        for _, item in open_items.iterrows():
            recommendation_card(item, stored=True, key="coach")

    if tested:
        st.divider()
        st.markdown("#### What happened when you took the advice")
        history = graded.merge(
            frame[["uid", "label"]].rename(columns={"uid": "roast_id", "label": "from_roast"}),
            on="roast_id", how="left")
        history["result"] = history["outcome"].map(OUTCOME_STYLE)
        history["target_metric"] = history["target_metric"].map(label_for)
        st.dataframe(
            history[["from_roast", "headline", "target_metric", "current_value",
                     "predicted_value", "observed_value", "result"]]
            .rename(columns={"from_roast": "after this roast", "headline": "suggestion",
                             "target_metric": "measure", "current_value": "was",
                             "predicted_value": "predicted", "observed_value": "actual"})
            .round(1),
            width="stretch", hide_index=True)


def page_roasts():
    frame = roasts()
    if frame.empty:
        empty_state("Roast Coach has no roasts yet.")

    brand_header("Every roast, by date and coffee")

    filters = st.columns([2, 2, 2, 2])
    beans = sorted(v for v in frame.get("bean", pd.Series(dtype=str)).dropna().unique() if v)
    recipes = sorted(v for v in frame.get("recipe_name", pd.Series(dtype=str)).dropna().unique() if v)
    picked = filters[0].multiselect(
        "Bean", beans or sorted(frame["coffee"].dropna().unique()), default=[],
        help="The bean RoasTime linked to the roast. Where no bean file matched, the "
             "grouping name is offered instead.")
    picked_recipes = filters[3].multiselect("Recipe", recipes, default=[])
    dates = frame["roasted_at"].dropna()
    if not dates.empty and dates.min().date() < dates.max().date():
        span = filters[1].date_input("Between", value=(dates.min().date(), dates.max().date()),
                                     min_value=dates.min().date(), max_value=dates.max().date())
    else:
        span = None
    only_flagged = filters[2].checkbox(
        "Only roasts with something flagged",
        help="Flagged means a measured condition crossed one of this app's thresholds — "
             "not that anything is wrong with the coffee.")

    view = frame.copy()
    if picked:
        column = "bean" if beans else "coffee"
        view = view[view[column].isin(picked) | view["coffee"].isin(picked)]
    if picked_recipes:
        view = view[view["recipe_name"].isin(picked_recipes)]
    if isinstance(span, tuple) and len(span) == 2:
        view = view[view["roasted_at"].dt.date.between(span[0], span[1])]
    if only_flagged:
        view = view[view.get("flagCount", 0).fillna(0) > 0]

    view = view.sort_values("roasted_at", ascending=False)
    if view.empty:
        st.warning("No roasts match those filters.")
        return

    view = view.copy()
    # Three names, not one: what the roast was called, the bean that was in the
    # drum, and the profile it was run from. Collapsing them loses the thing you
    # most often want to see — two roasts of one bean from different recipes.
    table = pd.DataFrame({
        "Date": view["roasted_at"].dt.strftime("%Y-%m-%d %H:%M"),
        "Roast name": view.get("roast_name", pd.Series("", index=view.index)).fillna(""),
        "Bean": view.get("bean", pd.Series("", index=view.index)).fillna(""),
        "Recipe": view.get("recipe_name", pd.Series("", index=view.index)).fillna(""),
        "Grouped as": view["coffee"],
        "Total": view["totalRoastMinutes"].round(1),
        "First crack": view["firstCrackTime"].round(1),
        "Development": (view["totalRoastMinutes"] - view["firstCrackTime"]).round(1),
        "Drop °C IBTS": view["drumDropTemperature"].round(0),
        "Weight loss %": view["weightLossPercent"].round(1),
        "Flags": view.get("flagSummary", pd.Series("", index=view.index)).fillna(""),
        "Rating": view["rating"].map(lambda v: "" if pd.isna(v) else f"{float(v):.1f}"),
    })

    event = st.dataframe(table, width="stretch", hide_index=True, height=320,
                         on_select="rerun", selection_mode="single-row",
                         key="roast_table")
    rows = event.selection.get("rows", []) if hasattr(event, "selection") else []
    selected = view.iloc[rows[0]] if rows else view.iloc[0]

    st.divider()
    st.markdown(f"### {selected['label']}")

    # One roast, two things to do with it: read what happened, and write down
    # what only you can know. They used to be two pages, which meant finding the
    # same roast twice.
    reading, writing = st.tabs(["What happened", "After the roast"])
    with reading:
        roast_detail(selected, frame, heading=False)
    with writing:
        after_the_roast(selected, frame)


PHASE_SHORT = {"Charge": "charge", "Drying": "dry", "Maillard": "Maillard",
               "Development": "dev"}


def phase_bar(row):
    """Drying / Maillard / Development, as RoasTime shows them."""
    total = float(row.get("totalRoastMinutes") or 0)
    yellow = float(row.get("yellowPointTime") or 0)
    crack = float(row.get("firstCrackTime") or 0)
    if not (total > 0 and 0 < yellow < crack < total):
        return

    shares = phase_shares(total, yellow, crack)
    spans = [("Drying", yellow, shares.get("drying"), "#2E7D6B"),
             ("Maillard", crack - yellow, shares.get("maillard"), "#C58A2E"),
             ("Development", total - crack, shares.get("development"), "#C2521F")]
    cells = []
    for name, minutes, share, colour in spans:
        if share is None:
            continue
        cells.append(
            f'<div style="flex:{max(share, 6)};background:{colour};color:#fff;'
            f'padding:8px 12px;border-radius:7px">'
            f'<div style="font-size:.72rem;letter-spacing:.09em;text-transform:uppercase;'
            f'opacity:.85">{name}</div>'
            f'<div style="font-weight:650;font-size:.95rem">'
            f'{int(minutes)}:{int(round((minutes % 1) * 60)):02d} · {share:.1f}%</div></div>')
    st.markdown(f'<div style="display:flex;gap:5px;margin:2px 0 16px">{"".join(cells)}</div>',
                unsafe_allow_html=True)


def pick(row, *names):
    """The first of these the roast actually has a number for."""
    for name in names:
        value = row.get(name)
        if value is not None and not (isinstance(value, float) and pd.isna(value)):
            return value
    return None


GRADE_COLOUR = {"A": "#2E7D6B", "B": "#C58A2E", "C": "#7A6A5F", "D": "#8C5AA8"}


@st.cache_data(show_spinner=False, max_entries=64)
def moves_for(roast_id: str, token):
    """The control changes this roast made, read back out of its own curve."""
    return recipe.timeline(store.load_curve(roast_id), None)


def identity(row):
    """The three names of a roast, written out in full.

    What it was called, what was in the drum, and what it was run from. These are
    the fields most worth reading exactly — "Zambia Isanya Estate AA" and "Zambia
    Isanya Estate AB" differ in their last letter — and a metric tile, which sets
    its value in 2.2rem and clips what does not fit, is the one component
    guaranteed to hide that. So: label above, full name below, at reading size.
    """
    fields = (
        ("Roast name", text_of(row.get("roast_name")) or "—", ""),
        ("Bean", text_of(row.get("bean")) or "no bean file",
         text_of(row.get("lot_name"))),
        ("Recipe", text_of(row.get("recipe_name")) or "—", ""),
        ("Roasted by", text_of(row.get("roasted_by")) or DEFAULT_ROASTER, ""),
    )
    for column, (label, value, note) in zip(st.columns(len(fields)), fields):
        column.markdown(
            f"<div style='font-size:.78rem;opacity:.62;letter-spacing:.02em'>{label}</div>"
            f"<div style='font-size:1.05rem;font-weight:600;line-height:1.3'>{value}</div>"
            + (f"<div style='font-size:.78rem;opacity:.62'>from the bag “{note}”</div>"
               if note else ""),
            unsafe_allow_html=True)
    st.write("")


def recipe_as_written(row):
    """The recipe RoasTime holds, beside the roast that was actually run.

    A recipe is a plan written against temperature; the roast is what happened.
    Showing them together is the only way to see whether the plan was followed.
    """
    name = text_of(row.get("recipe_name"))
    ref_id = optional(library, "link_id", dict(row), "recipe")
    if not (name or ref_id):
        return

    record = optional(library, "record", "recipe", ref_id) if ref_id else None
    with st.expander(f"The recipe as written: **{name or ref_id}**",
                     expanded=False):
        if not record:
            st.caption("This roast names a recipe, but that recipe file has not been "
                       "imported. The **Data** page says how to bring it in.")
            return

        summary = optional(library, "recipe_summary", record, default={}) or {}
        if summary:
            st.dataframe(pd.DataFrame([summary]), width="stretch", hide_index=True)

        steps = optional(library, "recipe_steps", record, default=[]) or []
        if steps:
            st.markdown("**Its steps** — as RoasTime stored them")
            st.dataframe(pd.DataFrame(steps), width="stretch", hide_index=True)
            st.caption("A Bullet recipe is written against temperature; the table above "
                       "this one is what the roast actually did, in both temperature and "
                       "time, so the two can be read against each other.")
        else:
            st.caption("That recipe carries no steps — only its name and settings.")

        with st.expander("Everything else in the recipe"):
            st.json(record, expanded=False)


def recipe_panel(row):
    """Every setting and the minute it was made — the roast as a list of moves.

    This is what a roast *is* to the person running it: not an average power of
    7.3 through Maillard, but power 9 at charge, 7 at 4:12, 5 at first crack.
    """
    moves = moves_for(row["uid"], token())
    if moves is None or moves.empty:
        st.caption("No control changes stored for this roast.")
        return

    events = {"Charge": 0.0, "Yellowing": row.get("yellowPointTime"),
              "First crack": row.get("firstCrackTime"), "Drop": row.get("totalRoastMinutes")}
    marks = []
    for name, minutes in events.items():
        try:
            minutes = float(minutes)
        except (TypeError, ValueError):
            continue
        if pd.notna(minutes):
            marks.append({"at": minutes, "clock": recipe.clock(minutes), "what": name,
                          "power": "", "fan": "", "drum": ""})

    def temperature(value):
        return "" if value is None or pd.isna(value) else f"{float(value):.0f}"

    rows = []
    for _, item in moves[moves["kind"] == "set"].iterrows():
        rows.append({"at": float(item["at"]), "clock": item["clock"],
                     "bt": temperature(item.get("bt")), "ibts": temperature(item.get("ibts")),
                     "what": "set", item["control"]: f"{float(item['to']):.0f}",
                     **{c: "" for c in recipe.CONTROLS if c != item["control"]}})
    for mark in marks:
        nearest = (moves["at"] - mark["at"]).abs().idxmin()
        mark["bt"] = temperature(moves.loc[nearest].get("bt"))
        mark["ibts"] = temperature(moves.loc[nearest].get("ibts"))

    table = pd.DataFrame(rows + marks).sort_values("at")
    # Moves made at the same second belong on one line — that is one action.
    table = (table.groupby(["clock", "at"], as_index=False)
             .agg({"what": lambda values: " · ".join(sorted(set(v for v in values if v))),
                   "bt": "max", "ibts": "max",
                   "power": "max", "fan": "max", "drum": "max"})
             .sort_values("at"))
    st.dataframe(
        table[["ibts", "clock", "bt", "power", "fan", "drum", "what"]].rename(
            columns={"clock": "time", "ibts": "IBTS °C", "bt": "bean probe °C",
                     "what": ""}),
        width="stretch", hide_index=True, height=min(420, 40 + 35 * len(table)))
    st.caption("Both ways of reading a recipe: the clock, and the temperature each move was "
               "made at. Power, fan and drum move in whole steps, and this is where each one "
               "was set.")


def findings_panel(row, frame):
    """Every finding, in three levels, with the cupping loop attached.

    Observation is what was measured. Diagnosis is the name practice gives it,
    under a threshold this app is willing to state. Cup risk is a hypothesis, and
    is the only thing anyone can settle — which is why there is a button for it.
    """
    baseline = optional(diagnostics, "baseline_for", frame, row.get("coffee"),
                        exclude=row.get("uid"))
    found = optional(diagnostics, "assess", row, baseline, default=[]) or []

    if baseline:
        st.caption(f"Compared against {baseline['from']} of **{baseline['coffee']}** — "
                   f"{baseline['roasts']} roasts of this bean. A matched baseline beats any "
                   "universal target, so where one exists it is what the app uses.")
    else:
        st.caption("No baseline for this bean yet: three roasts of it and comparisons switch "
                   "from this app's configured bands to your own record.")

    if not found:
        st.success("Nothing measured outside the ordinary, and nothing recorded by eye.",
                   icon=":material/check:")
        return

    verdicts = optional(store, "sensory_for", row["uid"], default={}) or {}

    for item in found:
        grade = item.get("grade", "C")
        with st.container(border=True):
            head = st.columns([6, 2])
            head[0].markdown(f"**{item['name']}**")
            head[1].markdown(
                f"<div style='text-align:right'><span style='background:{GRADE_COLOUR.get(grade)};"
                f"color:#fff;border-radius:5px;padding:1px 7px;font-size:.72rem'>"
                f"{grade} · {item['certainty']}</span></div>", unsafe_allow_html=True)

            st.markdown(f"<div style='font-variant-numeric:tabular-nums'>{item['observation']}"
                        "</div>", unsafe_allow_html=True)
            if item.get("diagnosis"):
                st.markdown(f"<div style='opacity:.72;margin-top:6px'>{item['diagnosis']}</div>",
                            unsafe_allow_html=True)

            if item.get("risk"):
                st.markdown(
                    f"<div style='margin-top:8px;padding:8px 10px;border-left:3px solid "
                    f"{GRADE_COLOUR['D']};opacity:.85'><b>Possible cup effect</b> — needs "
                    f"cupping: {item['risk']}</div>", unsafe_allow_html=True)

                recorded = verdicts.get(item["id"], {})
                said = recorded.get("verdict")
                if said:
                    st.caption(f"At the table: **{said}**"
                               + (f" — {recorded.get('note')}" if recorded.get("note") else ""))
                buttons = st.columns(3)
                for column, verdict, label in zip(buttons, store.VERDICTS,
                                                  ("Tasted it — confirmed",
                                                   "Cupped, not present", "Unsure")):
                    if column.button(label, key=f"cup_{row['uid']}_{item['id']}_{verdict}",
                                     use_container_width=True):
                        store.save_sensory(row["uid"], item["id"], verdict)
                        refresh()
                        st.rerun()

            if item.get("source"):
                st.caption(f"Source: {item['source']}")


def readout(row):
    """The numbers RoasTime puts beside the graph, in the same order."""
    def clock(minutes):
        if minutes is None or pd.isna(minutes):
            return "—"
        minutes = float(minutes)
        return f"{int(minutes)}:{int(round((minutes % 1) * 60)):02d}"

    crack, total = row.get("firstCrackTime"), row.get("totalRoastMinutes")
    development = row.get("developmentTime")
    if development is None or pd.isna(development):
        development = (total - crack) if pd.notna(crack) and pd.notna(total) else float("nan")
    # The same share the pattern checks and the coaching rules judge, so what is
    # shown here and what the app says about it can never be different numbers.
    share = phase_shares(total, row.get("yellowPointTime"), crack).get("development", float("nan"))
    rise = row.get("tempRiseAfterFirstCrack")
    green, roasted = row.get("greenWeight"), row.get("weightRoasted")
    loss = row.get("weightLossPercent")

    def block(title, pairs):
        rows = "".join(
            f'<div style="display:flex;justify-content:space-between;gap:14px;padding:3px 0">'
            f'<span style="opacity:.62">{label}</span>'
            f'<span style="font-variant-numeric:tabular-nums;font-weight:600">{value}</span></div>'
            for label, value in pairs if value not in (None, "—", "nan"))
        if not rows:
            return ""
        return (f'<div style="margin-bottom:14px">'
                f'<div style="font-size:.7rem;letter-spacing:.13em;text-transform:uppercase;'
                f'opacity:.5;margin-bottom:5px">{title}</div>{rows}</div>')

    html = "".join([
        block("Roast", [
            ("Preheat", number(row.get("preheatTemperature"), " °C", 0)),
            ("Charge", number(row.get("drumChargeTemperature"), " °C", 0)),
            ("Turning point", f'{clock(row.get("turningPointTime"))} · '
                              f'{number(row.get("ibtsTurningPointTemp"), " °C", 1)}'),
            # RoasTime's own field when it is there, the temperature read off the
            # curve at that point when it is not — never a blank for a roast that
            # plainly reached yellowing.
            ("Yellowing", f'{clock(row.get("yellowPointTime"))} · '
                          f'{number(pick(row, "drumTemperatureYellowingStart", "yellowPointTemp"), " °C", 1)}'),
            ("First crack", f'{clock(crack)} · '
                            f'{number(pick(row, "drumTemperatureFirstCrackStart", "firstCrackTemp"), " °C", 1)}'),
            ("Development", f'{clock(development)} · {number(share, "%")}'
                            + (f' · +{float(rise):.1f} °C' if pd.notna(rise) else "")),
            ("Total time", clock(total)),
            ("End temp", number(row.get("drumDropTemperature"), " °C", 1)),
        ]),
        # Everything here is measured on the IBTS. The bean probe is buried in the
        # drum and lags what the beans are actually doing — close enough to a drum
        # reading that nothing in this app is decided on it. It is shown because
        # RoasTime shows it, and labelled so it cannot be mistaken for the line
        # the roast is read by.
        block("Rate of rise", [
            ("Peak (IBTS)", number(row.get("peakIbtsROR"), " °C/min")),
            ("Peak (bean probe)", number(row.get("peakROR"), " °C/min")),
            ("At first crack", number(row.get("rorAtFirstCrack"), " °C/min")),
            ("At drop", number(row.get("rorAtDrop"), " °C/min")),
            ("Drying average", number(row.get("avgRoRDrying"), " °C/min")),
            ("Maillard average", number(row.get("avgRoRMaillard"), " °C/min")),
            ("Development average", number(row.get("avgRoRDevelopment"), " °C/min")),
        ]),
        block("Yield", [
            ("Green", number(green, " g", 0)),
            ("Roasted", number(roasted, " g", 0)),
            ("Weight loss", number(loss, "%")),
        ]),
        block("Settings", [
            ("Power", " · ".join(
                f"{short} {number(row.get('power' + phase), '', 1)}"
                for phase, short in PHASE_SHORT.items()
                if pd.notna(row.get("power" + phase)))),
            ("Fan", " · ".join(
                f"{short} {number(row.get('fan' + phase), '', 1)}"
                for phase, short in PHASE_SHORT.items()
                if pd.notna(row.get("fan" + phase)))),
            ("Changes", f"{int(row.get('powerChanges') or 0)} power, "
                        f"{int(row.get('fanChanges') or 0)} fan"),
        ]),
        block("Environment", [
            ("Ambient", number(row.get("ambient"), " °C")),
            ("Humidity", number(row.get("humidity"), "%", 0)),
            ("Energy", number(row.get("energy"), " kWh", 2)),
        ]),
        block("From RoasTime", [
            ("Recipe", text_of(row.get("recipe_name")) or "—"),
            ("Machine", text_of(row.get("machine_name")) or "—"),
            ("Roasted by", text_of(row.get("roasted_by")) or "—"),
            ("Roast number", text_of(row.get("roastNumber")) or "—"),
            ("Origin", text_of(row.get("origin")) or "—"),
            ("Process", text_of(row.get("process")) or "—"),
            ("Variety", text_of(row.get("variety")) or "—"),
            ("Altitude", text_of(row.get("altitude")) or "—"),
            ("Harvest", text_of(row.get("harvest")) or "—"),
        ]),
    ])
    st.markdown(f'<div style="font-size:.88rem;line-height:1.5">{html}</div>',
                unsafe_allow_html=True)


def roast_detail(row, frame, heading: bool = True):
    if heading:
        st.markdown(f"### {row['label']}")
    phase_bar(row)
    samples, events = curve_of(row["uid"])

    left, right = st.columns([3, 2])

    with left:
        if samples.empty:
            st.warning("No curve stored for this roast.")
        else:
            st.plotly_chart(
                charts.roast_profile_figure(samples, events, theme(), title=row["label"]),
                width="stretch")

        # Names are not numbers. A metric tile sets its value in 2.2rem, which
        # turns "Zambia Isanya Estate AA" into "Zambia Isan…" — the one thing on
        # the row nobody can guess from its first eleven letters. So they are
        # written out, in full, at reading size.
        identity(row)

        facts = st.columns(4)
        facts[0].metric("Total", number(row.get("totalRoastMinutes"), " min"))
        facts[1].metric("First crack", number(row.get("firstCrackTime"), " min"))
        shares = phase_shares(row.get("totalRoastMinutes"), row.get("yellowPointTime"),
                              row.get("firstCrackTime"))
        facts[2].metric("Development", number(row.get("developmentTime"), " min"),
                        help=(f"{shares['development']:.1f}% of the roast"
                              if "development" in shares else None))
        facts[3].metric("Drop IBTS", number(row.get("drumDropTemperature"), " °C", 0))

        more = st.columns(4)
        more[0].metric("Turning point", number(row.get("turningPointTime"), " min"))
        more[1].metric("Yellowing", number(row.get("yellowPointTime"), " min"))
        # The IBTS rate of rise, not the bean probe's. The IBTS is what the roast
        # is read and driven by; the probe lags it and, buried in the drum, is
        # closer to a drum reading than a bean one.
        more[2].metric("Peak RoR (IBTS)",
                       number(row.get("peakIbtsROR") if pd.notna(row.get("peakIbtsROR"))
                              else row.get("peakROR"), " °C/min"))
        more[3].metric("Weight loss", number(row.get("weightLossPercent"), " %"))

        st.markdown("#### How this roast was actually managed")
        st.caption("Every control change as it happened, at the IBTS temperature it was "
                   "made at. This is what you did on the day — not the recipe, which did "
                   "not change.")
        recipe_panel(row)
        recipe_as_written(row)

        st.markdown("#### What this roast did")
        findings_panel(row, frame)

    with right:
        with st.expander("Every number RoasTime records", expanded=True):
            readout(row)

        st.markdown("#### About this roast")
        with st.form(f"notes_{row['uid']}"):
            bean_linked = text_of(row.get("bean_name"))
            coffee = st.text_input(
                "Coffee", value=text_of(row.get("coffee")),
                help=("RoasTime links this roast to the bean "
                      f"“{bean_linked}”, which is what it is compared against. "
                      "Change the name here and every roast of that bean follows."
                      if bean_linked else
                      "No bean file matched this roast, so this name is what it is "
                      "grouped and compared by."))
            columns = st.columns(2)
            origin = columns[0].text_input("Origin", value=text_of(row.get("origin")))
            process = columns[1].text_input("Process", value=text_of(row.get("process")))
            columns = st.columns(2)
            variety = columns[0].text_input("Variety", value=text_of(row.get("variety")))
            farm = columns[1].text_input("Farm or supplier", value=text_of(row.get("farm")))
            columns = st.columns(2)
            green = columns[0].number_input(
                "Green weight (g)", value=float(row.get("greenWeight") or 0), step=10.0)
            # One list of roast levels in the whole app. Two would let a roast
            # saved on one screen be silently renamed by the other.
            _levels = list(getattr(store, "ROAST_LEVELS", ()))
            _now = text_of(row.get("roast_level"))
            level = columns[1].selectbox(
                "Roast level", ["", *_levels],
                index=(_levels.index(_now) + 1) if _now in _levels else 0)
            columns = st.columns(2)
            rating = columns[0].slider("Rating", 0.0, 5.0,
                                       float(row.get("rating") or 0), step=0.5)
            score = columns[1].number_input("Cupping score", value=float(row.get("cupping_score") or 0),
                                            step=0.25, min_value=0.0, max_value=100.0)
            notes = st.text_area("Tasting notes and what you changed",
                                 value=text_of(row.get("notes")), height=110)

            st.markdown("**What the curve cannot see**")
            st.caption("Measured roast colour carries more sensory weight than drop "
                       "temperature does. Quakers are a green-coffee defect, recorded here "
                       "so they are never blamed on the profile.")
            columns = st.columns(2)
            colour_whole = columns[0].number_input(
                "Roast colour — whole bean", value=float(row.get("colour_whole") or 0),
                step=1.0, help="Agtron, ColorTrack or whatever scale your meter uses.")
            colour_ground = columns[1].number_input(
                "Roast colour — ground", value=float(row.get("colour_ground") or 0), step=1.0)
            columns = st.columns(2)
            colour_sd = columns[0].number_input(
                "Batch colour spread", value=float(row.get("colour_sd") or 0), step=0.1,
                help="Standard deviation across individual beans, if you measure it.")
            quakers = columns[1].number_input(
                "Quakers picked out", value=float(row.get("quaker_count") or 0), step=1.0)
            defects = st.text_input(
                "Seen on the beans", value=text_of(row.get("visual_defects")),
                placeholder="scorching · tipping · facing · charring · mottling",
                help="Type what you saw. Scorching, tipping, facing and charring are "
                     "recognised and explained back with their likely mechanism.")
            if st.form_submit_button("Save", type="primary"):
                store.save_notes(row["uid"], {
                    "coffee": coffee.strip(), "origin": origin.strip(), "process": process.strip(),
                    "variety": variety.strip(), "farm": farm.strip(),
                    "green_weight": green or None, "roast_level": level or None,
                    "rating": rating or None, "cupping_score": score or None, "notes": notes,
                    "colour_whole": colour_whole or None, "colour_ground": colour_ground or None,
                    "colour_sd": colour_sd or None, "quaker_count": quakers or None,
                    "visual_defects": defects.strip()})
                refresh()
                st.toast("Saved.")
                st.rerun()

        if st.button("Use as the reference roast for this coffee", key=f"ref_{row['uid']}"):
            store.set_reference(row["uid"], row["coffee"])
            refresh()
            st.toast(f"{row['label']} is now the reference for {row['coffee']}.")
            st.rerun()
        if row.get("is_reference"):
            st.caption("⭐ This is the reference roast for this coffee.")

    st.markdown("#### What the coach makes of it")
    saved = store.recommendations(row["uid"])
    if saved.empty:
        if st.button("Review this roast", key=f"review_{row['uid']}"):
            coach.review_and_save(frame, row["uid"])
            refresh()
            st.rerun()
        st.caption("Not reviewed yet.")
    else:
        for _, item in saved.iterrows():
            recommendation_card(item, stored=True, key="detail")
        if st.button("Review again", key=f"rereview_{row['uid']}"):
            coach.review_and_save(frame, row["uid"])
            refresh()
            st.rerun()


def learning_section():
    """How far a change moves a measure, on this machine — and which rules earn it.

    Formerly its own page. It is the same question the rest of this page asks,
    asked of every roast at once instead of the two or three in front of you.
    """
    frame = roasts()
    if frame.empty:
        return

    st.markdown("## What the coach has learned from your roasting")

    st.markdown(
        "Generic advice knows which way to turn a knob. Only your roasts know how far. "
        "Every time you roast the same coffee twice with a different setting, that is one "
        "measurement of what the setting does **on your machine, with your batches** — and "
        "the coach uses your number instead of the textbook one as soon as there is enough "
        "evidence."
    )

    if st.button("Re-measure from my roasts", type="primary"):
        with st.spinner("Measuring…"):
            learning.relearn(frame)
        refresh()
        st.rerun()

    learned = learning.relearn(frame)
    learned = learned.sort_values("observations", ascending=False)

    table = pd.DataFrame({
        "What it moves": learned["description"],
        "Per step": learned["slope"].round(2).astype(str) + " " + learned["units"],
        "Your measurement": learned["measured"].map(
            lambda v: "" if pd.isna(v) else f"{float(v):+.2f}"),
        "Starting assumption": learned["prior"].round(2),
        "Roast pairs behind it": learned["observations"],
        "Confidence": (learned["confidence"] * 100).round(0).astype(int).astype(str) + "%",
    })
    st.dataframe(table, width="stretch", hide_index=True)
    st.caption("Blank measurements mean you have not yet roasted the same coffee twice with "
               "that control set differently — the coach is still using its starting assumption.")

    measured = learned[learned["observations"] > 0]
    if not measured.empty:
        st.plotly_chart(charts.effects_figure(measured, theme()), width="stretch")

    st.divider()
    st.markdown("#### How often each kind of advice has worked")
    board = store.rule_scoreboard()
    if board.empty or board["applied"].sum() == 0:
        st.info("No advice has been tested yet. Mark a suggestion as *I'll try this*, roast that "
                "coffee again, and the coach will grade its own prediction.")
    else:
        board = board.rename(columns={
            "rule_id": "advice", "suggested": "times suggested", "applied": "times tried",
            "achieved": "worked", "partial": "partly", "missed": "did not work",
            "mean_error": "average miss", "hit_rate": "hit rate"})
        st.dataframe(board[["advice", "times suggested", "times tried", "worked", "partly",
                            "did not work", "hit rate", "average miss"]].round(2),
                     width="stretch", hide_index=True)
        st.caption("Advice that keeps missing is shown with lower confidence next time.")


def _report_line(report: dict) -> str:
    """What an import actually did, in one sentence."""
    parts = []
    if report["added"]:
        parts.append(f"{report['added']} new roast" + ("s" if report["added"] != 1 else ""))
    if report["updated"]:
        parts.append(f"{report['updated']} updated")
    if report["skipped"]:
        parts.append(f"{report['skipped']} already here")
    if report.get("companions"):
        parts.append(f"{report['companions']} bean/recipe file(s)")
    return "Imported " + (", ".join(parts) if parts else "nothing new")


def page_data():
    brand_header("Where your roasts come from")
    frame = roasts()
    try:
        info = store.summary(frame=frame)
    except TypeError:                     # an older store.py in a half-updated deploy
        info = store.summary()

    columns = st.columns([1, 1, 1, 1, 1])
    columns[0].metric("Roasts", info["roasts"])
    columns[1].metric("Coffees", info["coffees"])
    columns[2].metric("Samples stored", f"{info['samples']:,}")
    columns[3].metric("Last import", (info["imported_at"] or "—")[:16].replace("T", " "),
                      help="When a roast was last written into this database, by anything "
                           "— this browser, another person, or the Mac sync.")
    with columns[4]:
        st.write("")
        if st.button("Re-read", help="Read the database again, in case something else "
                                     "wrote to it just now."):
            refresh()
            st.rerun()

    # How long since anything arrived. A sync that has quietly stopped looks
    # exactly like "no new roasts", and only this number tells them apart.
    stamp = info.get("imported_at")
    if stamp:
        try:
            landed = pd.to_datetime(str(stamp))
            hours = (pd.Timestamp.now(tz=landed.tz) - landed).total_seconds() / 3600
        except Exception:
            hours = None
        if hours is not None and hours > 36:
            caution(
                f"**Nothing new has arrived for {hours / 24:.0f} day(s).** If you have "
                "roasted since then, the Mac sync is not running.",
                "What to run on that Mac", key="quiet", icon=":material/sync_problem:",
                steps=(
                    "```bash\n"
                    "tail -20 ~/Library/Logs/roast-coach-sync.log   # what it did last\n"
                    "launchctl list | grep roastcoach               # is it scheduled\n"
                    "python3 mac/sync_to_database.py                # run it now, and watch\n"
                    "```\n\n"
                    "The last line prints how many roasts it sent **and which database it "
                    "sent them to**. If that is not the database named below, that is the "
                    "whole problem. Then press **Update** in the sidebar."))

    if info["roasts"] and len(frame) != info["roasts"]:
        caution(f"{info['roasts']} roasts are stored but only {len(frame)} could be read.",
                "Read them again", lambda: (refresh(), st.rerun()), key="mismatch")

    from roastcoach.uploader import add_roasts_button, folder_watcher

    st.markdown("### Add roasts")
    st.markdown(
        "**Choose folder…** opens a file dialog, you pick the folder, and every roast "
        "inside it goes in at once. Do that whenever you have roasted — anything already "
        "imported is skipped in the browser without being opened, so only new and changed "
        "roasts are actually read."
    )

    # Carried across the rerun that refreshes the counts above.
    if st.session_state.pop("_import_done", None):
        st.success(st.session_state.pop("_import_message", "Imported."))

    picked = add_roasts_button(known=store.known_sources(), key="add_roasts")

    # A component keeps its last value across reruns, so without this guard the same
    # batch would import again on every rerun. Each message carries a number; each
    # number is acted on once.
    fresh = (isinstance(picked, dict)
             and picked.get("seq") != st.session_state.get("_upload_seq"))
    if fresh:
        st.session_state["_upload_seq"] = picked.get("seq")

    if fresh and picked.get("action") == "files" and (picked.get("files")
                                                     or picked.get("others")):
        # Roasts and everything that names them arrive together: the browser sends
        # the bean, recipe and machine records alongside, so dropping the whole
        # RoasTime folder brings the names with it rather than 512 nameless roasts.
        report = import_files(list(picked.get("files") or [])
                              + list(picked.get("others") or []))
        message = _report_line(report)
        for problem in report["problems"][:5]:
            st.caption(problem)
        if picked.get("remaining"):
            st.success(message + f" — {picked['remaining']} still to read")
            st.rerun()
        st.session_state["_import_message"] = message
        st.session_state["_import_done"] = True
        st.rerun()
    elif fresh and picked.get("action") == "none" and picked.get("chosen"):
        st.info(f"All {picked['chosen']} of those are already imported. Nothing to do.")
    elif fresh and picked.get("action") == "folder-empty":
        st.warning(
            "**Chrome would not upload that folder.** If it said the folder *contains "
            "system files*, that is its rule about anything inside your Mac's Library — "
            "it cannot be overridden from inside a web app. Any of the three below will "
            "get the same files in.",
            icon=":material/block:")

    st.markdown("#### RoasTime's folder is inside your Library")
    st.markdown(
        "That is the one place Chrome will not accept as a folder upload. macOS also "
        "hides it, so it will not appear in the dialog until you go there directly. "
        "Three ways in, in order of least trouble:"
    )
    st.code("~/Library/Application Support/roast-time/roasts", language=None)
    st.markdown(
        "1. **Choose files…** — in the dialog press **⌘⇧G**, paste the path above, Return, "
        "then **⌘A** to select every file and Open. Picking files is allowed where picking "
        "the folder is not.\n"
        "2. **Drag it** — in Finder press ⌘⇧G, paste the path, then drag the folder onto "
        "the box above. Dragging is not the picker and has no such rule. Drag the folder "
        "into Finder's **sidebar** while you are there and it is one click away after that.\n"
        "3. **Zip it** — right-click the roasts folder in Finder, **Compress**, and send "
        "the zip below. A zip is one ordinary file, so nothing objects to it."
    )
    st.caption("Windows: the folder is `%APPDATA%\\roast-time\\roasts` and Chrome will "
               "upload it directly — **Choose folder…** works as-is.")

    st.markdown("#### Or send a zip of the folder")
    zipped = st.file_uploader("A .zip of the roasts folder", type=["zip"],
                              accept_multiple_files=False, key="zipped",
                              label_visibility="collapsed")
    if zipped is not None and st.button("Import that zip", type="primary"):
        import io
        import zipfile

        files = []
        try:
            with zipfile.ZipFile(io.BytesIO(zipped.getvalue())) as archive:
                for member in archive.infolist():
                    leaf = member.filename.split("/")[-1]
                    if member.is_dir() or not leaf or leaf.startswith(".") \
                            or "__MACOSX" in member.filename:
                        continue
                    data = archive.read(member)
                    files.append({"name": leaf,
                                  "text": data.decode("utf-8-sig", errors="replace"),
                                  "modified": 0, "size": len(data)})
        except zipfile.BadZipFile:
            st.error(f"{zipped.name} is not a zip file.")
            files = []
        if files:
            report = import_files(files)
            st.success(_report_line(report) + f" — from {len(files)} file(s) in the zip")
            for problem in report["problems"][:5]:
                st.caption(problem)

    with st.expander("Stop doing this by hand"):
        st.markdown(
            "A web page cannot reach into your Library to copy or zip anything — it has "
            "no access to your disk until you hand it files. Something on **your Mac** has "
            "to do it, on a schedule, outside the browser. The `mac/` folder in the "
            "download has two ways, both set up once and then forgotten:"
        )
        st.markdown(
            "**`roast-sync.command`** — double-click it. It copies your roasts to "
            "`~/Documents/RoastCoach` and offers to keep doing that every fifteen minutes "
            "and at login. After that, **Choose folder…** → Documents → RoastCoach works "
            "forever, because that folder is an ordinary one.\n\n"
            "**`sync_to_database.py`** — removes the import step instead of easing it. It "
            "writes new and changed roasts straight into the shared database, so they "
            "appear here on their own, on every computer. Needs the database from "
            "`SETUP.md`. `python3 mac/sync_to_database.py --install` and it runs itself."
        )
        st.caption("Both only read RoasTime's folder. Neither writes to it. "
                   "Full instructions are in `mac/README.md`.")

        st.markdown("---")
        st.markdown("**Copy it once, by hand** — if you would rather not install anything:")
        system = st.radio("Your computer", ["macOS", "Windows"], horizontal=True,
                          key="copy_platform", label_visibility="collapsed")
        if system == "macOS":
            st.code('mkdir -p ~/Documents/RoastCoach && cp -R ~/Library/Application\\ '
                    'Support/roast-time/roasts/. ~/Documents/RoastCoach/', language="bash")
        else:
            st.code('robocopy "$env:APPDATA\\roast-time\\roasts" '
                    '"$env:USERPROFILE\\Documents\\RoastCoach" /E', language="powershell")

        st.markdown(
            "**Or let the app watch that copy** — Chrome and Edge only, and not a folder "
            "inside your Library, but once connected it picks up new and changed roasts "
            "on every visit with nothing to press."
        )
        watched = folder_watcher(known=store.known_sources(), auto=True, key="folder_watcher")
        watch_fresh = (isinstance(watched, dict)
                       and watched.get("seq") != st.session_state.get("_folder_seq"))
        if watch_fresh:
            st.session_state["_folder_seq"] = watched.get("seq")
        if watch_fresh and watched.get("action") == "files" and watched.get("files"):
            report = import_files(watched["files"])
            if watched.get("remaining"):
                st.success(_report_line(report) +
                           f" — {watched['remaining']} still to read")
                st.rerun()
            store.note_sync(watched.get("folder", ""), watched.get("looked", 0))
            st.session_state["_import_message"] = _report_line(report)
            st.session_state["_import_done"] = True
            st.rerun()
        elif watch_fresh and watched.get("action") == "scanned":
            store.note_sync(watched.get("folder", ""), watched.get("looked", 0))

        st.markdown("---")
        st.markdown("**Try it without a roaster** — three coffees dialled in over a few "
                    "months, simulated.")
        if st.button("Load a demo roasting history"):
            with st.spinner("Simulating a few months of roasting…"):
                report = import_files(demo_data.as_files(demo_data.history()))
            st.success(f"Loaded {report['added']} simulated roasts across three coffees.")
            st.rerun()

    st.divider()

    if STALE:
        rows, package, beside = deploy_report()
        st.markdown("### Files this deploy is running")
        st.warning(
            "app.py is newer than the `roastcoach/` files next to it. Everything below "
            "still works — the app fills the gaps in — but phase percentages, the bean "
            "grouping and the re-measure only come right once these match.",
            icon=":material/sync_problem:")
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

        if package != beside / "roastcoach":
            st.error(
                f"**Python is reading `roastcoach/` from somewhere else.** app.py is in "
                f"`{beside}`, but the package it imported is `{package}`. Updating the copy "
                "beside app.py will not change anything until the other one is removed.",
                icon=":material/error:")
        else:
            st.caption(
                "Those paths are inside this deployment, not on your computer. If they look "
                "right, the files at those paths are simply the older ones: replace the whole "
                "`roastcoach/` folder in the repository — delete it and add the new one in the "
                "same commit, so nothing is left behind — and the app will restart by itself.")

    st.markdown("### Where this is stored")
    if db.is_shared():
        st.success(f"**{db.describe()}** — every computer signed in to this app sees the same "
                   "roasts, and they stay put when the app restarts.", icon=":material/cloud:")
    else:
        st.warning(f"**{db.describe()}** — this copy only. On Streamlit Community Cloud the "
                   "file is wiped whenever the app restarts, and other computers see nothing. "
                   "SETUP.md has the fix: a free Postgres database, one line in secrets.",
                   icon=":material/warning:")

    held = library.counts()
    if held:
        st.caption("Also stored from RoasTime: " +
                   ", ".join(f"{count} {kind}(s)" for kind, count in held.items()) +
                   " — these fill in origin, process, recipe and machine on each roast.")

    # A roast's numbers are worked out once, at import, and kept with it. When a
    # calculation is corrected, the roasts already in the database still carry the
    # old answer until they are measured again — so say so, and offer to do it.
    behind = optional(store, "outdated", default=0) or 0
    if behind:
        def measure_again():
            bar = st.progress(0.0, text="Measuring…")
            done = optional(
                store, "remeasure", default=0,
                progress=lambda position, total: bar.progress(
                    position / max(total, 1), text=f"Measuring… {position} of {total}"))
            bar.empty()
            learning.relearn(store.load_roasts())
            refresh()
            st.session_state["_import_done"] = True
            st.session_state["_import_message"] = f"Measured {done} roast(s) again."
            st.rerun()

        caution(
            f"**{behind} roast(s) were measured by an earlier version of the app.** "
            "Their phase percentages — and any pattern warnings that came from them — "
            "are the old calculation. Nothing needs re-importing: the curves are already "
            "here, so they can simply be measured again.",
            "Bring them up to date", measure_again, key="remeasure",
            icon=":material/calculate:")

    # Measurements can be redone from the stored curve; fields the importer never
    # kept cannot. Those need the files, which are on the Mac, so this is the one
    # caution whose button is a set of steps rather than an action.
    unread = optional(store, "unread", default=0) or 0
    if unread:
        caution(
            f"**{unread} roast(s) were read by an earlier version of the importer.** "
            "It kept a fixed list of id fields, and RoasTime does not always use those "
            "names — which is how a whole library of recipes can be here while every "
            "**Recipe** column stays empty. The files have the rest; they need reading "
            "again, and only the Mac that roasts can do that. Roasts whose file "
            "RoasTime no longer keeps are not counted here — the sync settles those "
            "on its own and stops asking.",
            "How to bring them across", key="unread", icon=":material/refresh:",
            steps=("On the Mac that roasts, open the sync page:\n\n"
                   "```bash\n./mac/roast-sync-app.command\n```\n\n"
                   "and press **Sync now**. It notices these roasts by itself and reads "
                   "their files again — nothing to tick. Each roast is matched by its "
                   "own id and updated in place, so nothing is duplicated and nothing "
                   "you have typed is touched.\n\n"
                   "From a terminal instead:\n\n"
                   "```bash\npython3 mac/sync_to_database.py\n```\n\n"
                   "Then press **Update** in the sidebar here."))

    settled = optional(store, "sealed", default=0) or 0
    if settled:
        st.caption(f"{settled} roast(s) were read by an older importer and their files "
                   "are no longer on the Mac, so they keep what they already had — the "
                   "curve, the measurements and anything typed here. Nothing further can "
                   "be recovered for them, and nothing asks again.")

    # Roasts are compared bean against bean, so it matters that the bean files
    # are actually there and actually match. Say so plainly rather than letting a
    # roast quietly fall back to a name read off its title.
    # Every companion kind, not only beans: a recipe name that never shows up is
    # the same story as a missing bean, and the answer is the same folder.
    rows = [] if frame.empty else frame.to_dict("records")
    cover = optional(library, "coverage", rows) if rows else None
    if cover:
        st.markdown("### What RoasTime knows, and what arrived")
        table = pd.DataFrame([{k: v for k, v in item.items()
                               if k not in ("kind", "worst", "how")} for item in cover])
        st.dataframe(table, width="stretch", hide_index=True)

        for item in cover:
            if item.get("how"):
                st.caption(
                    f"{item['what']} matched by: "
                    + " · ".join(f"{reason} ({count})" for reason, count in item["how"]))

        # Files here, roasts here, and nothing joining them. On this roaster's
        # RoasTime the roasts carry no field named `recipeId` at all, so the join
        # is made from whatever id-shaped field the roast does carry — which only
        # works for roasts imported by a version that kept those fields.
        stranded = [item for item in cover
                    if item["files imported"] and not item["roasts matched"]]
        if stranded:
            caution(
                "**" + ", ".join(f"{item['files imported']} {item['what'].lower()} file(s)"
                                 for item in stranded)
                + " are stored, and no roast is matched to any of them.** Roasts imported "
                  "by an earlier version of this app kept only the link fields it knew the "
                  "names of, and RoasTime does not use those names for recipes. Reading the "
                  "roast files over — nothing is duplicated, nothing you typed is touched — "
                  "brings the rest of the links across.",
                "How to read them again", key="relink", icon=":material/link_off:",
                steps=("On the Mac that roasts:\n\n"
                       "```bash\npython3 mac/sync_to_database.py --again\n```\n\n"
                       "It re-reads every roast file instead of only what has changed, "
                       "matches each one by its own id, and updates it in place. Then press "
                       "**Update** in the sidebar.\n\n"
                       "If the recipe column is still empty afterwards, RoasTime is not "
                       "recording which recipe a roast was run from — run "
                       "`python3 mac/what_roastime_has.py` and it will say so in as many "
                       "words, rather than leaving you to guess."))

        short = [item for item in cover
                 if item["file not here"] or (item["files imported"] == 0
                                              and item["no id on the roast"] < len(rows))]
        if short:
            caution(
                "**" + ", ".join(f"{item['folder']}" for item in short)
                + "** — these folders have not been imported, so the roasts that point at "
                  "them show no "
                + ", ".join(item["what"].lower() for item in short)
                + ". They live *beside* the roasts folder, not inside it:",
                "How to fix this", key="folders", icon=":material/folder_off:",
                steps=(
                    "**Either** drag the whole `roast-time` folder onto the box above — "
                    "not `roasts`. Every file in it is sorted into roasts, beans, "
                    "recipes, containers and profiles by itself.\n\n"
                    "**Or** run the Mac sync, which sends every sibling folder each "
                    "time:\n\n"
                    "```bash\npython3 mac/sync_to_database.py\n```\n\n"
                    "Then press **Update** in the sidebar."))
            st.code("~/Library/Application Support/roast-time/\n"
                    "├── roasts/     ← what you imported\n"
                    "├── beans/      ← the coffee\n"
                    "├── recipes/    ← the recipe names\n"
                    "├── containers/ ← the machine\n"
                    "└── userProfiles/", language=None)
            st.markdown(
                "**Two ways to fix it, both one-off:**\n\n"
                "1. Drag the whole **`roast-time`** folder onto the box above — not "
                "`roasts`. Everything that is not a roast is sorted into beans, recipes "
                "and machines by itself.\n"
                "2. Or run the Mac sync, which sends every sibling folder with each "
                "batch: `python3 mac/sync_to_database.py`\n\n"
                "**If a folder is empty or missing entirely** — which is what a recipe "
                "name that never appears usually means — RoasTime may be keeping that "
                "kind of record in its own database rather than in files. Run "
                "`python3 mac/what_roastime_has.py` on that Mac: it takes the ids your "
                "roasts carry and goes looking for them, folders *and* databases, and "
                "says which.")
            for item in short:
                if item["worst"]:
                    st.caption(f"{item['what']} ids with no file: "
                               + ", ".join(f"`{ref}` ({count})" for ref, count in item["worst"]))

    with st.expander("Every field RoasTime gave us, and what this app does with it"):
        st.caption("Nothing is thrown away on import — the whole roast is stored as it "
                   "arrived. This is what is *read*. Anything with a blank last column is "
                   "kept and searchable but not shown anywhere yet: if something here "
                   "should be on screen, that is the list to point at.")
        fields = optional(store, "field_report", default=pd.DataFrame())
        if fields is None or fields.empty:
            st.caption("No roasts stored yet.")
        else:
            unused = int((fields["used for"] == "").sum())
            st.caption(f"{len(fields)} fields present · {len(fields) - unused} read by the "
                       f"app · {unused} stored but not shown.")
            st.dataframe(fields, width="stretch", hide_index=True, height=360)

    st.markdown("### Where this is stored")
    if db.is_shared():
        st.success(f"**{db.describe()}** — every computer signed in to this app sees the same "
                   "roasts, and they stay put when the app restarts.", icon=":material/cloud:")
    else:
        st.warning(f"**{db.describe()}** — this copy only. On Streamlit Community Cloud the "
                   "file is wiped whenever the app restarts, and other computers see nothing. "
                   "SETUP.md has the fix: a free Postgres database, one line in secrets.",
                   icon=":material/warning:")

    held = library.counts()
    if held:
        st.caption("Also stored from RoasTime: " +
                   ", ".join(f"{count} {kind}(s)" for kind, count in held.items()) +
                   " — these fill in origin, process, recipe and machine on each roast.")

    # A roast's numbers are worked out once, at import, and kept with it. When a
    # calculation is corrected, the roasts already in the database still carry the
    # old answer until they are measured again — so say so, and offer to do it.
    behind = optional(store, "outdated", default=0) or 0
    if behind:
        def measure_again():
            bar = st.progress(0.0, text="Measuring…")
            done = optional(
                store, "remeasure", default=0,
                progress=lambda position, total: bar.progress(
                    position / max(total, 1), text=f"Measuring… {position} of {total}"))
            bar.empty()
            learning.relearn(store.load_roasts())
            refresh()
            st.session_state["_import_done"] = True
            st.session_state["_import_message"] = f"Measured {done} roast(s) again."
            st.rerun()

        caution(
            f"**{behind} roast(s) were measured by an earlier version of the app.** "
            "Their phase percentages — and any pattern warnings that came from them — "
            "are the old calculation. Nothing needs re-importing: the curves are already "
            "here, so they can simply be measured again.",
            "Bring them up to date", measure_again, key="remeasure",
            icon=":material/calculate:")

    # Roasts are compared bean against bean, so it matters that the bean files
    # are actually there and actually match. Say so plainly rather than letting a
    # roast quietly fall back to a name read off its title.
    link = None if frame.empty else optional(library, "link_report", frame.to_dict("records"))
    if link:
        st.markdown("### Which bean each roast is grouped under")
        missing = sum(link["missing"].values())
        counted = st.columns(3)
        counted[0].metric("Matched a bean file", link["matched"])
        counted[1].metric("Bean file not here", missing)
        counted[2].metric("No bean recorded", link["no_id"])

        if link["matched"] == link["roasts"]:
            st.caption("Every roast is grouped under the bean RoasTime says was in the drum.")
        else:
            st.caption(
                "Roasts without a matching bean fall back to what you typed, and then to a "
                "name read out of the roast title — which is why two roasts of one coffee can "
                "end up apart.")
        if missing:
            worst = sorted(link["missing"].items(), key=lambda pair: -pair[1])[:5]
            st.warning(
                f"**{missing} roast(s) name a bean whose file has not been imported.** "
                "Those roasts fall back to a name read off the roast title, which is why "
                "one coffee can appear under several headings.\n\n"
                "Most likely you imported the `roasts` folder on its own. The bean files "
                "live *beside* it, not inside it:",
                icon=":material/link_off:")
            st.code("~/Library/Application Support/roast-time/\n"
                    "├── roasts/     ← what you imported\n"
                    "├── beans/      ← what is missing\n"
                    "├── recipes/\n"
                    "└── containers/", language=None)
            st.markdown(
                "**Two ways to fix it, both one-off:**\n\n"
                "1. Drag the whole **`roast-time`** folder onto the box above — not "
                "`roasts`. Everything that is not a roast is sorted into beans, recipes "
                "and machines by itself.\n"
                "2. Or run the Mac sync, which sends the sibling folders with every "
                "batch: `python3 mac/sync_to_database.py`")
            st.caption("Unmatched ids, most common first: "
                       + ", ".join(f"`{ref}` ({count})" for ref, count in worst))

    with st.expander("Manage stored roasts"):
        st.caption("Removing roasts here does not touch RoasTime — the app only ever reads "
                   "from it. It does remove them for everyone, though.")
        if st.checkbox("I want to delete everything"):
            if st.button("Delete all roasts, notes and advice"):
                store.clear()
                refresh()
                st.rerun()


def after_the_roast(row, frame):
    """What only the roaster can know: colour, weights, defects, the cup.

    This used to be a page of its own, which meant finding the roast twice —
    once to look at it and again to write down what it tasted like. It belongs
    on the roast, so it lives here, under the roast you already have open.
    """
    scales = getattr(store, "COLOUR_SCALES", ())


    identity(row)
    facts = st.columns(4)
    facts[0].metric("Green in", number(row.get("greenWeight"), " g", 0))
    facts[1].metric("Total", number(row.get("totalRoastMinutes"), " min"))
    facts[2].metric("First crack", number(row.get("firstCrackTime"), " min"))
    facts[3].metric("Drop IBTS", number(row.get("drumDropTemperature"), " °C", 0))

    # The preparation is chosen outside the form, so the fields change the moment
    # it is switched rather than on submit. Both preparations are kept: filling in
    # ground does not clear whole bean, because they are two measurements of one
    # roast and neither is a correction of the other.
    preparations = getattr(store, "PREPARATIONS", ("whole bean", "ground"))
    prepared_on = st.radio(
        "Colour measured on", preparations, horizontal=True,
        index=preparations.index(text_of(row.get("colour_prepared_on")))
        if text_of(row.get("colour_prepared_on")) in preparations else 0,
        key=f"prep_{row['uid']}",
        help="A meter reads the same roast lighter ground than whole — by more than "
             "most of the differences you are trying to see — so which one it was is "
             "part of the reading. Both are kept; this only chooses what to fill in.")

    shown_scales = (optional(store, "scales_for", prepared_on, default=None)
                    or [item for item in scales if item[2] == prepared_on] or scales)

    with st.form(f'after_{row["uid"]}'):
        st.markdown(f"**Roast colour, read on {prepared_on}** — whichever meter you "
                    "have. Each scale is stored as you read it; the app never converts "
                    "between them, because they do not convert cleanly.")
        columns = st.columns(len(shown_scales) or 1)
        entered = {}
        for column, (key, name, where, (low, high)) in zip(columns, shown_scales):
            entered[key] = column.number_input(
                name, value=float(row.get(key) or 0), step=1.0, min_value=0.0,
                max_value=float(high) + 100, help=f"Read on the {where}. Typical range "
                                                  f"{low:.0f}–{high:.0f}.")

        # What is already recorded on the other preparation, so switching away
        # from a filled-in reading never looks like losing it.
        other = [item for item in scales if item[2] != prepared_on
                 and pd.notna(row.get(item[0])) and row.get(item[0])]
        if other:
            st.caption("Already recorded on the other preparation: "
                       + " · ".join(f"{name} {float(row.get(key)):.0f} ({where})"
                                    for key, name, where, _range in other))

        levels = getattr(store, "ROAST_LEVELS", ())
        current = text_of(row.get("roast_level"))
        level = st.selectbox(
            "Roast level", ["—", *levels],
            index=(list(levels).index(current) + 1) if current in levels else 0,
            help="The traditional names, light to dark. What the colour meter reads is "
                 "the measurement; this is what you would call it.")

        columns = st.columns(3)
        spread = columns[0].number_input("Batch colour spread (SD)",
                                         value=float(row.get("colour_sd") or 0), step=0.1)
        out_weight = columns[1].number_input("Weight out (g)",
                                             value=float(row.get("weightRoasted") or 0),
                                             step=1.0)
        quakers = columns[2].number_input("Quakers picked out",
                                          value=float(row.get("quaker_count") or 0), step=1.0)

        defects = st.text_input(
            "What the beans looked like", value=text_of(row.get("visual_defects")),
            placeholder="scorching · tipping · facing · charring · mottling · even")

        # RoasTime records a user id, not a name, so this arrives as a number or
        # not at all. One roastery is usually one roaster: default to them, and
        # let it be changed on the roast that somebody else ran.
        roasted_by = st.text_input(
            "Roasted by", value=text_of(row.get("roasted_by")) or DEFAULT_ROASTER,
            help="Defaults to whoever normally roasts here. Change it for a roast "
                 "somebody else ran, and it stays changed.")

        columns = st.columns(2)
        rating = columns[0].slider("Rating", 0.0, 5.0, float(row.get("rating") or 0), step=0.5)
        score = columns[1].number_input("Cupping score",
                                        value=float(row.get("cupping_score") or 0),
                                        step=0.25, min_value=0.0, max_value=100.0)
        notes = st.text_area("Tasting notes", value=text_of(row.get("notes")), height=110)

        if st.form_submit_button("Save and next", type="primary", use_container_width=True):
            values = {key: (value or None) for key, value in entered.items()}
            values.update({"colour_sd": spread or None, "quaker_count": quakers or None,
                           "visual_defects": defects.strip(), "rating": rating or None,
                           "cupping_score": score or None, "notes": notes})
            values["roasted_weight"] = out_weight or None
            values["roasted_by"] = roasted_by.strip() or None
            values["roast_level"] = None if level == "—" else level
            # Which preparation these numbers were read on. Only the scales for
            # that preparation are in `values`, so the other one is left exactly
            # as it was rather than blanked.
            values["colour_prepared_on"] = prepared_on
            store.save_notes(row["uid"], values)
            refresh()
            st.toast("Saved.")
            st.rerun()

    risks = [item for item in (optional(diagnostics, "assess", row.to_dict(),
                                        optional(diagnostics, "baseline_for", frame,
                                                 row.get("coffee"), exclude=row["uid"]),
                                        default=[]) or []) if item.get("risk")]
    if risks:
        st.markdown("#### Anything the roast warned about")
        st.caption("These are hypotheses until somebody tastes them. Recording *not present* "
                   "is as useful as confirming — it is how the app learns which of its own "
                   "warnings are worth hearing.")
        verdicts = optional(store, "sensory_for", row["uid"], default={}) or {}
        for item in risks:
            with st.container(border=True):
                st.markdown(f"**{item['name']}** — {item['risk']}")
                said = verdicts.get(item["id"], {}).get("verdict")
                if said:
                    st.caption(f"Recorded: **{said}**")
                buttons = st.columns(3)
                for column, verdict, label in zip(buttons, store.VERDICTS,
                                                  ("Confirmed at the table",
                                                   "Cupped, not present", "Unsure")):
                    if column.button(label, key=f'after_{row["uid"]}_{item["id"]}_{verdict}',
                                     use_container_width=True):
                        store.save_sensory(row["uid"], item["id"], verdict)
                        refresh()
                        st.rerun()


GROUP_BY = {
    "Bean": "bean",
    "Grouped-as coffee": "coffee",
    "Roast name": "roast_name",
    "Batch size": "_batch",
    "Recipe": "recipe_name",
    "Roast level": "roast_level",
    "Machine": "machine_name",
    "Roasted by": "roasted_by",
    "Month": "_month",
    "Origin": "origin",
    "Process": "process",
}


def with_groupings(frame: pd.DataFrame) -> pd.DataFrame:
    """The derived columns you can group by that are not stored as columns."""
    frame = frame.copy()
    weights = pd.to_numeric(frame.get("greenWeight"), errors="coerce")
    # Batch size in 50 g bands: the Bullet's batches cluster, and a band is what
    # a roaster actually thinks in — "the 800s" rather than 798 g and 803 g.
    frame["_batch"] = weights.apply(
        lambda value: f"{int(round(value / 50) * 50)} g" if pd.notna(value) else "unknown")
    frame["_month"] = pd.to_datetime(frame.get("roasted_at"), errors="coerce").dt.strftime("%Y-%m")
    for column in GROUP_BY.values():
        if column not in frame:
            frame[column] = "unknown"
        blank = "no bean file" if column == "bean" else "unknown"
        frame[column] = frame[column].fillna(blank).replace("", blank).astype(str)
    return frame


def picked_roasts(frame):
    """Choose particular roasts and see them on one pair of axes.

    The grouping below answers "what do my Zambia roasts have in common"; this
    answers the other question, the one asked standing at the machine — *these
    two, or these three: what was different?* It is the same data and a quite
    different act, so it comes first and takes one click.
    """
    if frame.empty:
        return

    ordered = frame.sort_values("roasted_at", ascending=False)
    labels = {}
    for _, row in ordered.iterrows():
        name = f"{str(row['roasted_at'])[:10]} · {row.get('roast_name') or row.get('bean') or row['uid'][:8]}"
        recipe_name = str(row.get("recipe_name") or "").strip()
        if recipe_name:
            name += f" · {recipe_name}"
        labels[name] = row["uid"]

    st.markdown("### Put particular roasts on top of one another")
    chosen = st.multiselect(
        "Roasts to compare", list(labels), default=list(labels)[:2],
        help="Pick two or three to see them overlaid. Pick more and each gets its own "
             "panel on the same axes, which is the readable way to hold four or five "
             "roasts in view at once.")
    if len(chosen) < 2:
        st.caption("Pick at least two.")
        return

    align = st.radio(
        "Line them up at", ["charge", "first crack"], horizontal=True,
        help="Aligning at first crack compares development like with like, whatever "
             "time each roast got there.", key="compare_align")

    series = []
    for name in chosen:
        uid = labels[name]
        curve, _ = curve_of(uid)
        row = frame[frame["uid"] == uid]
        if curve.empty or row.empty:
            continue
        row = row.iloc[0]
        series.append({
            "label": name.split(" · ", 1)[-1][:34],
            "curve": curve,
            "first_crack": row.get("firstCrackTime"),
            "drop": row.get("totalRoastMinutes"),
            "uid": uid,
        })
    if not series:
        st.info("No stored curves for those roasts.")
        return

    limit = getattr(charts, "OVERLAY_LIMIT", 3)
    if len(series) <= limit:
        st.plotly_chart(
            optional(charts, "compare_figure", series, theme(), align=align,
                     default=None) or go_placeholder(),
            width="stretch")
    else:
        st.caption(f"{len(series)} roasts — more than {limit} overlaid lines cannot be "
                   "told apart by colour, so each gets its own panel on the same axes, "
                   "with the first one you picked drawn faintly behind the others.")
        st.plotly_chart(
            optional(charts, "small_multiples_figure", series, theme(), default=None)
            or go_placeholder(), width="stretch")

    # The numbers behind the lines, which is also what makes the chart readable
    # for anyone who cannot separate its colours.
    side_by_side = pd.DataFrame([
        {"Roast": item["label"],
         "First crack": frame.loc[frame["uid"] == item["uid"], "firstCrackTime"].iloc[0],
         "Development": frame.loc[frame["uid"] == item["uid"], "developmentTime"].iloc[0],
         "Total": frame.loc[frame["uid"] == item["uid"], "totalRoastMinutes"].iloc[0],
         "Drop °C IBTS": frame.loc[frame["uid"] == item["uid"], "drumDropTemperature"].iloc[0],
         "Weight loss %": frame.loc[frame["uid"] == item["uid"], "weightLossPercent"].iloc[0]}
        for item in series])
    st.dataframe(side_by_side.round(2), width="stretch", hide_index=True)


def go_placeholder():
    """An empty figure, for a deploy whose charts.py predates a figure above."""
    import plotly.graph_objects as go

    return go.Figure()


def page_after():
    """The after-the-roast input screen: what only the roaster can know.

    This has to be a page of its own. Folding it into the roast detail made it
    disappear — and the job it serves is a different job: you sit down with four
    roasts you cupped this morning and fill them all in, one after another, which
    is a list to work through rather than a roast to read.
    """
    frame = roasts()
    if frame.empty:
        empty_state("Roast Coach has no roasts yet.")

    brand_header("What RoasTime cannot know — colour, weights, defects, the cup")

    scales = getattr(store, "COLOUR_SCALES", ())
    colour_columns = [key for key, *_ in scales]

    def missing(row) -> list[str]:
        gaps = []
        if not any(pd.notna(row.get(key)) and row.get(key) for key in colour_columns):
            gaps.append("colour")
        if not (pd.notna(row.get("weightRoasted")) and row.get("weightRoasted")):
            gaps.append("out weight")
        if not text_of(row.get("notes")):
            gaps.append("notes")
        return gaps

    recent = frame.sort_values("roasted_at", ascending=False)
    waiting = [(uid, label, gaps) for uid, label, gaps in
               ((r["uid"], r["label"], missing(r)) for _, r in recent.iterrows()) if gaps]

    top = st.columns([1, 1, 2])
    top[0].metric("Roasts", len(frame))
    top[1].metric("Still to fill in", len(waiting))
    if waiting:
        top[2].caption("Newest first. Pick one, fill in what you have, save — the roast "
                       "list and every comparison pick it up straight away.")

    choices = [(uid, f"{label}  ·  missing {', '.join(gaps)}") for uid, label, gaps in waiting]
    choices += [(r["uid"], f"{r['label']}  ·  complete")
                for _, r in recent.iterrows()
                if r["uid"] not in {uid for uid, _, _ in waiting}]
    if not choices:
        st.success("Nothing outstanding.", icon=":material/check:")
        return

    picked = st.selectbox("Roast", [uid for uid, _ in choices],
                          format_func=lambda uid: dict(choices)[uid])
    row = frame[frame["uid"] == picked].iloc[0]
    after_the_roast(row, frame)


def page_compare():
    frame = roasts()
    if frame.empty:
        empty_state("Roast Coach has no roasts yet.")

    brand_header("Compare roasts you pick, or groups of them")
    frame = with_groupings(frame)

    picked_roasts(frame)
    st.divider()
    st.markdown("### Or group every roast and see what changes")

    picked = st.multiselect(
        "Group by", list(GROUP_BY), default=["Bean"],
        help="Pick more than one to see combinations — bean × batch size, or recipe × "
             "roast level. Every measure below is worked out inside each group.")
    if not picked:
        st.info("Pick at least one thing to group by.")
        return

    columns = [GROUP_BY[name] for name in picked]

    with st.expander("Narrow it down first"):
        for name in list(GROUP_BY):
            column = GROUP_BY[name]
            values = sorted(frame[column].unique())
            if 1 < len(values) <= 40:
                keep = st.multiselect(name, values, default=[], key=f"filter_{column}")
                if keep:
                    frame = frame[frame[column].isin(keep)]
    if frame.empty:
        st.warning("Nothing matches those filters.")
        return

    grouped = frame.groupby(columns)
    summary = pd.DataFrame({
        "roasts": grouped.size(),
        "first crack": grouped["firstCrackTime"].mean().round(2),
        "development": grouped["developmentTime"].mean().round(2),
        "development %": grouped.apply(
            lambda part: float(np.nanmean([
                phase_shares(r.get("totalRoastMinutes"), r.get("yellowPointTime"),
                             r.get("firstCrackTime")).get("development", float("nan"))
                for _, r in part.iterrows()])), include_groups=False).round(1),
        "total": grouped["totalRoastMinutes"].mean().round(2),
        "drop °C IBTS": grouped["drumDropTemperature"].mean().round(0),
        "weight loss %": grouped["weightLossPercent"].mean().round(1),
        "spread of first crack": grouped["firstCrackTime"].std().round(2),
        "rating": grouped["rating"].mean().round(2),
    }).sort_values("roasts", ascending=False)

    # Where every roast in a group shares a bean, a recipe or a roast name, say
    # so: grouping by batch size is much more useful when the row can also tell
    # you it was all one bean on one recipe.
    for name, column in (("bean", "bean"), ("recipe", "recipe_name"),
                         ("roast name", "roast_name")):
        if column in frame and column not in columns:
            single = grouped[column].agg(
                lambda values: (sorted(set(values))[0] if len(set(values)) == 1
                                else f"{len(set(values))} different"))
            if single.astype(str).str.strip().replace("unknown", "").any():
                summary[name] = single

    # Colour, where it has been measured — averaged per scale so the numbers stay
    # in the units they were read in.
    for key, name, _where, _range in getattr(store, "COLOUR_SCALES", ()):
        if key in frame and pd.to_numeric(frame[key], errors="coerce").notna().any():
            summary[name] = grouped[key].mean().round(1)

    st.dataframe(summary, width="stretch")
    st.caption("Spread is the standard deviation within the group — how repeatable that "
               "combination has been, which is usually the more interesting number.")

    measure = st.selectbox(
        "Chart", ["development %", "first crack", "development", "total", "drop °C IBTS",
                  "weight loss %", "rating"], index=0)
    chart = summary[measure].dropna()
    if not chart.empty:
        chart.index = [" · ".join(map(str, index)) if isinstance(index, tuple) else str(index)
                       for index in chart.index]
        st.bar_chart(chart, horizontal=True, height=max(220, 32 * len(chart)))

    # One group, in full: how it has trended, how repeatable it is, and every
    # roast in it drawn on top of the others.
    st.divider()
    keys = list(summary.index)
    labels = {(" · ".join(map(str, key)) if isinstance(key, tuple) else str(key)): key
              for key in keys}
    chosen = st.selectbox("Look closer at", list(labels))
    key = labels[chosen]
    picks = key if isinstance(key, tuple) else (key,)
    same = frame.copy()
    for column, value in zip(columns, picks):
        same = same[same[column] == value]
    same = same.sort_values("roasted_at")
    if same.empty:
        return

    facts = st.columns(4)
    facts[0].metric("Roasts", len(same))
    facts[1].metric("First roasted", same["roasted_at"].min().strftime("%Y-%m-%d"))
    reference = same[same.get("is_reference", 0) == 1]
    facts[2].metric("Reference roast",
                    reference.iloc[0]["roasted_at"].strftime("%Y-%m-%d")
                    if not reference.empty else "not set")
    facts[3].metric("Best rating", number(same["rating"].max(), "", 1))

    st.plotly_chart(charts.trend_figure(same, theme()), width="stretch")

    if len(same) > 1:
        st.markdown("#### How repeatable is this combination")
        spread = learning.consistency(same.assign(coffee="_group"), "_group")
        if not spread.empty:
            spread["measure"] = spread["measure"].map(label_for)
            st.dataframe(spread.round(2), width="stretch", hide_index=True)
            st.caption("Spread is the standard deviation inside this group. Smaller is more "
                       "repeatable — and repeatability is what makes a change readable.")

        st.markdown("#### Every roast in it, one on top of another")
        curves = {}
        for _, row in same.tail(12).iterrows():
            frame_of, _ = curve_of(row["uid"])
            if not frame_of.empty:
                curves[row["uid"]] = (row["label"], frame_of)
        if curves:
            highlight = (reference.iloc[0]["uid"] if not reference.empty
                         else same.iloc[-1]["uid"])
            st.plotly_chart(
                charts.all_roasts_figure(curves, theme(), measure="smoothDrumDerivative",
                                         highlight=highlight), width="stretch")
            st.caption("The reference roast — or the most recent one — is drawn in front.")

    # What the coach has learned is the same act at a longer range: comparing
    # roasts to find out how much a change moves a measure. It reads as the last
    # section of this page, not a page of its own.
    st.divider()
    learning_section()


def page_setup():
    """Everything that is about the app rather than about a roast.

    Two questions live here and they are not the same question: *is my data all
    right* — where it is kept, what arrived, what is behind — and *why should I
    believe any of this*. Two tabs, one page, and neither of them in the way of
    the four things a roaster came to do.
    """
    data, method = st.tabs(["Data and sync", "How it decides"])
    with data:
        page_data()
    with method:
        page_method()


def page_method():
    brand_header("What the app is willing to say, and how sure it is")

    st.markdown(
        "Roasting software has a habit of reading a curve and announcing a taste. This one "
        "does not. Every finding is built in three levels, and each claims less than the "
        "one before it."
    )

    levels = st.columns(3)
    levels[0].info("**Observation**  \nWhat was measured. *Rate of rise fell from 10.2 to "
                   "5.6 °C/min between 8:16 and 8:49.* Reproducible, no opinion in it.",
                   icon=":material/straighten:")
    levels[1].warning("**Diagnosis**  \nThe name roasting practice gives that shape, under "
                      "a stated threshold. *Pronounced first-crack crash.*",
                      icon=":material/label:")
    levels[2].error("**Possible cup effect**  \nA hypothesis. *Associated with muted or "
                    "baked character.* Only cupping settles it — so the app asks you.",
                    icon=":material/local_cafe:")

    st.markdown("### How much each claim is worth")
    grades = getattr(evidence, "GRADES", {})
    st.dataframe(pd.DataFrame([
        {"Grade": key, "Means": entry["name"], "Said as": entry["wording"],
         "In practice": entry["meaning"]}
        for key, entry in grades.items()], columns=["Grade", "Means", "Said as", "In practice"]),
        width="stretch", hide_index=True)

    st.markdown("### Every threshold, and what it is")
    st.caption("These are application settings, not published boundaries. They live at the "
               "top of `roastcoach/metrics.py`, in one place, so they can be argued with.")
    st.dataframe(pd.DataFrame([
        {"Setting": "Crash bands",
         "Value": "<15% minimal · 15–30% mild · 30–45% moderate · >45% pronounced",
         "Why it is this way": "A crash is measured as a percentage fall from the roast's "
                               "own settled pre-crack rate, never a fixed °C/min: that "
                               "number depends on probe placement, sampling rate, smoothing "
                               "and batch size, so it does not travel between machines."},
        {"Setting": "Crash raised at",
         "Value": f"{metric_rules.CRASH_FLAG_FROM:.0f}% fall",
         "Why it is this way": "Some decline after first crack is ordinary. This is where "
                               "the app starts calling it a crash."},
        {"Setting": "Flick",
         "Value": f"sustained rise ≥ {metric_rules.FLICK_POSSIBLE_SECONDS:.0f}s "
                  f"(< {metric_rules.FLICK_TRANSIENT_SECONDS:.0f}s is noise)",
         "Why it is this way": "A reversal has to last longer than the probe's own wobble "
                               "before it is worth a name."},
        {"Setting": "Stall",
         "Value": f"±{metric_rules.STALL_DEAD_BAND:.1f} °C/min for "
                  f"{metric_rules.STALL_SECONDS:.0f}s",
         "Why it is this way": "The dead band is for probe noise. Otherwise this is the most "
                               "objective condition here — the roast stopped climbing."},
        {"Setting": "Phase bands",
         "Value": f"development {metric_rules.DEVELOPMENT_BAND[0]:.0f}–"
                  f"{metric_rules.DEVELOPMENT_BAND[1]:.0f}% · drying "
                  f"{metric_rules.DRYING_BAND[0]:.0f}–{metric_rules.DRYING_BAND[1]:.0f}%",
         "Why it is this way": "Used only until you have three roasts of a bean. After that "
                               "the comparison is against your own record, which is a much "
                               "stronger statement than any universal figure. Excellent "
                               "roasts are made outside these bands."},
    ]), width="stretch", hide_index=True)

    st.markdown("### Where the confidence comes from")
    sources = getattr(evidence, "SOURCES", [])
    st.dataframe(pd.DataFrame([
        {"Source": item["cite"], "Kind": item["kind"], "What it supports": item["what"]}
        for item in sources]), width="stretch", hide_index=True)
    st.caption("Practitioner and educational sources are listed as such. They are the origin "
               "of most modern rate-of-rise vocabulary — crash, flick, declining rate of "
               "rise, development ratio — and that is a different thing from experimental "
               "validation.")

    st.markdown("### Which warnings have actually landed")
    board = optional(store, "sensory_scoreboard", default=pd.DataFrame())
    if board is None or board.empty:
        st.caption("Nothing cupped against a warning yet. Each roast's findings carry "
                   "**Tasted it — confirmed** / **Cupped, not present** buttons; once a few "
                   "are recorded, this becomes the honest measure of whether a heuristic "
                   "earns its place — not how often it fires, but how often somebody tasted "
                   "what it warned about.")
    else:
        st.dataframe(board.rename(columns={"condition_id": "condition"}),
                     width="stretch", hide_index=True)


# ---------------------------------------------------------------------------


def main():
    """Draw the app. Streamlit runs this file as ``__main__``; nothing else does.

    Everything above is definitions, so this file can be imported — by the tests,
    or by anything else that wants one of these functions — without a page being
    drawn. That is not only tidiness: drawing a page outside a Streamlit session
    quietly damages Streamlit itself, because with no session there is no cursor
    to hang a block on, so ``st.form`` stamps its form id onto the *root* element
    and never takes it off. Every widget drawn afterwards in that process then
    believes it is inside a form. An import should not be able to do that.
    """
    st.logo(str(ASSETS / "logo-full.svg"), icon_image=str(ASSETS / "icon-64.png"))

    # Nothing below this line renders for anyone who has not signed in.
    user = auth.require(logo=_mark())
    account_strip(user)

    # Five pages, in the order a roast lives through them: what to change next,
    # the roast itself, what you learned from cupping it, how it compares, and
    # everything that is about the app rather than about coffee. Seven was too
    # many — Learning belongs at the foot of Compare and Method beside Data — but
    # writing down what a roast tasted like is its own sitting, with its own list
    # of what is still outstanding, and it needs a door of its own.
    pages = [
        st.Page(page_coach, title="Coach", icon=":material/insights:"),
        st.Page(page_roasts, title="Roasts", icon=":material/local_fire_department:"),
        st.Page(page_after, title="After the roast", icon=":material/edit_note:"),
        st.Page(page_compare, title="Compare", icon=":material/analytics:"),
        st.Page(page_setup, title="Setup", icon=":material/settings:"),
    ]

    # The test suite opens one page at a time; the browser always gets all five.
    only = os.environ.get("ROAST_COACH_PAGE")
    if only:
        pages = [page for page in pages if page.title == only] or pages

    st.navigation(pages).run()


if __name__ == "__main__":
    main()
