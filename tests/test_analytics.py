import random

from fastapi.testclient import TestClient

from realtymap import analytics, db, demo


def test_change_summary():
    pts = [{"period": f"{y}-{m:02d}-01", "value": 100 + (y - 2020) * 12 + m} for y in range(2020, 2027) for m in range(1, 13)]
    s = analytics.change_summary(pts)
    assert s["period"] == "2026-12-01"
    assert s["changes"]["1y"]["previous"] == pts[-13]["value"]
    assert s["changes"]["5y"]["previous"] == pts[-61]["value"]


def test_ols_recovers_coefficients():
    rng = random.Random(1)
    X, y = [], []
    for _ in range(200):
        a, b = rng.uniform(0, 10), rng.randint(0, 1)
        X.append([1.0, a, b])
        y.append(5 + 3 * a + 7 * b + rng.gauss(0, 0.01))
    beta, ses, r2 = analytics.ols(X, y)
    assert [round(v, 2) for v in beta] == [5, 3, 7]
    assert r2 > 0.999


def test_amenities_and_api(tmp_path, monkeypatch, region):
    path = tmp_path / "api.sqlite3"
    monkeypatch.setenv("REALTYMAP_DB", str(path))
    conn = db.connect(path)
    demo.load(conn, region, 200)
    with db.transaction(conn):
        db.upsert_metrics(conn, [
            ("realtor", "county", "26017", "2025-09-01", "median_listing_price", 200000),
            ("realtor", "county", "26017", "2026-09-01", "median_listing_price", 210000),
        ])
    a = analytics.amenities(conn, region)
    assert a["count"] == 200
    assert {t["term"] for t in a["model"]["terms"]} >= {"sqft", "garage", "basement"}
    garage = next(t for t in a["model"]["terms"] if t["term"] == "garage")
    assert 8000 < garage["estimate"] < 25000  # demo data is generated with a $16k garage premium

    from realtymap.api import app
    client = TestClient(app)
    assert client.get("/api/region").json()["demo_listings"] == 200
    kpi = client.get("/api/summary").json()["kpis"]["median_listing_price"]
    assert abs(kpi["changes"]["1y"]["pct"] - 0.05) < 1e-9
    assert client.get("/api/summary", params={"zip": "90210"}).status_code == 404
    assert client.get("/api/trends", params={"metric": "zhvi", "zip": "48706"}).status_code == 200
    assert len(client.get("/api/zips").json()) == len(region.zips)
    assert client.get("/api/listings", params={"status": "sold"}).status_code == 200
    assert client.get("/").status_code == 200
