"""Import listings or past sales from a CSV file.

This is the way to bring in amenity details that free aggregate sources do
not publish - basement, garage, etc. - for example from an MLS export, a
county assessor download or a hand-maintained spreadsheet.

Column names are matched case-insensitively against common aliases (see
``ALIASES``); see ``samples/listings_template.csv`` for the canonical names.
"""

from __future__ import annotations

import csv
import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Iterator

from ..config import Region
from .base import Source, to_float

ALIASES: dict[str, tuple[str, ...]] = {
    "source_id": ("source_id", "id", "mls", "mls#", "mls number", "mls_number", "listing id", "parcel", "parcel id"),
    "status": ("status", "listing status"),
    "address": ("address", "street address", "full address", "property address"),
    "city": ("city",),
    "zip": ("zip", "zip code", "zipcode", "postal code", "postal_code"),
    "lat": ("lat", "latitude"),
    "lon": ("lon", "lng", "long", "longitude"),
    "property_type": ("property_type", "property type", "type", "style"),
    "list_price": ("list_price", "list price", "price", "asking price", "current price"),
    "list_date": ("list_date", "list date", "listing date", "listed"),
    "sold_price": ("sold_price", "sold price", "sale price", "close price", "closed price"),
    "sold_date": ("sold_date", "sold date", "sale date", "close date", "closing date"),
    "bedrooms": ("bedrooms", "beds", "bed", "br", "bedrooms total", "total bedrooms"),
    "bathrooms": ("bathrooms", "baths", "bath", "ba", "bathrooms total", "total baths", "baths total"),
    "full_baths": ("full baths", "baths full", "full_baths"),
    "half_baths": ("half baths", "baths half", "half_baths"),
    "sqft": ("sqft", "square feet", "sq ft", "living area", "finished sqft", "above grade finished area", "gla"),
    "lot_sqft": ("lot_sqft", "lot sqft", "lot size sqft", "lot square feet"),
    "lot_acres": ("lot acres", "lot_acres", "acres", "lot size acres"),
    "year_built": ("year_built", "year built", "yr built"),
    "garage": ("garage", "has garage", "garage yn"),
    "garage_spaces": ("garage_spaces", "garage spaces", "garage stalls", "garage cars"),
    "basement": ("basement", "has basement", "basement yn", "basement type"),
    "basement_finished": ("basement_finished", "basement finished", "finished basement"),
    "stories": ("stories", "levels", "floors"),
    "pool": ("pool", "has pool"),
    "fireplace": ("fireplace", "fireplaces", "has fireplace"),
    "url": ("url", "link"),
}

NEGATIVE = {"", "0", "n", "no", "none", "false", "f", "slab", "crawl", "crawl space", "crawlspace", "n/a"}


def parse_flag(value) -> int | None:
    """Turn yes/no-ish text into 1/0. Anything descriptive (e.g. 'Full, Finished',
    'Attached 2 car') counts as present; blank means unknown."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if s == "":
        return None
    if s in NEGATIVE:
        return 0
    num = to_float(s)
    if num is not None:
        return 1 if num > 0 else 0
    return 1


def parse_finished(value) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    s = str(value).strip().lower()
    if "unfinished" in s:
        return 0
    if "finish" in s:
        return 1
    return parse_flag(s)


def parse_date(value) -> str | None:
    if not value or not str(value).strip():
        return None
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_status(value, sold_price) -> str:
    s = (value or "").strip().lower()
    if s in ("sold", "closed", "close"):
        return "sold"
    if s in ("pending", "contingent", "under contract", "active under contract"):
        return "pending"
    if s:
        return "active" if s in ("active", "new", "for sale", "coming soon") else s
    return "sold" if sold_price else "active"


def _resolve_columns(fieldnames: list[str]) -> dict[str, str]:
    # Compare on letters/digits only so "MLS #", "mls_number" and "Zip-Code" all match.
    key = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())  # noqa: E731
    norm = {key(f): f for f in reversed(fieldnames)}
    mapping = {}
    for field, aliases in ALIASES.items():
        for alias in aliases:
            if key(alias) in norm:
                mapping[field] = norm[key(alias)]
                break
    return mapping


def map_row(row: dict, cols: dict[str, str], source: str) -> dict:
    get = lambda f: row.get(cols[f]) if f in cols else None  # noqa: E731
    bathrooms = to_float(get("bathrooms"))
    if bathrooms is None and ("full_baths" in cols or "half_baths" in cols):
        bathrooms = (to_float(get("full_baths")) or 0) + 0.5 * (to_float(get("half_baths")) or 0)
    lot = to_float(get("lot_sqft"))
    if lot is None and to_float(get("lot_acres")) is not None:
        lot = to_float(get("lot_acres")) * 43560
    garage_spaces = to_float(get("garage_spaces"))
    garage = parse_flag(get("garage"))
    if garage is None and garage_spaces is not None:
        garage = 1 if garage_spaces > 0 else 0
    basement_raw = get("basement")
    basement = parse_flag(basement_raw)
    finished = parse_finished(get("basement_finished"))
    if finished is None and basement == 1:
        finished = parse_finished(basement_raw) if "finish" in str(basement_raw).lower() else None
    sold_price = to_float(get("sold_price"))
    address = (get("address") or "").strip() or None
    zip_code = (str(get("zip") or "").strip()[:5]) or None
    source_id = (get("source_id") or "").strip() if "source_id" in cols else ""
    if not source_id:
        source_id = hashlib.sha1(f"{address}|{zip_code}|{get('sold_date')}".lower().encode()).hexdigest()[:16]
    year = to_float(get("year_built"))
    return {
        "source": source,
        "source_id": source_id,
        "status": normalize_status(get("status"), sold_price),
        "address": address,
        "city": (get("city") or "").strip() or None,
        "zip": zip_code,
        "lat": to_float(get("lat")),
        "lon": to_float(get("lon")),
        "property_type": (get("property_type") or "").strip() or None,
        "list_price": to_float(get("list_price")),
        "list_date": parse_date(get("list_date")),
        "sold_price": sold_price,
        "sold_date": parse_date(get("sold_date")),
        "bedrooms": to_float(get("bedrooms")),
        "bathrooms": bathrooms,
        "sqft": to_float(get("sqft")),
        "lot_sqft": lot,
        "year_built": int(year) if year else None,
        "garage": garage,
        "garage_spaces": garage_spaces,
        "basement": basement,
        "basement_finished": finished,
        "stories": to_float(get("stories")),
        "pool": parse_flag(get("pool")),
        "fireplace": parse_flag(get("fireplace")),
        "url": (get("url") or "").strip() or None,
    }


class CSVImport(Source):
    name = "csv"
    label = "CSV import"
    kind = "listings"
    description = "Listings or past sales imported from a CSV (e.g. an MLS export) - supports basement/garage."

    def __init__(self, path: str | Path, source_name: str = "csv", restrict_to_region: bool = True):
        self.path = Path(path)
        self.source_name = source_name
        self.restrict_to_region = restrict_to_region

    def fetch(self, region: Region) -> Iterator[dict]:
        with self.path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            cols = _resolve_columns(reader.fieldnames or [])
            if "list_price" not in cols and "sold_price" not in cols:
                raise ValueError(f"{self.path}: needs a price column (one of {ALIASES['list_price']} "
                                 f"or {ALIASES['sold_price']})")
            for row in reader:
                item = map_row(row, cols, self.source_name)
                if self.restrict_to_region and item["zip"] not in region.zip_codes:
                    continue
                yield item
