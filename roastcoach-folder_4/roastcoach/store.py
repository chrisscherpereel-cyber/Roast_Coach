"""
Roast Coach's database.

Everything the app knows lives here: the roasts themselves, their curves,
whatever the roaster has added by hand, every recommendation the coach has made,
and what came of it. :mod:`roastcoach.db` decides *where* here is — a Postgres
server every computer can reach, or a SQLite file for one.

Tables
------
``roasts``           one row per roast. The identity and the date are columns;
                     everything RoasTime records is JSON in ``data``, so a file
                     with new fields never changes the table's shape.
``roast_curve``      one row per roast, holding the whole sampled curve —
                     seconds, both temperatures, both rates of rise, power, fan
                     and drum — compressed. :func:`load_curve` hands it back as
                     the per-sample table the rest of the app works with.
``roast_notes``      what the roaster typed: coffee, origin, process, weights,
                     tasting notes, score. Never overwritten by an import.
``recommendations``  what the coach suggested, what it predicted, and — once a
                     later roast tests it — what actually happened.
``effects``          how much a control change actually moves a measure *on this
                     roaster's machine*, learned from their own roasts.
``rule_stats``       how often each rule's prediction has come true.
``sources``          which file produced which roast, by name and by content, so
                     importing the same folder twice reads nothing twice.
"""

from __future__ import annotations

import base64
import hashlib
import json
import zlib
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import db, library
from .fields import create_roast
from .metrics import METRICS_VERSION, curve_frame, curve_metrics
from .origin import origin_from_name, roast_number_from_name

CURVE_COLUMNS = ["roast_id", "seconds", "ibts_temp", "bean_temp",
                 "ibts_ror", "bean_ror", "power", "fan", "drum"]

SERIES_COLUMNS = [column for column in CURVE_COLUMNS if column != "roast_id"]

NOTE_FIELDS = ["coffee", "origin", "process", "variety", "farm", "green_weight",
               "roast_level", "notes", "rating", "cupping_score", "is_reference",
               # What no probe can see: measured colour, how evenly the batch
               # roasted, quakers picked out, and what the beans looked like.
               "colour_whole", "colour_ground", "colour_sd", "quaker_count",
               "visual_defects", "roasted_weight",
               # Read off whichever meter the roaster owns. Kept as four separate
               # numbers because the scales are not interchangeable and this app
               # will not invent a conversion between them.
               "agtron_commercial", "agtron_gourmet", "probat_colorette", "colortrack"]

# The colour scales, in the order the entry screen shows them.
COLOUR_SCALES = (
    ("agtron_commercial", "Agtron Commercial", "whole bean", (25, 95)),
    ("agtron_gourmet", "Agtron Gourmet (GRmt)", "ground", (25, 95)),
    ("probat_colorette", "Probat Colorette", "ground", (30, 160)),
    ("colortrack", "Color Track", "whole or ground", (0, 160)),
)

TABLES = ("roasts", "roast_curve", "roast_notes", "recommendations",
          "effects", "rule_stats", "sources", "reference", "sensory")

# What this file can do, for a deploy that updated some files and not others.
# app.py says which version it needs and names anything older on screen instead
# of failing three pages in. Raise it whenever app.py starts calling something
# new here.
#   1  the original      2  fingerprint(), summary(frame=)
#   3  outdated() / remeasure(), the bean as the coffee's identity
#   4  colour and defect fields, and the cupping verdicts in `sensory`
#   5  the four colour scales, and advice stored as control moves
#   6  roast_name, bean and recipe_name kept apart as three columns
VERSION = 6


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _who() -> str:
    """Whoever is signed in, when anyone is."""
    try:
        from . import auth

        return auth.current_user() or ""
    except Exception:
        return ""


def database_path(path: str | None = None) -> str:
    """Kept for the tests and the README; the real answer is in :mod:`db`."""
    return db.database_url(path)


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
# Turning a roast into rows, and back
# ---------------------------------------------------------------------------


