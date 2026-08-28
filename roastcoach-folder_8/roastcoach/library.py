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


# What this file can do — see the note in store.py. 2 adds enrich_many(),
# tables(), bean_labels() and link_report(); 3 stops treating a pandas NaN as a
# bean id called "nan"; 4 reports coverage for every companion kind, not just
# beans; 5 resolves a roast's bean id against containers as well as beans.
VERSION = 5


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# A roast row that has been through pandas carries NaN where a field was absent,
# and str(NaN) is "nan" — which is how 387 roasts came to report that they point
# at a bean called `nan`. Anything in here is "no value", whatever its type.
_EMPTY = {"", "nan", "none", "null", "undefined", "0", "n/a"}


def _blank(value) -> bool:
    if value is None or value in ("", [], {}):
        return True
    try:                                    # NaN is not equal to itself
        if value != value:
            return True
    except Exception:
        pass
    return str(value).strip().lower() in _EMPTY


def _first(record: dict, keys) -> str | None:
    for key in keys:
        value = record.get(key)
        if not _blank(value):
            return str(value).strip()
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


# RoasTime's `containers` are bags of coffee, not machines — the machine is
# identified by the serial number on the roast itself, which store.py reads.
OTHER_KINDS = (("recipe", "recipe_name"), ("userProfile", "roasted_by"))


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


def coffee_lookup(tables: dict) -> dict:
    """Everything a roast's bean id might point at: a bean, or a container of one.

    RoasTime keeps *containers* — a bag or lot of a coffee, named the way the
    roaster thinks of it ("Del Campo") — as well as *beans*, which carry the
    origin and process. A roast's `beanId` can be either, and looking only in
    `beans/` is why 510 roasts reported a bean whose file "had not arrived" while
    the file was sitting in `containers/` all along.
    """
    found = {}
    for ref_id, record in (tables.get("bean") or {}).items():
        found[ref_id] = ("bean", record)
    for ref_id, record in (tables.get("container") or {}).items():
        found.setdefault(ref_id, ("container", record))
    return found


def _enrich_one(roast_row: dict, tables: dict, labels: dict | None = None,
                lookup: dict | None = None) -> dict:
    found: dict = {}

    lookup = lookup if lookup is not None else coffee_lookup(tables)
    bean_id = link_id(roast_row, "bean")
    if bean_id and bean_id in lookup:
        kind, record = lookup[bean_id]
        found["bean_id"] = bean_id
        found["bean_name"] = (labels or {}).get(bean_id) or _first(record, NAME_KEYS)
        found["bean_from"] = kind

        # A container names the lot; the bean behind it carries origin and
        # process, so follow the link when there is one.
        bean = record if kind == "bean" else None
        if bean is None:
            behind = _first(record, ("beanId", "beanUid", "bean_id"))
            bean = (tables.get("bean") or {}).get(behind) if behind else None

        if bean:
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
    lookup = coffee_lookup(loaded)
    labels = bean_labels({ref_id: record for ref_id, (_kind, record) in lookup.items()})
    return [_enrich_one(row, loaded, labels, lookup) for row in rows]


def link_report(roast_rows, path: str | None = None, kind: str = "bean") -> dict:
    """How the roasts and one kind of companion file line up — for the Data page.

    A roast missing its bean, its recipe or its machine is nearly always one of
    three things, and this says which: it carries no id at all, it points at a
    file that has not been synced, or it matched.
    """
    loaded = tables(path)
    rows = list(roast_rows)
    known = coffee_lookup(loaded) if kind == "bean" else loaded.get(kind, {})
    report = {"kind": kind, "roasts": len(rows), "matched": 0, "no_id": 0,
              "missing": {}, "stored": len(known)}
    for row in rows:
        ref_id = link_id(row, kind)
        if not ref_id:
            report["no_id"] += 1
        elif ref_id in known:
            report["matched"] += 1
        else:
            report["missing"][ref_id] = report["missing"].get(ref_id, 0) + 1
    report["beans"] = report["stored"]          # kept for older callers
    return report


# What each kind is called on screen, and which RoasTime folder carries it.
COMPANIONS = (("bean", "Bean or lot", "beans + containers"),
              ("recipe", "Recipe", "recipes + officialRecipes"),
              ("userProfile", "Roasted by", "userProfiles"))


def coverage(roast_rows, path: str | None = None) -> list[dict]:
    """One row per companion kind: matched, missing its file, or never recorded.

    "A lot of what RoasTime knows is not showing up" is nearly always one folder
    that never arrived. This says which, by name, with the count.
    """
    rows = list(roast_rows)
    loaded = tables(path)
    coffees = coffee_lookup(loaded)
    found = []
    for kind, label, folder in COMPANIONS:
        # A bean id may name a bean or a container of one — both count as found.
        known = coffees if kind == "bean" else loaded.get(kind, {})
        matched = no_id = 0
        missing: dict[str, int] = {}
        for row in rows:
            ref_id = link_id(row, kind)
            if not ref_id:
                no_id += 1
            elif ref_id in known:
                matched += 1
            else:
                missing[ref_id] = missing.get(ref_id, 0) + 1
        found.append({
            "what": label, "kind": kind, "folder": f"{folder}/",
            "files imported": len(known),
            "roasts matched": matched,
            "file not here": sum(missing.values()),
            "no id on the roast": no_id,
            "worst": sorted(missing.items(), key=lambda pair: -pair[1])[:3],
        })
    return found


def enrich(roast_row: dict, path: str | None = None) -> dict:
    """Everything the reference records add to one roast.

    The same joining as :func:`enrich_many`, for a single roast — one query
    rather than one per record, and identical results either way.
    """
    loaded = tables(path)
    lookup = coffee_lookup(loaded)
    labels = bean_labels({ref_id: record for ref_id, (_kind, record) in lookup.items()})
    return _enrich_one(roast_row, loaded, labels, lookup)


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
