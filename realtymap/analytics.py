"""Turn stored rows into the numbers shown in the UI."""

from __future__ import annotations

import math
import sqlite3
import statistics
from datetime import date

from .config import Region

# Which source/metric pairs feed each headline series, in order of preference.
SERIES = {
    "median_listing_price": ("realtor", "median_listing_price", "Median list price (on market)"),
    "average_listing_price": ("realtor", "average_listing_price", "Average list price (on market)"),
    "active_listing_count": ("realtor", "active_listing_count", "Active listings"),
    "median_days_on_market": ("realtor", "median_days_on_market", "Median days on market"),
    "median_listing_price_per_square_foot": ("realtor", "median_listing_price_per_square_foot", "List price per sq ft"),
    "median_square_feet": ("realtor", "median_square_feet", "Median listing size (sq ft)"),
    "price_reduced_share": ("realtor", "price_reduced_share", "Share of listings with a price cut"),
    "median_sale_price": ("redfin", "median_sale_price", "Median sale price (closed)"),
    "homes_sold": ("redfin", "homes_sold", "Homes sold"),
    "median_sale_ppsf": ("redfin", "median_sale_ppsf", "Sale price per sq ft"),
    "avg_sale_to_list": ("redfin", "avg_sale_to_list", "Sale-to-list ratio"),
    "months_of_supply": ("redfin", "months_of_supply", "Months of supply"),
    "zhvi": ("zillow", "zhvi", "Typical home value (ZHVI)"),
}

PRICE_UNITS = {"median_listing_price", "average_listing_price", "median_sale_price", "zhvi"}


def geo_for(region: Region, zip_code: str | None) -> tuple[str, str]:
    if zip_code:
        if zip_code not in region.zip_codes:
            raise ValueError(f"ZIP {zip_code} is not part of {region.name}")
        return "zip", zip_code
    return "county", region.full_county_fips


def _shift_months(period: str, months: int) -> str:
    y, m = int(period[:4]), int(period[5:7])
    total = y * 12 + (m - 1) + months
    return f"{total // 12:04d}-{total % 12 + 1:02d}-01"


def series(conn: sqlite3.Connection, geo_type: str, geo_id: str, key: str) -> list[dict]:
    source, metric, _ = SERIES[key]
    rows = conn.execute(
        """SELECT period, value FROM market_metrics
           WHERE source = ? AND geo_type = ? AND geo_id = ? AND metric = ?
           ORDER BY period""",
        (source, geo_type, geo_id, metric),
    ).fetchall()
    return [{"period": r["period"], "value": r["value"]} for r in rows]


def change_summary(points: list[dict]) -> dict:
    """Latest value and its change versus 1, 3, 12, 36 and 60 months earlier."""
    if not points:
        return {"latest": None, "period": None, "changes": {}}
    by_period = {p["period"]: p["value"] for p in points}
    latest = points[-1]
    changes = {}
    for label, months in (("1m", 1), ("3m", 3), ("1y", 12), ("3y", 36), ("5y", 60)):
        prev = by_period.get(_shift_months(latest["period"], -months))
        if prev not in (None, 0) and latest["value"] is not None:
            changes[label] = {"previous": prev, "pct": (latest["value"] - prev) / abs(prev),
                              "abs": latest["value"] - prev}
    return {"latest": latest["value"], "period": latest["period"], "changes": changes}


def latest_metrics(conn: sqlite3.Connection, source: str, geo_type: str, geo_id: str) -> dict:
    """{metric: (period, value)} for the most recent period of each metric."""
    rows = conn.execute(
        """SELECT m.metric, m.period, m.value FROM market_metrics m
           JOIN (SELECT metric, MAX(period) AS period FROM market_metrics
                 WHERE source = ? AND geo_type = ? AND geo_id = ? GROUP BY metric) last
             ON last.metric = m.metric AND last.period = m.period
           WHERE m.source = ? AND m.geo_type = ? AND m.geo_id = ?""",
        (source, geo_type, geo_id, source, geo_type, geo_id),
    ).fetchall()
    return {r["metric"]: (r["period"], r["value"]) for r in rows}


