"""
Import a RoasTime CSV roast export.

RoasTime can export a single roast as a CSV "roast report": a few metadata rows,
a Milestones block (yellowing, first crack, second crack), then a Timeline block
with one row per sample::

    CRT Aug 22, 2026
    22/08/2026
    Costa Rica La Minita Tarrazu RFA

    Milestones
    1st Crack,Start,,,End
    ,Index,Time,,Index,Time
    ,1012,8:26,,-,-

    Yellowing,Start index,Start time
    ,526,4:23

    Timeline,Index,Time,IBTS Temp,Bean Probe Temp,IBTS ROR,Bean Probe ROR,Power Setting,Drum Setting,Fan Setting
    ,0,0:00,276.93,193.86,66.10,-10.04,6,9,2

The parser turns one of these into the same dictionary shape as a RoasTime JSON
roast file, so everything downstream -- field extraction, curves, charts, CSV
export -- works on CSV imports without knowing where the roast came from.

Two details worth knowing about the format:

* Milestone indices are counted at the machine's full sample rate, while the
  Timeline rows are often written at a lower rate (in the example above,
  1st crack index 1012 lands at 8:26 = 506 s, i.e. 2 samples/second, but the
  Timeline advances 1 index per second). The milestone *times* are therefore
  what we trust, and event positions are recomputed from them.
* The export carries RoasTime's own rate-of-rise columns, which are better than
  anything we can recover by differencing 1 Hz temperatures, so they are kept.
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import io
import re
from typing import Any

from .fields import codes_by_control

TIMELINE_MARKER = "timeline"

# Timeline columns, matched loosely against the header text.
TIMELINE_COLUMNS = [
    ("index", lambda h: h == "index"),
    ("time", lambda h: h == "time"),
    ("drumTemperature", lambda h: "ibts" in h and "ror" not in h),
    ("beanTemperature", lambda h: "bean" in h and "ror" not in h),
    ("drumDerivative", lambda h: "ibts" in h and "ror" in h),
    ("beanDerivative", lambda h: "bean" in h and "ror" in h),
    ("power", lambda h: "power" in h),
    ("drum", lambda h: "drum" in h and "ibts" not in h),
    ("fan", lambda h: "fan" in h),
]

# Metadata rows sometimes carried above the timeline, as "label,value".
METADATA_LABELS = [
    ("weightGreen", ("green weight", "weight green", "green in", "charge weight", "green")),
    ("weightRoasted", ("roasted weight", "weight roasted", "roasted out", "roasted")),
    ("ambient", ("ambient", "room temp", "room temperature")),
    ("humidity", ("humidity", "room humidity")),
    ("preheatTemperature", ("preheat",)),
    ("rating", ("rating", "score")),
    ("roastNumber", ("roast number", "roast #")),
    ("serialNumber", ("serial",)),
    ("firmware", ("firmware",)),
]

# Milestone blocks, and the roast fields they populate.
MILESTONES = [
    (("1st crack", "first crack"), "indexFirstCrackStart", "indexFirstCrackEnd"),
    (("2nd crack", "2st crack", "second crack"), "indexSecondCrackStart", "indexSecondCrackEnd"),
    (("yellowing", "drying end"), "indexYellowingStart", None),
]

_MONTH_DATE = re.compile(
    r"([A-Z][a-z]{2,8})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})|(\d{1,2})\s+([A-Z][a-z]{2,8})\.?,?\s+(\d{4})"
)


class RoastCsvError(ValueError):
    """The file is not a RoasTime CSV roast export."""


# ---------------------------------------------------------------------------
# Small parsing helpers
# ---------------------------------------------------------------------------


def _clean(cell: Any) -> str:
    return str(cell or "").strip()


def _number(cell: Any):
    text = _clean(cell).replace(",", "")
    if text in ("", "-", "--", "n/a", "N/A", "None"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _seconds(cell: Any):
    """'8:26' or '1:08:26' -> seconds."""
    text = _clean(cell)
    if text in ("", "-", "--"):
        return None
    if ":" not in text:
        return _number(text)
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def _parse_date(rows: list[list[str]]):
    """Find a roast date in the metadata rows above the timeline."""
    text_cells = [_clean(cell) for row in rows for cell in row if _clean(cell)]

    for text in text_cells:
        match = _MONTH_DATE.search(text)
        if match:
            month_name, day, year = (match.group(1), match.group(2), match.group(3))
            if not month_name:
                day, month_name, year = (match.group(4), match.group(5), match.group(6))
            for fmt in ("%b %d %Y", "%B %d %Y"):
                try:
                    return datetime.datetime.strptime(f"{month_name[:3]} {day} {year}", "%b %d %Y")
                except ValueError:
                    continue

    for text in text_cells:
        for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
            try:
                parsed = datetime.datetime.strptime(text, fmt)
            except ValueError:
                continue
            # dd/mm and mm/dd are indistinguishable when both parts are <= 12;
            # RoasTime writes day first, which is what the format order reflects.
            return parsed
    return None


def _find_timeline_header(rows: list[list[str]]) -> int:
    for i, row in enumerate(rows):
        if _clean(row[0]).lower() == TIMELINE_MARKER and any("temp" in _clean(c).lower() for c in row):
            return i
    # Fall back to a header row that names the temperature columns.
    for i, row in enumerate(rows):
        lowered = [_clean(c).lower() for c in row]
        if any("ibts" in c for c in lowered) and any("time" == c for c in lowered):
            return i
    raise RoastCsvError("no Timeline section found (expected a row starting with 'Timeline')")


def _map_timeline_columns(header: list[str]) -> dict[str, int]:
    lowered = [_clean(c).lower() for c in header]
    mapping: dict[str, int] = {}
    for name, matches in TIMELINE_COLUMNS:
        for position, text in enumerate(lowered):
            if position in mapping.values() or not text:
                continue
            if matches(text):
                mapping[name] = position
                break
    if "beanTemperature" not in mapping and "drumTemperature" not in mapping:
        raise RoastCsvError("the Timeline section has no temperature columns")
    return mapping


def _milestone_times(rows: list[list[str]], limit: int) -> dict[str, float]:
    """Pull '(start, end)' seconds out of each milestone block above the timeline."""
    found: dict[str, float] = {}

    for i in range(min(limit, len(rows))):
        label = _clean(rows[i][0]).lower()
        if not label:
            continue
        match = next((m for m in MILESTONES if any(label.startswith(n) for n in m[0])), None)
        if match is None:
            continue
        _, start_field, end_field = match

        # The sub-header naming Index/Time is either this row or the next one;
        # the values sit on the first row after it.
        for header_offset in (0, 1):
            header_row = rows[i + header_offset] if i + header_offset < len(rows) else []
            time_columns = [j for j, cell in enumerate(header_row) if "time" in _clean(cell).lower()]
            if not time_columns:
                continue
            value_row = rows[i + header_offset + 1] if i + header_offset + 1 < len(rows) else []
            times = [_seconds(value_row[j]) if j < len(value_row) else None for j in time_columns]
            if times and times[0] is not None:
                found[start_field] = times[0]
            if end_field and len(times) > 1 and times[1] is not None:
                found[end_field] = times[1]
            break

    return found


def _metadata_values(rows: list[list[str]], limit: int) -> dict[str, float]:
    values: dict[str, float] = {}
    for row in rows[:limit]:
        label = _clean(row[0]).lower().rstrip(":")
        if not label:
            continue
        value = next((_number(cell) for cell in row[1:] if _number(cell) is not None), None)
        if value is None:
            continue
        for field, names in METADATA_LABELS:
            if field in values:
                continue
            if any(name in label for name in names):
                values[field] = value
                break
    return values


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def looks_like_roast_csv(text: str) -> bool:
    head = text[:8000].lower()
    return "timeline" in head and ("ibts" in head or "bean probe" in head)


def parse_roast_csv(text: str, source_name: str = "roast.csv") -> dict:
    """Parse a RoasTime CSV export into a roast dictionary shaped like the JSON files."""
    rows = [row for row in csv.reader(io.StringIO(text))]
    rows = [row + [""] * (10 - len(row)) if len(row) < 10 else row for row in rows]
    if not rows:
        raise RoastCsvError("the file is empty")

    header_index = _find_timeline_header(rows)
    columns = _map_timeline_columns(rows[header_index])

    # --- timeline ---------------------------------------------------------
    series: dict[str, list] = {name: [] for name in columns}
    for row in rows[header_index + 1:]:
        time_cell = row[columns["time"]] if "time" in columns else ""
        index_cell = row[columns["index"]] if "index" in columns else ""
        if _seconds(time_cell) is None and _number(index_cell) is None:
            if any(_clean(cell) for cell in row):
                continue  # a stray label row inside the timeline
            if series.get("time") or series.get("index"):
                break  # blank line after the data
            continue
        for name, position in columns.items():
            cell = row[position] if position < len(row) else ""
            series[name].append(_seconds(cell) if name == "time" else _number(cell))

    sample_count = max((len(v) for v in series.values()), default=0)
    if sample_count < 3:
        raise RoastCsvError("the Timeline section has no sample rows")

    # --- sample rate ------------------------------------------------------
    times = [t for t in series.get("time", []) if t is not None]
    interval = 1.0
    if len(times) > 2:
        steps = sorted(round(b - a, 3) for a, b in zip(times, times[1:]) if b > a)
        if steps:
            interval = steps[len(steps) // 2]
    sample_rate = 1.0 / interval if interval else 1.0

    # --- controls ---------------------------------------------------------
    actions = []
    for control, code in codes_by_control.items():
        values = series.get(control) or []
        previous = None
        for position, value in enumerate(values):
            if value is None or value == previous:
                continue
            actions.append({"ctrlType": code, "index": position, "value": value})
            previous = value
    actions.sort(key=lambda action: (action["index"], action["ctrlType"]))

    # --- events -----------------------------------------------------------
    metadata_rows = rows[:header_index]
    milestones = _milestone_times(rows, header_index)
    roast = {field: int(round(seconds / interval)) for field, seconds in milestones.items()}

    # --- identity ---------------------------------------------------------
    when = _parse_date(metadata_rows)
    labels = [_clean(row[0]) for row in metadata_rows if _clean(row[0]) and not _clean(row[1])]
    labels = [text for text in labels if text.lower() not in ("milestones", "timeline")]
    title = labels[0] if labels else source_name
    bean = next((text for text in labels[1:] if not _parse_date([[text]])), None)

    digest = hashlib.sha1(f"{source_name}|{title}|{when}|{sample_count}".encode()).hexdigest()[:12]

    bean_temperature = series.get("beanTemperature") or []
    drum_temperature = series.get("drumTemperature") or []

    roast.update(
        {
            "uid": f"csv-{digest}",
            "guid": f"csv-{digest}",
            "dateTime": int(when.timestamp() * 1000) if when else None,
            "roastName": bean or title,
            "beanId": bean or "",
            "sampleRate": sample_rate,
            "roastStartIndex": 0,
            "roastEndIndex": sample_count - 1,
            "totalRoastTime": (sample_count - 1) * interval,
            "beanTemperature": bean_temperature,
            "drumTemperature": drum_temperature,
            "beanDerivative": series.get("beanDerivative") or [],
            "drumDerivative": series.get("drumDerivative") or [],
            "actions": {"actionTimeList": actions},
            "importedFrom": "csv",
            "sourceName": source_name,
            "roastTitle": title,
        }
    )

    roast.setdefault("indexYellowingStart", 0)
    roast.setdefault("indexFirstCrackStart", 0)
    roast.setdefault("indexFirstCrackEnd", 0)
    roast.setdefault("indexSecondCrackStart", 0)
    roast.setdefault("indexSecondCrackEnd", 0)

    if bean_temperature:
        roast["beanChargeTemperature"] = bean_temperature[0]
        roast["beanDropTemperature"] = bean_temperature[-1]
    if drum_temperature:
        roast["drumChargeTemperature"] = drum_temperature[0]
        roast["drumDropTemperature"] = drum_temperature[-1]

    roast.update(_metadata_values(rows, header_index))
    return roast


def read_roast_csv(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig", errors="replace") as csv_file:
        text = csv_file.read()
    import os

    return parse_roast_csv(text, os.path.basename(path))
