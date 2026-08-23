"""
Roast Coach's database.

Everything the app knows lives in one SQLite file: the roasts themselves, their
curves, whatever the roaster has added by hand, every recommendation the coach
has made, and what came of it.

Tables
------
``roasts``           one row per roast: identity, weights, milestones, phase
                     metrics, rate-of-rise figures, pattern flags. Rebuilt from
                     the source file on re-import.
``roast_curve``      one row per sample: seconds, both temperatures, both rates
                     of rise, power, fan, drum.
``roast_notes``      what the roaster typed: coffee, origin, process, weights,
                     tasting notes, score. Never overwritten by an import.
``recommendations``  what the coach suggested, what it predicted, and -- once a
                     later roast tests it -- what actually happened.
``effects``          how much a control change actually moves a measure *on this
                     roaster's machine*, learned from their own roasts.
``rule_stats``       how often each rule's prediction has come true.
``sources``          which file produced which roast, so a re-sync only reads
                     what changed.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .fields import create_roast
from .metrics import curve_frame, curve_metrics
from .origin import origin_from_name, roast_number_from_name

SCHEMA_VERSION = 2

CURVE_COLUMNS = ["roast_id", "seconds", "ibts_temp", "bean_temp",
                 "ibts_ror", "bean_ror", "power", "fan", "drum"]

NOTE_FIELDS = ["coffee", "origin", "process", "variety", "farm", "green_weight",
               "roast_level", "notes", "rating", "cupping_score", "is_reference"]

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS roast_curve (
           roast_id TEXT NOT NULL, seconds REAL NOT NULL,
           ibts_temp REAL, bean_temp REAL, ibts_ror REAL, bean_ror REAL,
           power REAL, fan REAL, drum REAL)""",
    "CREATE INDEX IF NOT EXISTS roast_curve_id ON roast_curve (roast_id)",
    """CREATE TABLE IF NOT EXISTS roast_notes (
           roast_id TEXT PRIMARY KEY,
           coffee TEXT, origin TEXT, process TEXT, variety TEXT, farm TEXT,
           green_weight REAL, roast_level TEXT, notes TEXT,
           rating REAL, cupping_score REAL, is_reference INTEGER DEFAULT 0,
           updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS recommendations (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           roast_id TEXT NOT NULL, coffee TEXT, rule_id TEXT NOT NULL,
           created_at TEXT NOT NULL,
           headline TEXT, finding TEXT, action TEXT, reason TEXT,
           target_metric TEXT, current_value REAL, predicted_value REAL,
           direction TEXT, confidence REAL, basis TEXT,
           status TEXT DEFAULT 'open',
           applied_roast_id TEXT, observed_value REAL, outcome TEXT,
           evaluated_at TEXT, note TEXT)""",
    "CREATE INDEX IF NOT EXISTS recommendation_roast ON recommendations (roast_id)",
    """CREATE TABLE IF NOT EXISTS effects (
           key TEXT PRIMARY KEY, control TEXT, phase TEXT, metric TEXT,
           slope REAL, observations INTEGER, spread REAL, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS rule_stats (
           rule_id TEXT PRIMARY KEY, suggested INTEGER DEFAULT 0,
           applied INTEGER DEFAULT 0, achieved INTEGER DEFAULT 0,
           partial INTEGER DEFAULT 0, missed INTEGER DEFAULT 0,
           mean_error REAL, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS sources (
           name TEXT PRIMARY KEY, roast_id TEXT, modified REAL, size INTEGER,
           imported_at TEXT)""",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
]


