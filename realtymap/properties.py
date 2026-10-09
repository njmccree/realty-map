"""Physical homes and their history.

Listings come from several sources and the same house can appear many times:
listed in 2019, sold, relisted in 2026, imported again from an MLS export.
Every listing is matched to one ``properties`` row by a normalized street
address + ZIP, and changes are recorded as ``property_events``:

    listed        a listing first appears (dated by its list date when known)
    price_change  the asking price of an active listing changes
    pending       the listing goes under contract
    sold          a closed sale with its price
    off_market    an active listing disappears without a recorded sale
    assessment    a tax assessment (from property-record sources)
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date

from .db import now_iso

ACTIVE = ("active", "pending")
# Characteristics copied from listings onto the property (newest non-empty value wins).
FACT_FIELDS = (
    "address", "city", "zip", "lat", "lon", "property_type", "bedrooms", "bathrooms", "sqft", "lot_sqft",
    "year_built", "garage", "garage_spaces", "basement", "basement_finished", "stories", "pool", "fireplace",
)

ABBREVIATIONS = {
    "STREET": "ST", "AVENUE": "AVE", "AV": "AVE", "ROAD": "RD", "DRIVE": "DR", "LANE": "LN", "COURT": "CT",
    "BOULEVARD": "BLVD", "PLACE": "PL", "CIRCLE": "CIR", "TERRACE": "TER", "HIGHWAY": "HWY", "PARKWAY": "PKWY",
    "TRAIL": "TRL", "POINT": "PT", "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
    "APARTMENT": "UNIT", "APT": "UNIT", "SUITE": "UNIT", "STE": "UNIT", "LOT": "UNIT",
}


def address_key(address: str | None, zip_code: str | None) -> str | None:
    """'1205 N. Johnson Street, Bay City, MI 48708' -> '1205 N JOHNSON ST|48708'."""
    if not address or not address.strip():
        return None
    street = address.split(",")[0].upper().replace("#", " UNIT ")
    words = re.sub(r"[^A-Z0-9 ]", " ", street).split()
    norm = " ".join(ABBREVIATIONS.get(w, w) for w in words)
    if not norm:
        return None
    if not zip_code:
        m = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", address)
        zip_code = m.group(1) if m else ""
    return f"{norm}|{zip_code or ''}"


def _street(address: str) -> str:
    return address.split(",")[0].strip()


def get_or_create_property(conn: sqlite3.Connection, record: dict) -> int | None:
    key = address_key(record.get("address"), record.get("zip"))
    if key is None:
        return None
    row = conn.execute("SELECT id FROM properties WHERE address_key = ?", (key,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO properties (address_key, address, zip, updated) VALUES (?, ?, ?, ?)",
        (key, _street(record["address"]), record.get("zip") or key.split("|")[1] or None, now_iso()),
    )
    return cur.lastrowid


def update_facts(conn: sqlite3.Connection, property_id: int, facts: dict) -> None:
    values = {f: facts.get(f) for f in FACT_FIELDS if facts.get(f) not in (None, "")}
    if "address" in values:
        values["address"] = _street(values["address"])
    if facts.get("parcel_id"):
        values["parcel_id"] = facts["parcel_id"]
    if not values:
        return
    sets = ", ".join(f"{f} = ?" for f in values)
    conn.execute(f"UPDATE properties SET {sets}, updated = ? WHERE id = ?",
                 [*values.values(), now_iso(), property_id])


def add_event(conn: sqlite3.Connection, property_id: int, event: str, event_date: str | None, price: float | None,
              source: str, listing_id: int | None = None, note: str | None = None) -> None:
    if not event_date:
        return
    conn.execute(
        """INSERT INTO property_events (property_id, event_date, event, price, source, listing_id, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (property_id, event, event_date, source) DO UPDATE SET
             price = COALESCE(excluded.price, price),
             listing_id = COALESCE(excluded.listing_id, listing_id),
             note = COALESCE(excluded.note, note)""",
        (property_id, event_date[:10], event, price, source, listing_id, note),
    )


def record_listing(conn: sqlite3.Connection, row: sqlite3.Row, previous: sqlite3.Row | None,
                   history: list[dict] | None = None, facts: dict | None = None) -> int | None:
    """Link a just-upserted listing to its property and record what changed."""
    listing = dict(row)
    pid = listing.get("property_id") or get_or_create_property(conn, listing)
    if pid is None:
        return None
    if listing.get("property_id") != pid:
        conn.execute("UPDATE listings SET property_id = ? WHERE id = ?", (pid, listing["id"]))
    update_facts(conn, pid, listing)
    if facts:
        update_facts(conn, pid, facts)

    today = date.today().isoformat()
    src, lid = listing["source"], listing["id"]
    status = listing["status"]
    prev = dict(previous) if previous else None
    price = listing["list_price"]

    if prev is None:
        if listing["list_date"]:
            add_event(conn, pid, "listed", listing["list_date"], price, src, lid)
        elif status in ACTIVE:
            add_event(conn, pid, "listed", listing["first_seen"][:10], price, src, lid)
    else:
        if status in ACTIVE and price and prev["list_price"] and price != prev["list_price"]:
            add_event(conn, pid, "price_change", today, price, src, lid, note=f"from {prev['list_price']:.0f}")
        if status not in ACTIVE and status != "sold" and prev["status"] in ACTIVE:
            add_event(conn, pid, "off_market", today, price, src, lid)
    if status == "pending" and (prev is None or prev["status"] != "pending"):
        add_event(conn, pid, "pending", today, price, src, lid)
    if status == "sold" and listing["sold_price"]:
        add_event(conn, pid, "sold", listing["sold_date"] or today, listing["sold_price"], src, lid)

    for ev in history or ():
        add_event(conn, pid, ev["event"], ev.get("date"), ev.get("price"), ev.get("source", src), None, ev.get("note"))
    return pid


def apply_record(conn: sqlite3.Connection, property_id: int, facts: dict, history: list[dict], source: str) -> None:
    """Store a property-record lookup (facts + past sales/assessments) for a home."""
    update_facts(conn, property_id, facts)
    for ev in history:
        add_event(conn, property_id, ev["event"], ev.get("date"), ev.get("price"), ev.get("source", source),
                  None, ev.get("note"))
    conn.execute("UPDATE properties SET looked_up = ? WHERE id = ?", (now_iso(), property_id))


def mark_off_market(conn: sqlite3.Connection, source: str, seen_before: str) -> int:
    """After a complete snapshot from ``source``, active listings it no longer
    returned have left the market (sold or withdrawn)."""
    stale = conn.execute(
        """SELECT * FROM listings WHERE source = ? AND status IN ('active', 'pending') AND last_seen < ?""",
        (source, seen_before),
    ).fetchall()
    for row in stale:
        conn.execute("UPDATE listings SET status = 'off_market' WHERE id = ?", (row["id"],))
        updated = conn.execute("SELECT * FROM listings WHERE id = ?", (row["id"],)).fetchone()
        record_listing(conn, updated, row)
    return len(stale)


def backfill(conn: sqlite3.Connection) -> int:
    """Link listings stored before properties existed."""
    rows = conn.execute("SELECT * FROM listings WHERE property_id IS NULL AND address IS NOT NULL").fetchall()
    for row in rows:
        record_listing(conn, row, None)
    return len(rows)


def delete_source(conn: sqlite3.Connection, source: str) -> int:
    """Remove a source's events and any homes left with no data at all."""
    n = conn.execute("DELETE FROM property_events WHERE source = ?", (source,)).rowcount
    conn.execute(
        """DELETE FROM properties WHERE id NOT IN (SELECT property_id FROM listings WHERE property_id IS NOT NULL)
           AND id NOT IN (SELECT property_id FROM property_events)"""
    )
    return n
