"""Zillow Research - Zillow Home Value Index (ZHVI).

Free, public. A smoothed, seasonally adjusted estimate of the typical home
value (all homes, not just those for sale) by month, going back to 2000.

https://www.zillow.com/research/data/
"""

from __future__ import annotations

import re
from typing import Iterator

from ..config import Region
from .base import Source, parse_csv_line, stream_lines, to_float

BASE = "https://files.zillowstatic.com/research/public_csvs/zhvi"
ZIP_URL = f"{BASE}/Zip_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv"
COUNTY_URL = f"{BASE}/County_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ZillowZHVI(Source):
    name = "zillow"
    label = "Zillow Home Value Index"
    kind = "metrics"
    description = "Typical home value (ZHVI) for all homes, monthly since 2000."
    homepage = "https://www.zillow.com/research/data/"

    def __init__(self, zip_url: str = ZIP_URL, county_url: str = COUNTY_URL):
        self.zip_url = zip_url
        self.county_url = county_url

    def fetch(self, region: Region) -> Iterator[tuple]:
        yield from self._read(self.county_url, region, "county")
        yield from self._read(self.zip_url, region, "zip")

    def _read(self, url: str, region: Region, geo_type: str) -> Iterator[tuple]:
        lines = stream_lines(url)
        header = parse_csv_line(next(lines))
        idx = {h: i for i, h in enumerate(header)}
        date_cols = [(h[:7] + "-01", i) for h, i in idx.items() if DATE_RE.match(h)]
        for line in lines:
            if f",{region.state}," not in line and f'"{region.state}"' not in line:
                continue
            row = parse_csv_line(line)
            if row[idx["State"]] != region.state:
                continue
            if geo_type == "zip":
                geo_id = row[idx["RegionName"]].zfill(5)
                if geo_id not in region.zip_codes:
                    continue
            else:
                fips = row[idx["StateCodeFIPS"]].zfill(2) + row[idx["MunicipalCodeFIPS"]].zfill(3)
                if fips != region.full_county_fips:
                    continue
                geo_id = fips
            for period, i in date_cols:
                value = to_float(row[i]) if i < len(row) else None
                if value is not None:
                    yield (self.name, geo_type, geo_id, period, "zhvi", value)
