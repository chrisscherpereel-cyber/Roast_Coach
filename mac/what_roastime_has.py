"""Find out where RoasTime actually keeps things — and what Roast Coach can see.

Run this on the Mac that roasts. It reads, prints and changes nothing:

    python3 mac/what_roastime_has.py

It answers three questions, in order:

1. **Where is RoasTime's data?** Every folder it can find, with how many files,
   what kind, and when they were last written.
2. **What is in those files?** The field names of a sample from each folder —
   names only where it helps, never a dump of your roasts.
3. **Do the ids line up?** Your roasts point at beans and recipes by id. This
   collects those ids and then goes looking for them: in the JSON folders, and
   inside RoasTime's own databases (an Electron app keeps a lot in IndexedDB,
   which is a LevelDB store, not files you can read in Finder).

That third answer is the one that matters. If the recipe ids on your roasts turn
up inside IndexedDB and nowhere else, then no amount of folder-copying will ever
bring recipe names across — and we know exactly what to build instead.

Paste the output back and it says what needs doing.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

HOME = Path.home()

# Everywhere an Aillio app has been known to put its data, plus a wildcard for
# whatever this version calls itself.
ROOTS = [
    HOME / "Library/Application Support/roast-time",
    HOME / "Library/Application Support/RoasTime",
    HOME / "Library/Application Support/roasttime",
    HOME / "Library/Application Support/Roast.World",
    Path(os.environ.get("APPDATA", "")) / "roast-time",
]

# Fields a roast uses to point at something else.
LINK_FIELDS = ("beanId", "beanGuid", "greenBeanId",
               "recipeId", "recipeGuid", "officialRecipeId", "profileId",
               "containerId", "machineId", "userProfileId")

NAME_FIELDS = ("name", "beanName", "recipeName", "title", "label", "displayName")

DATABASE_HINTS = (".leveldb", ".ldb", ".log", ".sqlite", ".sqlite3", ".db", ".manifest")


def roots() -> list[Path]:
    found = [path for path in ROOTS if path.is_dir()]
    support = HOME / "Library/Application Support"
    if support.is_dir():
        for path in sorted(support.glob("*oas*ime*")):
            if path.is_dir() and path not in found:
                found.append(path)
        for path in sorted(support.glob("*illio*")):
            if path.is_dir() and path not in found:
                found.append(path)
    return found


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.0f} GB"


def describe_folder(folder: Path, depth: int = 0) -> list[str]:
    """One line per folder: how many files, what kind, how big, how recent."""
    lines = []
    try:
        entries = sorted(folder.iterdir())
    except OSError as problem:
        return [f"{'  ' * depth}{folder.name}/  (cannot read: {problem.strerror})"]

    files = [item for item in entries if item.is_file()]
    folders = [item for item in entries if item.is_dir()]
    kinds = Counter(item.suffix.lower() or "no extension" for item in files)
    size = sum(item.stat().st_size for item in files)
    newest = max((item.stat().st_mtime for item in files), default=0)

    when = ""
    if newest:
        import datetime

        when = " · newest " + datetime.datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M")

    summary = ", ".join(f"{count} {kind}" for kind, count in kinds.most_common(4))
    lines.append(f"{'  ' * depth}{folder.name}/  {len(files)} file(s)"
                 + (f" — {summary}" if summary else "")
                 + (f" · {human(size)}" if size else "") + when)

    if depth < 2:
        for child in folders:
            lines += describe_folder(child, depth + 1)
    elif folders:
        lines.append(f"{'  ' * (depth + 1)}…and {len(folders)} more folder(s)")
    return lines


def sample_json(folder: Path, how_many: int = 3) -> tuple[list[str], list[str]]:
    """The field names in a folder's JSON files, and any names inside them."""
    keys: Counter = Counter()
    names: list[str] = []
    read = 0
    try:
        candidates = [item for item in sorted(folder.iterdir())
                      if item.is_file() and item.suffix.lower() in (".json", "")]
    except OSError:
        return [], []

    for path in candidates:
        if read >= how_many:
            break
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
        except Exception:
            continue
        records = record if isinstance(record, list) else [record]
        for one in records[:5]:
            if not isinstance(one, dict):
                continue
            keys.update(one.keys())
            for field in NAME_FIELDS:
                if one.get(field):
                    names.append(f"{field} = {one[field]}")
                    break
        read += 1
    return [key for key, _ in keys.most_common(24)], names[:5]


