import sqlite3

import httpx
from fastapi.testclient import TestClient

from realtymap import db, homes, properties
from realtymap.sources import run_source
from realtymap.sources.rentcast import RentCast, parse_record


def listing(**kw):
    base = {"source": "csv", "source_id": kw.get("address", "x"), "status": "active", "zip": "48706"}
    base.update(kw)
    return base


def upsert(conn, *items):
    with db.transaction(conn):
        db.upsert_listings(conn, items)


def test_address_key_normalizes_variants():
    a = properties.address_key("1205 N. Johnson Street, Bay City, MI 48708", None)
    b = properties.address_key("1205 North Johnson St", "48708")
    assert a == b == "1205 N JOHNSON ST|48708"
    assert properties.address_key("12 Elm Ave #3", "48706") == properties.address_key("12 Elm Avenue Apt 3", "48706")
    assert properties.address_key("", "48706") is None


def test_same_home_from_two_sources_is_one_property(conn):
    upsert(conn, listing(address="12 Elm Street", list_price=200000, bedrooms=3),
           listing(source="mls", source_id="M1", address="12 ELM ST, Bay City, MI 48706", sqft=1500,
                   status="sold", sold_price=195000, sold_date="2024-05-01"))
    assert conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0] == 1
    p = dict(conn.execute("SELECT * FROM properties").fetchone())
    assert p["bedrooms"] == 3 and p["sqft"] == 1500 and p["address"] == "12 ELM ST"


def test_listing_lifecycle_events(conn):
    upsert(conn, listing(address="1 Main St", list_price=200000, list_date="2026-01-10"))
    upsert(conn, listing(address="1 Main St", list_price=189000))           # price cut
    upsert(conn, listing(address="1 Main St", list_price=189000, status="pending"))
    upsert(conn, listing(address="1 Main St", list_price=189000, status="sold", sold_price=185000, sold_date="2026-03-01"))
    evts = [(e["event"], e["price"]) for e in homes.events(conn, 1)]
    assert ("listed", 200000) in evts
    assert ("price_change", 189000) in evts
    assert ("pending", 189000) in evts
    assert ("sold", 185000) in evts


def test_full_snapshot_marks_missing_listings_off_market(conn, region, monkeypatch):
    monkeypatch.setenv("RENTCAST_API_KEY", "test")
    batches = [["A", "B"], ["A"]]

    def handler(request):
        if request.url.params["zipCode"] != "48706":
            return httpx.Response(200, json=[])
        ids = batches[0]
        return httpx.Response(200, json=[{"id": i, "formattedAddress": f"{i} Oak St, Bay City, MI 48706",
                                          "zipCode": "48706", "price": 100000, "status": "Active"} for i in ids])

    client = httpx.Client(base_url="https://api.rentcast.io/v1", transport=httpx.MockTransport(handler))
    run_source(conn, RentCast(client=client), region)
    conn.execute("UPDATE listings SET last_seen = '2000-01-01T00:00:00+00:00'")  # pretend time passed
    batches.pop(0)
    run_source(conn, RentCast(client=client), region)
    status = dict(conn.execute("SELECT source_id, status FROM listings").fetchall())
    assert status == {"A": "active", "B": "off_market"}
    pid = conn.execute("SELECT property_id FROM listings WHERE source_id = 'B'").fetchone()[0]
    assert "off_market" in [e["event"] for e in homes.events(conn, pid)]


def test_parse_rentcast_record_skips_owner():
    facts, history = parse_record({
        "formattedAddress": "5 Bay St, Bay City, MI 48706", "zipCode": "48706", "bedrooms": 3, "assessorID": "160-1",
        "features": {"garage": True, "foundationType": "Basement"},
        "lastSaleDate": "2019-06-01T00:00:00.000Z", "lastSalePrice": 150000,
        "history": {"2019-06-01": {"event": "Sale", "date": "2019-06-01T00:00:00.000Z", "price": 150000},
                    "2010-03-02": {"event": "Sale", "date": "2010-03-02T00:00:00.000Z", "price": 90000}},
        "taxAssessments": {"2024": {"year": 2024, "value": 80000}},
        "owner": {"names": ["Jane Doe"]},
    })
    assert facts["garage"] == 1 and facts["basement"] == 1 and facts["parcel_id"] == "160-1"
    assert "owner" not in facts and "Jane Doe" not in str(facts)
    assert sorted((h["event"], h["date"], h["price"]) for h in history) == [
        ("assessment", "2024-01-01", 80000), ("sold", "2010-03-02", 90000),
        ("sold", "2019-06-01", 150000), ("sold", "2019-06-01", 150000)]