def _plain(value):
    """numpy and pandas values, as something json can write."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return None if np.isnan(number) else number
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if value is pd.NaT or (isinstance(value, float) and pd.isna(value)):
        return None
    return value


def _encode_curve(frame: pd.DataFrame) -> tuple[int, str]:
    """The whole curve as one compressed string.

    Nine float columns at two samples a second is a few thousand numbers; as
    rows over a network it is a few thousand round trips' worth of work. As one
    compressed block it is about twenty kilobytes.
    """
    if frame.empty:
        return 0, ""
    stack = np.column_stack([
        pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype="float32")
        for column in SERIES_COLUMNS
    ])
    header = json.dumps({"columns": SERIES_COLUMNS, "rows": int(stack.shape[0])}).encode()
    body = zlib.compress(stack.tobytes(), 6)
    packed = len(header).to_bytes(4, "big") + header + body
    return int(stack.shape[0]), base64.b64encode(packed).decode()


def _decode_curve(payload: str, roast_id: str) -> pd.DataFrame:
    if not payload:
        return pd.DataFrame(columns=CURVE_COLUMNS)
    packed = base64.b64decode(payload)
    size = int.from_bytes(packed[:4], "big")
    header = json.loads(packed[4:4 + size].decode())
    stack = np.frombuffer(zlib.decompress(packed[4 + size:]), dtype="float32")
    stack = stack.reshape(header["rows"], len(header["columns"]))
    frame = pd.DataFrame(stack, columns=header["columns"]).astype("float64")
    frame.insert(0, "roast_id", roast_id)
    return frame


def _curve_rows(roast_json: dict) -> pd.DataFrame:
    frame = curve_frame(roast_json)
    if frame.empty:
        return pd.DataFrame(columns=SERIES_COLUMNS)
    return pd.DataFrame({
        "seconds": frame["seconds"],
        "ibts_temp": frame["drumTemperature"],
        "bean_temp": frame["beanTemperature"],
        "ibts_ror": frame["ibtsDerivative"],
        "bean_ror": frame["beanDerivative"],
        "power": frame["Power"],
        "fan": frame["Fan"],
        "drum": frame["Drum"],
    })[SERIES_COLUMNS]


# ---------------------------------------------------------------------------
# Importing
# ---------------------------------------------------------------------------


def known_sources(path: str | None = None) -> dict:
    """``{file name: (modified, size)}`` for everything already imported.

    The uploader is handed this so it can leave files it has already read on
    the disk, unopened.
    """
    return {row[0]: (row[1], row[2])
            for row in db.rows("SELECT name, modified, size FROM sources", override=path)}


def note_sync(folder: str, looked: int, path: str | None = None) -> None:
    """Remember that the folder was checked, and when — shown on the Data page."""
    db.upsert("meta", "key", {"key": "last_sync", "value": _now()}, override=path)
    db.upsert("meta", "key", {"key": "last_folder", "value": str(folder or "")}, override=path)
    db.upsert("meta", "key", {"key": "last_looked", "value": str(int(looked or 0))},
              override=path)


def sync_state(path: str | None = None) -> dict:
    """When the watched folder was last checked, and what it is called."""
    rows = db.rows("SELECT key, value FROM meta WHERE key IN "
                   "('last_sync', 'last_folder', 'last_looked', 'last_import')", override=path)
    found = {key: value for key, value in rows}
    return {"checked_at": found.get("last_sync"), "folder": found.get("last_folder"),
            "looked": int(found.get("last_looked") or 0),
            "imported_at": found.get("last_import")}


def known_hashes(path: str | None = None) -> set:
    return {row[0] for row in db.rows(
        "SELECT content_hash FROM sources WHERE content_hash IS NOT NULL", override=path)}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def add_roasts(files: list[dict], path: str | None = None) -> dict:
    """Import roasts from ``[{name, text, modified, size}, …]``.

    Files are the app's only input: they come from an upload or a folder the
    browser granted access to. Nothing is read from the server's disk.

    A file is skipped when its name, size and timestamp match one already
    imported, and again when its contents hash to something already stored —
    so a copied or renamed file does not become a second roast.
    """
    from .fields import parse_roast_text

    report = {"added": 0, "updated": 0, "skipped": 0, "failed": 0, "problems": []}
    if not files:
        return report

    db.prepare(path)
    known = known_sources(path)
    hashes = known_hashes(path)
    existing = {row[0] for row in db.rows("SELECT uid FROM roasts", override=path)}
    who, stamp = _who(), _now()

    for item in files:
        name = str(item.get("name") or "roast")
        modified = float(item.get("modified") or 0)
        size = int(item.get("size") or 0)

        previous = known.get(name)
        if previous and previous[0] == modified and previous[1] == size:
            report["skipped"] += 1
            continue

        text = item.get("text") or ""
        digest = _hash(text)
        if digest in hashes:          # same contents under some name — nothing new
            report["skipped"] += 1
            continue

        try:
            roast_json = parse_roast_text(text, name)
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
        row["imported_at"] = stamp
        row["metrics_version"] = METRICS_VERSION
        # The ids that point at the bean, the recipe and the machine are not
        # roast measurements, so create_roast() does not carry them. Keep them.
        for key in ("beanId", "beanGuid", "recipeId", "recipeGuid", "officialRecipeId",
                    "containerId", "machineId", "userProfileId", "userId",
                    "roastNumber", "energy", "energyUsed", "totalEnergy",
                    "preheatTemperature", "recipeName", "beanName"):
            if key in roast_json and key not in row:
                row[key] = _plain(roast_json[key])

        db.upsert("roasts", "uid", {
            "uid": roast_id,
            "date": str(row.get("date") or "")[:19] or None,
            "date_time": _plain(row.get("dateTime")),
            "coffee_guess": row["coffee_guess"],
            "source_name": name,
            "content_hash": digest,
            "imported_at": stamp,
            "imported_by": who,
            "data": json.dumps({k: _plain(v) for k, v in row.items()}),
        }, override=path)

        samples, payload = _encode_curve(_curve_rows(roast_json))
        db.upsert("roast_curve", "roast_id",
                  {"roast_id": roast_id, "samples": samples, "payload": payload},
                  override=path)

        db.upsert("sources", "name", {
            "name": name, "roast_id": roast_id, "modified": modified, "size": size,
            "content_hash": digest, "imported_at": stamp, "imported_by": who,
        }, override=path)

        hashes.add(digest)
        report["updated" if roast_id in existing else "added"] += 1
        existing.add(roast_id)

    if report["added"] or report["updated"]:
        db.upsert("meta", "key", {"key": "last_import", "value": stamp}, override=path)
    return report


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def fingerprint(path: str | None = None) -> tuple:
    """A cheap signature of the stored data, for deciding whether a cache is stale.

    Roasts do not only arrive through the app. `mac/sync_to_database.py` writes
    straight into the same database, and so does anybody else signed in on
    another computer. A page that holds a table it read once would then keep
    showing yesterday's roasts — or, from a cold start, none at all — while the
    Data page, which counts rows every time, cheerfully reports five hundred.

    One query answers "has anything changed", so every rerun can ask.
    """
    try:
        row = db.one(
            "SELECT (SELECT COUNT(*) FROM roasts),"
            "       (SELECT MAX(imported_at) FROM roasts),"
            "       (SELECT COUNT(*) FROM roast_notes),"
            "       (SELECT MAX(updated_at) FROM roast_notes),"
            "       (SELECT COUNT(*) FROM reference),"
            "       (SELECT MAX(updated_at) FROM reference)",
            override=path)
    except Exception:
        return ()
    return tuple("" if value is None else str(value) for value in (row or ()))


def summary(path: str | None = None, frame: pd.DataFrame | None = None) -> dict:
    """The counts for the Data page.

    ``frame`` is the already-loaded roast table, if the caller has one. Counting
    distinct coffees needs the resolved names, and reading five hundred roasts a
    second time to get them is work nobody asked for.
    """
    empty = {"roasts": 0, "coffees": 0, "samples": 0, "first": None, "last": None,
             "imported_at": None, "open_recommendations": 0}
    try:
        roasts = db.one("SELECT COUNT(*) FROM roasts", override=path)[0]
        samples = db.one("SELECT COALESCE(SUM(samples), 0) FROM roast_curve", override=path)[0]
        span = db.one("SELECT MIN(date), MAX(date) FROM roasts", override=path)
        imported = db.one("SELECT value FROM meta WHERE key = 'last_import'", override=path)
        open_count = db.one("SELECT COUNT(*) FROM recommendations WHERE status = 'open'",
                            override=path)[0]
    except Exception:
        return empty

    if not roasts:
        return empty

    if frame is None:
        frame = load_roasts(path)
    return {
        "roasts": int(roasts), "samples": int(samples or 0),
        "coffees": int(frame["coffee"].nunique()) if not frame.empty else 0,
        "first": span[0], "last": span[1],
        "imported_at": imported[0] if imported else None,
        "open_recommendations": int(open_count),
    }


def load_roasts(path: str | None = None) -> pd.DataFrame:
    """Every roast, with the roaster's own entries merged in and a coffee resolved."""
    records = db.rows("SELECT uid, data FROM roasts", override=path)
    if not records:
        return pd.DataFrame()

    roasts = pd.DataFrame([{**json.loads(data), "uid": uid} for uid, data in records])
    notes = db.frame("SELECT * FROM roast_notes", override=path)

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

    typed = roasts["coffee"].astype("object").where(
        roasts["coffee"].notna() & (roasts["coffee"] != ""))
    roasts["is_reference"] = roasts.get("is_reference", 0).fillna(0).astype(int)

    # Whatever the bean, recipe and machine files add. Only columns that were
    # actually found appear, so nothing changes for a folder of roasts alone.
    rows = roasts.to_dict("records")
    joined = getattr(library, "enrich_many", None)     # an older library.py still works
    extra = joined(rows, path) if joined else [library.enrich(row, path) for row in rows]
    for column in sorted({key for found in extra for key in found}):
        values = [found.get(column) for found in extra]
        if column in roasts.columns:
            roasts[column] = roasts[column].fillna(pd.Series(values, index=roasts.index))
        else:
            roasts[column] = values

    # What the coffee is called, in order of how much it can be trusted: the bean
    # RoasTime says was in the drum, then what the roaster typed, then a guess
    # scraped out of the roast name.
    #
    # The bean comes first on purpose. Roasts are compared bean against bean —
    # trends, consistency, effect sizes, the coach's "next roast of this coffee"
    # all group on this name — and a roast title is a label somebody typed that
    # afternoon, not an identity. Two roasts of one bean called "CR 800 v4" and
    # "Costa Rica test" are the same coffee and have to land in the same group.
    # A typed name still wins wherever no bean file matched.
    #
    # The country is guessed from the roast name only when nothing better exists:
    # a bean file that says Costa Rica must beat "Ethiopia" read out of a title.
    if "origin" in roasts:
        roasts["origin"] = roasts["origin"].fillna(roasts.get("origin_guess"))

    guess = roasts.get("coffee_guess", pd.Series("", index=roasts.index))
    named = roasts.get("bean_name", pd.Series(np.nan, index=roasts.index)).replace("", np.nan)
    bean_id = roasts.get("bean_id", pd.Series(np.nan, index=roasts.index))

    # Typing a coffee name on a roast that RoasTime linked to a bean renames that
    # bean everywhere, rather than splitting one roast off from its own history.
    # It is the only reading that keeps both promises: the roaster's own words
    # win, and roasts of one bean stay together.
    if bean_id.notna().any():
        renames = (pd.DataFrame({"bean_id": bean_id, "typed": typed,
                                 "at": roasts["roasted_at"]})
                   .dropna(subset=["bean_id", "typed"])
                   .sort_values("at")
                   .groupby("bean_id")["typed"].last())
        named = bean_id.map(renames).fillna(named)

    roasts["coffee"] = (named
                        .fillna(typed)
                        .fillna(guess).replace("", np.nan)
                        .fillna("Unnamed coffee"))
    # Where the name came from, so the app can say why two roasts are together.
    roasts["coffee_source"] = np.where(
        bean_id.notna(), "bean file",
        np.where(typed.notna(), "typed", "roast name"))

    # Three different things, kept apart on purpose:
    #
    #   roast_name   what RoasTime called this roast — what you typed at the
    #                machine that afternoon, batch number and all
    #   bean         the bean file RoasTime linked to it, which is the identity
    #                roasts are compared under
    #   recipe_name  the profile it was roasted from
    #
    # They were being collapsed into one "coffee", which meant a table could not
    # tell you that two roasts of one bean ran from different recipes.
    roasts["roast_name"] = (roasts.get("roastName", pd.Series("", index=roasts.index))
                            .fillna("").astype(str).str.strip())
    roasts["bean"] = named.fillna("")
    if "recipe_name" not in roasts:
        roasts["recipe_name"] = ""
    roasts["recipe_name"] = roasts["recipe_name"].fillna("")

    roasts["label"] = [roast_label(row) for _, row in roasts.iterrows()]

    # The weights the roaster typed win over whatever RoasTime recorded — the
    # out weight especially, since it is only known once the roast is cool.
    green = roasts["green_weight"].where(roasts["green_weight"] > 0)
    roasts["greenWeight"] = green.fillna(roasts.get("weightGreen"))
    if "roasted_weight" in roasts:
        typed_out = pd.to_numeric(roasts["roasted_weight"], errors="coerce")
        roasts["weightRoasted"] = typed_out.where(typed_out > 0).fillna(
            roasts.get("weightRoasted"))
    with np.errstate(invalid="ignore"):
        roasts["weightLossPercent"] = np.where(
            (roasts["greenWeight"] > 0) & (roasts.get("weightRoasted", pd.Series()).fillna(0) > 0),
            (1 - roasts["weightRoasted"] / roasts["greenWeight"]) * 100, np.nan)

    return roasts


