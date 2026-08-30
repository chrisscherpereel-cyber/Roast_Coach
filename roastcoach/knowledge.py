"""
What the app has read, and what it is entitled to say from it.

`library/` holds six sources: three roasting texts, one on processing, World
Coffee Research's Sensory Lexicon and their variety catalogue. This module reads
that folder — it is not compiled into the code, so adding a source is dropping a
file in beside the others and adding a line to `index.json`.

Two things it is careful about.

**What a source does not say.** Every entry carries a `silent_on` list, and it
earns its place: not one of the three roasting texts mentions rate of rise, the
crash or the flick. The app has plenty to say about all three, and it should say
where that comes from — practitioner convention — rather than borrowing the
authority of a book that never mentions it.

**Whose words these are.** The findings files are our own summaries with page
citations. The lexicon's *definitions* are World Coffee Research's writing and
are not in the repository; :func:`definitions` finds them if the roaster has
imported their own copy, and the app does without them if not.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# What this file can do — see the note in store.py.
#   1  reads library/, offers sources, findings, the lexicon and the varieties
VERSION = 1

HERE = Path(__file__).resolve().parent
LIBRARY = HERE.parent / "library"

# Where the roaster's own copy of the lexicon definitions may be, if they have
# imported one. Kept out of the repository on purpose — see library/README.md.
DEFINITIONS = (
    HERE.parent / "private" / "wcr-lexicon-definitions.json",
    Path.home() / ".roastcoach" / "wcr-lexicon-definitions.json",
)


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


@lru_cache(maxsize=1)
def shelf() -> list[dict]:
    """Every source, as `index.json` describes it. Empty if the folder is absent."""
    found = _read(LIBRARY / "index.json")
    return list(found.get("sources", [])) if isinstance(found, dict) else []


def source(source_id: str) -> dict:
    return next((item for item in shelf() if item.get("id") == source_id), {})


def text_of(source_id: str) -> str:
    """The findings file for one source, as written."""
    item = source(source_id)
    if not item.get("file"):
        return ""
    try:
        return (LIBRARY / item["file"]).read_text(encoding="utf-8")
    except OSError:
        return ""


def covering(topic: str) -> list[dict]:
    """Sources that speak to a topic — and, separately, those that pointedly do not.

    Asking "what does the library say about the crash" and getting *nothing* is a
    real answer, and one the app should be able to give.
    """
    topic = str(topic or "").strip().lower()
    return [item for item in shelf()
            if any(topic in str(word).lower() for word in item.get("covers", []))]


def silent_on(topic: str) -> list[dict]:
    topic = str(topic or "").strip().lower()
    return [item for item in shelf()
            if any(topic in str(word).lower() for word in item.get("silent_on", []))]


def cite(source_id: str) -> str:
    """One line naming a source, the way it would be cited on screen."""
    item = source(source_id)
    if not item:
        return ""
    parts = [item.get("author", ""), f"({item['year']})" if item.get("year") else "",
             item.get("title", "")]
    return " ".join(part for part in parts if part).strip()


# ---------------------------------------------------------------------------
# The sensory lexicon
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def lexicon() -> dict:
    """WCR's flavour vocabulary: 109 attributes in 17 categories.

    Names, categories and reference intensities — the parts that are facts about
    a measuring instrument. Definitions arrive separately if the roaster has
    imported their own copy.
    """
    found = _read(LIBRARY / "sensory" / "wcr-sensory-lexicon-vocabulary.json")
    return found if isinstance(found, dict) else {"attributes": [], "categories": []}


@lru_cache(maxsize=1)
def definitions() -> dict:
    """`{attribute: definition}` from the roaster's own copy, or nothing.

    World Coffee Research reserve all rights over the Sensory Lexicon, so its
    definitions are not in this repository. `tools/import_lexicon.py` reads the
    PDF they publish free and writes them somewhere private; if that has been
    done, every attribute in the app carries its definition, and if not, the
    vocabulary works without them.
    """
    for path in DEFINITIONS:
        found = _read(path)
        if isinstance(found, dict) and found.get("definitions"):
            return dict(found["definitions"])
    return {}


def flavours(category: str | None = None) -> list[dict]:
    """The attributes, optionally in one category, each with its definition if we have it."""
    known = definitions()
    found = []
    for item in lexicon().get("attributes", []):
        if category and item.get("category") != category:
            continue
        found.append({**item, "definition": known.get(item.get("name"), "")})
    return found


def categories() -> list[str]:
    return list(lexicon().get("categories", []))


def describe(flavour: str) -> str:
    """What one attribute means, if the roaster has imported the definitions."""
    return definitions().get(str(flavour).strip(), "")


# ---------------------------------------------------------------------------
# The varieties
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def varieties() -> list[dict]:
    """World Coffee Research's catalogue: 117 varieties, Arabica and Robusta."""
    found = _read(LIBRARY / "varieties" / "wcr-arabica-and-robusta-varieties.json")
    return list(found.get("varieties", [])) if isinstance(found, dict) else []


def variety(name: str) -> dict:
    """One variety by name, matched loosely enough to survive how people type them."""
    wanted = str(name or "").strip().lower()
    if not wanted:
        return {}
    for item in varieties():
        if str(item.get("name", "")).strip().lower() == wanted:
            return item
    for item in varieties():
        names = [item.get("name", "")] + list(item.get("also_known_as") or [])
        if any(wanted == str(other).strip().lower() for other in names):
            return item
    return {}


def variety_names() -> list[str]:
    return sorted({str(item.get("name")) for item in varieties() if item.get("name")})


def variety_summary(name: str) -> str:
    """A variety in one line, for the bean record — or nothing if we do not hold it."""
    item = variety(name)
    if not item:
        return ""
    parts = []
    if item.get("genetic_group"):
        parts.append(str(item["genetic_group"]))
    if item.get("stature"):
        parts.append(f"{str(item['stature']).lower()} stature")
    if item.get("bean_size"):
        parts.append(f"{str(item['bean_size']).lower()} bean")
    if item.get("optimal_altitude"):
        parts.append(f"{str(item['optimal_altitude']).lower()} altitude")
    if item.get("quality_potential_at_high_altitude"):
        parts.append(f"quality potential {str(item['quality_potential_at_high_altitude']).lower()} "
                     "at high altitude")
    return " · ".join(parts)


def rights() -> list[dict]:
    """What may be done with each source — shown wherever the library is."""
    return [{"source": cite(item["id"]) or item.get("title", ""),
             "terms": item.get("rights", "")}
            for item in shelf() if item.get("rights")]
