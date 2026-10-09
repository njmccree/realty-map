"""Command line interface.

    python -m realtymap ingest                 # all free sources (+ RentCast if a key is set)
    python -m realtymap ingest realtor redfin  # selected sources
    python -m realtymap import listings.csv    # listings / past sales from a CSV
    python -m realtymap demo                   # synthetic listings to try the UI
    python -m realtymap clear --source demo
    python -m realtymap serve                  # web UI on http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import db, demo
from .config import load_region
from .sources import SOURCES, CSVImport, SourceUnavailable, run_source
from .sources.redfin import Redfin
from .sources.rentcast import RentCast


def cmd_ingest(args) -> int:
    region = load_region(args.region)
    conn = db.connect()
    names = args.sources or list(SOURCES)
    failures = 0
    for name in names:
        if name not in SOURCES:
            print(f"unknown source {name!r}; choose from {', '.join(SOURCES)}", file=sys.stderr)
            return 2
        if name == "redfin":
            source = Redfin(include_zips=args.redfin_zips)
        elif name == "rentcast":
            source = RentCast(enrich=args.enrich)
        else:
            source = SOURCES[name]()
        start = time.monotonic()
        print(f"[{name}] fetching for {region.name} ...", flush=True)
        try:
            n = run_source(conn, source, region)
        except SourceUnavailable as exc:
            print(f"[{name}] skipped: {exc}")
            continue
        except Exception as exc:  # keep going with the other sources
            failures += 1
            print(f"[{name}] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        print(f"[{name}] stored {n:,} rows in {time.monotonic() - start:.0f}s")
    return 1 if failures else 0


def cmd_import(args) -> int:
    region = load_region(args.region)
    conn = db.connect()
    source = CSVImport(args.path, source_name=args.source, restrict_to_region=not args.all_zips)
    n = run_source(conn, source, region)
    print(f"imported {n:,} listings from {args.path} as source {args.source!r}")
    return 0


def cmd_demo(args) -> int:
    region = load_region(args.region)
    conn = db.connect()
    run_id = db.start_run(conn, "demo")
    n = demo.load(conn, region, args.count)
    db.finish_run(conn, run_id, "ok", n, "synthetic data")
    print(f"loaded {n} synthetic listings (source 'demo'). Remove with: python -m realtymap clear --source demo")
    return 0


def cmd_clear(args) -> int:
    conn = db.connect()
    with db.transaction(conn):
        a = conn.execute("DELETE FROM listings WHERE source = ?", (args.source,)).rowcount
        b = conn.execute("DELETE FROM market_metrics WHERE source = ?", (args.source,)).rowcount
        conn.execute("DELETE FROM ingest_runs WHERE source = ?", (args.source,))
    print(f"removed {a} listings and {b} metric rows from source {args.source!r}")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    if args.region:
        os.environ["REALTYMAP_REGION"] = args.region
    uvicorn.run("realtymap.api:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="realtymap", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--region", help="region id from config/regions (default: bay_county_mi)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest", help="download and store data from sources")
    s.add_argument("sources", nargs="*", help=f"subset of: {', '.join(SOURCES)}")
    s.add_argument("--redfin-zips", action="store_true",
                   help="also scan Redfin's ZIP-level file (~1.5 GB download)")
    s.add_argument("--enrich", type=int, default=0,
                   help="RentCast: look up features (garage, basement) for up to N listings (1 API call each)")
    s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("import", help="import listings / sales from a CSV file")
    s.add_argument("path")
    s.add_argument("--source", default="csv", help="name to tag these rows with (default: csv)")
    s.add_argument("--all-zips", action="store_true", help="keep rows outside the region's ZIP list")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("demo", help="load synthetic listings for trying out the UI")
    s.add_argument("--count", type=int, default=240)
    s.set_defaults(func=cmd_demo)

    s = sub.add_parser("clear", help="delete all rows from one source")
    s.add_argument("--source", required=True)
    s.set_defaults(func=cmd_clear)

    s = sub.add_parser("serve", help="run the web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")
    s.set_defaults(func=cmd_serve)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