# ---------------------------------------------------------------------------
# Bringing stored roasts up to date
# ---------------------------------------------------------------------------


_VERSION_MARKER = f'%"metrics_version": {METRICS_VERSION}%'


def outdated(path: str | None = None) -> int:
    """How many stored roasts were measured by an older version of the metrics.

    A roast's numbers are worked out once, at import, and kept with it — which is
    what makes the pages quick. The cost is that correcting a calculation does
    not reach roasts already in the database. This counts the ones left behind so
    the app can offer to redo them.
    """
    try:
        row = db.one("SELECT COUNT(*) FROM roasts WHERE data NOT LIKE :marker",
                     {"marker": _VERSION_MARKER}, override=path)
    except Exception:
        return 0
    return int(row[0]) if row else 0


def remeasure(path: str | None = None, limit: int | None = None, progress=None) -> int:
    """Recompute the metrics of roasts measured by an older version.

    The curve is already stored, so nothing needs re-importing and nothing is
    read from anybody's disk: the roast is rebuilt from its own samples, measured
    again, and written back. What the roaster typed is untouched.
    """
    statement = ("SELECT uid FROM roasts WHERE data NOT LIKE :marker ORDER BY date")
    ids = [row[0] for row in db.rows(statement, {"marker": _VERSION_MARKER}, override=path)]
    if limit:
        ids = ids[:limit]

    done = 0
    for position, roast_id in enumerate(ids):
        stored = db.one("SELECT data FROM roasts WHERE uid = :id", {"id": roast_id},
                        override=path)
        if not stored:
            continue
        row = json.loads(stored[0])
        rebuilt = roast_dict(roast_id, path)
        if rebuilt:
            row.update(curve_metrics(rebuilt))
        row["metrics_version"] = METRICS_VERSION
        db.run("UPDATE roasts SET data = :data WHERE uid = :id",
               {"data": json.dumps({k: _plain(v) for k, v in row.items()}), "id": roast_id},
               override=path)
        done += 1
        if progress:
            progress(position + 1, len(ids))
    return done


