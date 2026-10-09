import json

import httpx

from realtymap.sources import run_source
from realtymap.sources.census import CensusACS
from realtymap.sources.csv_import import CSVImport, parse_flag
from realtymap.sources.realtor_research import RealtorResearch
from realtymap.sources.redfin import Redfin
from realtymap.sources.rentcast import RentCast
from realtymap.sources.zillow import ZillowZHVI

from .conftest import FIXTURES


def as_dict(rows):
    return {(r[1], r[2], r[3], r[4]): r[5] for r in rows}


def test_realtor_filters_to_region(region):
    src = RealtorResearch(zip_url=str(FIXTURES / "realtor_zip.csv"), county_url=str(FIXTURES / "realtor_county.csv"))
    rows = as_dict(src.fetch(region))
    assert rows[("county", "26017", "2026-09-01", "median_listing_price")] == 184325.0
    assert rows[("zip", "48706", "2026-09-01", "average_listing_price")] == 300117.0
    assert rows[("zip", "48706", "2025-09-01", "active_listing_count")] == 80
    assert {k[1] for k in rows} == {"26017", "48706"}  # no SD/NJ/CA rows, no footer


def test_redfin_county_and_zip(region):
    path = str(FIXTURES / "redfin.tsv.gz")
    rows = as_dict(Redfin(county_url=path, zip_url=path, include_zips=True).fetch(region))
    assert rows[("county", "26017", "2026-05-01", "median_sale_price")] == 171800
    assert rows[("county", "26017", "2026-05-01", "homes_sold")] == 82
    assert rows[("zip", "48706", "2026-05-01", "median_sale_price")] == 150000
    assert len([k for k in rows if k[0] == "county"]) == 3  # only All Residential, not SA, not FL


def test_zillow(region):
    src = ZillowZHVI(zip_url=str(FIXTURES / "zillow_zip.csv"), county_url=str(FIXTURES / "zillow_county.csv"))
    rows = as_dict(src.fetch(region))
    assert rows == {
        ("county", "26017", "2025-08-01", "zhvi"): 170000,
        ("county", "26017", "2025-09-01", "zhvi"): 171000,
        ("zip", "48706", "2025-08-01", "zhvi"): 190000.5,
        ("zip", "48706", "2025-09-01", "zhvi"): 191000.25,
    }


def test_census_derives_bedrooms_and_falls_back_a_year(region):
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        if "/2024/" in request.url.path:
            return httpx.Response(404)
        header = ["NAME", "B25041_001E", "B25041_002E", "B25041_003E", "B25041_004E", "B25041_005E",
                  "B25041_006E", "B25041_007E", "B25077_001E", "B25003_001E", "B25003_002E"]
        vals = ["x", "100", "0", "10", "30", "40", "15", "5", "-666666666", "80", "60"]
        if "county" in request.url.params["for"]:
            return httpx.Response(200, json=[header + ["state", "county"], vals + ["26", "017"]])
        return httpx.Response(200, json=[header + ["zip code tabulation area"], vals + ["48706"]])

    src = CensusACS(client=httpx.Client(transport=httpx.MockTransport(handler)))
    rows = as_dict(src.fetch(region))
    assert any("/2023/" in c for c in calls)
    key = ("zip", "48706", "2023-01-01", "avg_bedrooms")
    assert abs(rows[key] - (10 + 60 + 120 + 60 + 25) / 100) < 1e-9
    assert rows[("county", "26017", "2023-01-01", "owner_occupied_share")] == 0.75
    assert ("zip", "48706", "2023-01-01", "median_home_value") not in rows  # sentinel dropped


def test_rentcast_maps_listings_and_features(region, monkeypatch):
    monkeypatch.setenv("RENTCAST_API_KEY", "test")

    def handler(request: httpx.Request):
        if request.url.path.endswith("/listings/sale"):
            if request.url.params["zipCode"] != "48706":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[{
                "id": "1-Main", "formattedAddress": "1 Main St, Bay City, MI 48706", "city": "Bay City",
                "zipCode": "48706", "latitude": 43.6, "longitude": -83.9, "price": 199000, "bedrooms": 3,
                "bathrooms": 1.5, "squareFootage": 1400, "yearBuilt": 1950, "status": "Active",
                "listedDate": "2026-09-01T00:00:00.000Z"}])
        return httpx.Response(200, json=[{"features": {"garage": True, "garageSpaces": 2,
                                                       "foundationType": "Basement", "fireplace": False}}])

    client = httpx.Client(base_url="https://api.rentcast.io/v1", transport=httpx.MockTransport(handler))
    (listing,) = list(RentCast(enrich=5, client=client).fetch(region))
    assert listing["list_price"] == 199000 and listing["zip"] == "48706"
    assert listing["list_date"] == "2026-09-01"
    assert listing["garage"] == 1 and listing["garage_spaces"] == 2
    assert listing["basement"] == 1 and listing["fireplace"] == 0


def test_rentcast_without_key_is_skipped(conn, region, monkeypatch):
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    try:
        run_source(conn, RentCast(), region)
    except Exception as exc:
        assert "RENTCAST_API_KEY" in str(exc)
    status = conn.execute("SELECT status FROM ingest_runs").fetchone()[0]
    assert status == "skipped"


def test_parse_flag():
    assert parse_flag("Yes") == 1 and parse_flag("No") == 0 and parse_flag("") is None
    assert parse_flag("Full, Finished") == 1 and parse_flag("Crawl Space") == 0 and parse_flag("2") == 1


def test_csv_import_aliases(conn, region):
    n = run_source(conn, CSVImport(FIXTURES / "listings_mls.csv"), region)
    assert n == 3  # Midland ZIP is outside the region
    rows = {r["source_id"]: dict(r) for r in conn.execute("SELECT * FROM listings")}
    a1, a2, a4 = rows["A1"], rows["A2"], rows["A4"]
    assert a1["list_price"] == 199900 and a1["sqft"] == 1450 and a1["bathrooms"] == 1.5
    assert abs(a1["lot_sqft"] - 0.25 * 43560) < 1e-6
    assert a1["garage"] == 1 and a1["basement"] == 1 and a1["basement_finished"] == 1
    assert a2["status"] == "sold" and a2["sold_date"] == "2026-03-15" and a2["garage"] == 0 and a2["basement"] == 0
    assert a4["status"] == "pending" and a4["basement"] == 1 and a4["basement_finished"] == 0 and a4["garage"] is None
    # re-import updates in place rather than duplicating
    run_source(conn, CSVImport(FIXTURES / "listings_mls.csv"), region)
    assert conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 3
