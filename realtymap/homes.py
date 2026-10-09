"""Individual homes: detail, sale history, value estimates, comparable sales."""

from __future__ import annotations

import math
import sqlite3
import statistics
from datetime import date

from . import analytics
from .config import Region

# Area price series used to put a home's own history in context, in order of
# preference. The first one with data covering the dates involved is used.
INDEX_CANDIDATES = (
    ("zillow", "zip", "zhvi", "Zillow home value index (ZIP)"),
    ("redfin", "zip", "median_sale_price", "Redfin median sale price (ZIP)"),
    ("zillow", "county", "zhvi", "Zillow home value index (county)"),
    ("redfin", "county", "median_sale_price", "Redfin median sale price (county)"),
)
SMOOTH_MONTHS = 6  # monthly sale-price medians in a small county are noisy; use a trailing average

COMP_MONTHS = 24
COMP_RADIUS_MI = 3.0
COMP_LIMIT = 8


def _years_between(a: str, b: str) -> float:
    return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days / 365.25


def _month(d: str) -> str:
    return d[:7] + "-01"


# --------------------------------------------------------------------------- area index

class AreaIndex:
    """A monthly area price series that can be looked up at any date."""

    def __init__(self, label: str, source: str, geo_type: str, geo_id: str, points: list[tuple[str, float]]):
        self.label, self.source, self.geo_type, self.geo_id = label, source, geo_type, geo_id
        self.points = points
        self._by_month = dict(points)

    @property
    def first(self) -> str:
        return self.points[0][0]

    @property
    def last(self) -> str:
        return self.points[-1][0]

    def at(self, d: str) -> float | None:
        m = _month(d)
        if m < self.first:
            return None
        if m > self.last:
            m = self.last
        # walk back to the nearest month with a value
        y, mo = int(m[:4]), int(m[5:7])
        for _ in range(12):
            v = self._by_month.get(f"{y:04d}-{mo:02d}-01")
            if v is not None:
                return v
            mo -= 1
            if mo == 0:
                y, mo = y - 1, 12
        return None

    def to_dict(self) -> dict:
        return {"label": self.label, "source": self.source, "geo_type": self.geo_type, "geo_id": self.geo_id}


def area_index(conn: sqlite3.Connection, region: Region, zip_code: str | None, since: str | None = None) -> AreaIndex | None:
    for source, geo_type, metric, label in INDEX_CANDIDATES:
        geo_id = zip_code if geo_type == "zip" else region.full_county_fips
        if not geo_id:
            continue
        rows = conn.execute(
            """SELECT period, value FROM market_metrics WHERE source = ? AND geo_type = ? AND geo_id = ? AND metric = ?
               ORDER BY period""", (source, geo_type, geo_id, metric)).fetchall()
        if not rows or (since and rows[0]["period"] > _month(since)):
            continue
        values = [r["value"] for r in rows]
        smoothed = []
        for i, r in enumerate(rows):
            window = values[max(0, i - SMOOTH_MONTHS + 1): i + 1] if metric != "zhvi" else [values[i]]
            smoothed.append((r["period"], sum(window) / len(window)))
        return AreaIndex(label, source, geo_type, geo_id, smoothed)
    return None


# --------------------------------------------------------------------------- queries