def load_curve(roast_id: str, path: str | None = None) -> pd.DataFrame:
    row = db.one("SELECT payload FROM roast_curve WHERE roast_id = :id",
                 {"id": roast_id}, override=path)
    if not row:
        return pd.DataFrame(columns=CURVE_COLUMNS)
    return _decode_curve(row[0], roast_id)


def roast_dict(roast_id: str, path: str | None = None) -> dict:
    """Rebuild a roast from the database, in the shape the charts want."""
    curve = load_curve(roast_id, path)
    if curve.empty:
        return {}

    stored = db.one("SELECT data FROM roasts WHERE uid = :id", {"id": roast_id}, override=path)
    row = json.loads(stored[0]) if stored else {}

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
    fields = {k: _plain(v) for k, v in values.items() if k in NOTE_FIELDS}
    fields["roast_id"] = roast_id
    fields["updated_at"] = _now()
    fields["updated_by"] = _who()
    db.upsert("roast_notes", "roast_id", fields, override=path)


# ---------------------------------------------------------------------------
# Cupping: where a risk stops being a hypothesis
# ---------------------------------------------------------------------------

VERDICTS = ("confirmed", "not present", "unsure")


def save_sensory(roast_id: str, condition_id: str, verdict: str, note: str = "",
                 path: str | None = None) -> None:
    """What the cupping table said about one risk the app raised.

    The app is allowed to say a pattern is *associated* with baked character. Only
    this table is allowed to say the coffee tasted baked.
    """
    db.upsert("sensory", "key", {
        "key": f"{roast_id}/{condition_id}",
        "roast_id": roast_id, "condition_id": condition_id,
        "verdict": verdict, "note": note or "",
        "recorded_at": _now(), "recorded_by": _who(),
    }, override=path)