def database_path() -> str:
    """Where the database lives. ``ROAST_COACH_DB`` overrides it."""
    return os.environ.get("ROAST_COACH_DB") or str(
        Path(__file__).resolve().parent.parent / "roast_coach.db"
    )


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or database_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    for statement in SCHEMA:
        connection.execute(statement)
    connection.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema', ?)",
                       (str(SCHEMA_VERSION),))
    connection.commit()
    return connection


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _has(connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


# ---------------------------------------------------------------------------
# Identity: a roast is a date and a coffee
# ---------------------------------------------------------------------------

_NOISE = ("roast", "batch", "test", "profile")


def coffee_from_roast_name(name: str | None) -> str:
    """The coffee out of a RoasTime roast name.

    Roast names carry batch numbers, dates and repeat markers -- "#48 Eth
    Yirgacheffe 3rd" is the same coffee as "Eth Yirgacheffe". Strip the
    bookkeeping and keep the coffee.
    """
    import re

    text = str(name or "").strip()
    if not text:
        return ""
    text = re.sub(r"#\s*\d+", " ", text)
    text = re.sub(r"\b\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?\b", " ", text)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", text)
    text = re.sub(r"\b(\d+)(st|nd|rd|th)\b", " ", text, flags=re.I)
    text = re.sub(r"\b(v|rev|attempt|try)\s*\d+\b", " ", text, flags=re.I)
    text = re.sub(r"\s{2,}", " ", text).strip(" -_·,")
    if text.lower() in _NOISE or len(text) < 2:
        return origin_from_name(name) or text
    return text


def roast_label(row) -> str:
    """How a roast is named everywhere in the app: the date and the coffee."""
    date = str(row.get("date") or "")[:10] or "undated"
    coffee = row.get("coffee") or coffee_from_roast_name(row.get("roastName")) or "unnamed coffee"
    return f"{date} · {coffee}"


# ---------------------------------------------------------------------------
# Importing
# ---------------------------------------------------------------------------


def _curve_rows(roast_json: dict, roast_id: str) -> pd.DataFrame:
    frame = curve_frame(roast_json)
    if frame.empty:
        return pd.DataFrame(columns=CURVE_COLUMNS)
    rows = pd.DataFrame({
        "roast_id": roast_id,
        "seconds": frame["seconds"],
        "ibts_temp": frame["drumTemperature"],
        "bean_temp": frame["beanTemperature"],
        "ibts_ror": frame["ibtsDerivative"],
        "bean_ror": frame["beanDerivative"],
        "power": frame["Power"],
        "fan": frame["Fan"],
        "drum": frame["Drum"],
    })
    return rows[CURVE_COLUMNS]


def known_sources(path: str | None = None) -> dict:
    """{file name: (modified, size)} for everything already imported."""
    with closing(connect(path)) as connection:
        return {
            row[0]: (row[1], row[2])
            for row in connection.execute("SELECT name, modified, size FROM sources")
        }


def add_roasts(files: list[dict], path: str | None = None) -> dict:
    """Import roasts from ``[{name, text, modified, size}, …]``.

    Files are the app's only input: they come from a folder the browser granted
    access to, or from an upload. Nothing is read from the server's disk.
    """
    from .fields import parse_roast_text

    report = {"added": 0, "updated": 0, "skipped": 0, "failed": 0, "problems": []}
    rows, curves, seen, sources = [], [], [], []

    with closing(connect(path)) as connection:
        known = {r[0]: (r[1], r[2]) for r in connection.execute(
            "SELECT name, modified, size FROM sources")}
        existing_ids = set()
        if _has(connection, "roasts"):
            existing_ids = {r[0] for r in connection.execute("SELECT uid FROM roasts")}

        for item in files:
            name = str(item.get("name") or "roast")
            modified = float(item.get("modified") or 0)
            size = int(item.get("size") or 0)

            previous = known.get(name)
            if previous and previous[0] == modified and previous[1] == size:
                report["skipped"] += 1
                continue

            try:
                roast_json = parse_roast_text(item["text"], name)
            except Exception as exc:
                report["failed"] += 1
                report["problems"].append(str(exc))
                continue

            if not isinstance(roast_json, dict) or "uid" not in roast_json:
                report["failed"] += 1
                report["problems"].append(f"{name}: no roast data in this file")
                continue

            roast_id = str(roast_json["uid"])
            row = create_roast(roast_json)
            row.update(curve_metrics(roast_json))
            row["uid"] = roast_id
            row["coffee_guess"] = coffee_from_roast_name(roast_json.get("roastName"))
            row["origin_guess"] = origin_from_name(roast_json.get("roastName"))
            row["batch_number"] = roast_number_from_name(roast_json.get("roastName"))
            row["source_name"] = name
            row["imported_at"] = _now()
            rows.append(row)

            curve = _curve_rows(roast_json, roast_id)
            if not curve.empty:
                curves.append(curve)

            seen.append(roast_id)
            sources.append((name, roast_id, modified, size, row["imported_at"]))
            report["updated" if roast_id in existing_ids else "added"] += 1

        if not rows:
            return report

        existing = pd.read_sql_query("SELECT * FROM roasts", connection) if _has(connection, "roasts") else pd.DataFrame()
        if not existing.empty and seen:
            existing = existing[~existing["uid"].isin(seen)]

        incoming = pd.DataFrame(rows)
        combined = pd.concat([existing, incoming], ignore_index=True) if not existing.empty else incoming
        combined = combined.loc[:, ~combined.columns.duplicated()]
        for column in combined.columns:
            if combined[column].dtype == "object":
                combined[column] = combined[column].map(
                    lambda v: json.dumps(v) if isinstance(v, (list, dict)) else v)

        combined.to_sql("roasts", connection, if_exists="replace", index=False)
        connection.execute("CREATE INDEX IF NOT EXISTS roasts_uid ON roasts (uid)")

        if seen:
            marks = ",".join("?" for _ in seen)
            connection.execute(f"DELETE FROM roast_curve WHERE roast_id IN ({marks})", seen)
        if curves:
            pd.concat(curves, ignore_index=True).to_sql(
                "roast_curve", connection, if_exists="append", index=False)

        connection.executemany(
            "INSERT OR REPLACE INTO sources (name, roast_id, modified, size, imported_at)"
            " VALUES (?, ?, ?, ?, ?)", sources)
        connection.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_import', ?)",
                           (_now(),))
        connection.commit()

    return report


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def summary(path: str | None = None) -> dict:
    try:
        with closing(connect(path)) as connection:
            if not _has(connection, "roasts"):
                return {"roasts": 0, "coffees": 0, "samples": 0, "first": None,
                        "last": None, "imported_at": None, "open_recommendations": 0}
            roasts = connection.execute("SELECT COUNT(*) FROM roasts").fetchone()[0]
            samples = connection.execute("SELECT COUNT(*) FROM roast_curve").fetchone()[0]
            span = connection.execute("SELECT MIN(date), MAX(date) FROM roasts").fetchone()
            imported = connection.execute("SELECT value FROM meta WHERE key='last_import'").fetchone()
            open_count = connection.execute(
                "SELECT COUNT(*) FROM recommendations WHERE status='open'").fetchone()[0]
    except sqlite3.Error:
        return {"roasts": 0, "coffees": 0, "samples": 0, "first": None,
                "last": None, "imported_at": None, "open_recommendations": 0}

    frame = load_roasts(path)
    return {
        "roasts": roasts, "samples": samples,
        "coffees": int(frame["coffee"].nunique()) if not frame.empty else 0,
        "first": span[0], "last": span[1],
        "imported_at": imported[0] if imported else None,
        "open_recommendations": open_count,
    }