# --------------------------------------------------------------------------- snapshot

def snapshot(conn: sqlite3.Connection, region: Region, zip_code: str | None = None) -> dict:
    geo_type, geo_id = geo_for(region, zip_code)
    kpis = {}
    for key in ("average_listing_price", "median_listing_price", "active_listing_count",
                "median_days_on_market", "median_listing_price_per_square_foot", "median_square_feet",
                "median_sale_price", "homes_sold", "avg_sale_to_list", "zhvi"):
        summary = change_summary(series(conn, geo_type, geo_id, key))
        if summary["latest"] is not None:
            kpis[key] = {"label": SERIES[key][2], "source": SERIES[key][0], **summary}

    listing_stats = _listing_prices(conn, region, zip_code, status="active")
    census = latest_metrics(conn, "census", geo_type, geo_id)
    return {
        "geo": {"type": geo_type, "id": geo_id, "name": _geo_name(region, zip_code)},
        "kpis": kpis,
        "listings": listing_stats,
        "census": {k: {"period": p, "value": v} for k, (p, v) in census.items()},
    }


def _geo_name(region: Region, zip_code: str | None) -> str:
    if not zip_code:
        return region.name
    name = next((z.name for z in region.zips if z.zip == zip_code), "")
    return f"{zip_code} {name}".strip()


# --------------------------------------------------------------------------- listings

PRICE_SQL = "COALESCE(CASE WHEN status = 'sold' THEN sold_price END, list_price)"


def _listing_filter(region: Region, zip_code: str | None, status: str | None,
                    include_demo: bool = True) -> tuple[str, list]:
    where = [f"{PRICE_SQL} IS NOT NULL"]
    params: list = []
    if zip_code:
        where.append("zip = ?")
        params.append(zip_code)
    else:
        zips = sorted(region.zip_codes)
        where.append(f"zip IN ({','.join('?' * len(zips))})")
        params.extend(zips)
    if status == "active":
        where.append("status IN ('active', 'pending')")
    elif status:
        where.append("status = ?")
        params.append(status)
    if not include_demo:
        where.append("source != 'demo'")
    return " AND ".join(where), params


def listings(conn: sqlite3.Connection, region: Region, zip_code: str | None = None,
             status: str | None = None, limit: int = 1000) -> list[dict]:
    where, params = _listing_filter(region, zip_code, status)
    rows = conn.execute(
        f"""SELECT *, {PRICE_SQL} AS price FROM listings WHERE {where}
            ORDER BY COALESCE(sold_date, list_date, last_seen) DESC LIMIT ?""",
        params + [limit],
    ).fetchall()
    return [dict(r) for r in rows]


def _listing_prices(conn, region, zip_code, status) -> dict:
    rows = listings(conn, region, zip_code, status, limit=100000)
    prices = [r["price"] for r in rows]
    if not prices:
        return {"count": 0}
    return {
        "count": len(prices),
        "average_price": statistics.fmean(prices),
        "median_price": statistics.median(prices),
        "sources": sorted({r["source"] for r in rows}),
    }


AMENITY_FIELDS = {
    "bedrooms": "Bedrooms",
    "bathrooms": "Bathrooms",
    "sqft": "Living area (sq ft)",
    "lot_sqft": "Lot size (sq ft)",
    "year_built": "Year built",
    "garage_spaces": "Garage spaces",
}
FLAG_FIELDS = {
    "garage": "Has garage",
    "basement": "Has basement",
    "basement_finished": "Finished basement (of homes with basement)",
    "fireplace": "Has fireplace",
    "pool": "Has pool",
}


