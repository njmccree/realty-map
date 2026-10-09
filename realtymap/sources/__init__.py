"""Data source registry and ingestion runner."""

from __future__ import annotations

import sqlite3
from itertools import islice

from .. import db, properties
from ..config import Region
from .base import Source, SourceUnavailable
from .census import CensusACS
from .csv_import import CSVImport
from .realtor_research import RealtorResearch
from .redfin import Redfin
from .rentcast import RentCast
from .zillow import ZillowZHVI

# Sources that can run unattended. CSV import needs a file path so it is
# invoked separately (``realtymap import``).
SOURCES: dict[str, type[Source]] = {
    "realtor": RealtorResearch,
    "redfin": Redfin,
    "zillow": ZillowZHVI,
    "census": CensusACS,
    "rentcast": RentCast,
}

SOURCE_INFO = {
    cls.name: {"label": cls.label, "kind": cls.kind, "description": cls.description,
               "homepage": cls.homepage, "api_key_env": cls.api_key_env}
    for cls in (*SOURCES.values(), CSVImport)
}
SOURCE_INFO["demo"] = {"label": "Demo data (synthetic)", "kind": "listings",
                       "description": "Randomly generated listings for trying out the UI. Not real homes.",
                       "homepage": "", "api_key_env": None}

BATCH = 5000


def run_source(conn: sqlite3.Connection, source: Source, region: Region) -> int:
    """Run one source and store its output. Returns the number of rows written."""
    run_id = db.start_run(conn, source.name if not isinstance(source, CSVImport) else source.source_name)
    started = conn.execute("SELECT started FROM ingest_runs WHERE id = ?", (run_id,)).fetchone()[0]
    total = 0
    try:
        it = iter(source.fetch(region))
        while batch := list(islice(it, BATCH)):
            with db.transaction(conn):
                if source.kind == "metrics":
                    db.upsert_metrics(conn, batch)
                else:
                    db.upsert_listings(conn, batch)
            total += len(batch)
    except SourceUnavailable as exc:
        db.finish_run(conn, run_id, "skipped", total, str(exc))
        raise
    except Exception as exc:
        db.finish_run(conn, run_id, "error", total, f"{type(exc).__name__}: {exc}")
        raise
    message = ""
    if source.full_snapshot:
        with db.transaction(conn):
            gone = properties.mark_off_market(conn, source.name, started)
        message = f"{gone} listings went off market"
    db.finish_run(conn, run_id, "ok", total, message)
    return total


__all__ = ["SOURCES", "SOURCE_INFO", "Source", "SourceUnavailable", "CSVImport", "run_source"]