def load_roasts(path: str | None = None) -> pd.DataFrame:
    """Every roast, with the roaster's own entries merged in and a coffee resolved."""
    with closing(connect(path)) as connection:
        if not _has(connection, "roasts"):
            return pd.DataFrame()
        roasts = pd.read_sql_query("SELECT * FROM roasts", connection)
        notes = pd.read_sql_query("SELECT * FROM roast_notes", connection)

    if roasts.empty:
        return roasts

    if not notes.empty:
        notes = notes.rename(columns={"roast_id": "uid"})
        overlap = [c for c in notes.columns if c in roasts.columns and c != "uid"]
        roasts = roasts.drop(columns=overlap, errors="ignore").merge(notes, on="uid", how="left")
    for column in NOTE_FIELDS:
        if column not in roasts:
            roasts[column] = np.nan

    numeric = ["dateTime", "ambient", "humidity", "weightGreen", "weightRoasted",
               "green_weight", "rating", "cupping_score"]
    for column in numeric:
        if column in roasts:
            roasts[column] = pd.to_numeric(roasts[column], errors="coerce")

    roasts["roasted_at"] = pd.to_datetime(roasts["dateTime"], unit="ms", errors="coerce")
    roasts = roasts.sort_values("roasted_at", na_position="first").reset_index(drop=True)

    guess = roasts.get("coffee_guess", pd.Series("", index=roasts.index))
    roasts["coffee"] = (
        roasts["coffee"].astype("object").where(roasts["coffee"].notna() & (roasts["coffee"] != ""))
        .fillna(guess).replace("", np.nan)
        .fillna("Unnamed coffee")
    )
    if "origin" in roasts:
        roasts["origin"] = roasts["origin"].fillna(roasts.get("origin_guess"))
    roasts["is_reference"] = roasts.get("is_reference", 0).fillna(0).astype(int)
    roasts["label"] = [roast_label(row) for _, row in roasts.iterrows()]

    # The weights the roaster typed win over whatever RoasTime recorded.
    green = roasts["green_weight"].where(roasts["green_weight"] > 0)
    roasts["greenWeight"] = green.fillna(roasts.get("weightGreen"))
    with np.errstate(invalid="ignore"):
        roasts["weightLossPercent"] = np.where(
            (roasts["greenWeight"] > 0) & (roasts.get("weightRoasted", pd.Series()).fillna(0) > 0),
            (1 - roasts["weightRoasted"] / roasts["greenWeight"]) * 100, np.nan)

    return roasts