def amenities(conn: sqlite3.Connection, region: Region, zip_code: str | None = None,
              status: str | None = None) -> dict:
    rows = listings(conn, region, zip_code, status, limit=100000)
    n = len(rows)
    averages = {}
    for field, label in AMENITY_FIELDS.items():
        vals = [r[field] for r in rows if r[field] is not None]
        if vals:
            averages[field] = {"label": label, "mean": statistics.fmean(vals),
                               "median": statistics.median(vals), "known": len(vals)}
    shares = {}
    for field, label in FLAG_FIELDS.items():
        pool = [r for r in rows if field != "basement_finished" or r["basement"] == 1]
        vals = [r[field] for r in pool if r[field] is not None]
        if vals:
            shares[field] = {"label": label, "share": sum(vals) / len(vals), "known": len(vals)}

    by_bedrooms = _group_prices(rows, lambda r: None if r["bedrooms"] is None
                                else ("5+" if r["bedrooms"] >= 5 else str(int(r["bedrooms"]))))
    by_bathrooms = _group_prices(rows, lambda r: None if r["bathrooms"] is None
                                 else ("3+" if r["bathrooms"] >= 3 else f"{r['bathrooms']:g}"))
    premiums = {}
    for field, label in (("garage", "Garage"), ("basement", "Basement"), ("basement_finished", "Finished basement"),
                         ("fireplace", "Fireplace"), ("pool", "Pool")):
        with_ = [r["price"] for r in rows if r[field] == 1]
        without = [r["price"] for r in rows if r[field] == 0]
        if len(with_) >= 3 and len(without) >= 3:
            premiums[field] = {
                "label": label,
                "with_median": statistics.median(with_), "with_n": len(with_),
                "without_median": statistics.median(without), "without_n": len(without),
                "difference": statistics.median(with_) - statistics.median(without),
            }
    ppsf = [r["price"] / r["sqft"] for r in rows if r["sqft"]]
    geo_type, geo_id = geo_for(region, zip_code)
    census = latest_metrics(conn, "census", geo_type, geo_id)
    return {
        "count": n,
        "sources": sorted({r["source"] for r in rows}),
        "averages": averages,
        "shares": shares,
        "price_per_sqft": {"median": statistics.median(ppsf), "known": len(ppsf)} if ppsf else None,
        "by_bedrooms": by_bedrooms,
        "by_bathrooms": by_bathrooms,
        "premiums": premiums,
        "model": hedonic_model(rows),
        "housing_stock": {k: {"period": p, "value": v} for k, (p, v) in census.items()},
    }


def _group_prices(rows, key) -> list[dict]:
    groups: dict[str, list[float]] = {}
    for r in rows:
        k = key(r)
        if k is not None:
            groups.setdefault(k, []).append(r["price"])

    def order(k: str) -> float:
        return float(k.rstrip("+"))

    return [{"group": k, "n": len(v), "median_price": statistics.median(v), "average_price": statistics.fmean(v)}
            for k, v in sorted(groups.items(), key=lambda kv: order(kv[0]))]


# --------------------------------------------------------------------------- hedonic model

MODEL_TERMS = (
    ("sqft", "Per sq ft of living area", lambda r: r["sqft"]),
    ("bedrooms", "Per bedroom (holding size fixed)", lambda r: r["bedrooms"]),
    ("bathrooms", "Per bathroom", lambda r: r["bathrooms"]),
    ("garage", "Garage", lambda r: r["garage"]),
    ("basement", "Basement", lambda r: r["basement"]),
    ("age", "Per year of age", lambda r: (date.today().year - r["year_built"]) if r["year_built"] else None),
)
MIN_ROWS_PER_TERM = 8


