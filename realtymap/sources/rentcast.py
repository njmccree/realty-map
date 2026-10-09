"""RentCast API - individual for-sale listings (the micro-level data).

Requires an API key (RENTCAST_API_KEY); there is a free developer tier with a
small monthly request quota. Returns each active listing with price,
bedrooms, bathrooms, square footage, lot size, year built and coordinates.

Optionally (``--enrich N``) each listing is matched to its property record to
pull features such as garage and foundation/basement. Each enrichment costs
one API request, so it is capped.

https://developers.rentcast.io/
"""

from __future__ import annotations

from typing import Iterator

import httpx

from ..config import Region
from .base import TIMEOUT, USER_AGENT, Source, to_float

API = "https://api.rentcast.io/v1"
PAGE_SIZE = 500


def _bool(v) -> int | None:
    if v is None:
        return None
    return 1 if v else 0


def map_listing(item: dict) -> dict:
    status = (item.get("status") or "Active").lower()
    return {
        "source": "rentcast",
        "source_id": str(item.get("id") or item.get("formattedAddress")),
        "status": "active" if status == "active" else "inactive",
        "address": item.get("formattedAddress") or item.get("addressLine1"),
        "city": item.get("city"),
        "zip": str(item.get("zipCode") or "")[:5] or None,
        "lat": to_float(item.get("latitude")),
        "lon": to_float(item.get("longitude")),
        "property_type": item.get("propertyType"),
        "list_price": to_float(item.get("price")),
        "list_date": (item.get("listedDate") or "")[:10] or None,
        "bedrooms": to_float(item.get("bedrooms")),
        "bathrooms": to_float(item.get("bathrooms")),
        "sqft": to_float(item.get("squareFootage")),
        "lot_sqft": to_float(item.get("lotSize")),
        "year_built": int(item["yearBuilt"]) if item.get("yearBuilt") else None,
    }


def apply_features(listing: dict, prop: dict) -> dict:
    features = prop.get("features") or {}
    listing["garage"] = _bool(features.get("garage"))
    listing["garage_spaces"] = to_float(features.get("garageSpaces"))
    listing["pool"] = _bool(features.get("pool"))
    listing["fireplace"] = _bool(features.get("fireplace"))
    listing["stories"] = to_float(features.get("floorCount"))
    foundation = (features.get("foundationType") or "").lower()
    # Only assert a basement when the record says so; absence is "unknown".
    if "basement" in foundation:
        listing["basement"] = 1
    for key in ("bedrooms", "bathrooms", "sqft", "lot_sqft", "year_built"):
        if listing.get(key) is None:
            src = {"sqft": "squareFootage", "lot_sqft": "lotSize", "year_built": "yearBuilt"}.get(key, key)
            listing[key] = prop.get(src)
    return listing


class RentCast(Source):
    name = "rentcast"
    label = "RentCast listings"
    kind = "listings"
    description = "Individual active for-sale listings with beds, baths, size, lot and location."
    homepage = "https://www.rentcast.io/api"
    api_key_env = "RENTCAST_API_KEY"

    def __init__(self, enrich: int = 0, client: httpx.Client | None = None):
        self.enrich = enrich
        self.client = client

    def _client(self) -> httpx.Client:
        if self.client is None:
            self.client = httpx.Client(
                base_url=API, timeout=TIMEOUT,
                headers={"X-Api-Key": self.api_key(), "Accept": "application/json", "User-Agent": USER_AGENT},
            )
        return self.client

    def fetch(self, region: Region) -> Iterator[dict]:
        client = self._client()
        enriched = 0
        for zip_code in sorted(region.zip_codes):
            offset = 0
            while True:
                resp = client.get("/listings/sale", params={
                    "zipCode": zip_code, "status": "Active", "limit": PAGE_SIZE, "offset": offset,
                })
                resp.raise_for_status()
                page = resp.json()
                for item in page:
                    listing = map_listing(item)
                    if enriched < self.enrich and listing["address"]:
                        r = client.get("/properties", params={"address": listing["address"]})
                        if r.status_code == 200 and r.json():
                            apply_features(listing, r.json()[0])
                        enriched += 1
                    yield listing
                if len(page) < PAGE_SIZE:
                    break
                offset += PAGE_SIZE
