"""
Roast Coach — read your Aillio Bullet roasts, understand them, improve them.

    streamlit run app.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from roastcoach import auth, charts, coach, db, demo_data, learning, store
from roastcoach.curves import create_roast_samples, roast_events
from roastcoach.metrics import FLAG_EXPLANATIONS, FLAG_LABELS
from roastcoach.naming import label_for

ASSETS = Path(__file__).parent / "assets"

st.set_page_config(page_title="Roast Coach", page_icon=str(ASSETS / "icon-64.png"),
                   layout="wide", initial_sidebar_state="expanded")


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False)
def load(token: int) -> pd.DataFrame:
    return store.load_roasts()


def refresh():
    st.session_state["data_token"] = st.session_state.get("data_token", 0) + 1
    load.clear()
    samples_for.clear()


def roasts() -> pd.DataFrame:
    return load(st.session_state.get("data_token", 0))


@st.cache_data(show_spinner=False, max_entries=128)
def samples_for(roast_id: str, token: int):
    roast = store.roast_dict(roast_id)
    if not roast:
        return pd.DataFrame(), []
    return create_roast_samples(roast, drop_factor=2), roast_events(roast)


def curve_of(roast_id: str):
    return samples_for(roast_id, st.session_state.get("data_token", 0))


def theme() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


def number(value, suffix="", decimals=1, dash="—"):
    if value is None or pd.isna(value):
        return dash
    return f"{float(value):.{decimals}f}{suffix}"


def import_files(files: list[dict]) -> dict:
    """Bring roasts in, then re-learn and re-grade — the whole cycle."""
    report = store.add_roasts(files)
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


def account_strip(user: str):
    """Who is signed in, where the roasts are kept, and the way out."""
    with st.sidebar:
        st.divider()
        st.caption(f"Signed in as **{user}**")
        st.caption(db.describe())
        if not db.is_shared():
            st.caption(":orange[This computer only — see the README to share a database.]")
        if st.button("Sign out", use_container_width=True):
            auth.sign_out()
            st.rerun()


def empty_state(message: str):
    brand_header("Nothing to work with yet")
    st.info(message + "  \n\nGo to **Data** in the sidebar to connect your roasts, "
            "or load the demo history to look around first.")
    st.stop()


# ---------------------------------------------------------------------------
# Recommendation cards
# ---------------------------------------------------------------------------

OUTCOME_STYLE = {"achieved": "✅ worked", "partial": "🟡 partly worked",
                 "missed": "❌ did not work", "unknown": "— not measurable"}


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
        st.markdown(f"👉 **{item['action']}**")

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
        with st.spinner("Reading the roasts…"):
            for coffee in frame["coffee"].dropna().unique():
                latest = frame[frame["coffee"] == coffee].sort_values("roasted_at").iloc[-1]
                coach.review_and_save(frame, latest["uid"])
            learning.relearn(frame)
            result = coach.auto_evaluate(frame)
        st.success(f"Reviewed {frame['coffee'].nunique()} coffee(s); "
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

    saved = store.recommendations(latest["uid"])
    if saved.empty and plan["items"]:
        coach.review_and_save(frame, latest["uid"])
        saved = store.recommendations(latest["uid"])

    open_items = saved[saved["status"].isin(["open", "applied"])] if not saved.empty else saved
    if open_items.empty:
        st.success("Nothing to change — that roast landed inside every target the coach checks.")
    else:
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

    filters = st.columns([2, 2, 2, 1])
    coffees = sorted(frame["coffee"].dropna().unique())
    picked = filters[0].multiselect("Coffee", coffees, default=[])
    dates = frame["roasted_at"].dropna()
    if not dates.empty and dates.min().date() < dates.max().date():
        span = filters[1].date_input("Between", value=(dates.min().date(), dates.max().date()),
                                     min_value=dates.min().date(), max_value=dates.max().date())
    else:
        span = None
    only_flagged = filters[2].checkbox("Only roasts with something flagged")

    view = frame.copy()
    if picked:
        view = view[view["coffee"].isin(picked)]
    if isinstance(span, tuple) and len(span) == 2:
        view = view[view["roasted_at"].dt.date.between(span[0], span[1])]
    if only_flagged:
        view = view[view.get("flagCount", 0).fillna(0) > 0]

    view = view.sort_values("roasted_at", ascending=False)
    if view.empty:
        st.warning("No roasts match those filters.")
        return

    view = view.copy()
    table = pd.DataFrame({
        "Date": view["roasted_at"].dt.strftime("%Y-%m-%d %H:%M"),
        "Coffee": view["coffee"],
        "Total": view["totalRoastMinutes"].round(1),
        "First crack": view["firstCrackTime"].round(1),
        "Development": (view["totalRoastMinutes"] - view["firstCrackTime"]).round(1),
        "Drop °C": view["drumDropTemperature"].round(0),
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
    roast_detail(selected, frame)


def roast_detail(row, frame):
    st.markdown(f"### {row['label']}")
    samples, events = curve_of(row["uid"])

    left, right = st.columns([3, 2])

    with left:
        if samples.empty:
            st.warning("No curve stored for this roast.")
        else:
            st.plotly_chart(
                charts.roast_profile_figure(samples, events, theme(), title=row["label"]),
                width="stretch")

        facts = st.columns(4)
        facts[0].metric("Total", number(row.get("totalRoastMinutes"), " min"))
        facts[1].metric("First crack", number(row.get("firstCrackTime"), " min"))
        development = row.get("totalRoastMinutes", float("nan")) - row.get("firstCrackTime", float("nan"))
        facts[2].metric("Development", number(development, " min"))
        facts[3].metric("Drop", number(row.get("drumDropTemperature"), " °C", 0))

        more = st.columns(4)
        more[0].metric("Turning point", number(row.get("turningPointTime"), " min"))
        more[1].metric("Yellowing", number(row.get("yellowPointTime"), " min"))
        more[2].metric("Peak RoR", number(row.get("peakROR"), " °C/min"))
        more[3].metric("Weight loss", number(row.get("weightLossPercent"), " %"))

        flags = [f for f in FLAG_LABELS if bool(row.get(f))]
        if flags:
            for flag in flags:
                st.warning(f"**{FLAG_LABELS[flag]}** — {FLAG_EXPLANATIONS[flag]}", icon="⚠️")

    with right:
        st.markdown("#### About this roast")
        with st.form(f"notes_{row['uid']}"):
            coffee = st.text_input("Coffee", value=text_of(row.get("coffee")))
            columns = st.columns(2)
            origin = columns[0].text_input("Origin", value=text_of(row.get("origin")))
            process = columns[1].text_input("Process", value=text_of(row.get("process")))
            columns = st.columns(2)
            variety = columns[0].text_input("Variety", value=text_of(row.get("variety")))
            farm = columns[1].text_input("Farm or supplier", value=text_of(row.get("farm")))
            columns = st.columns(2)
            green = columns[0].number_input(
                "Green weight (g)", value=float(row.get("greenWeight") or 0), step=10.0)
            level = columns[1].selectbox(
                "Roast level", ["", "light", "light-medium", "medium", "medium-dark", "dark"],
                index=(["", "light", "light-medium", "medium", "medium-dark", "dark"].index(text_of(row.get("roast_level")))
                       if text_of(row.get("roast_level")) in
                       ["", "light", "light-medium", "medium", "medium-dark", "dark"] else 0))
            columns = st.columns(2)
            rating = columns[0].slider("Rating", 0.0, 5.0,
                                       float(row.get("rating") or 0), step=0.5)
            score = columns[1].number_input("Cupping score", value=float(row.get("cupping_score") or 0),
                                            step=0.25, min_value=0.0, max_value=100.0)
            notes = st.text_area("Tasting notes and what you changed",
                                 value=text_of(row.get("notes")), height=110)
            if st.form_submit_button("Save", type="primary"):
                store.save_notes(row["uid"], {
                    "coffee": coffee.strip(), "origin": origin.strip(), "process": process.strip(),
                    "variety": variety.strip(), "farm": farm.strip(),
                    "green_weight": green or None, "roast_level": level or None,
                    "rating": rating or None, "cupping_score": score or None, "notes": notes})
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


def page_coffees():
    frame = roasts()
    if frame.empty:
        empty_state("Roast Coach has no roasts yet.")

    brand_header("How each coffee is going")

    grouped = frame.groupby("coffee")
    summary = pd.DataFrame({
        "roasts": grouped.size(),
        "last roasted": grouped["roasted_at"].max().dt.strftime("%Y-%m-%d"),
        "avg first crack": grouped["firstCrackTime"].mean().round(1),
        "avg development": (grouped["totalRoastMinutes"].mean() - grouped["firstCrackTime"].mean()).round(1),
        "avg drop °C": grouped["drumDropTemperature"].mean().round(0),
        "best rating": grouped["rating"].max(),
    }).sort_values("last roasted", ascending=False)
    st.dataframe(summary, width="stretch")

    chosen = st.selectbox("Look at", summary.index.tolist())
    same = frame[frame["coffee"] == chosen].sort_values("roasted_at")

    columns = st.columns(4)
    columns[0].metric("Roasts", len(same))
    columns[1].metric("First roasted", same["roasted_at"].min().strftime("%Y-%m-%d"))
    reference = same[same["is_reference"] == 1]
    columns[2].metric("Reference roast",
                      reference.iloc[0]["roasted_at"].strftime("%Y-%m-%d") if not reference.empty else "not set")
    columns[3].metric("Best rating", number(same["rating"].max(), "", 1))

    st.plotly_chart(charts.trend_figure(same, theme()), width="stretch")

    if len(same) > 1:
        st.markdown("#### How repeatable is it")
        spread = learning.consistency(frame, chosen)
        if not spread.empty:
            spread["measure"] = spread["measure"].map(label_for)
            st.dataframe(spread.round(2), width="stretch", hide_index=True)
            st.caption("Spread is the standard deviation across your roasts of this coffee. "
                       "Smaller is more repeatable.")

        st.markdown("#### Every roast, one on top of another")
        curves = {}
        for _, row in same.tail(12).iterrows():
            frame_of, _ = curve_of(row["uid"])
            if not frame_of.empty:
                curves[row["uid"]] = (row["label"], frame_of)
        if curves:
            highlight = reference.iloc[0]["uid"] if not reference.empty else same.iloc[-1]["uid"]
            st.plotly_chart(
                charts.all_roasts_figure(curves, theme(), measure="smoothDrumDerivative",
                                         highlight=highlight),
                width="stretch")
            st.caption("The reference roast — or the most recent one — is drawn in front.")


def page_learning():
    frame = roasts()
    if frame.empty:
        empty_state("Roast Coach has no roasts yet.")

    brand_header("What the coach has learned from your roasting")

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


def page_data():
    brand_header("Where your roasts come from")
    info = store.summary()

    columns = st.columns(4)
    columns[0].metric("Roasts", info["roasts"])
    columns[1].metric("Coffees", info["coffees"])
    columns[2].metric("Samples stored", f"{info['samples']:,}")
    columns[3].metric("Last import", (info["imported_at"] or "—")[:16].replace("T", " "))

    # ---- the one button ---------------------------------------------------
    st.markdown("### Add roasts")
    st.markdown(
        "Pick the roast files you want. In Chrome and Edge the dialog reopens in whatever "
        "folder you used last, so after the first time this is: click, select all, done. "
        "**Files you have already imported are left alone** — only new and changed ones are "
        "read, so selecting the whole folder every time costs nothing."
    )

    from roastcoach.uploader import add_roasts_button, folder_picker

    # Carried across the rerun that updates the counts above.
    just_imported = bool(st.session_state.pop("_import_done", None))
    if just_imported:
        st.success(st.session_state.pop("_import_message", "Imported."))

    result = add_roasts_button(known=store.known_sources(), key="add_roasts")

    # A component keeps its last value across reruns, so without this guard the
    # same batch would be imported again on every rerun. Each message the
    # browser sends carries a number; each number is acted on once.
    fresh = (isinstance(result, dict)
             and result.get("seq") != st.session_state.get("_upload_seq"))
    if fresh:
        st.session_state["_upload_seq"] = result.get("seq")

    if fresh and result.get("action") == "files" and result.get("files"):
        report = import_files(result["files"])
        message = f"Imported {report['added']} new roast(s)"
        if report["updated"]:
            message += f", updated {report['updated']}"
        if report["skipped"]:
            message += f", skipped {report['skipped']} already here"
        for problem in report["problems"][:5]:
            st.caption(problem)
        if result.get("notRoasts"):
            st.caption("Not roast files, ignored: " + ", ".join(result["notRoasts"][:6]))
        if result.get("remaining"):
            st.success(message + f" — {result['remaining']} still to read")
            st.rerun()
        st.session_state["_import_message"] = message
        st.session_state["_import_done"] = True
        st.rerun()
    elif fresh and result.get("action") == "none" and result.get("chosen") and not just_imported:
        st.info(f"All {result['chosen']} of those are already imported. Nothing to do.")

    with st.expander("Where RoasTime keeps your roasts"):
        st.markdown(
            """