def test_sale_history_compares_to_area(conn, region):
    with db.transaction(conn):
        db.upsert_metrics(conn, [("zillow", "zip", "48706", f"{y}-01-01", "zhvi", v)
                                 for y, v in ((2016, 100000), (2020, 120000), (2026, 150000))])
    upsert(conn, listing(address="9 Pine St", status="sold", sold_price=100000, sold_date="2016-01-15", source_id="s1"),
           listing(address="9 Pine St", status="sold", sold_price=180000, sold_date="2026-01-15", source_id="s2"))
    d = homes.detail(conn, region, 1)
    first, second = d["sales"]
    assert "change_pct" not in first
    assert abs(second["change_pct"] - 0.8) < 1e-9
    assert abs(second["area_change_pct"] - 0.5) < 1e-9
    assert second["area_annual_pct"] < second["annual_pct"]
    assert d["valuation"]["index"]["from_price"] == 180000
    assert d["chart_index"][0]["value"] == 100000  # area line anchored at the first sale


def test_comparables_filter_by_size_beds_and_distance(conn, region):
    base = dict(status="sold", sold_date="2026-06-01", bedrooms=3, sqft=1500, lat=43.60, lon=-83.90)
    upsert(conn,
           listing(address="1 Subject St", status="active", list_price=200000, bedrooms=3, sqft=1500, lat=43.60, lon=-83.90),
           listing(address="2 Near St", sold_price=190000, **base),
           listing(address="3 Huge St", sold_price=400000, **{**base, "sqft": 3200}),
           listing(address="4 Far St", sold_price=190000, **{**base, "lat": 44.2}),
           listing(address="5 Studio St", sold_price=90000, **{**base, "bedrooms": 1}),
           listing(address="6 Old Sale St", sold_price=150000, **{**base, "sold_date": "2015-01-01"}))
    subject = homes.get_property(conn, 1)
    assert [c["address"] for c in homes.comparables(conn, region, subject)] == ["2 Near St"]


def test_migrates_database_without_properties(tmp_path):
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.executescript("""CREATE TABLE listings (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
        source_id TEXT NOT NULL, status TEXT NOT NULL, address TEXT, city TEXT, zip TEXT, lat REAL, lon REAL,
        property_type TEXT, list_price REAL, list_date TEXT, sold_price REAL, sold_date TEXT, bedrooms REAL,
        bathrooms REAL, sqft REAL, lot_sqft REAL, year_built INTEGER, garage INTEGER, garage_spaces REAL,
        basement INTEGER, basement_finished INTEGER, stories REAL, pool INTEGER, fireplace INTEGER, url TEXT,
        first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, UNIQUE (source, source_id));
        INSERT INTO listings (source, source_id, status, address, zip, sold_price, sold_date, first_seen, last_seen)
        VALUES ('csv', 'a', 'sold', '7 Lake St', '48706', 120000, '2023-04-01', '2024-01-01', '2024-01-01');""")
    old.commit()
    old.close()
    conn = db.connect(path)
    assert conn.execute("SELECT property_id FROM listings").fetchone()[0] == 1
    assert [(e["event"], e["price"]) for e in homes.events(conn, 1)] == [("sold", 120000)]


def test_home_api(tmp_path, monkeypatch, region):
    path = tmp_path / "api.sqlite3"
    monkeypatch.setenv("REALTYMAP_DB", str(path))
    conn = db.connect(path)
    upsert(conn, listing(address="8 Shore Dr", list_price=210000, bedrooms=3, sqft=1400))

    from realtymap import api
    client = TestClient(api.app)
    (hit,) = client.get("/api/homes", params={"q": "8 shore"}).json()
    detail = client.get(f"/api/homes/{hit['id']}").json()
    assert detail["current_listing"]["list_price"] == 210000
    assert client.get("/api/homes/999").status_code == 404

    record = {"zipCode": "48706", "lastSaleDate": "2018-07-01", "lastSalePrice": 140000, "features": {"garage": True}}
    monkeypatch.setattr("realtymap.sources.rentcast.lookup_property", lambda address: record)
    looked = client.post(f"/api/homes/{hit['id']}/lookup").json()
    assert looked["property"]["garage"] == 1 and looked["property"]["looked_up"]
    assert [s["price"] for s in looked["sales"]] == [140000]
    sales = client.get("/api/sales", params={"months": 0}).json()
    assert sales[0]["sold_price"] == 140000


def test_clear_demo_removes_homes(conn, region):
    from realtymap import demo
    demo.load(conn, region, 30)
    assert conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0] == 30
    with db.transaction(conn):
        conn.execute("DELETE FROM listings WHERE source = 'demo'")
        properties.delete_source(conn, "demo")
    assert conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM property_events").fetchone()[0] == 0
