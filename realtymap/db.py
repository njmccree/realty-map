"""SQLite storage.

Two kinds of data are kept:

* ``market_metrics`` - aggregate time series in long format, one row per
  (source, geography, period, metric). Realtor.com, Redfin, Zillow and the
  Census all land here.
* ``listings`` - individual listings or sales (active, sold, ...) with their
  amenities, as reported by one source. This is the micro-level data used for
  price-vs-amenity analysis.
* ``properties`` / ``property_events`` - the physical homes behind those
  listings, matched by address, and each home's history: listed, price
  changes, sold, taken off market, tax assessments. See ``properties.py``.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .config import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_metrics (
    source    TEXT NOT NULL,
    geo_type  TEXT NOT NULL,          -- 'county' | 'zip'
    geo_id    TEXT NOT NULL,          -- county FIPS or ZIP code
    period    TEXT NOT NULL,          -- first day of the period, YYYY-MM-DD
    metric    TEXT NOT NULL,
    value     REAL,
    PRIMARY KEY (source, geo_type, geo_id, period, metric)
);
CREATE INDEX IF NOT EXISTS idx_metrics_lookup
    ON market_metrics (geo_type, geo_id, metric, period);

CREATE TABLE IF NOT EXISTS listings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source            TEXT NOT NULL,
    source_id         TEXT NOT NULL,
    status            TEXT NOT NULL,  -- 'active' | 'pending' | 'sold'
    address           TEXT,
    city              TEXT,
    zip               TEXT,
    lat               REAL,
    lon               REAL,
    property_type     TEXT,
    list_price        REAL,
    list_date         TEXT,
    sold_price        REAL,
    sold_date         TEXT,
    bedrooms          REAL,
    bathrooms         REAL,
    sqft              REAL,
    lot_sqft          REAL,
    year_built        INTEGER,
    garage            INTEGER,        -- 1 / 0 / NULL (unknown)
    garage_spaces     REAL,
    basement          INTEGER,        -- 1 / 0 / NULL (unknown)
    basement_finished INTEGER,
    stories           REAL,
    pool              INTEGER,
    fireplace         INTEGER,
    url               TEXT,
    property_id       INTEGER REFERENCES properties (id),
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_listings_zip ON listings (zip, status);

CREATE TABLE IF NOT EXISTS properties (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    address_key       TEXT NOT NULL UNIQUE,   -- normalized street address + ZIP
    address           TEXT,
    city              TEXT,
    zip               TEXT,
    lat               REAL,
    lon               REAL,
    property_type     TEXT,
    bedrooms          REAL,
    bathrooms         REAL,
    sqft              REAL,
    lot_sqft          REAL,
    year_built        INTEGER,
    garage            INTEGER,
    garage_spaces     REAL,
    basement          INTEGER,
    basement_finished INTEGER,
    stories           REAL,
    pool              INTEGER,
    fireplace         INTEGER,
    parcel_id         TEXT,
    looked_up         TEXT,                   -- when a property-record lookup last ran
    updated           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_properties_zip ON properties (zip);

CREATE TABLE IF NOT EXISTS property_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER NOT NULL REFERENCES properties (id) ON DELETE CASCADE,
    event_date  TEXT NOT NULL,                -- YYYY-MM-DD
    event       TEXT NOT NULL,                -- listed | price_change | pending | sold | off_market | assessment
    price       REAL,
    source      TEXT NOT NULL,
    listing_id  INTEGER REFERENCES listings (id) ON DELETE SET NULL,
    note        TEXT,
    UNIQUE (property_id, event, event_date, source)
);
CREATE INDEX IF NOT EXISTS idx_events_lookup ON property_events (event, event_date);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    source    TEXT NOT NULL,
    started   TEXT NOT NULL,
    finished  TEXT,
    status    TEXT NOT NULL,          -- 'running' | 'ok' | 'error'
    rows      INTEGER DEFAULT 0,
    message   TEXT
);
"""

LISTING_FIELDS = (
    "source", "source_id", "status", "address", "city", "zip", "lat", "lon",
    "property_type", "list_price", "list_date", "sold_price", "sold_date",
    "bedrooms", "bathrooms", "sqft", "lot_sqft", "year_built", "garage",
    "garage_spaces", "basement", "basement_finished", "stories", "pool",
    "fireplace", "url",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path) if path else db_path()
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    # Each API request opens its own connection, but FastAPI may create and use it
    # on different worker threads, so same-thread checking is disabled.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring databases created by older versions up to the current schema."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(listings)")}
    if "property_id" not in cols:
        conn.execute("ALTER TABLE listings ADD COLUMN property_id INTEGER REFERENCES properties (id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_listings_property ON listings (property_id)")
    if conn.execute("SELECT 1 FROM listings WHERE property_id IS NULL AND address IS NOT NULL LIMIT 1").fetchone():
        from .properties import backfill
        backfill(conn)
    conn.commit()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def upsert_metrics(conn: sqlite3.Connection, rows: Iterable[tuple]) -> int:
    """rows: (source, geo_type, geo_id, period, metric, value)."""
    cur = conn.executemany(
        """INSERT INTO market_metrics (source, geo_type, geo_id, period, metric, value)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (source, geo_type, geo_id, period, metric)
           DO UPDATE SET value = excluded.value""",
        list(rows),
    )
    return cur.rowcount


def upsert_listings(conn: sqlite3.Connection, listings: Iterable[dict]) -> int:
    """Insert or update listings, link each to its property and record history
    events (listed, price change, sold, ...).

    A listing dict may also carry ``_history`` (a list of past events for the
    home, e.g. prior sales from a property-records source) and ``_facts``
    (property characteristics from such a source).
    """
    from .properties import record_listing

    ts = now_iso()
    cols = ", ".join(LISTING_FIELDS)
    placeholders = ", ".join("?" for _ in LISTING_FIELDS)
    updates = ", ".join(f"{f} = excluded.{f}" for f in LISTING_FIELDS if f not in ("source", "source_id"))
    n = 0
    for item in listings:
        previous = conn.execute(
            "SELECT * FROM listings WHERE source = ? AND source_id = ?", (item.get("source"), item.get("source_id"))
        ).fetchone()
        values = [item.get(f) for f in LISTING_FIELDS]
        conn.execute(
            f"""INSERT INTO listings ({cols}, first_seen, last_seen)
                VALUES ({placeholders}, ?, ?)
                ON CONFLICT (source, source_id) DO UPDATE SET {updates}, last_seen = excluded.last_seen""",
            values + [ts, ts],
        )
        row = conn.execute(
            "SELECT * FROM listings WHERE source = ? AND source_id = ?", (item.get("source"), item.get("source_id"))
        ).fetchone()
        record_listing(conn, row, previous, history=item.get("_history"), facts=item.get("_facts"))
        n += 1
    return n


def start_run(conn: sqlite3.Connection, source: str) -> int:
    cur = conn.execute(
        "INSERT INTO ingest_runs (source, started, status) VALUES (?, ?, 'running')",
        (source, now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, rows: int = 0, message: str = "") -> None:
    conn.execute(
        "UPDATE ingest_runs SET finished = ?, status = ?, rows = ?, message = ? WHERE id = ?",
        (now_iso(), status, rows, message[:2000], run_id),
    )
    conn.commit()
