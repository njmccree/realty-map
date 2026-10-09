"""Synthetic listings for trying out the interface before real listing data
is connected. Every row is tagged source='demo' and the UI shows a banner
while any demo rows exist. Remove them with ``realtymap clear --source demo``.
"""

from __future__ import annotations

import random
import sqlite3

from . import db
from .config import Region


def generate(region: Region, n: int = 240, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
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
            "address": f"{rng.randint(100, 9999)} Demo St (synthetic)",
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
        out.append(rec)
    return out


def load(conn: sqlite3.Connection, region: Region, n: int = 240) -> int:
    with db.transaction(conn):
        return db.upsert_listings(conn, generate(region, n))