def get_property(conn: sqlite3.Connection, property_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
    return dict(row) if row else None


def events(conn: sqlite3.Connection, property_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM property_events WHERE property_id = ? ORDER BY event_date, id""", (property_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def sales_from_events(evts: list[dict]) -> list[dict]:
    """One entry per sale date (the same sale may be reported by several sources)."""
    by_date: dict[str, dict] = {}
    for e in evts:
        if e["event"] == "sold" and e["price"]:
            d = e["event_date"]
            if d in by_date:
                by_date[d]["sources"].append(e["source"])
            else:
                by_date[d] = {"date": d, "price": e["price"], "sources": [e["source"]]}
    return [by_date[d] for d in sorted(by_date)]


def sale_history(sales: list[dict], index: AreaIndex | None) -> list[dict]:
    """Add the change since the previous sale, and the area's change over the same span."""
    out = []
    for i, s in enumerate(sales):
        row = dict(s)
        if i > 0:
            prev = sales[i - 1]
            years = _years_between(prev["date"], s["date"])
            row["previous_price"] = prev["price"]
            row["years_held"] = years
            row["change_pct"] = s["price"] / prev["price"] - 1
            if years > 0.25:
                row["annual_pct"] = (s["price"] / prev["price"]) ** (1 / years) - 1
            if index:
                a, b = index.at(prev["date"]), index.at(s["date"])
                if a and b:
                    row["area_change_pct"] = b / a - 1
                    if years > 0.25:
                        row["area_annual_pct"] = (b / a) ** (1 / years) - 1
        out.append(row)
    return out


# --------------------------------------------------------------------------- comparables

def _distance_mi(lat1, lon1, lat2, lon2) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def comparables(conn: sqlite3.Connection, region: Region, home: dict, months: int = COMP_MONTHS,
                limit: int = COMP_LIMIT) -> list[dict]:
    """Recently sold homes most similar to ``home``: nearby, similar size and bedrooms."""
    today = date.today()
    y, m = today.year, today.month - months
    while m <= 0:
        y, m = y - 1, m + 12
    since = f"{y:04d}-{m:02d}-01"
    zips = sorted(region.zip_codes)
    rows = conn.execute(
        f"""SELECT p.*, e.event_date AS sold_date, e.price AS sold_price
            FROM property_events e JOIN properties p ON p.id = e.property_id
            WHERE e.event = 'sold' AND e.event_date >= ? AND e.price > 0 AND p.id != ?
              AND p.zip IN ({','.join('?' * len(zips))})""",
        [since, home["id"], *zips],
    ).fetchall()
    seen: dict[tuple, dict] = {}
    for r in rows:
        seen.setdefault((r["id"], r["sold_date"]), dict(r))

    has_geo = home.get("lat") is not None and home.get("lon") is not None
    scored = []
    for c in seen.values():
        if has_geo and c["lat"] is not None and c["lon"] is not None:
            dist = _distance_mi(home["lat"], home["lon"], c["lat"], c["lon"])
            if dist > COMP_RADIUS_MI:
                continue
        elif c["zip"] == home.get("zip"):
            dist = None
        else:
            continue
        if home.get("bedrooms") is not None and c["bedrooms"] is not None and abs(c["bedrooms"] - home["bedrooms"]) > 1:
            continue
        if home.get("sqft") and c["sqft"] and not (0.7 <= c["sqft"] / home["sqft"] <= 1.3):
            continue
        months_ago = _years_between(c["sold_date"], today.isoformat()) * 12
        score = (dist if dist is not None else 1.5) + months_ago / 12
        if home.get("sqft") and c["sqft"]:
            score += abs(c["sqft"] / home["sqft"] - 1) * 4
        if home.get("bedrooms") is not None and c["bedrooms"] is not None:
            score += abs(c["bedrooms"] - home["bedrooms"]) * 0.5
        if home.get("bathrooms") is not None and c["bathrooms"] is not None:
            score += abs(c["bathrooms"] - home["bathrooms"]) * 0.3
        scored.append((score, {
            "id": c["id"], "address": c["address"], "zip": c["zip"], "sold_date": c["sold_date"],
            "sold_price": c["sold_price"], "bedrooms": c["bedrooms"], "bathrooms": c["bathrooms"], "sqft": c["sqft"],
            "year_built": c["year_built"], "garage": c["garage"], "basement": c["basement"],
            "price_per_sqft": c["sold_price"] / c["sqft"] if c["sqft"] else None,
            "distance_mi": dist,
        }))
    scored.sort(key=lambda t: t[0])
    return [c for _, c in scored[:limit]]


# --------------------------------------------------------------------------- detail

def _current_listing(listings: list[dict]) -> dict | None:
    active = [l for l in listings if l["status"] in ("active", "pending")]
    return max(active, key=lambda l: l["last_seen"]) if active else None


def _model_estimate(conn, region, home) -> dict | None:
    rows = analytics.listings(conn, region, None, None, limit=100000)
    rows = [r for r in rows if r["property_id"] != home["id"]]
    model = analytics.hedonic_model(rows)
    if not model:
        return None
    fns = {name: fn for name, _, fn in analytics.MODEL_TERMS}
    total = model["intercept"]
    for t in model["terms"]:
        v = fns[t["term"]](home)
        if v is None:
            return None
        total += t["estimate"] * v
    return {"value": total, "n": model["n"], "r2": model["r2"]}


def detail(conn: sqlite3.Connection, region: Region, property_id: int) -> dict | None:
    home = get_property(conn, property_id)
    if home is None:
        return None
    listing_rows = [dict(r) for r in conn.execute(
        "SELECT * FROM listings WHERE property_id = ? ORDER BY COALESCE(sold_date, list_date, first_seen)", (property_id,))]
    evts = events(conn, property_id)
    sales = sales_from_events(evts)
    first_date = evts[0]["event_date"] if evts else None
    index = area_index(conn, region, home["zip"], since=sales[0]["date"] if sales else None) \
        or area_index(conn, region, home["zip"])
    history = sale_history(sales, index)
    current = _current_listing(listing_rows)
    today = date.today().isoformat()

    valuation = {}
    if sales and index:
        last = sales[-1]
        a, b = index.at(last["date"]), index.at(today)
        if a and b:
            valuation["index"] = {
                "value": last["price"] * b / a, "from_price": last["price"], "from_date": last["date"],
                "area_change_pct": b / a - 1, "index": index.to_dict(),
            }
    comps = comparables(conn, region, home)
    ppsf = [c["price_per_sqft"] for c in comps if c["price_per_sqft"]]
    if home.get("sqft") and len(ppsf) >= 3:
        valuation["comps"] = {"value": statistics.median(ppsf) * home["sqft"], "price_per_sqft": statistics.median(ppsf),
                              "n": len(ppsf)}
    model = _model_estimate(conn, region, home)
    if model:
        valuation["model"] = model

    chart_index = []
    if index:
        # Area series rescaled to start at this home's first sale price, so the
        # two can be compared on one dollar axis.
        anchor = sales[0] if sales else None
        base = index.at(anchor["date"]) if anchor else None
        start = min(first_date or index.last, anchor["date"] if anchor else index.last)
        for period, value in index.points:
            if period >= _month(start) and base:
                chart_index.append({"date": period, "value": anchor["price"] * value / base})

    return {
        "property": home,
        "current_listing": current,
        "listings": listing_rows,
        "events": evts,
        "sales": history,
        "valuation": valuation,
        "comparables": comps,
        "area_index": index.to_dict() if index else None,
        "chart_index": chart_index,
    }


# --------------------------------------------------------------------------- search & lists

def search(conn: sqlite3.Connection, region: Region, q: str, limit: int = 15) -> list[dict]:
    terms = [t for t in q.upper().replace(",", " ").split() if t]
    if not terms:
        return []
    zips = sorted(region.zip_codes)
    where = " AND ".join("(p.address_key LIKE ? OR UPPER(COALESCE(p.city, '')) LIKE ?)" for _ in terms)
    params: list = []
    for t in terms:
        params += [f"%{t}%", f"%{t}%"]
    rows = conn.execute(
        f"""SELECT p.id, p.address, p.city, p.zip, p.bedrooms, p.bathrooms, p.sqft,
                   (SELECT COUNT(*) FROM property_events e WHERE e.property_id = p.id AND e.event = 'sold') AS sales
            FROM properties p WHERE {where} AND p.zip IN ({','.join('?' * len(zips))})
            ORDER BY p.address LIMIT ?""",
        params + zips + [limit],
    ).fetchall()
    return [dict(r) for r in rows]


def recent_sales(conn: sqlite3.Connection, region: Region, zip_code: str | None = None, months: int = 24,
                 limit: int = 500) -> list[dict]:
    """Closed sales in the area, newest first, each with the home's previous sale."""
    zips = [zip_code] if zip_code else sorted(region.zip_codes)
    today = date.today()
    y, m = today.year, today.month - months
    while m <= 0:
        y, m = y - 1, m + 12
    since = f"{y:04d}-{m:02d}-01" if months else "0000-01-01"
    rows = conn.execute(
        f"""SELECT e.property_id, e.event_date AS sold_date, MAX(e.price) AS sold_price,
                   GROUP_CONCAT(DISTINCT e.source) AS sources,
                   p.address, p.city, p.zip, p.lat, p.lon, p.bedrooms, p.bathrooms, p.sqft, p.year_built,
                   p.garage, p.basement,
                   (SELECT e2.price FROM property_events e2 WHERE e2.property_id = e.property_id AND e2.event = 'sold'
                      AND e2.event_date < e.event_date ORDER BY e2.event_date DESC LIMIT 1) AS previous_price,
                   (SELECT e2.event_date FROM property_events e2 WHERE e2.property_id = e.property_id AND e2.event = 'sold'
                      AND e2.event_date < e.event_date ORDER BY e2.event_date DESC LIMIT 1) AS previous_date,
                   (SELECT e3.price FROM property_events e3 WHERE e3.property_id = e.property_id
                      AND e3.event IN ('listed', 'price_change') AND e3.event_date <= e.event_date
                      AND e3.event_date >= date(e.event_date, '-365 days')
                    ORDER BY e3.event_date DESC LIMIT 1) AS last_list_price
            FROM property_events e JOIN properties p ON p.id = e.property_id
            WHERE e.event = 'sold' AND e.price > 0 AND e.event_date >= ?
              AND p.zip IN ({','.join('?' * len(zips))})
            GROUP BY e.property_id, e.event_date
            ORDER BY e.event_date DESC LIMIT ?""",
        [since, *zips, limit],
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d["previous_price"]:
            d["change_pct"] = d["sold_price"] / d["previous_price"] - 1
        if d["last_list_price"]:
            d["sale_to_list"] = d["sold_price"] / d["last_list_price"]
        out.append(d)
    return out