def sensory_for(roast_id: str, path: str | None = None) -> dict:
    """``{condition_id: {verdict, note, …}}`` for one roast."""
    frame = db.frame("SELECT * FROM sensory WHERE roast_id = :id", {"id": roast_id},
                     override=path)
    if frame.empty:
        return {}
    return {row["condition_id"]: dict(row) for _, row in frame.iterrows()}


def sensory_scoreboard(path: str | None = None):
    """How often each risk the app raised was confirmed at the table.

    This is the honest measure of whether a heuristic earns its place: not how
    often it fires, but how often somebody tasted what it warned about.
    """
    return db.frame(
        "SELECT condition_id, "
        "       COUNT(*) AS cupped, "
        "       SUM(CASE WHEN verdict = 'confirmed' THEN 1 ELSE 0 END) AS confirmed, "
        "       SUM(CASE WHEN verdict = 'not present' THEN 1 ELSE 0 END) AS absent "
        "FROM sensory GROUP BY condition_id ORDER BY condition_id", override=path)


def set_reference(roast_id: str, coffee: str, path: str | None = None) -> None:
    """Mark one roast as the benchmark for its coffee; clear any previous one."""
    roasts = load_roasts(path)
    same = roasts[roasts["coffee"] == coffee]["uid"].tolist()
    stamp, who = _now(), _who()
    for other in same:
        db.upsert("roast_notes", "roast_id",
                  {"roast_id": other, "is_reference": 0, "updated_at": stamp, "updated_by": who},
                  override=path)
    db.upsert("roast_notes", "roast_id",
              {"roast_id": roast_id, "is_reference": 1, "updated_at": stamp, "updated_by": who},
              override=path)


