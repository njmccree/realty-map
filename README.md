# Realty Map

A web tool that pulls local housing data from several public sources into one
place and shows, for an area and each of its ZIP codes:

- **Average and median price of homes on the market**, active listings, days on market, price per sq ft
- **The average home's amenities**: bedrooms, bathrooms, living area, lot size, age, garage, basement
- **What amenities are worth**: median price with vs. without a feature, price by bedroom count,
  and a regression estimating each amenity's contribution to price
- **Trends**: list prices, closed-sale prices and home values over 1–5+ years, with 3-month,
  1-year, 3-year and 5-year changes
- A **map and ZIP-by-ZIP comparison table**
- **Individual homes**: search by address and open any home to see its details, every listing,
  price change and sale on a timeline, how much it gained between sales compared with the area,
  tax assessments, comparable recent sales nearby, and three rough value estimates

The first configured region is **Bay County, Michigan** (county FIPS 26017) and its ZIP codes
48706, 48708, 48732, 48611, 48631, 48634, 48650, 48613 and 48747.

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

python -m realtymap ingest          # download free sources (Realtor.com, Redfin, Zillow, Census)
python -m realtymap serve           # open http://127.0.0.1:8000
```

The first ingest streams roughly 1 GB of public files and keeps only Bay County rows (a few
minutes). Re-run it monthly — the sources update monthly — e.g. from cron:

```
0 6 3 * * cd /path/to/realty-map && .venv/bin/python -m realtymap ingest >> data/ingest.log 2>&1
```

To see the amenity views before connecting listing data, load clearly labelled synthetic
listings with `python -m realtymap demo` (the UI shows a banner while they exist) and remove
them with `python -m realtymap clear --source demo`.

## Data sources

| Source | Key needed | Level | What it contributes |
|---|---|---|---|
| [Realtor.com Research](https://www.realtor.com/research/data/) | no | county, ZIP · monthly since 2016 | Homes **on the market**: median & average list price, active/new/pending listings, days on market, $/sq ft, median size, price cuts |
| [Redfin Data Center](https://www.redfin.com/news/data-center/) | no | county (ZIP with `--redfin-zips`, ~1.5 GB) · monthly since 2012 | **Closed sales**: median sale price, homes sold, sale-to-list ratio, months of supply |
| [Zillow ZHVI](https://www.zillow.com/research/data/) | no | county, ZIP · monthly since 2000 | Typical value of **all homes**, for long-run trends |
| [Census ACS 5-year](https://www.census.gov/data/developers.html) | optional `CENSUS_API_KEY` | county, ZIP | The **housing stock**: bedroom mix, median value, median year built, owner share |
| [RentCast API](https://www.rentcast.io/api) | `RENTCAST_API_KEY` | **individual listings** | Each active listing's price, beds, baths, sq ft, lot, year built, location; `--enrich N` adds garage / basement / fireplace from property records (1 request per home) |
| CSV import | no | **individual listings / sales** | Anything you can export — MLS, county assessor, a spreadsheet — including basement and garage details |

The aggregate sources (first four) are free and cover prices and trends. **Per-home amenity data
(bedrooms, bathrooms, basement, garage) only comes from listing-level sources** — RentCast or a CSV
import. Public aggregate datasets don't publish basement or garage information, and the large
listing portals don't permit scraping, so the tool doesn't scrape them.

### Importing listings from CSV

```bash
python -m realtymap import my_export.csv                 # rows outside the region's ZIPs are skipped
python -m realtymap import sold_2025.csv --source mls    # tag rows by origin
```

Column names are matched loosely (`Beds`/`Bedrooms`/`BR`, `Sold Price`/`Close Price`,
`Baths Full` + `Baths Half`, `Lot Acres`, `Garage Spaces`, ...). Yes/no fields accept
values like `Yes`, `No`, `Full, Finished`, `Crawl Space` or `2`; blanks are treated as unknown,
not "no". Re-importing the same file updates rows instead of duplicating them. Canonical
headers are in [`samples/listings_template.csv`](samples/listings_template.csv); the full
alias list is in `realtymap/sources/csv_import.py`.

## Individual homes and past sales

Every listing or sale is matched to a **home** by its street address and ZIP ("1205 N. Johnson
Street" and "1205 North Johnson St" are the same home), so the same house reported by several
sources, or sold several times, has one history. Find a home with the search box at the top, or click
any row in **Recent sales** or **Listings**, or a pin on the map. Each home's page has its own link
(`/#home=<id>`) you can bookmark or share.

