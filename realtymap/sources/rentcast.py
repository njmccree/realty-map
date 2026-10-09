"""RentCast API - individual for-sale listings (the micro-level data).

Requires an API key (RENTCAST_API_KEY); there is a free developer tier with a
small monthly request quota. Returns each active listing with price,
bedrooms, bathrooms, square footage, lot size, year built and coordinates.

Optionally (``--enrich N``) each listing is matched to its property record to
pull features such as garage and foundation/basement, plus the home's past
sales and tax assessments. Each enrichment costs one API request, so it is
capped. Single homes can also be looked up on demand from the home detail
view or with ``realtymap lookup "<address>"``.

Owner names and mailing addresses in property records are deliberately not
stored.

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


def _date(v) -> str | None:
    return str(v)[:10] if v else None


def parse_record(prop: dict) -> tuple[dict, list[dict]]:
    """Split a RentCast property record into (facts, history events)."""
    features = prop.get("features") or {}
    facts = {
        "address": prop.get("formattedAddress") or prop.get("addressLine1"),
        "city": prop.get("city"),
        "zip": str(prop.get("zipCode") or "")[:5] or None,
        "lat": to_float(prop.get("latitude")),
        "lon": to_float(prop.get("longitude")),
        "property_type": prop.get("propertyType"),
        "bedrooms": to_float(prop.get("bedrooms")),
        "bathrooms": to_float(prop.get("bathrooms")),
        "sqft": to_float(prop.get("squareFootage")),
        "lot_sqft": to_float(prop.get("lotSize")),
        "year_built": int(prop["yearBuilt"]) if prop.get("yearBuilt") else None,
        "garage": _bool(features.get("garage")),
        "garage_spaces": to_float(features.get("garageSpaces")),
        "pool": _bool(features.get("pool")),
        "fireplace": _bool(features.get("fireplace")),
        "stories": to_float(features.get("floorCount")),
        "parcel_id": prop.get("assessorID"),
    }
    # Only assert a basement when the record says so; absence is "unknown".
    if "basement" in (features.get("foundationType") or "").lower():
        facts["basement"] = 1

    history = []
    for item in (prop.get("history") or {}).values():
        if (item.get("event") or "").lower() == "sale" and item.get("price"):
            history.append({"event": "sold", "date": _date(item.get("date")), "price": to_float(item["price"])})
    if prop.get("lastSalePrice") and prop.get("lastSaleDate"):
        history.append({"event": "sold", "date": _date(prop["lastSaleDate"]), "price": to_float(prop["lastSalePrice"])})
    for year, item in (prop.get("taxAssessments") or {}).items():
        value = to_float(item.get("value"))
        if value:
            history.append({"event": "assessment", "date": f"{int(item.get('year') or year)}-01-01", "price": value,
                            "note": "assessed value"})
    return facts, history


def apply_features(listing: dict, prop: dict) -> dict:
    facts, history = parse_record(prop)
    for key in ("garage", "garage_spaces", "pool", "fireplace", "stories", "basement"):
        if facts.get(key) is not None:
            listing[key] = facts[key]
    for key in ("bedrooms", "bathrooms", "sqft", "lot_sqft", "year_built"):
        if listing.get(key) is None:
            listing[key] = facts.get(key)
    listing["_facts"] = facts
    listing["_history"] = history
    return listing


def lookup_property(address: str, client: httpx.Client | None = None) -> dict | None:
    """Fetch one property record by full address (one API request)."""
    client = client or RentCast().client_()
    resp = client.get("/properties", params={"address": address})
    resp.raise_for_status()
    records = resp.json()
    return records[0] if records else None


class RentCast(Source):
    name = "rentcast"
    label = "RentCast listings"
    kind = "listings"
    description = "Individual active for-sale listings with beds, baths, size, lot and location."
    homepage = "https://www.rentcast.io/api"
    api_key_env = "RENTCAST_API_KEY"
    #: each run returns every active listing, so ones missing have left the market
    full_snapshot = True

    def __init__(self, enrich: int = 0, client: httpx.Client | None = None):
        self.enrich = enrich
        self.client = client

    def client_(self) -> httpx.Client:
        if self.client is None:
            self.client = httpx.Client(
                base_url=API, timeout=TIMEOUT,
                headers={"X-Api-Key": self.api_key(), "Accept": "application/json", "User-Agent": USER_AGENT},
            )
        return self.client

    def fetch(self, region: Region) -> Iterator[dict]:
        client = self.client_()
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
