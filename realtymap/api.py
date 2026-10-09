"""HTTP API and static web UI."""

from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import analytics, db
from .config import ROOT, load_region
from .sources import SOURCE_INFO

WEB_DIR = ROOT / "web"

app = FastAPI(title="Realty Map", version="0.1.0")
region = load_region()


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def _zip(zip_code: str | None) -> str | None:
    if zip_code and zip_code not in region.zip_codes:
        raise HTTPException(404, f"ZIP {zip_code} is not part of {region.name}")
    return zip_code or None


Status = Query(None, pattern="^(active|sold|pending)$")


@app.get("/api/region")
def get_region(conn: sqlite3.Connection = Depends(get_conn)):
    demo = conn.execute("SELECT COUNT(*) FROM listings WHERE source = 'demo'").fetchone()[0]
    return {
        "id": region.id,
        "name": region.name,
        "county_fips": region.full_county_fips,
        "center": region.center,
        "zoom": region.zoom,
        "zips": [{"zip": z.zip, "name": z.name, "lat": z.lat, "lon": z.lon} for z in region.zips],
        "sources": SOURCE_INFO,
        "source_status": analytics.source_status(conn),
        "demo_listings": demo,
        "series": {k: {"source": s, "label": label, "is_price": k in analytics.PRICE_UNITS}
                   for k, (s, _, label) in analytics.SERIES.items()},
    }


@app.get("/api/summary")
def get_summary(zip: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    return analytics.snapshot(conn, region, _zip(zip))


@app.get("/api/trends")
def get_trends(metric: list[str] = Query(...), zip: str | None = None,
               conn: sqlite3.Connection = Depends(get_conn)):
    geo_type, geo_id = analytics.geo_for(region, _zip(zip))
    out = {}
    for key in metric:
        if key not in analytics.SERIES:
            raise HTTPException(400, f"Unknown metric {key!r}")
        points = analytics.series(conn, geo_type, geo_id, key)
        out[key] = {"label": analytics.SERIES[key][2], "source": analytics.SERIES[key][0],
                    "points": points, "summary": analytics.change_summary(points)}
    return {"geo": {"type": geo_type, "id": geo_id}, "series": out}


@app.get("/api/amenities")
def get_amenities(zip: str | None = None, status: str | None = Status,
                  conn: sqlite3.Connection = Depends(get_conn)):
    return analytics.amenities(conn, region, _zip(zip), status)


@app.get("/api/listings")
def get_listings(zip: str | None = None, status: str | None = Status,
                 limit: int = Query(500, le=5000), conn: sqlite3.Connection = Depends(get_conn)):
    return analytics.listings(conn, region, _zip(zip), status, limit)


@app.get("/api/zips")
def get_zips(conn: sqlite3.Connection = Depends(get_conn)):
    return analytics.zip_table(conn, region)


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
