"""
Roast field extraction for Aillio RoasTime JSON files.

This is a refactor of the original ``dump_roasts.py`` script: the same field
definitions and mapping logic, but importable, exception-safe, and free of any
command-line / stdout assumptions so it can be reused by the Streamlit app and
by the CLI alike.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Any, Callable

# ---------------------------------------------------------------------------
# RoasTime control codes
# ---------------------------------------------------------------------------

codes_by_control: dict[str, int] = {
    "power": 0,
    "fan": 1,
    "drum": 2,
}

controls_by_code: dict[int, str] = {v: k for k, v in codes_by_control.items()}


# Per-sample time series stored in every roast file.
roast_sample_fields = [
    "beanDerivative",
    "beanTemperature",
    "drumTemperature",
]


# ---------------------------------------------------------------------------
# Field mapping helpers
# ---------------------------------------------------------------------------


def make_get_sample(index_field: str) -> Callable[[dict, str], Any]:
    """Return a function that reads a sampled series at the given index field."""

    def get_sample(roast_json: dict, source_field: str):
        try:
            samples = roast_json[source_field]
            if not samples:
                return None
            index = min(int(roast_json[index_field]), len(samples) - 1)
            if index < 0:
                return None
            return samples[index]
        except Exception:
            return None

    return get_sample


def make_get_control(control: str) -> Callable[[dict, str], Any]:
    """Return a function that reads a control setting at the given index field."""

    control_code = codes_by_control[control]

    def get_control(roast_json: dict, source_field: str):
        current_value = None
        try:
            index_value = roast_json[source_field]
            action_times = roast_json["actions"]["actionTimeList"]
            for action in action_times:
                if action.get("ctrlType") != control_code:
                    continue
                if action.get("index", 0) > index_value:
                    return current_value
                current_value = action.get("value")
            return current_value
        except Exception:
            return None

    return get_control


def make_conversion(conversion_type: Callable[[Any], Any]) -> Callable[[dict, str], Any]:
    """Coerce a roast field to a specific type, tolerating junk values."""

    def conversion(roast_json: dict, source_field: str):
        value = roast_json.get(source_field)
        if value is None or value == "":
            return None
        try:
            return conversion_type(value)
        except (TypeError, ValueError):
            return None

    return conversion


def seconds_from_index(roast_json: dict, source_field: str):
    """Convert a sample index to a time value in seconds."""
    try:
        sample_rate = roast_json.get("sampleRate")
        if not sample_rate:
            return None
        return roast_json[source_field] / sample_rate
    except Exception:
        return None


def _timestamp_part(fmt: str) -> Callable[[dict, str], Any]:
    def to_part(roast_json: dict, source_field: str):
        try:
            millis = roast_json[source_field]
            return datetime.datetime.fromtimestamp(millis / 1000).strftime(fmt)
        except Exception:
            return None

    return to_part


# ---------------------------------------------------------------------------
# Field table
# ---------------------------------------------------------------------------

roast_fields: list = [
    {"fields": ["dateTime"], "mapped_field": ("date", _timestamp_part("%Y-%m-%d"))},
    {"fields": ["dateTime"], "mapped_field": ("time", _timestamp_part("%H:%M:%S"))},
    "dateTime",
    "uid",
    "roastNumber",
    "roastName",
    "beanId",
    "rating",
    "serialNumber",
    "firmware",
    "hardware",
    {"fields": ["ambient", "ambientTemp"], "mapped_field": ("ambient", make_conversion(float))},
    {"fields": ["humidity", "roomHumidity"], "mapped_field": ("humidity", make_conversion(float))},
    {"fields": ["weightGreen"], "mapped_field": ("weightGreen", make_conversion(float))},
    {"fields": ["weightRoasted"], "mapped_field": ("weightRoasted", make_conversion(float))},
    "preheatTemperature",
    "beanChargeTemperature",
    "beanDropTemperature",
    "drumChargeTemperature",
    "drumDropTemperature",
    "totalRoastTime",
    "sampleRate",
    "roastStartIndex",
    "indexYellowingStart",
    "indexFirstCrackStart",
    "indexFirstCrackEnd",
    "indexSecondCrackStart",
    "indexSecondCrackEnd",
    "roastEndIndex",
]


# RoasTime is inconsistent about where the event name sits in the field name:
# ``roastStartIndex`` but ``indexFirstCrackStart``.
events: list[tuple[str, bool]] = [
    ("roastStart", True),
    ("roastEnd", True),
    ("YellowingStart", False),
    ("FirstCrackStart", False),
    ("FirstCrackEnd", False),
    ("SecondCrackStart", False),
    ("SecondCrackEnd", False),
]


def get_event_field(event_name: str, prepend: bool, field: str) -> str:
    return f"{event_name}{field.capitalize()}" if prepend else f"{field}{event_name}"


# Derived per-event columns: time in seconds, control settings, sampled values.
for _event_name, _prepend in events:
    _source_field = get_event_field(_event_name, _prepend, "index")

    _destination_field = get_event_field(_event_name, _prepend, "seconds")
    roast_fields.append(
        {"fields": [_source_field], "mapped_field": (_destination_field, seconds_from_index)}
    )

    for _control in codes_by_control:
        _destination_field = get_event_field(_event_name, _prepend, _control)
        roast_fields.append(
            {"fields": [_source_field], "mapped_field": (_destination_field, make_get_control(_control))}
        )

    for _roast_sample_field in roast_sample_fields:
        _destination_field = get_event_field(_event_name, _prepend, _roast_sample_field)
        roast_fields.append(
            {
                "fields": [_roast_sample_field],
                "mapped_field": (_destination_field, make_get_sample(_source_field)),
            }
        )


# ---------------------------------------------------------------------------
# Roast record construction
# ---------------------------------------------------------------------------


def set_roast_column(roast_json: dict, roast_columns: dict, roast_field, warnings: list[str] | None = None) -> None:
    if isinstance(roast_field, dict) and "mapped_field" in roast_field:
        mapped_field, mapping_fn = roast_field["mapped_field"]
        if "fields" in roast_field:
            for field in roast_field["fields"]:
                if field in roast_json:
                    roast_columns[mapped_field] = mapping_fn(roast_json, field)
                    return
        else:
            roast_columns[mapped_field] = mapping_fn(roast_json, None)
            return

        if warnings is not None:
            warnings.append(f"no source data for {mapped_field}")
        roast_columns[mapped_field] = None
        return

    roast_columns[roast_field] = roast_json.get(roast_field, None)


def create_roast(roast_json: dict, warnings: list[str] | None = None) -> dict:
    """Flatten one roast JSON document into a single row of scalar values."""
    roast: dict[str, Any] = {}
    for roast_field in roast_fields:
        try:
            set_roast_column(roast_json, roast, roast_field, warnings)
        except Exception as exc:  # pragma: no cover - defensive
            name = roast_field["mapped_field"][0] if isinstance(roast_field, dict) else roast_field
            if warnings is not None:
                warnings.append(f"{name}: {exc}")
            roast[name] = None
    return roast


def get_fields() -> list[str]:
    """All column names this module can produce, in table order."""
    return [f if not isinstance(f, dict) else f["mapped_field"][0] for f in roast_fields]


DEFAULT_CSV_FIELDS = ["date", "time", "beanId", "weightGreen"]


# ---------------------------------------------------------------------------
# Locating roast files
# ---------------------------------------------------------------------------


def list_roast_files(roast_dir: str) -> list[str]:
    """Every readable roast file in a directory, sorted by name."""
    if not roast_dir or not os.path.isdir(roast_dir):
        return []
    paths = []
    for name in sorted(os.listdir(roast_dir)):
        path = os.path.join(roast_dir, name)
        if not os.path.isfile(path):
            continue
        if name.startswith("."):
            continue
        paths.append(path)
    return paths


def read_roast_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as roast_file:
        return json.load(roast_file)


def parse_roast_text(text: str, name: str = "roast") -> dict:
    """Parse one roast from text held in memory.

    The app never touches a filesystem of its own: roasts arrive as bytes from a
    browser folder or an upload, and this is where they become roast data.
    """
    from .csv_import import RoastCsvError, looks_like_roast_csv, parse_roast_csv

    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        return json.loads(text)
    if looks_like_roast_csv(text):
        return parse_roast_csv(text, name)
    raise RoastCsvError(
        f"{name}: not a roast file (expected a RoasTime roast, or a CSV export "
        "with a Timeline section)"
    )


def read_roast_file(path: str) -> dict:
    """Read one roast, whether it is a RoasTime JSON file or a CSV export.

    Both come back in the same dictionary shape, so nothing downstream has to
    know which kind of file it started as.
    """
    with open(path, "r", encoding="utf-8-sig", errors="replace") as roast_file:
        text = roast_file.read()

    if text.lstrip()[:1] in ("{", "["):
        return json.loads(text)

    from .csv_import import RoastCsvError, looks_like_roast_csv, parse_roast_csv

    if looks_like_roast_csv(text):
        return parse_roast_csv(text, os.path.basename(path))

    raise RoastCsvError(
        "not a roast file (expected RoasTime JSON, or a CSV export with a Timeline section)"
    )
