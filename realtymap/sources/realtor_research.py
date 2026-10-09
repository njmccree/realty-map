"""Realtor.com Economic Research - monthly inventory metrics.

Free, public, updated monthly. Covers homes currently *on the market*:
median / average listing price, active listing count, days on market,
price per square foot and median listing size, by ZIP and by county.

https://www.realtor.com/research/data/
"""

from __future__ import annotations

from typing import Iterator

from ..config import Region
from .base import Source, parse_csv_line, stream_lines, to_float

BASE = "https://econdata.s3-us-west-2.amazonaws.com/Reports/Core"
ZIP_URL = f"{BASE}/RDC_Inventory_Core_Metrics_Zip_History.csv"
COUNTY_URL = f"{BASE}/RDC_Inventory_Core_Metrics_County_History.csv"

METRICS = (
    "median_listing_price",
    "average_listing_price",
    "active_listing_count",
    "new_listing_count",
    "pending_listing_count",
    "total_listing_count",
    "median_days_on_market",
    "median_listing_price_per_square_foot",
    "median_square_feet",
    "price_reduced_share",
    "price_increased_share",
)


class RealtorResearch(Source):
    name = "realtor"
    label = "Realtor.com Research"
    kind = "metrics"
    description = "Monthly on-market inventory: listing prices, active listings, days on market, size."
    homepage = "https://www.realtor.com/research/data/"

    def __init__(self, zip_url: str = ZIP_URL, county_url: str = COUNTY_URL):
        self.zip_url = zip_url
        self.county_url = county_url

    def fetch(self, region: Region) -> Iterator[tuple]:
        yield from self._read(self.county_url, "county", "county_fips", {region.full_county_fips}, pad=5)
        yield from self._read(self.zip_url, "zip", "postal_code", region.zip_codes, pad=5)

    def _read(self, url: str, geo_type: str, id_col: str, wanted: set[str], pad: int) -> Iterator[tuple]:
        lines = stream_lines(url)
        header = parse_csv_line(next(lines))
        idx = {h: i for i, h in enumerate(header)}
        id_i, month_i = idx[id_col], idx["month_date_yyyymm"]
        metric_idx = [(m, idx[m]) for m in METRICS if m in idx]
        # Cheap pre-filter: only parse lines that contain one of the wanted ids.
        needles = tuple(f",{w}," for w in wanted) + tuple(f",{w.lstrip('0')}," for w in wanted)
        for line in lines:
            if not any(n in line for n in needles):
                continue
            row = parse_csv_line(line)
            if len(row) <= id_i or not row[month_i].isdigit():
                continue  # footer / notes rows
            geo_id = row[id_i].strip().zfill(pad)
            if geo_id not in wanted:
                continue
            ym = row[month_i]
            period = f"{ym[:4]}-{ym[4:6]}-01"
            for metric, i in metric_idx:
                value = to_float(row[i]) if i < len(row) else None
                if value is not None:
                    yield (self.name, geo_type, geo_id, period, metric, value)
