"""Synthetic listings for trying out the interface before real listing data
is connected. Every row is tagged source='demo' and the UI shows a banner
while any demo rows exist. Remove them with ``realtymap clear --source demo``.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta

from . import db
from .config import Region


def _deflator(conn: sqlite3.Connection | None, region: Region):
    """Return f(date) -> price level relative to today, from the stored Redfin
    county series when available (so demo histories follow the real market),
    otherwise a flat 4% a year."""
    points = []
    if conn is not None:
        points = conn.execute(
            """SELECT period, value FROM market_metrics WHERE source = 'redfin' AND geo_type = 'county'
               AND geo_id = ? AND metric = 'median_sale_price' ORDER BY period""",
            (region.full_county_fips,)).fetchall()
    if len(points) >= 24:
        smooth = {}
        vals = [p[1] for p in points]
        for i, p in enumerate(points):
            w = vals[max(0, i - 5): i + 1]
            smooth[p[0][:7]] = sum(w) / len(w)
        latest = smooth[points[-1][0][:7]]
        first = points[0][0][:7]
        return lambda d: smooth.get(max(d[:7], first), latest) / latest
    return lambda d: 1.04 ** -((date.today() - date.fromisoformat(d)).days / 365.25)


def _history(rng: random.Random, value: float, level, sold_date: str | None) -> list[dict]:
    """Prior sales and annual assessments for a synthetic home."""
    events = []
    last = sold_date or date.today().isoformat()
    for _ in range(rng.choice([0, 1, 1, 2, 2, 3])):
        y = int(last[:4]) - rng.randint(4, 11)
        if y < 2012:
            break
        d = f"{y}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        events.append({"event": "sold", "date": d, "price": round(value * level(d) * rng.uniform(0.9, 1.1), -2)})
        last = d
    for y in range(date.today().year - 5, date.today().year + 1):
        # Michigan assessed value (SEV) is set at half of market value.
        events.append({"event": "assessment", "date": f"{y}-01-01",
                       "price": round(value * level(f"{y - 1}-12-01") * 0.5 * rng.uniform(0.85, 1.0), -2),
                       "note": "assessed value"})
    return events


def generate(region: Region, n: int = 240, seed: int = 7, conn: sqlite3.Connection | None = None) -> list[dict]:
    rng = random.Random(seed)
    level = _deflator(conn, region)
    zips = [z for z in region.zips if z.lat is not None]
    out = []
    for i in range(n):
        z = rng.choice(zips)
        beds = rng.choices([2, 3, 4, 5], weights=[25, 45, 24, 6])[0]
        baths = max(1.0, min(beds, rng.choice([1, 1.5, 2, 2, 2.5, 3])))
        sqft = round(rng.gauss(650 + beds * 330, 220), -1)
        sqft = max(700, sqft)
        garage = 1 if rng.random() < 0.72 else 0
        basement = 1 if rng.random() < 0.55 else 0
        year = rng.randint(1900, 2022)
        price = 20000 + sqft * 92 + baths * 9000 + garage * 16000 + basement * 11000 + (year - 1960) * 450
        price *= rng.uniform(0.82, 1.18)
        sold = rng.random() < 0.4
        month = rng.randint(1, 12)
        rec = {
            "source": "demo",
            "source_id": f"demo-{i}",
            "status": "sold" if sold else "active",
            "address": f"{rng.randint(1, 9)}{i:03d} Demo St (synthetic)",
            "city": z.name.split(" (")[0].split(" /")[0],
            "zip": z.zip,
            "lat": z.lat + rng.uniform(-0.03, 0.03),
            "lon": z.lon + rng.uniform(-0.04, 0.04),
            "property_type": "Single Family",
            "list_price": round(price, -2),
            "bedrooms": beds,
            "bathrooms": baths,
            "sqft": sqft,
            "lot_sqft": round(rng.uniform(5000, 40000), -2),
            "year_built": year,
            "garage": garage,
            "garage_spaces": rng.choice([1, 2, 2, 3]) if garage else 0,
            "basement": basement,
            "basement_finished": (1 if rng.random() < 0.4 else 0) if basement else None,
            "fireplace": 1 if rng.random() < 0.3 else 0,
        }
        if sold:
            rec["sold_price"] = round(price * rng.uniform(0.93, 1.03), -2)
            rec["sold_date"] = f"2025-{month:02d}-15"
            rec["list_date"] = f"2025-{max(1, month - 2):02d}-01"
        elif rng.random() < 0.35:
            # an asking-price cut a few weeks after listing
            listed = date.today() - timedelta(days=rng.randint(40, 120))
            rec["list_date"] = listed.isoformat()
            rec["_history"] = [{"event": "price_change", "date": (listed + timedelta(days=30)).isoformat(),
                                "price": rec["list_price"], "note": "price cut"}]
            rec["_history"].append({"event": "listed", "date": listed.isoformat(),
                                    "price": round(rec["list_price"] * rng.uniform(1.03, 1.08), -2)})
        rec.setdefault("_history", []).extend(_history(rng, price, level, rec.get("sold_date")))
        out.append(rec)
    return out


def load(conn: sqlite3.Connection, region: Region, n: int = 240) -> int:
    with db.transaction(conn):
        return db.upsert_listings(conn, generate(region, n, conn=conn))
