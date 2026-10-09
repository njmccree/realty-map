"""SQLite storage.

Two kinds of data are kept:

* ``market_metrics`` - aggregate time series in long format, one row per
  (source, geography, period, metric). Realtor.com, Redfin, Zillow and the
  Census all land here.
* ``listings`` - individual properties (active or sold) with their amenities.
  This is the micro-level data used for price-vs-amenity analysis.
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
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_listings_zip ON listings (zip, status);

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
    conn.executescript(SCHEMA)
    return conn


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
    ts = now_iso()
    cols = ", ".join(LISTING_FIELDS)
    placeholders = ", ".join("?" for _ in LISTING_FIELDS)
    updates = ", ".join(f"{f} = excluded.{f}" for f in LISTING_FIELDS if f not in ("source", "source_id"))
    n = 0
    for item in listings:
        values = [item.get(f) for f in LISTING_FIELDS]
        conn.execute(
            f"""INSERT INTO listings ({cols}, first_seen, last_seen)
                VALUES ({placeholders}, ?, ?)
                ON CONFLICT (source, source_id) DO UPDATE SET {updates}, last_seen = excluded.last_seen""",
            values + [ts, ts],
        )
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
