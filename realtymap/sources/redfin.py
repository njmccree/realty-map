"""Redfin Data Center - monthly market tracker based on closed sales.

Free, public. Provides what actually *sold*: median sale price, homes sold,
sale-to-list ratio, inventory, months of supply and more. The county file is
~240 MB compressed; the ZIP file is ~1.5 GB so it is opt-in (``--redfin-zips``).

https://www.redfin.com/news/data-center/
"""

from __future__ import annotations

from typing import Iterator

from ..config import Region
from .base import Source, parse_csv_line, stream_lines, to_float

BASE = "https://redfin-public-data.s3.us-west-2.amazonaws.com/redfin_market_tracker"
COUNTY_URL = f"{BASE}/county_market_tracker.tsv000.gz"
ZIP_URL = f"{BASE}/zip_code_market_tracker.tsv000.gz"

METRICS = {
    "MEDIAN_SALE_PRICE": "median_sale_price",
    "MEDIAN_LIST_PRICE": "median_list_price",
    "MEDIAN_PPSF": "median_sale_ppsf",
    "HOMES_SOLD": "homes_sold",
    "PENDING_SALES": "pending_sales",
    "NEW_LISTINGS": "new_listings",
    "INVENTORY": "inventory",
    "MONTHS_OF_SUPPLY": "months_of_supply",
    "MEDIAN_DOM": "median_dom",
    "AVG_SALE_TO_LIST": "avg_sale_to_list",
    "SOLD_ABOVE_LIST": "sold_above_list",
    "PRICE_DROPS": "price_drops",
}

PROPERTY_TYPE = "All Residential"


class Redfin(Source):
    name = "redfin"
    label = "Redfin Data Center"
    kind = "metrics"
    description = "Monthly closed-sale data: median sale price, homes sold, sale-to-list ratio."
    homepage = "https://www.redfin.com/news/data-center/"

    def __init__(self, county_url: str = COUNTY_URL, zip_url: str = ZIP_URL, include_zips: bool = False):
        self.county_url = county_url
        self.zip_url = zip_url
        self.include_zips = include_zips

    def fetch(self, region: Region) -> Iterator[tuple]:
        yield from self._read(self.county_url, {region.redfin_county_name: ("county", region.full_county_fips)})
        if self.include_zips:
            yield from self._read(self.zip_url, {f"Zip Code: {z}": ("zip", z) for z in region.zip_codes})

    def _read(self, url: str, wanted: dict[str, tuple[str, str]]) -> Iterator[tuple]:
        lines = stream_lines(url, gz=url.endswith(".gz"))
        header = [h.strip('"') for h in parse_csv_line(next(lines), "\t")]
        idx = {h: i for i, h in enumerate(header)}
        needles = tuple(f'"{w}"' for w in wanted)
        for line in lines:
            if not any(n in line for n in needles):
                continue
            row = [c.strip('"') for c in parse_csv_line(line, "\t")]
            if len(row) < len(header):
                continue
            target = wanted.get(row[idx["REGION"]])
            if target is None:
                continue
            if row[idx["PROPERTY_TYPE"]] != PROPERTY_TYPE:
                continue
            if row[idx["IS_SEASONALLY_ADJUSTED"]].lower() == "true":
                continue
            if row[idx["PERIOD_DURATION"]] not in ("30", ""):
                continue
            geo_type, geo_id = target
            period = row[idx["PERIOD_BEGIN"]][:7] + "-01"
            for col, metric in METRICS.items():
                value = to_float(row[idx[col]]) if col in idx else None
                if value is not None:
                    yield (self.name, geo_type, geo_id, period, metric, value)