def load_curve(roast_id: str, path: str | None = None) -> pd.DataFrame:
    with closing(connect(path)) as connection:
        return pd.read_sql_query(
            "SELECT * FROM roast_curve WHERE roast_id = ? ORDER BY seconds",
            connection, params=(roast_id,))


def roast_dict(roast_id: str, path: str | None = None) -> dict:
    """Rebuild a roast from the database, in the shape the charts want."""
    curve = load_curve(roast_id, path)
    if curve.empty:
        return {}

    with closing(connect(path)) as connection:
        frame = pd.read_sql_query("SELECT * FROM roasts WHERE uid = ? LIMIT 1",
                                  connection, params=(roast_id,))
    row = frame.iloc[0] if not frame.empty else None

    seconds = curve["seconds"].to_numpy(dtype="float64")
    steps = np.diff(seconds)
    interval = float(np.median(steps)) if len(steps) else 1.0
    roast = {
        "uid": roast_id,
        "sampleRate": (1.0 / interval) if interval else 1.0,
        "beanTemperature": curve["bean_temp"].tolist(),
        "drumTemperature": curve["ibts_temp"].tolist(),
        "beanDerivative": curve["bean_ror"].tolist(),
        "drumDerivative": curve["ibts_ror"].tolist(),
        "roastStartIndex": 0,
        "roastEndIndex": len(curve) - 1,
    }
    actions = []
    for control, code in (("power", 0), ("fan", 1), ("drum", 2)):
        previous = None
        for position, value in enumerate(curve[control]):
            if pd.isna(value) or value == previous:
                continue
            actions.append({"ctrlType": code, "index": position, "value": float(value)})
            previous = value
    roast["actions"] = {"actionTimeList": sorted(actions, key=lambda a: (a["index"], a["ctrlType"]))}

    if row is not None:
        for field in ("roastName", "dateTime", "totalRoastTime", "indexYellowingStart",
                      "indexFirstCrackStart", "indexFirstCrackEnd", "indexSecondCrackStart",
                      "indexSecondCrackEnd", "roastEndIndex", "weightGreen", "weightRoasted",
                      "ambient", "humidity", "drumChargeTemperature", "drumDropTemperature",
                      "beanChargeTemperature", "beanDropTemperature"):
            value = row.get(field)
            if value is not None and not (isinstance(value, float) and np.isnan(value)):
                roast[field] = value
    return roast