def hedonic_model(rows: list[dict]) -> dict | None:
    """Ordinary least squares of price on amenities.

    Terms with no variation or too few known values are dropped. Returns each
    term's estimated contribution to price with a standard error, or None
    when there isn't enough data for a meaningful fit.
    """
    terms = []
    for name, label, fn in MODEL_TERMS:
        vals = [fn(r) for r in rows]
        known = [v for v in vals if v is not None]
        if len(known) >= 20 and len(set(known)) > 1:
            terms.append((name, label, fn))
    while terms:
        data = [(r["price"], [fn(r) for _, _, fn in terms]) for r in rows]
        data = [(y, x) for y, x in data if all(v is not None for v in x)]
        if len(data) >= MIN_ROWS_PER_TERM * (len(terms) + 1):
            break
        terms.pop()  # drop the least important (last) term and retry
    else:
        return None
    if not terms:
        return None
    y = [d[0] for d in data]
    X = [[1.0] + [float(v) for v in d[1]] for d in data]
    fit = ols(X, y)
    if fit is None:
        return None
    coefs, ses, r2 = fit
    return {
        "n": len(y),
        "r2": r2,
        "intercept": coefs[0],
        "terms": [{"term": name, "label": label, "estimate": c, "std_error": se}
                  for (name, label, _), c, se in zip(terms, coefs[1:], ses[1:])],
    }


def ols(X: list[list[float]], y: list[float]):
    n, k = len(X), len(X[0])
    if n <= k:
        return None
    xtx = [[sum(X[r][i] * X[r][j] for r in range(n)) for j in range(k)] for i in range(k)]
    xty = [sum(X[r][i] * y[r] for r in range(n)) for i in range(k)]
    inv = _invert(xtx)
    if inv is None:
        return None
    beta = [sum(inv[i][j] * xty[j] for j in range(k)) for i in range(k)]
    resid = [y[r] - sum(X[r][i] * beta[i] for i in range(k)) for r in range(n)]
    sse = sum(e * e for e in resid)
    mean_y = sum(y) / n
    sst = sum((v - mean_y) ** 2 for v in y)
    sigma2 = sse / (n - k)
    ses = [math.sqrt(max(inv[i][i] * sigma2, 0.0)) for i in range(k)]
    return beta, ses, (1 - sse / sst) if sst else 0.0


def _invert(m: list[list[float]]) -> list[list[float]] | None:
    k = len(m)
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(k)] for i, row in enumerate(m)]
    for col in range(k):
        pivot = max(range(col, k), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        p = a[col][col]
        a[col] = [v / p for v in a[col]]
        for r in range(k):
            if r != col and a[r][col]:
                f = a[r][col]
                a[r] = [rv - f * cv for rv, cv in zip(a[r], a[col])]
    return [row[k:] for row in a]


# --------------------------------------------------------------------------- ZIP comparison

def zip_table(conn: sqlite3.Connection, region: Region) -> list[dict]:
    out = []
    for z in region.zips:
        row = {"zip": z.zip, "name": z.name, "lat": z.lat, "lon": z.lon}
        for key in ("median_listing_price", "average_listing_price", "active_listing_count",
                    "median_days_on_market", "median_listing_price_per_square_foot", "median_square_feet",
                    "zhvi", "median_sale_price"):
            s = change_summary(series(conn, "zip", z.zip, key))
            if s["latest"] is not None:
                row[key] = s["latest"]
                row[key + "_period"] = s["period"]
                if "1y" in s["changes"]:
                    row[key + "_yoy"] = s["changes"]["1y"]["pct"]
        census = latest_metrics(conn, "census", "zip", z.zip)
        for metric in ("avg_bedrooms", "median_home_value", "median_year_built"):
            if metric in census:
                row["census_" + metric] = census[metric][1]
        stats = _listing_prices(conn, region, z.zip, "active")
        row["listing_count"] = stats["count"]
        out.append(row)
    return out


def source_status(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT r.* FROM ingest_runs r
           JOIN (SELECT source, MAX(id) AS id FROM ingest_runs GROUP BY source) last ON last.id = r.id
           ORDER BY r.source"""
    ).fetchall()
    out = [dict(r) for r in rows]
    for r in out:
        if r["source"] in ("realtor", "redfin", "zillow", "census"):
            latest = conn.execute("SELECT MAX(period) FROM market_metrics WHERE source = ?", (r["source"],)).fetchone()[0]
            r["latest_period"] = latest
        else:
            r["listing_count"] = conn.execute("SELECT COUNT(*) FROM listings WHERE source = ?", (r["source"],)).fetchone()[0]
    return out
