"""Settings and region configuration."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGIONS_DIR = ROOT / "config" / "regions"


@dataclass(frozen=True)
class Zip:
    zip: str
    name: str
    lat: float | None = None
    lon: float | None = None


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    state: str
    state_fips: str
    county_fips: str
    redfin_county_name: str
    center: tuple[float, float]
    zoom: int
    zips: tuple[Zip, ...] = field(default_factory=tuple)

    @property
    def full_county_fips(self) -> str:
        return self.state_fips + self.county_fips

    @property
    def zip_codes(self) -> set[str]:
        return {z.zip for z in self.zips}


def load_region(region_id: str | None = None) -> Region:
    region_id = region_id or os.environ.get("REALTYMAP_REGION", "bay_county_mi")
    path = REGIONS_DIR / f"{region_id}.toml"
    if not path.exists():
        available = sorted(p.stem for p in REGIONS_DIR.glob("*.toml"))
        raise FileNotFoundError(f"Unknown region {region_id!r}; available: {available}")
    raw = tomllib.loads(path.read_text())
    return Region(
        id=raw["id"],
        name=raw["name"],
        state=raw["state"],
        state_fips=raw["state_fips"],
        county_fips=raw["county_fips"],
        redfin_county_name=raw["redfin_county_name"],
        center=tuple(raw.get("center", (0.0, 0.0))),
        zoom=int(raw.get("zoom", 10)),
        zips=tuple(Zip(**z) for z in raw.get("zips", [])),
    )


def db_path() -> Path:
    return Path(os.environ.get("REALTYMAP_DB", ROOT / "data" / "realtymap.sqlite3"))