# ---------------------------------------------------------------------------
# What the roaster adds
# ---------------------------------------------------------------------------


def save_notes(roast_id: str, values: dict, path: str | None = None) -> None:
    fields = {k: v for k, v in values.items() if k in NOTE_FIELDS}
    fields["updated_at"] = _now()
    columns = ", ".join(["roast_id"] + list(fields))
    marks = ", ".join("?" for _ in range(len(fields) + 1))
    with closing(connect(path)) as connection:
        connection.execute(
            f"INSERT INTO roast_notes ({columns}) VALUES ({marks}) "
            f"ON CONFLICT(roast_id) DO UPDATE SET "
            + ", ".join(f"{k}=excluded.{k}" for k in list(fields)),
            [roast_id] + list(fields.values()))
        connection.commit()


def set_reference(roast_id: str, coffee: str, path: str | None = None) -> None:
    """Mark one roast as the benchmark for its coffee; clear any previous one."""
    roasts = load_roasts(path)
    same = roasts[roasts["coffee"] == coffee]["uid"].tolist()
    with closing(connect(path)) as connection:
        for other in same:
            connection.execute(
                "INSERT INTO roast_notes (roast_id, is_reference, updated_at) VALUES (?, 0, ?) "
                "ON CONFLICT(roast_id) DO UPDATE SET is_reference=0, updated_at=excluded.updated_at",
                (other, _now()))
        connection.execute(
            "INSERT INTO roast_notes (roast_id, is_reference, updated_at) VALUES (?, 1, ?) "
            "ON CONFLICT(roast_id) DO UPDATE SET is_reference=1, updated_at=excluded.updated_at",
            (roast_id, _now()))
        connection.commit()


def forget(roast_ids, path: str | None = None) -> int:
    ids = [str(i) for i in roast_ids]
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    with closing(connect(path)) as connection:
        for table, column in (("roasts", "uid"), ("roast_curve", "roast_id"),
                              ("roast_notes", "roast_id"), ("sources", "roast_id"),
                              ("recommendations", "roast_id")):
            if _has(connection, table):
                connection.execute(f"DELETE FROM {table} WHERE {column} IN ({marks})", ids)
        connection.commit()
    return len(ids)


def clear(path: str | None = None) -> None:
    with closing(connect(path)) as connection:
        for table in ("roasts", "roast_curve", "roast_notes", "recommendations",
                      "effects", "rule_stats", "sources"):
            if _has(connection, table):
                connection.execute(f"DELETE FROM {table}")
        connection.execute("DELETE FROM meta WHERE key='last_import'")
        connection.commit()


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


