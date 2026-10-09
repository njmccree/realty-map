"""US Census Bureau American Community Survey (ACS) 5-year estimates.

Free (an API key is optional but recommended: CENSUS_API_KEY). Describes the
whole housing stock - not just homes for sale - so it gives the baseline
"typical home" in each ZIP: bedroom mix, median value, age, owner share.

https://www.census.gov/data/developers/data-sets/acs-5year.html
"""

from __future__ import annotations

import os
from typing import Iterator

import httpx

from ..config import Region
from .base import TIMEOUT, USER_AGENT, Source, to_float

API = "https://api.census.gov/data/{year}/acs/acs5"

VARIABLES = {
    "B25001_001E": "housing_units",
    "B25003_001E": "occupied_units",
    "B25003_002E": "owner_occupied_units",
    "B25077_001E": "median_home_value",
    "B25035_001E": "median_year_built",
    "B25018_001E": "median_rooms",
    "B25041_001E": "bedrooms_total",
    "B25041_002E": "bedrooms_0",
    "B25041_003E": "bedrooms_1",
    "B25041_004E": "bedrooms_2",
    "B25041_005E": "bedrooms_3",
    "B25041_006E": "bedrooms_4",
    "B25041_007E": "bedrooms_5plus",
}

DEFAULT_YEARS = (2024, 2023, 2022)


def derived(values: dict[str, float | None]) -> dict[str, float]:
    out: dict[str, float] = {}
    total = values.get("bedrooms_total")
    if total:
        counts = [values.get(f"bedrooms_{k}") or 0 for k in ("0", "1", "2", "3", "4", "5plus")]
        # 5+ is counted as 5, so this is a slight under-estimate.
        out["avg_bedrooms"] = sum(n * c for n, c in enumerate(counts)) / total
        out["share_3plus_bedrooms"] = sum(counts[3:]) / total
    occupied = values.get("occupied_units")
    if occupied and values.get("owner_occupied_units") is not None:
        out["owner_occupied_share"] = values["owner_occupied_units"] / occupied
    return out


class CensusACS(Source):
    name = "census"
    label = "Census ACS 5-year"
    kind = "metrics"
    description = "Housing stock profile: bedroom mix, median value, median year built, owner share."
    homepage = "https://www.census.gov/programs-surveys/acs"

    def __init__(self, years: tuple[int, ...] = DEFAULT_YEARS, client: httpx.Client | None = None):
        self.years = years
        self.client = client

    def fetch(self, region: Region) -> Iterator[tuple]:
        client = self.client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        last_error: Exception | None = None
        for year in self.years:
            try:
                rows = list(self._fetch_year(client, region, year))
            except httpx.HTTPError as exc:  # the newest vintage may not be published yet
                last_error = exc
                continue
            yield from rows
            return
        raise RuntimeError(f"Census ACS request failed for years {self.years}: {last_error}")

    def _fetch_year(self, client: httpx.Client, region: Region, year: int) -> Iterator[tuple]:
        get = "NAME," + ",".join(VARIABLES)
        key = os.environ.get("CENSUS_API_KEY", "").strip()
        queries = [
            ("county", {"get": get, "for": f"county:{region.county_fips}", "in": f"state:{region.state_fips}"}),
            ("zip", {"get": get, "for": "zip code tabulation area:" + ",".join(sorted(region.zip_codes))}),
        ]
        period = f"{year}-01-01"
        for geo_type, params in queries:
            if key:
                params["key"] = key
            resp = client.get(API.format(year=year), params=params)
            resp.raise_for_status()
            table = resp.json()
            header, *data = table
            for row in data:
                rec = dict(zip(header, row))
                if geo_type == "county":
                    geo_id = rec["state"] + rec["county"]
                else:
                    geo_id = rec["zip code tabulation area"]
                values = {}
                for var, metric in VARIABLES.items():
                    v = to_float(rec.get(var))
                    # The ACS uses large negative sentinels for "not available".
                    values[metric] = v if v is not None and v >= 0 else None
                values.update(derived(values))
                for metric, v in values.items():
                    if v is not None:
                        yield (self.name, geo_type, geo_id, period, metric, v)