A home's page shows:

- **Details**: beds, baths, size, lot, year built, garage, basement
- **Price history chart and timeline**: listed, price changes, under contract, sold, taken off market
- **Sale history**: each sale with the change since the previous one, per year, next to the area's
  change over the same years
- **Tax assessments**, when a property-record source supplies them
- **Comparable recent sales**: sold in the last 2 years within 3 miles, within one bedroom and 30% of
  the size
- **Value estimates**: last sale price moved by the area trend since then; comparable sales' median
  $/sq ft times this home's size; and the price model. These are rough guides, not appraisals.

Where the history comes from:

| Source | How |
|---|---|
| CSV of past sales | `python -m realtymap import sold.csv`. One row per sale; several rows for the same address become that home's sale history. Include `Sold Price` and `Close Date` (plus `List Price` / `List Date` if you have them). MLS "sold" exports and county sales lists work well. |
| RentCast property records | With `RENTCAST_API_KEY` set, a home's page has a **Fetch sale history** button (1 API request), or run `python -m realtymap lookup "1 Main St, Bay City, MI 48706"`. `ingest rentcast --enrich N` does it for the first N listings. Owner names in these records are not stored. |
| Listings over time | Each RentCast ingest records new listings and price changes. Listings that disappear from a later ingest are marked **off market**, so homes that sold or were withdrawn stop counting as for sale. |

## Reading the numbers

- **List price vs. sale price vs. home value** measure different things: what sellers ask for
  homes currently listed, what buyers paid for homes that closed, and an estimate for every home.
  They're plotted together on one dollar axis so the gaps are visible.
- Small ZIPs (Bentley, Munger, Kawkawlin) often have only a handful of listings, so their
  month-to-month changes are noisy. Prefer the 1-year and 3-year columns there.
- The **price model** is an ordinary least-squares regression on whatever listings are loaded. It needs
  at least ~20 homes with amenity data, and each estimate is shown with its standard error — treat
  wide ± values as "not enough data", not as a precise price.

## Adding another area

Copy `config/regions/bay_county_mi.toml`, change the FIPS codes, Redfin county name and ZIP list,
then pass `--region <id>` to any command (or set `REALTYMAP_REGION`). Each region should use its own
database file (`REALTYMAP_DB=data/other.sqlite3`).

## API

| Endpoint | Returns |
|---|---|
| `GET /api/region` | region, ZIPs, sources and their last ingest |
| `GET /api/summary?zip=` | headline numbers with year-over-year changes |
| `GET /api/trends?metric=median_listing_price&metric=zhvi&zip=` | time series + change summary |
| `GET /api/amenities?zip=&status=active\|sold` | amenity averages, price by bedrooms, premiums, model |
| `GET /api/zips` | per-ZIP comparison |
| `GET /api/homes?q=` | search homes by address |
| `GET /api/homes/{id}` | one home: details, timeline, sale history, comparables, value estimates |
| `POST /api/homes/{id}/lookup` | fetch the home's property record from RentCast (1 API request) |
| `GET /api/sales?zip=&months=` | recent closed sales, each with the home's previous sale |
| `GET /api/listings?zip=&status=&limit=` | individual homes |

Omit `zip` for the whole county.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Layout: `realtymap/sources/` (one module per source), `realtymap/analytics.py` (metrics, trends,
regression), `realtymap/properties.py` (matching listings to homes, recording history),
`realtymap/homes.py` (home detail, sale history, comparables), `realtymap/api.py` (FastAPI), `web/`
(static UI, Chart.js + Leaflet), data in SQLite at `data/realtymap.sqlite3`. Databases from earlier
versions are upgraded automatically the first time they are opened.