def forget(roast_ids, path: str | None = None) -> int:
    ids = [str(i) for i in roast_ids]
    if not ids:
        return 0
    marks = ", ".join(f":id{position}" for position in range(len(ids)))
    params = {f"id{position}": value for position, value in enumerate(ids)}
    for table, column in (("roasts", "uid"), ("roast_curve", "roast_id"),
                          ("roast_notes", "roast_id"), ("sources", "roast_id"),
                          ("recommendations", "roast_id"), ("sensory", "roast_id")):
        db.run(f"DELETE FROM {table} WHERE {column} IN ({marks})", params, override=path)
    return len(ids)


def clear(path: str | None = None) -> None:
    for table in TABLES:
        db.run(f"DELETE FROM {table}", override=path)
    db.run("DELETE FROM meta WHERE key = 'last_import'", override=path)


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


def save_recommendations(roast_id: str, coffee: str, items: list[dict],
                         path: str | None = None) -> int:
    """Replace the open recommendations for a roast with a fresh set.

    Anything already applied or dismissed is left alone -- the record of what was
    suggested and what happened is the point.
    """
    db.run("DELETE FROM recommendations WHERE roast_id = :id AND status = 'open'",
           {"id": roast_id}, override=path)
    if not items:
        return 0

    # One round trip for the lot rather than two per suggestion. Reviewing forty
    # coffees used to be several hundred small writes, which over a hosted
    # database is what a spinner that never stops is made of.
    stamp = _now()
    rows = [{
        "roast_id": roast_id, "coffee": coffee, "rule_id": item["rule_id"],
        "created_at": stamp, "headline": item["headline"], "finding": item["finding"],
        "action": item["action"], "reason": item.get("reason"),
        "target_metric": item.get("target_metric"),
        "current_value": _plain(item.get("current_value")),
        "predicted_value": _plain(item.get("predicted_value")),
        "direction": item.get("direction"),
        "confidence": _plain(item.get("confidence")), "basis": item.get("basis"),
        "moves": json.dumps([_plain(m) for m in (item.get("moves") or [])]),
    } for item in items]

    db.run(
        """INSERT INTO recommendations
           (roast_id, coffee, rule_id, created_at, headline, finding, action, reason,
            target_metric, current_value, predicted_value, direction, confidence, basis,
            moves)
           VALUES (:roast_id, :coffee, :rule_id, :created_at, :headline, :finding, :action,
                   :reason, :target_metric, :current_value, :predicted_value, :direction,
                   :confidence, :basis, :moves)""",
        rows, override=path)

    db.run(
        """INSERT INTO rule_stats (rule_id, suggested, updated_at)
           VALUES (:rule_id, 1, :updated_at)
           ON CONFLICT (rule_id) DO UPDATE SET suggested = rule_stats.suggested + 1,
             updated_at = EXCLUDED.updated_at""",
        [{"rule_id": item["rule_id"], "updated_at": stamp} for item in items],
        override=path)
    return len(items)


