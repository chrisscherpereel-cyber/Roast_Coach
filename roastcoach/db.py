"""
Where Roast Coach keeps things, and how it talks to it.

The app runs on more than one computer, so the database cannot live on any of
them. Point ``ROAST_COACH_DATABASE_URL`` (or ``[database] url`` in Streamlit
secrets) at a Postgres server and every machine sees the same roasts. With
nothing configured it falls back to a SQLite file beside the app, which is what
the tests and a single-machine setup use.

The same SQL runs on both. Two rules keep it that way:

* every statement uses named parameters (``:name``), never ``?`` or ``%s``
* the handful of statements that genuinely differ between the two -- auto
  incrementing keys, mostly -- are chosen in :func:`schema` by dialect

Nothing here knows about roasts. That is :mod:`roastcoach.store`.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

_engines: dict[str, Engine] = {}
_lock = threading.Lock()
_ready: set[str] = set()

DEFAULT_SQLITE = Path(__file__).resolve().parent.parent / "roast_coach.db"


# ---------------------------------------------------------------------------
# Which database
# ---------------------------------------------------------------------------


def _secret(*names: str) -> str | None:
    """A Streamlit secret, without requiring Streamlit to be running."""
    try:
        import streamlit as st

        section = st.secrets
        for name in names[:-1]:
            section = section[name]
        return str(section[names[-1]])
    except Exception:
        return None


def normalise(url: str) -> str:
    """Accept the URL a Postgres host hands you and make it one SQLAlchemy takes.

    Supabase and Heroku both print ``postgres://…``, which SQLAlchemy dropped
    support for. Postgres over the open internet should also insist on TLS.
    """
    if url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    if url.startswith("postgresql+psycopg2://") and "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def database_url(override: str | None = None) -> str:
    """The database to use, in order of precedence.

    ``override`` is what callers pass as ``path``: a full URL, or a SQLite file.
    """
    if override:
        return normalise(override) if "://" in override else f"sqlite:///{override}"

    for value in (os.environ.get("ROAST_COACH_DATABASE_URL"),
                  _secret("database", "url"),
                  _secret("DATABASE_URL")):
        if value:
            return normalise(value)

    sqlite_path = os.environ.get("ROAST_COACH_DB") or str(DEFAULT_SQLITE)
    return f"sqlite:///{sqlite_path}"


def is_shared(override: str | None = None) -> bool:
    """True when roasts are kept somewhere every computer can reach."""
    return not database_url(override).startswith("sqlite")


def describe(override: str | None = None) -> str:
    """The database, said in a way that is safe to put on screen."""
    url = database_url(override)
    if url.startswith("sqlite"):
        return f"SQLite file — {Path(url.replace('sqlite:///', '')).name}"
    host = url.split("@")[-1].split("/")[0].split("?")[0]
    return f"Postgres — {host}"


# ---------------------------------------------------------------------------
# Talking to it
# ---------------------------------------------------------------------------


def engine(override: str | None = None) -> Engine:
    url = database_url(override)
    with _lock:
        if url not in _engines:
            if url.startswith("sqlite"):
                Path(url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
                made = create_engine(url, future=True,
                                     connect_args={"timeout": 30, "check_same_thread": False})
            else:
                # Streamlit reruns constantly and Postgres hosts drop idle
                # connections; pre-ping so a stale one is replaced, not raised.
                made = create_engine(url, future=True, pool_pre_ping=True,
                                     pool_size=3, max_overflow=2, pool_recycle=280)
            _engines[url] = made
        return _engines[url]


def dialect(override: str | None = None) -> str:
    return engine(override).dialect.name


def schema(name: str) -> list[str]:
    """The table definitions, in the dialect's own words."""
    serial = ("INTEGER PRIMARY KEY AUTOINCREMENT" if name == "sqlite"
              else "BIGSERIAL PRIMARY KEY")
    return [
        # One row per roast. Everything that varies by RoasTime version lives in
        # `data` as JSON, so importing a file with new fields never alters the
        # table -- which matters when two people import at once.
        """CREATE TABLE IF NOT EXISTS roasts (
               uid TEXT PRIMARY KEY,
               date TEXT,
               date_time DOUBLE PRECISION,
               coffee_guess TEXT,
               source_name TEXT,
               content_hash TEXT,
               imported_at TEXT,
               imported_by TEXT,
               data TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS roasts_date ON roasts (date)",

        # One row per roast, not per sample: the curve is a compressed frame of
        # the same nine columns. Over a network that is one round trip instead
        # of fifteen hundred. load_curve() hands back the per-sample table.
        """CREATE TABLE IF NOT EXISTS roast_curve (
               roast_id TEXT PRIMARY KEY,
               samples INTEGER NOT NULL,
               payload TEXT NOT NULL)""",

        # What the roaster adds. The last five are the things no probe can see —
        # measured colour, batch spread, quakers, and what the beans looked like —
        # and they are added to existing databases by ensure_columns() below,
        # because CREATE TABLE IF NOT EXISTS will not alter a table that is there.
        """CREATE TABLE IF NOT EXISTS roast_notes (
               roast_id TEXT PRIMARY KEY,
               coffee TEXT, origin TEXT, process TEXT, variety TEXT, farm TEXT,
               green_weight DOUBLE PRECISION, roast_level TEXT, notes TEXT,
               rating DOUBLE PRECISION, cupping_score DOUBLE PRECISION,
               is_reference INTEGER DEFAULT 0, roasted_by TEXT,
               colour_whole DOUBLE PRECISION, colour_ground DOUBLE PRECISION,
               colour_sd DOUBLE PRECISION, quaker_count DOUBLE PRECISION,
               visual_defects TEXT, roasted_weight DOUBLE PRECISION,
               agtron_commercial DOUBLE PRECISION, agtron_gourmet DOUBLE PRECISION,
               probat_colorette DOUBLE PRECISION, colortrack DOUBLE PRECISION,
               colortrack_ground DOUBLE PRECISION, colour_prepared_on TEXT,
               updated_at TEXT, updated_by TEXT)""",

        # A cup risk the app raised, and what the cupping table said about it.
        # This is where a hypothesis becomes an observation, or stops being one.
        """CREATE TABLE IF NOT EXISTS sensory (
               key TEXT PRIMARY KEY,
               roast_id TEXT NOT NULL, condition_id TEXT NOT NULL,
               verdict TEXT, note TEXT,
               recorded_at TEXT, recorded_by TEXT)""",
        "CREATE INDEX IF NOT EXISTS sensory_roast ON sensory (roast_id)",

        f"""CREATE TABLE IF NOT EXISTS recommendations (
               id {serial},
               roast_id TEXT NOT NULL, coffee TEXT, rule_id TEXT NOT NULL,
               created_at TEXT NOT NULL,
               headline TEXT, finding TEXT, action TEXT, reason TEXT,
               target_metric TEXT,
               current_value DOUBLE PRECISION, predicted_value DOUBLE PRECISION,
               direction TEXT, confidence DOUBLE PRECISION, basis TEXT,
               status TEXT DEFAULT 'open',
               moves TEXT,
               applied_roast_id TEXT, observed_value DOUBLE PRECISION, outcome TEXT,
               evaluated_at TEXT, note TEXT)""",
        "CREATE INDEX IF NOT EXISTS recommendation_roast ON recommendations (roast_id)",

        """CREATE TABLE IF NOT EXISTS effects (
               key TEXT PRIMARY KEY, control TEXT, phase TEXT, metric TEXT,
               slope DOUBLE PRECISION, observations INTEGER,
               spread DOUBLE PRECISION, updated_at TEXT)""",

        """CREATE TABLE IF NOT EXISTS rule_stats (
               rule_id TEXT PRIMARY KEY, suggested INTEGER DEFAULT 0,
               applied INTEGER DEFAULT 0, achieved INTEGER DEFAULT 0,
               partial INTEGER DEFAULT 0, missed INTEGER DEFAULT 0,
               mean_error DOUBLE PRECISION, updated_at TEXT)""",

        # What has been imported already, so a second click reads nothing twice.
        """CREATE TABLE IF NOT EXISTS sources (
               name TEXT PRIMARY KEY, roast_id TEXT,
               modified DOUBLE PRECISION, size INTEGER,
               content_hash TEXT, imported_at TEXT, imported_by TEXT)""",
        "CREATE INDEX IF NOT EXISTS sources_hash ON sources (content_hash)",

        # Everything RoasTime keeps beside a roast — the bean, the recipe, the
        # machine, the profile it was roasted from. One row per record, stored as
        # it arrived, so a RoasTime update that adds a field loses nothing.
        """CREATE TABLE IF NOT EXISTS reference (
               key TEXT PRIMARY KEY,
               kind TEXT NOT NULL,
               ref_id TEXT NOT NULL,
               name TEXT,
               updated_at TEXT,
               data TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS reference_kind ON reference (kind)",

        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
    ]


# Columns added to tables that already exist in databases created by an earlier
# version. CREATE TABLE IF NOT EXISTS does nothing to a table that is there, so
# without this a roaster who has been using the app since last week would find
# the new fields missing and every write failing.
ADDED_COLUMNS = {
    "recommendations": {
        # The advice as control moves: [{control, at, from, to, step, why}, …].
        "moves": "TEXT",
    },
    "roast_notes": {
        # RoasTime records a user id and no name, so who roasted it is typed.
        "roasted_by": "TEXT",
        # Colour read on ground coffee reads lighter than the same roast whole,
        # so the preparation is part of the measurement.
        "colortrack_ground": "DOUBLE PRECISION",
        "colour_prepared_on": "TEXT",
        "colour_whole": "DOUBLE PRECISION",
        "colour_ground": "DOUBLE PRECISION",
        "colour_sd": "DOUBLE PRECISION",
        "quaker_count": "DOUBLE PRECISION",
        "visual_defects": "TEXT",
        "roasted_weight": "DOUBLE PRECISION",
        # One column per scale rather than one "colour" number: roasters read
        # whichever meter they own, and the scales are not interchangeable.
        "agtron_commercial": "DOUBLE PRECISION",
        "agtron_gourmet": "DOUBLE PRECISION",
        "probat_colorette": "DOUBLE PRECISION",
        "colortrack": "DOUBLE PRECISION",
    },
}


def columns(table: str, override: str | None = None) -> set[str]:
    """The columns a table actually has, asked in each dialect's own way."""
    made = engine(override)
    with made.connect() as connection:
        if made.dialect.name == "sqlite":
            rows = connection.execute(text(f"PRAGMA table_info({table})")).fetchall()
            return {row[1] for row in rows}
        rows = connection.execute(
            text("SELECT column_name FROM information_schema.columns "
                 "WHERE table_name = :table"), {"table": table}).fetchall()
        return {row[0] for row in rows}


def ensure_columns(override: str | None = None) -> list[str]:
    """Add anything ADDED_COLUMNS lists that this database has not got yet."""
    added = []
    for table, wanted in ADDED_COLUMNS.items():
        try:
            present = columns(table, override)
        except Exception:
            continue
        missing = {name: kind for name, kind in wanted.items() if name not in present}
        if not missing:
            continue
        with engine(override).begin() as connection:
            for name, kind in missing.items():
                try:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {kind}"))
                    added.append(f"{table}.{name}")
                except Exception:          # a parallel process got there first
                    continue
    return added


def prepare(override: str | None = None) -> Engine:
    """Create the tables once per process, then get out of the way."""
    made = engine(override)
    url = str(made.url)
    if url in _ready:
        return made
    with _lock:
        if url in _ready:
            return made
        with made.begin() as connection:
            for statement in schema(made.dialect.name):
                connection.execute(text(statement))

    # Outside the lock on purpose: ensure_columns() asks the engine for the
    # tables it has, and engine() takes the same lock. Holding it here deadlocked
    # the whole app on the first query.
    ensure_columns(override)
    with _lock:
        _ready.add(url)
    return made


def run(statement: str, params: dict | list[dict] | None = None,
        override: str | None = None) -> None:
    """Run a statement (or the same statement over a list of parameter sets)."""
    with prepare(override).begin() as connection:
        connection.execute(text(statement), params or {})


def one(statement: str, params: dict | None = None, override: str | None = None):
    with prepare(override).connect() as connection:
        return connection.execute(text(statement), params or {}).fetchone()


def rows(statement: str, params: dict | None = None, override: str | None = None) -> list:
    with prepare(override).connect() as connection:
        return connection.execute(text(statement), params or {}).fetchall()


def frame(statement: str, params: dict | None = None,
          override: str | None = None) -> pd.DataFrame:
    with prepare(override).connect() as connection:
        return pd.read_sql_query(text(statement), connection, params=params or None)


def upsert(table: str, key: str, values: dict, override: str | None = None) -> None:
    """One INSERT … ON CONFLICT, which both dialects spell the same way."""
    columns = list(values)
    assignments = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != key)
    statement = (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join(':' + c for c in columns)}) "
        f"ON CONFLICT ({key}) DO UPDATE SET {assignments}"
        if assignments else
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join(':' + c for c in columns)}) "
        f"ON CONFLICT ({key}) DO NOTHING"
    )
    run(statement, values, override)


def reset(override: str | None = None) -> None:
    """Forget the cached engine — used by tests that switch databases."""
    with _lock:
        for made in _engines.values():
            made.dispose()
        _engines.clear()
        _ready.clear()