def roast_links(folder: Path, limit: int = 200) -> dict:
    """Every id your roasts point at, by field name."""
    found: dict[str, set] = {field: set() for field in LINK_FIELDS}
    read = 0
    for path in sorted(folder.iterdir(), reverse=True):
        if read >= limit or not path.is_file():
            continue
        if path.suffix.lower() not in (".json", ""):
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        for field in LINK_FIELDS:
            value = record.get(field)
            if value not in (None, "", 0):
                found[field].add(str(value))
        read += 1
    return {field: ids for field, ids in found.items() if ids}


def hunt(ids: set, where: Path, budget_mb: int = 400) -> dict:
    """Which files anywhere under ``where`` contain these ids.

    RoasTime is an Electron app, and Electron apps keep a great deal in
    IndexedDB — which on disk is a LevelDB store: .log and .ldb files that look
    like binary rubbish in Finder but hold plain JSON inside. Reading them as
    bytes and looking for the id is crude, and it is enough to answer *where the
    data is*, which is the whole question.
    """
    wanted = {value.encode() for value in ids if value}
    hits: dict[str, int] = {}
    spent = 0
    if not wanted:
        return hits

    for path in where.rglob("*"):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 60 * 1024 * 1024:
            continue
        spent += size
        if spent > budget_mb * 1024 * 1024:
            break
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        matched = sum(1 for value in wanted if value in blob)
        if matched:
            label = str(path.relative_to(where))
            hits[label] = matched
    return hits


def scalar_keys(folder: Path, how_many: int = 3) -> dict:
    """Every field of a roast that is a single value, with what it holds.

    The long arrays are what a roast mostly is; the interesting part for linking
    is the handful of short fields around them, and there is no point guessing at
    their names when they can simply be listed.
    """
    found: dict[str, str] = {}
    read = 0
    for path in sorted(folder.iterdir(), reverse=True):
        if read >= how_many or not path.is_file():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        for key, value in record.items():
            if isinstance(value, (list, dict)):
                continue
            if key not in found and value not in (None, ""):
                text = str(value)
                found[key] = text[:44] + "…" if len(text) > 44 else text
        read += 1
    return found


def nested_shape(folder: Path, how_many: int = 2) -> dict:
    """The fields of a roast that hold an object or a list, and what is in them.

    A link does not have to be a plain field. If RoasTime writes the recipe as a
    nested object on the roast — name and all — then the name is already on this
    Mac and no id needs matching at all. Listing scalars alone would never show it.
    """
    found: dict[str, str] = {}
    read = 0
    for path in sorted(folder.iterdir(), reverse=True):
        if read >= how_many or not path.is_file():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        for key, value in record.items():
            if key in found:
                continue
            if isinstance(value, dict):
                inside = ", ".join(list(value.keys())[:12]) or "empty"
                found[key] = f"object {{{inside}}}"
            elif isinstance(value, list):
                if value and isinstance(value[0], dict):
                    inside = ", ".join(list(value[0].keys())[:12])
                    found[key] = f"list of {len(value)} object(s) {{{inside}}}"
                elif len(value) > 20:
                    found[key] = f"list of {len(value)} numbers — this is the curve"
                elif value:
                    found[key] = f"list: {str(value[:6])[:60]}"
        read += 1
    return found


def which_field_links(roasts: Path, catalogue: dict, how_many: int = 5) -> list[str]:
    """For each field of a roast, whether its value is a record we can name.

    This is the question turned around. Instead of guessing what RoasTime calls
    the link to a recipe and looking for that name, take every value on the roast
    and ask which folder holds a record with that id. Whatever the field is
    called, if it points at a recipe this finds it — and if nothing on a roast
    points at a recipe, that is an answer too, and a final one.
    """
    lines: list[str] = []
    read = 0
    for path in sorted(roasts.iterdir(), reverse=True):
        if read >= how_many or not path.is_file():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        read += 1
        title = record.get("roastName") or record.get("name") or path.name
        hits = []
        for key, value in record.items():
            if isinstance(value, (list, dict)) or value in (None, ""):
                continue
            text = str(value).strip()
            if len(text) < 4:
                continue
            for folder, known in catalogue.items():
                if text in known:
                    hits.append(f"    {key:24s} → {folder}/  "
                                f"{known[text] or '(record has no name)'}")
        lines.append(f"  {title}")
        lines += hits or ["    nothing on this roast names a bean, recipe or container"]
    return lines