def recommendations(roast_id: str | None = None, status: str | None = None,
                    path: str | None = None) -> pd.DataFrame:
    query = "SELECT * FROM recommendations"
    clauses, params = [], {}
    if roast_id:
        clauses.append("roast_id = :roast_id")
        params["roast_id"] = roast_id
    if status:
        clauses.append("status = :status")
        params["status"] = status
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC, id DESC"
    return db.frame(query, params, override=path)


def update_recommendation(recommendation_id: int, path: str | None = None, **values) -> None:
    allowed = {"status", "applied_roast_id", "observed_value", "outcome", "evaluated_at", "note"}
    fields = {k: _plain(v) for k, v in values.items() if k in allowed}
    if not fields:
        return
    assignments = ", ".join(f"{k} = :{k}" for k in fields)
    db.run(f"UPDATE recommendations SET {assignments} WHERE id = :id",
           {**fields, "id": int(recommendation_id)}, override=path)


def record_outcome(recommendation_id: int, applied_roast_id: str, observed: float,
                   outcome: str, path: str | None = None) -> None:
    row = db.one("SELECT rule_id, predicted_value FROM recommendations WHERE id = :id",
                 {"id": int(recommendation_id)}, override=path)
    stamp = _now()
    db.run(
        """UPDATE recommendations SET status = 'evaluated', applied_roast_id = :applied,
           observed_value = :observed, outcome = :outcome, evaluated_at = :stamp
           WHERE id = :id""",
        {"applied": applied_roast_id, "observed": _plain(observed), "outcome": outcome,
         "stamp": stamp, "id": int(recommendation_id)}, override=path)

    if not row:
        return
    rule_id, predicted = row[0], row[1]
    error = None
    if predicted is not None and observed is not None:
        error = abs(float(observed) - float(predicted))
    column = {"achieved": "achieved", "partial": "partial"}.get(outcome, "missed")
    db.run(
        f"""INSERT INTO rule_stats (rule_id, applied, {column}, mean_error, updated_at)
            VALUES (:rule_id, 1, 1, :error, :stamp)
            ON CONFLICT (rule_id) DO UPDATE SET
              applied = rule_stats.applied + 1,
              {column} = rule_stats.{column} + 1,
              mean_error = CASE
                WHEN :error IS NULL THEN rule_stats.mean_error
                WHEN rule_stats.mean_error IS NULL THEN :error
                ELSE (rule_stats.mean_error * rule_stats.applied + :error)
                     / (rule_stats.applied + 1) END,
              updated_at = EXCLUDED.updated_at""",
        {"rule_id": rule_id, "error": error, "stamp": stamp}, override=path)


def rule_scoreboard(path: str | None = None) -> pd.DataFrame:
    frame = db.frame("SELECT * FROM rule_stats ORDER BY suggested DESC", override=path)
    if frame.empty:
        return frame
    tested = frame[["achieved", "partial", "missed"]].sum(axis=1).replace(0, np.nan)
    frame["hit_rate"] = (frame["achieved"] + 0.5 * frame["partial"]) / tested
    return frame


# ---------------------------------------------------------------------------
# Learned effects
# ---------------------------------------------------------------------------


def save_effect(key: str, control: str, phase: str, metric: str,
                slope: float, observations: int, spread: float,
                path: str | None = None) -> None:
    db.upsert("effects", "key", {
        "key": key, "control": control, "phase": phase, "metric": metric,
        "slope": _plain(slope), "observations": int(observations or 0),
        "spread": _plain(spread), "updated_at": _now(),
    }, override=path)


def effects(path: str | None = None) -> pd.DataFrame:
    return db.frame("SELECT * FROM effects ORDER BY observations DESC", override=path)


def effect(key: str, path: str | None = None) -> dict | None:
    row = db.one(
        "SELECT key, control, phase, metric, slope, observations, spread "
        "FROM effects WHERE key = :key", {"key": key}, override=path)
    if not row:
        return None
    return dict(zip(("key", "control", "phase", "metric", "slope", "observations", "spread"),
                    row))
