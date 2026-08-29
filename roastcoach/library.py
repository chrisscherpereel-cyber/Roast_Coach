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
# Spelling matters less than it looks: _first() falls back to a case-blind
# match, which is what finds this roaster's `recipeID` from `recipeId`.
LINKS = {
    "bean": ("beanId", "beanGuid", "bean_id", "beanUid", "greenBeanId"),
    "recipe": ("recipeID", "recipeId", "recipeGuid", "recipe_id", "recipeUid",
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
# beans; 5 resolves a roast's bean id against containers as well as beans;
# 6 reads a recipe's own steps and fields, not only its name; 7 stops relying on
# RoasTime calling the link what we expect — any id-shaped field on a roast that
# names a record we hold is the link, and a recipe that names the roast it came
# from counts too; 8 works out which id field identifies a record instead of
# assuming — twenty-six recipes sharing one machine guid were being stored on top
# of one another — and matches field names case-blind, which is what finds
# `recipeID`.
VERSION = 8


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
    """The first of ``keys`` this record actually has, whatever its capitals.

    A roast on this roaster's machine spells the link to its recipe ``recipeID``.
    Older RoasTime files spell it ``recipeId``. One capital letter is not a
    different field, and a lookup that thinks it is loses every recipe name in
    the app while the data sits there in plain sight — so match exactly first,
    then case-blind.
    """
    for key in keys:
        value = record.get(key)
        if not _blank(value):
            return str(value).strip()

    folded = {str(key).lower(): key for key in record}
    for key in keys:
        actual = folded.get(str(key).lower())
        if actual is not None and not _blank(record[actual]):
            return str(record[actual]).strip()
    return None


def identity(record: dict) -> tuple[str | None, str | None]:
    """The id a roast would point at, and something human to call it."""
    return _first(record, ID_KEYS), _first(record, NAME_KEYS)


def choose_key(records: list) -> str | None:
    """Which id field actually identifies one record in this batch.

    Not every id on a RoasTime record is the record's own. Twenty-six of this
    roaster's seventy recipes carry the same `guid` — it belongs to the machine
    they were written on — while each has a `uid` of its own. Filing them under
    the first id field in a fixed list therefore stored twenty-six recipes on top
    of one another, and sixty-one rows came back from eighty-seven files.

    So the field is not assumed, it is worked out: the one whose values are
    present on every record and different for every record is the one that
    identifies them. Where none is, the caller falls back to the file name, which
    is what RoasTime names these files after anyway.
    """
    usable = [record for record in records if isinstance(record, dict)]
    if not usable:
        return None
    for key in ID_KEYS:
        values = [_first({key: record.get(key)}, (key,)) for record in usable]
        if all(value is not None for value in values) and len(set(values)) == len(values):
            return key
    return None


# ---------------------------------------------------------------------------
# Putting it in
# ---------------------------------------------------------------------------


def add_records(kind: str, files: list[dict], path: str | None = None) -> dict:
    """Store records of one kind from ``[{name, text}, …]``.

    Anything that parses as a JSON object is kept. Records without a usable id
    fall back to the file name, so nothing is dropped for want of a field.
    """
    report = {"stored": 0, "failed": 0, "problems": [], "keyed_by": ""}
    stamp = _now()

    # Read the whole batch first, so the id that identifies one record can be
    # worked out from the records rather than assumed. See :func:`choose_key`.
    parsed: list[tuple[dict, dict, int]] = []
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
            parsed.append((item, one, position if len(records) > 1 else -1))

    key_field = choose_key([one for _item, one, _position in parsed])
    report["keyed_by"] = key_field or "file name"

    for item, one, position in parsed:
        ref_id = _first(one, (key_field,)) if key_field else None
        name = _first(one, NAME_KEYS)
        if not ref_id:
            stem = str(item.get("name") or "record").rsplit(".", 1)[0]
            ref_id = stem if position < 0 else f"{stem}#{position}"
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


# What a recipe holds beyond its name. RoasTime writes these on every recipe and
# official recipe; anything not listed here is still stored and still shown.
RECIPE_FIELDS = {
    "roast degree": ("roastDegree", "roast_degree"),
    "target weight": ("weight", "targetWeight"),
    "preheat": ("preheatTemp", "preheatTemperature"),
    "country": ("country",),
    "process": ("process",),
    "temperature scale": ("tempMeasurement",),
    "device": ("deviceType", "deviceId"),
    "from roast": ("referenceRoastGuid", "referenceRoastUid"),
    "downloads": ("downloadCount",),
    "updated": ("updatedAt",),
}

# What a step inside a recipe might be called, whichever way this version writes
# it. A Bullet recipe is a list of moves against temperature or time.
STEP_TIME = ("time", "seconds", "at", "index", "elapsed", "timestamp")
STEP_TEMPERATURE = ("temp", "temperature", "beanTemperature", "bt",
                    "targetTemp", "triggerTemp")
STEP_CONTROLS = {"power": ("power", "p", "heat", "burner"),
                 "fan": ("fan", "f", "airflow"),
                 "drum": ("drum", "d", "drumSpeed", "rpm")}

# RoasTime writes a recipe step as numbers, not words: what to watch for
# (`trigger`), the number to watch for (`value`), and what to do when it arrives
# (`actions`, each its own number). These are those numbers, read off this
# roaster's own seventy recipes — 442 temperature triggers, 470 time triggers,
# and every action code that appears in any of them.
# The Bullet has two probes and RoasTime's field names invite exactly the wrong
# reading of them. `drumTemperature` is the **IBTS** — an infrared sensor looking
# at the bean mass — and `beanTemperature` is the physical probe buried in it.
# Recipes are written against the IBTS, and it is the line the roaster watches,
# so it is named for what it is everywhere the roaster can see it.
TRIGGERS = {0: "IBTS", 1: "bean probe", 2: "temperature", 3: "time"}
ACTIONS = {0: "power", 1: "drum", 2: "fan", 3: "note", 4: "alert"}


def _clock(seconds) -> str:
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return str(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _decode_event(event: dict) -> dict:
    """One `{trigger, value, actions}` block as something readable."""
    step: dict = {}
    trigger = event.get("trigger")
    value = event.get("value")

    kind = TRIGGERS.get(trigger)
    if kind == "time":
        # A step commonly carries a second condition of five seconds, which is
        # RoasTime holding the setting rather than a step of its own.
        step["after"] = _clock(value)
    elif kind:
        step["temperature"] = value
        step["watching"] = kind

    for action in event.get("actions") or []:
        if not isinstance(action, dict):
            continue
        name = ACTIONS.get(action.get("action"), f"action {action.get('action')}")
        held = action.get("value")
        if held in (None, "", "None"):
            continue
        step[name] = str(held).strip()
    return step


def recipe_steps(record: dict) -> list[dict]:
    """A recipe's own steps, in whatever shape this RoasTime version wrote them.

    Bullet recipes are written against *temperature* — "power 7 at 176 °C" — and
    sometimes against time as well, so both are carried through when they are
    there. Two shapes are read: RoasTime's own, where `events` is a list of steps
    and each step is a list of `{trigger, value, actions}` blocks, and the plainer
    `{time, temperature, power, fan}` shape other exports use. A step this app
    cannot name is still shown, as it was written.
    """
    steps: list[dict] = []

    start = record.get("startSettings")
    if isinstance(start, dict) and start:
        opening = {"at": "charge"}
        for control, keys in STEP_CONTROLS.items():
            value = _first(start, keys)
            if value is not None:
                opening[control] = value
        for key, value in start.items():
            if not isinstance(value, (list, dict)):
                opening.setdefault(key, value)
        steps.append(opening)

    def read(block, label: str = "") -> None:
        events = block if isinstance(block, list) else [block]
        merged: dict = {}
        for event in events:
            if isinstance(event, list):                   # a step of conditions
                read(event, label)
                return
            if not isinstance(event, dict):
                continue
            if "trigger" in event:
                merged.update(_decode_event(event))
            else:                                         # the plainer shape
                plain = {}
                when = _first(event, STEP_TIME)
                temperature = _first(event, STEP_TEMPERATURE)
                if when is not None:
                    plain["at"] = when
                if temperature is not None:
                    plain["temperature"] = temperature
                for control, keys in STEP_CONTROLS.items():
                    value = _first(event, keys)
                    if value is not None:
                        plain[control] = value
                for key, value in event.items():
                    if not isinstance(value, (list, dict)) and key not in plain:
                        plain.setdefault(key, value)
                if plain:
                    steps.append(plain)
        if merged:
            if label:
                merged["at"] = label
            steps.append(merged)

    for block in (record.get("events") or record.get("steps")
                  or record.get("actions") or []):
        read(block)

    ending = record.get("endSettings")
    if ending:
        read(ending, "drop")
    return steps


def recipe_summary(record: dict) -> dict:
    """The named fields of a recipe, for a table beside its steps."""
    found = {}
    for label, keys in RECIPE_FIELDS.items():
        value = _first(record, keys)
        if value is not None:
            found[label] = value
    return found


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

    # And the other way round: a container says which bean is in it. If a roast
    # points at a bean whose own file has not arrived, the container holding that
    # bean still knows what the roaster calls it.
    for record in (tables.get("container") or {}).values():
        behind = _first(record, ("beanId", "beanUid", "bean_id"))
        if behind:
            found.setdefault(behind, ("container", record))

    # A bean answers to its uid and its guid alike; a roast may quote either —
    # but an id that more than one record carries identifies none of them.
    shared: dict[str, list] = {}
    for ref_id, (kind, record) in list(found.items()):
        for key in ID_KEYS:
            value = record.get(key)
            if not _blank(value) and str(value).strip() not in found:
                shared.setdefault(str(value).strip(), []).append((kind, record))
    for value, holders in shared.items():
        if len(holders) == 1:
            found[value] = holders[0]
    return found


# An id is long and unlike a measurement. This is what stops a scan for
# "any field that matches a record" from matching a roast number or a power
# setting that happens to equal something.
def _id_like(value) -> bool:
    if _blank(value) or not isinstance(value, (str, int)):
        return False
    text = str(value).strip()
    return len(text) >= 8 and not text.replace(".", "", 1).lstrip("-").isdigit()


def _scan_for(roast_row: dict, known: dict) -> str | None:
    """Any id-shaped field on this roast that names a record we hold.

    RoasTime has changed what it calls the link to a recipe more than once, and
    on this roaster's files it does not use any of the names we know. Rather than
    keep guessing names, look at it from the other end: take every id-shaped
    value on the roast and ask whether it is a record we already have. A false
    match would need a roast to carry, in some other field, the exact id of a
    recipe — which is what a link *is*.
    """
    if not known:
        return None
    for key, value in roast_row.items():
        if key in ("uid", "guid", "id") or not _id_like(value):
            continue
        text = str(value).strip()
        if text in known:
            return text
    return None


def id_index(records: dict) -> dict:
    """Every id a record answers to, pointing at the id it is stored under.

    A RoasTime recipe carries both a `uid` and a `guid`, and they are different
    strings. Storing it under one and looking it up by the other finds nothing —
    which is exactly how a recipe whose file was sitting in the library came to
    show as "no recipe" on the roast that ran it.
    """
    seen: dict[str, set] = {}
    for ref_id, record in (records or {}).items():
        seen.setdefault(str(ref_id), set()).add(ref_id)
        for key in ID_KEYS:
            value = record.get(key)
            if not _blank(value):
                seen.setdefault(str(value).strip(), set()).add(ref_id)

    # An id that several records share — RoasTime writes the machine's guid onto
    # every recipe made on it — names none of them. Drop it rather than pick one.
    found = {value: next(iter(ids)) for value, ids in seen.items() if len(ids) == 1}
    for ref_id in (records or {}):
        found[str(ref_id)] = ref_id
    return found


def back_links(records: dict) -> dict:
    """``{roast id: record id}`` for records that name the roast they came from.

    A recipe carries `referenceRoastGuid`: the roast it was built from. That is
    the one recipe-to-roast link RoasTime definitely writes, so where a roast
    carries no recipe id of its own, this at least names the recipe for the roast
    that *is* the recipe.
    """
    found = {}
    for ref_id, record in (records or {}).items():
        for key in ("referenceRoastGuid", "referenceRoastUid", "referenceRoastId",
                    "roastGuid", "roastUid", "roastId"):
            value = record.get(key)
            if not _blank(value):
                found.setdefault(str(value).strip(), ref_id)
    return found


def name_index(records: dict) -> dict:
    """``{name in lower case: record id}``, only where a name identifies one record."""
    seen: dict[str, list] = {}
    for ref_id, record in (records or {}).items():
        name = _first(record, NAME_KEYS)
        if name:
            seen.setdefault(name.strip().lower(), []).append(ref_id)
    return {name: ids[0] for name, ids in seen.items() if len(ids) == 1}


def _recipe_for(roast_row: dict, recipes: dict, backwards: dict,
                by_name: dict, aliases: dict | None = None) -> tuple[str | None, str]:
    """Which recipe this roast was run from, and how we know.

    In order of how much it can be trusted: the roast says so; the roast carries
    the recipe's id under a name we did not expect; the recipe says it came from
    this roast; the roast and the recipe have the same name.
    """
    aliases = aliases if aliases is not None else id_index(recipes)

    ref_id = link_id(roast_row, "recipe")
    if ref_id and ref_id in aliases:
        return aliases[ref_id], "id on the roast"

    scanned = _scan_for(roast_row, aliases)
    if scanned:
        return aliases[scanned], "id on the roast"

    for key in ("uid", "guid", "id", "roastGuid"):
        value = roast_row.get(key)
        if not _blank(value) and str(value).strip() in backwards:
            return backwards[str(value).strip()], "the recipe names this roast"

    title = _first(roast_row, ("roast_name", "roastName", "name", "title"))
    if title and title.strip().lower() in by_name:
        return by_name[title.strip().lower()], "same name"
    return None, ""


def labels_for(lookup: dict, beans: dict | None = None) -> dict:
    """A name for every id in a coffee lookup, counting each *coffee* once.

    Two things make one coffee look like several here. A record is filed under
    every id it answers to, so a bean with a uid and a guid appears twice; and a
    bag in `containers/` carries the same name as the bean in it. :func:`bean_labels`
    would see the repeats as different coffees of the same name and pull them
    apart with a piece of each id — "Costa Rica La Minita Tarrazu RFA · 0640",
    for a bean that never had a twin.

    So resolve each id to the coffee behind it — a container to its bean — name
    the coffees, and give each id the name of the one it leads to. What survives
    is real: three beans genuinely called "Colombia Ca" still separate.
    """
    beans = beans or {}
    anchors: dict[str, dict] = {}
    for ref_id, (kind, record) in lookup.items():
        anchor = record
        if kind == "container":
            behind = _first(record, ("beanId", "beanUid", "bean_id"))
            if behind:
                found = beans.get(behind)
                if found is None and behind in lookup and lookup[behind][0] == "bean":
                    found = lookup[behind][1]
                if found is not None:
                    anchor = found
        anchors[ref_id] = anchor

    canonical: dict[int, str] = {}
    once: dict[str, dict] = {}
    for ref_id, anchor in anchors.items():
        if id(anchor) not in canonical:
            canonical[id(anchor)] = ref_id
            once[ref_id] = anchor

    named = bean_labels(once)
    return {ref_id: named.get(canonical[id(anchor)], ref_id)
            for ref_id, anchor in anchors.items()}


def _enrich_one(roast_row: dict, tables: dict, labels: dict | None = None,
                lookup: dict | None = None, recipe_links: tuple | None = None) -> dict:
    found: dict = {}

    lookup = lookup if lookup is not None else coffee_lookup(tables)
    bean_id = link_id(roast_row, "bean") or _scan_for(roast_row, lookup)
    if bean_id and bean_id in lookup:
        kind, record = lookup[bean_id]
        found["bean_id"] = bean_id
        found["bean_name"] = (labels or {}).get(bean_id) or _first(record, NAME_KEYS)
        found["bean_from"] = kind
        # The bag is what the roaster reaches for; the bean is what is compared.
        # Two bags of one coffee must not become two coffees, so the *bean* names
        # the roast and the lot is kept beside it rather than instead of it.
        if kind == "container":
            found["lot_name"] = _first(record, NAME_KEYS) or bean_id

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

    recipes = tables.get("recipe") or {}
    backwards, by_name, aliases = recipe_links or (
        back_links(recipes), name_index(recipes), id_index(recipes))
    recipe_id, how = _recipe_for(roast_row, recipes, backwards, by_name, aliases)
    if recipe_id:
        found["recipe_id"] = recipe_id
        found["recipe_name"] = _first(recipes[recipe_id], NAME_KEYS) or recipe_id
        found["recipe_from"] = how

    for kind, column in OTHER_KINDS:
        if kind == "recipe":
            continue
        ref_id = link_id(roast_row, kind) or _scan_for(roast_row, tables.get(kind, {}))
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
    labels = labels_for(lookup, loaded.get("bean"))
    recipes = loaded.get("recipe") or {}
    links = (back_links(recipes), name_index(recipes), id_index(recipes))
    return [_enrich_one(row, loaded, labels, lookup, links) for row in rows]


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
    recipes = loaded.get("recipe") or {}
    backwards, by_name = back_links(recipes), name_index(recipes)
    aliases = id_index(recipes)

    found = []
    for kind, label, folder in COMPANIONS:
        # A bean id may name a bean or a container of one — both count as found.
        known = coffees if kind == "bean" else loaded.get(kind, {})
        matched = no_id = 0
        missing: dict[str, int] = {}
        how: dict[str, int] = {}
        for row in rows:
            if kind == "recipe":
                ref_id, reason = _recipe_for(row, recipes, backwards, by_name, aliases)
                if ref_id:
                    matched += 1
                    how[reason] = how.get(reason, 0) + 1
                    continue
                stated = link_id(row, kind)
                if stated:
                    missing[stated] = missing.get(stated, 0) + 1
                else:
                    no_id += 1
                continue

            ref_id = link_id(row, kind) or _scan_for(row, known)
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
            "how": sorted(how.items(), key=lambda pair: -pair[1]),
        })
    return found


def enrich(roast_row: dict, path: str | None = None) -> dict:
    """Everything the reference records add to one roast.

    The same joining as :func:`enrich_many`, for a single roast — one query
    rather than one per record, and identical results either way.
    """
    loaded = tables(path)
    lookup = coffee_lookup(loaded)
    labels = labels_for(lookup, loaded.get("bean"))
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