| System | Folder |
| --- | --- |
| macOS | `~/Library/Application Support/roast-time/roasts` |
| Windows | `%APPDATA%\\roast-time\\roasts` |

Picking **files** from these folders works. Picking the **folder itself** does not —
Chrome refuses anything inside your system Library, saying it *contains system files*.
That is why the button above asks for files.

On macOS the dialog can jump straight there: press **⌘⇧G** and paste the path. Do that
once; after that it reopens there by itself.

Nothing is ever written back to RoasTime's folder. The app only reads.
"""
        )

    st.divider()

    # ---- everything else --------------------------------------------------
    with st.expander("Other ways in"):
        st.markdown("**Drag files in** — works in every browser, including Safari. "
                    "Roast files, or a zip of the whole folder.")
        uploads = st.file_uploader("Roast files or a .zip", accept_multiple_files=True,
                                   type=None, key="uploads", label_visibility="collapsed")
        if uploads and st.button("Import these"):
            files = []
            for uploaded in uploads:
                payload = uploaded.getvalue()
                if uploaded.name.lower().endswith(".zip"):
                    import io
                    import zipfile
                    try:
                        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                            for member in archive.namelist():
                                if member.endswith("/") or "__MACOSX" in member:
                                    continue
                                leaf = member.split("/")[-1]
                                if not leaf or leaf.startswith("."):
                                    continue
                                data = archive.read(member)
                                files.append({"name": leaf,
                                              "text": data.decode("utf-8-sig", errors="replace"),
                                              "modified": 0, "size": len(data)})
                    except zipfile.BadZipFile:
                        st.error(f"{uploaded.name} is not a valid zip.")
                else:
                    files.append({"name": uploaded.name,
                                  "text": payload.decode("utf-8-sig", errors="replace"),
                                  "modified": 0, "size": len(payload)})
            report = import_files(files)
            st.success(f"Imported {report['added']} roast(s), "
                       f"skipped {report['skipped']} already here.")
            for problem in report["problems"][:5]:
                st.caption(problem)

        st.markdown("---")
        st.markdown("**Connect a whole folder** — Chrome and Edge only, and not a folder "
                    "inside your system Library. Once connected it syncs on every visit "
                    "without you picking anything.")
        folder = folder_picker(known=store.known_sources(), autosync=True, key="folder_picker")
        if isinstance(folder, dict):
            if folder.get("action") == "files" and folder.get("files"):
                report = import_files(folder["files"])
                st.success(f"Imported {report['added']} new roast(s) from {folder.get('folder')}")
                if folder.get("remaining"):
                    st.rerun()
            elif folder.get("action") == "error":
                st.error(folder.get("message"))

        st.markdown("---")
        st.markdown("**Try it without a roaster** — a simulated history of three coffees "
                    "dialled in over a few months.")
        if st.button("Load a demo roasting history"):
            with st.spinner("Simulating a few months of roasting…"):
                report = import_files(demo_data.as_files(demo_data.history()))
            st.success(f"Loaded {report['added']} simulated roasts across three coffees.")
            st.rerun()

    # ---- where it all lives ----------------------------------------------
    st.markdown("### Where this is stored")
    if db.is_shared():
        st.success(f"**{db.describe()}** — every computer signed in to this app sees the same "
                   "roasts, and they stay put when the app restarts.", icon=":material/cloud:")
    else:
        st.warning(f"**{db.describe()}** — this copy only. On Streamlit Community Cloud the "
                   "file is wiped whenever the app restarts, and other computers see nothing. "
                   "The README has the five-minute fix: a free Postgres database, one line in "
                   "secrets.", icon=":material/warning:")

    with st.expander("Manage stored roasts"):
        st.caption("Removing roasts here does not touch RoasTime — the app only ever reads "
                   "from it. It does remove them for everyone, though.")
        if st.checkbox("I want to delete everything"):
            if st.button("Delete all roasts, notes and advice"):
                store.clear()
                refresh()
                st.rerun()


# ---------------------------------------------------------------------------

st.logo(str(ASSETS / "logo-full.svg"), icon_image=str(ASSETS / "icon-64.png"))

# Nothing below this line renders for anyone who has not signed in.
user = auth.require(logo=_mark())
account_strip(user)

pages = [
    st.Page(page_coach, title="Coach", icon=":material/insights:"),
    st.Page(page_roasts, title="Roasts", icon=":material/local_fire_department:"),
    st.Page(page_coffees, title="Coffees", icon=":material/coffee:"),
    st.Page(page_learning, title="Learning", icon=":material/school:"),
    st.Page(page_data, title="Data", icon=":material/folder:"),
]

# The test suite opens one page at a time; the browser always gets all five.
only = os.environ.get("ROAST_COACH_PAGE")
if only:
    pages = [page for page in pages if page.title == only] or pages

st.navigation(pages).run()