def ids_in_folder(folder: Path, how_many: int = 400) -> dict:
    """``{id: name}`` for every record in a folder, by every id field it uses."""
    found: dict[str, str] = {}
    read = 0
    try:
        entries = sorted(folder.iterdir())
    except OSError:
        return found
    for path in entries:
        if read >= how_many or not path.is_file():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
        except Exception:
            continue
        for one in (record if isinstance(record, list) else [record]):
            if not isinstance(one, dict):
                continue
            name = next((str(one[field]) for field in NAME_FIELDS if one.get(field)), "")
            for field in ("uid", "id", "guid", "_id", "objectId"):
                if one.get(field):
                    found[str(one[field])] = name
        read += 1
    return found


def main() -> None:
    print("\nWHERE ROASTIME KEEPS THINGS")
    print("=" * 72)
    places = roots()
    if not places:
        print("No RoasTime folder found under ~/Library/Application Support.")
        print("If RoasTime is installed, find it with:")
        print('  ls ~/Library/Application\\ Support | grep -i roast')
        return

    for place in places:
        print(f"\n{place}")
        for line in describe_folder(place):
            print("  " + line)

    main_root = places[0]
    roasts = main_root / "roasts"

    print("\n\nWHAT IS IN THE FILES")
    print("=" * 72)
    for folder in sorted(p for p in main_root.iterdir() if p.is_dir()):
        keys, names = sample_json(folder)
        if not keys:
            continue
        print(f"\n{folder.name}/")
        print("  fields: " + ", ".join(keys))
        if names:
            print("  names:  " + " · ".join(names))

    if not roasts.is_dir():
        print(f"\nNo roasts folder at {roasts} — nothing to cross-check.")
        return

    print("\n\nWHAT ONE ROAST ACTUALLY SAYS")
    print("=" * 72)
    print("Every field of a roast that is a single value — this is where a link to a")
    print("recipe would be, if RoasTime records one at all.\n")
    for key, value in sorted(scalar_keys(roasts).items()):
        print(f"  {key:34s} {value}")

    print("\n\nAND WHAT ONE ROAST KEEPS IN OBJECTS AND LISTS")
    print("=" * 72)
    print("If a recipe is written onto the roast as an object, its name is already here.\n")
    for key, value in sorted(nested_shape(roasts).items()):
        print(f"  {key:34s} {value}")

    print("\n\nDO THE IDS LINE UP?")
    print("=" * 72)

    # Every id in every folder, so a roast's id can be matched exactly rather
    # than by searching for its text inside files.
    catalogue = {}
    for folder in sorted(p for p in main_root.iterdir() if p.is_dir()):
        known = ids_in_folder(folder)
        if known:
            catalogue[folder.name] = known
    print("ids on file: " + ", ".join(f"{name} {len(ids)}" for name, ids in catalogue.items()))

    print("\nWHICH FIELD IS THE LINK — every value on a roast, against every record")
    print("-" * 72)
    for line in which_field_links(roasts, catalogue):
        print(line)
    print("-" * 72)
    print("A roast with no line under it for recipes is the whole answer to \"why is the")
    print("recipe name blank\": RoasTime is not recording which recipe the roast ran.\n")

    links = roast_links(roasts)
    if not links:
        print("Your roasts carry no bean, recipe or machine ids at all.")
        print("Nothing can be linked to them, by this app or any other.")
        return

    for field, ids in links.items():
        print(f"\n{field}: {len(ids)} distinct value(s) across your roasts")
        for value in sorted(ids)[:3]:
            print(f"    {value}")
        if len(ids) > 3:
            print(f"    …and {len(ids) - 3} more")

        # First the exact answer: does a file in some folder carry this id?
        matched = False
        for name, known in catalogue.items():
            hits = [value for value in ids if value in known]
            if not hits:
                continue
            matched = True
            example = known.get(hits[0]) or "(no name in the record)"
            print(f"    {len(hits)} of {len(ids)} are records in  {name}/   "
                  f"e.g. {hits[0][:12]}… = {example}")
        if matched:
            continue

        print("    No file in any folder has these as its own id.")
        print("    Searching the raw bytes for where they do appear…")
        for label, count in sorted(hunt(ids, main_root).items(),
                                   key=lambda pair: -pair[1])[:4]:
            print(f"      {count:3d} of them inside  {label}")
        print("    If they only appear in indexes/ or IndexedDB, the record itself is")
        print("    not on this Mac as a file — it lives in your Roast.World account.")

    print("\n" + "=" * 72)
    print("Paste this whole output back to Roast Coach and it will say what to build.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