def save_recommendations(roast_id: str, coffee: str, items: list[dict],
                         path: str | None = None) -> int:
    """Replace the open recommendations for a roast with a fresh set.

    Anything already applied or dismissed is left alone -- the record of what was
    suggested and what happened is the point.
    """
    with closing(connect(path)) as connection:
        connection.execute(
            "DELETE FROM recommendations WHERE roast_id = ? AND status = 'open'", (roast_id,))
        for item in items:
            connection.execute(
                """INSERT INTO recommendations
                   (roast_id, coffee, rule_id, created_at, headline, finding, action, reason,
                    target_metric, current_value, predicted_value, direction, confidence, basis)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (roast_id, coffee, item["rule_id"], _now(), item["headline"], item["finding"],
                 item["action"], item.get("reason"), item.get("target_metric"),
                 item.get("current_value"), item.get("predicted_value"),
                 item.get("direction"), item.get("confidence"), item.get("basis")))
            connection.execute(
                """INSERT INTO rule_stats (rule_id, suggested, updated_at) VALUES (?, 1, ?)
                   ON CONFLICT(rule_id) DO UPDATE SET suggested = suggested + 1,
                   updated_at = excluded.updated_at""", (item["rule_id"], _now()))
        connection.commit()
    return len(items)


def recommendations(roast_id: str | None = None, status: str | None = None,
                    path: str | None = None) -> pd.DataFrame:
    query = "SELECT * FROM recommendations"
    clauses, params = [], []
    if roast_id:
        clauses.append("roast_id = ?")
        params.append(roast_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, id DESC"
    with closing(connect(path)) as connection:
        return pd.read_sql_query(query, connection, params=params)


def update_recommendation(recommendation_id: int, path: str | None = None, **values) -> None:
    allowed = {"status", "applied_roast_id", "observed_value", "outcome", "evaluated_at", "note"}
    fields = {k: v for k, v in values.items() if k in allowed}
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    with closing(connect(path)) as connection:
        connection.execute(f"UPDATE recommendations SET {assignments} WHERE id = ?",
                           list(fields.values()) + [recommendation_id])
        connection.commit()


def record_outcome(recommendation_id: int, applied_roast_id: str, observed: float,
                   outcome: str, path: str | None = None) -> None:
    with closing(connect(path)) as connection:
        row = connection.execute(
            "SELECT rule_id, predicted_value, current_value FROM recommendations WHERE id = ?",
            (recommendation_id,)).fetchone()
        connection.execute(
            """UPDATE recommendations SET status='evaluated', applied_roast_id=?,
               observed_value=?, outcome=?, evaluated_at=? WHERE id=?""",
            (applied_roast_id, observed, outcome, _now(), recommendation_id))

        if row:
            rule_id, predicted, current = row
            error = None
            if predicted is not None and observed is not None:
                error = abs(float(observed) - float(predicted))
            column = {"achieved": "achieved", "partial": "partial"}.get(outcome, "missed")
            connection.execute(
                f"""INSERT INTO rule_stats (rule_id, applied, {column}, mean_error, updated_at)
                    VALUES (?, 1, 1, ?, ?)
                    ON CONFLICT(rule_id) DO UPDATE SET
                      applied = applied + 1,
                      {column} = {column} + 1,
                      mean_error = CASE WHEN ? IS NULL THEN mean_error
                                        WHEN mean_error IS NULL THEN ?
                                        ELSE (mean_error * applied + ?) / (applied + 1) END,
                      updated_at = excluded.updated_at""",
                (rule_id, error, _now(), error, error, error))
        connection.commit()


def rule_scoreboard(path: str | None = None) -> pd.DataFrame:
    with closing(connect(path)) as connection:
        frame = pd.read_sql_query("SELECT * FROM rule_stats ORDER BY suggested DESC", connection)
    if frame.empty:
        return frame
    tested = frame[["achieved", "partial", "missed"]].sum(axis=1).replace(0, np.nan)
    frame["hit_rate"] = (frame["achieved"] + 0.5 * frame["partial"]) / tested
    return frame


# ---------------------------------------------------------------------------
# Learned effects
# ---------------------------------------------------------------------------


def save_effect(key: str, control: str, phase: str, metric: str,
                slope: float, observations: int, spread: float, path: str | None = None) -> None:
    with closing(connect(path)) as connection:
        connection.execute(
            """INSERT INTO effects (key, control, phase, metric, slope, observations, spread, updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET slope=excluded.slope,
                 observations=excluded.observations, spread=excluded.spread,
                 updated_at=excluded.updated_at""",
            (key, control, phase, metric, slope, observations, spread, _now()))
        connection.commit()


def effects(path: str | None = None) -> pd.DataFrame:
    with closing(connect(path)) as connection:
        return pd.read_sql_query("SELECT * FROM effects ORDER BY observations DESC", connection)


def effect(key: str, path: str | None = None) -> dict | None:
    with closing(connect(path)) as connection:
        row = connection.execute(
            "SELECT key, control, phase, metric, slope, observations, spread FROM effects WHERE key=?",
            (key,)).fetchone()
    if not row:
        return None
    return dict(zip(("key", "control", "phase", "metric", "slope", "observations", "spread"), row))
