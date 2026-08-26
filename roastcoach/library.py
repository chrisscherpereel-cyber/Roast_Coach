"""
The rest of what RoasTime knows.

A roast file is only part of the story. RoasTime keeps the bean, the recipe the
roast was run from, the machine, and the roaster's own profiles in sibling
folders, and the roast file points at them by id:

    roast-time/
      roasts/            one file per roast          → the `roasts` table
      beans/             the coffee: origin, process, variety, supplier
      recipes/           the profile you roasted from
      officialRecipes/   Aillio's own profiles
      containers/        machines and storage
      containerGroups/   how they are grouped
      configs/           machine configuration
      userProfiles/      who roasted
      users/

Everything except `roasts` lands in one `reference` table, stored exactly as it
arrived. Nothing is thrown away because this module did not recognise it — a
RoasTime update that adds a field simply appears in the record.

Linking a roast to its bean and recipe is done by trying the id fields RoasTime
has actually used, in order, rather than assuming one name. :func:`describe_link`
reports what matched, so a version that names things differently is a fixable
observation rather than a silent blank.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import db

# The folders worth keeping, and what to call each kind.
KINDS = {
    "beans": "bean",
    "recipes": "recipe",
    "officialRecipes": "recipe",
    "containers": "container",
    "containerGroups": "containerGroup",
    "configs": "config",
    "userProfiles": "userProfile",
    "users": "user",
    "device": "device",
}

# RoasTime has spelled these several ways across versions. Try them all.
ID_KEYS = ("id", "_id", "guid", "uid", "uuid", "beanId", "recipeId", "objectId")
NAME_KEYS = ("name", "beanName", "recipeName", "title", "label", "displayName",
             "nickname", "origin")

# How a roast points at each kind of record.
LINKS = {
    "bean": ("beanId", "beanGuid", "bean_id", "beanUid", "greenBeanId"),
    "recipe": ("recipeId", "recipeGuid", "recipe_id", "recipeUid",
               "officialRecipeId", "profileId"),
    "container": ("containerId", "containerGuid", "machineId", "roasterId"),
    "userProfile": ("userProfileId", "userId", "profileId"),
}

# Fields worth lifting out of a bean record and onto the roast itself.
BEAN_FIELDS = {
    "origin": ("origin", "country", "countryOfOrigin", "originCountry"),
    "process": ("process", "processing", "processingMethod", "method"),
    "variety": ("variety", "varietal", "cultivar"),
    "farm": ("farm", "producer", "estate", "supplier", "vendor", "region"),
    "altitude": ("altitude", "elevation", "altitudeMeters"),
    "harvest": ("harvest", "harvestDate", "crop", "cropYear"),
    "bean_notes": ("notes", "description", "comment", "tastingNotes"),
}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _first(record: dict, keys) -> str | None:
    for key in keys:
        value = record.get(key)
        if value not in (None, "", [], {}):
            return str(value)
    return None


def identity(record: dict) -> tuple[str | None, str | None]:
    """The id a roast would point at, and something human to call it."""
    return _first(record, ID_KEYS), _first(record, NAME_KEYS)


# ---------------------------------------------------------------------------
# Putting it in
# ---------------------------------------------------------------------------


def add_records(kind: str, files: list[dict], path: str | None = None) -> dict:
    """Store records of one kind from ``[{name, text}, …]``.

    Anything that parses as a JSON object is kept. Records without a usable id
    fall back to the file name, so nothing is dropped for want of a field.
    """
    report = {"stored": 0, "failed": 0, "problems": []}
    stamp = _now()

    for item in files:
        try:
            record = json.loads(item.get("text") or "")
        except Exception:
            report["failed"] += 1
            report["problems"].append(f"{item.get('name')}: not JSON")
            continue

        if isinstance(record, list):                 # some files hold an array
            records = [r for r in record if isinstance(r, dict)]
        elif isinstance(record, dict):
            records = [record]
        else:
            report["failed"] += 1
            continue

        for position, one in enumerate(records):
            ref_id, name = identity(one)
            if not ref_id:
                stem = str(item.get("name") or "record").rsplit(".", 1)[0]
                ref_id = stem if len(records) == 1 else f"{stem}#{position}"
            db.upsert("reference", "key", {
                "key": f"{kind}/{ref_id}",
                "kind": kind,
                "ref_id": ref_id,
                "name": name,
                "updated_at": stamp,
                "data": json.dumps(one),
            }, override=path)
            report["stored"] += 1

    return report


def counts(path: str | None = None) -> dict:
    """How many of each kind are stored — shown on the Data page."""
    rows = db.rows("SELECT kind, COUNT(*) FROM reference GROUP BY kind ORDER BY kind",
                   override=path)
    return {kind: int(total) for kind, total in rows}


def records(kind: str, path: str | None = None) -> dict:
    """``{id: record}`` for one kind."""
    rows = db.rows("SELECT ref_id, data FROM reference WHERE kind = :kind",
                   {"kind": kind}, override=path)
    return {ref_id: json.loads(data) for ref_id, data in rows}


def record(kind: str, ref_id: str, path: str | None = None) -> dict | None:
    row = db.one("SELECT data FROM reference WHERE key = :key",
                 {"key": f"{kind}/{ref_id}"}, override=path)
    return json.loads(row[0]) if row else None


# ---------------------------------------------------------------------------
# Joining it to the roasts
# ---------------------------------------------------------------------------


def link_id(roast: dict, kind: str) -> str | None:
    """Which record of ``kind`` this roast points at, whatever RoasTime called it."""
    return _first(roast, LINKS.get(kind, ()))


OTHER_KINDS = (("recipe", "recipe_name"), ("container", "machine_name"),
               ("userProfile", "roasted_by"))


def bean_labels(beans: dict) -> dict:
    """``{id: what to call this bean}``, unique across the whole library.

    Roasts are compared bean by bean, so the label has to identify one bean and
    not merely describe it. Two lots of "Ethiopia Guji" bought a year apart are
    different coffees; when RoasTime holds them as two records with one name,
    each keeps a short piece of its own id so they stay apart.
    """
    names: dict[str, str] = {}
    for ref_id, record in beans.items():
        names[ref_id] = _first(record, NAME_KEYS) or ref_id

    seen: dict[str, list[str]] = {}
    for ref_id, name in names.items():
        seen.setdefault(name, []).append(ref_id)
    for name, ids in seen.items():
        if len(ids) < 2:
            continue
        # The tail of the id, not the head: RoasTime ids often share a prefix,
        # and a suffix that is still ambiguous falls back to the whole id.
        tails = {ref_id: str(ref_id)[-4:] for ref_id in ids}
        distinct = len(set(tails.values())) == len(ids)
        for ref_id in ids:
            names[ref_id] = f"{name} · {tails[ref_id] if distinct else ref_id}"
    return names


def _enrich_one(roast_row: dict, tables: dict, labels: dict | None = None) -> dict:
    found: dict = {}

    bean_id = link_id(roast_row, "bean")
    if bean_id:
        bean = tables.get("bean", {}).get(bean_id)
        if bean:
            found["bean_id"] = bean_id
            found["bean_name"] = (labels or {}).get(bean_id) or _first(bean, NAME_KEYS)
            for column, keys in BEAN_FIELDS.items():
                value = _first(bean, keys)
                if value is not None:
                    found[column] = value

    for kind, column in OTHER_KINDS:
        ref_id = link_id(roast_row, kind)
        if not ref_id:
            continue
        stored = tables.get(kind, {}).get(ref_id)
        if stored:
            found[column] = _first(stored, NAME_KEYS) or ref_id

    return found


def tables(path: str | None = None) -> dict:
    """Every reference record, by kind then id, in one query.

    Five hundred roasts asking for their bean one at a time is fifteen hundred
    round trips, which over a network is the difference between a page that
    opens and one that does not. The whole reference table is small — a few
    hundred small JSON records — so it is read once and joined in memory.
    """
    grouped: dict[str, dict] = {}
    for kind, ref_id, data in db.rows(
            "SELECT kind, ref_id, data FROM reference", override=path):
        try:
            grouped.setdefault(kind, {})[ref_id] = json.loads(data)
        except Exception:
            continue
    return grouped


def enrich_many(roast_rows, path: str | None = None) -> list[dict]:
    """:func:`enrich` for a whole table of roasts, with one query for the lot."""
    rows = list(roast_rows)
    if not rows:
        return []
    loaded = tables(path)
    labels = bean_labels(loaded.get("bean", {}))
    return [_enrich_one(row, loaded, labels) for row in rows]


def link_report(roast_rows, path: str | None = None) -> dict:
    """How the roasts and the bean files line up — for the Data page.

    A roast grouped under the wrong heading is nearly always one of three things,
    and this says which: it carries no bean id at all, it points at a bean whose
    file has not been synced, or it matched.
    """
    rows = list(roast_rows)
    beans = tables(path).get("bean", {})
    report = {"roasts": len(rows), "matched": 0, "no_id": 0,
              "missing": {}, "beans": len(beans)}
    for row in rows:
        bean_id = link_id(row, "bean")
        if not bean_id:
            report["no_id"] += 1
        elif bean_id in beans:
            report["matched"] += 1
        else:
            report["missing"][bean_id] = report["missing"].get(bean_id, 0) + 1
    return report


def enrich(roast_row: dict, path: str | None = None) -> dict:
    """Everything the reference records add to one roast.

    Returns only what was found, so a setup with no bean or recipe files behaves
    exactly as before rather than filling the roast with blanks.
    """
    found: dict = {}

    bean_id = link_id(roast_row, "bean")
    if bean_id:
        bean = record("bean", bean_id, path)
        if bean:
            found["bean_id"] = bean_id
            found["bean_name"] = _first(bean, NAME_KEYS)
            for column, keys in BEAN_FIELDS.items():
                value = _first(bean, keys)
                if value is not None:
                    found[column] = value

    for kind, column in OTHER_KINDS:
        ref_id = link_id(roast_row, kind)
        if not ref_id:
            continue
        stored = record(kind, ref_id, path)
        if stored:
            found[column] = _first(stored, NAME_KEYS) or ref_id

    return found


def describe_link(roast_row: dict, path: str | None = None) -> list[str]:
    """Plain sentences about what linked and what did not — for the Data page."""
    lines = []
    for kind in ("bean", "recipe", "container"):
        ref_id = link_id(roast_row, kind)
        if not ref_id:
            lines.append(f"This roast carries no {kind} id.")
        elif record(kind, ref_id, path):
            lines.append(f"{kind} → {ref_id} ✓")
        else:
            lines.append(f"{kind} id {ref_id} has no matching file yet.")
    return lines


def clear(path: str | None = None) -> None:
    db.run("DELETE FROM reference", override=path)
