"""Common plumbing for data sources."""

from __future__ import annotations

import csv
import gzip
import io
import os
import zlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable, Iterator

import httpx

from ..config import Region

USER_AGENT = "realty-map/0.1 (local housing market research tool)"
TIMEOUT = httpx.Timeout(60.0, read=300.0)


class SourceUnavailable(RuntimeError):
    """Raised when a source can't run, e.g. a missing API key."""


class Source(ABC):
    #: short machine name used on the CLI and in the database
    name: str
    #: human readable label for the UI
    label: str
    #: 'metrics' (aggregate time series) or 'listings' (individual homes)
    kind: str
    description: str = ""
    homepage: str = ""
    #: environment variable holding an API key, if one is required
    api_key_env: str | None = None
    #: True when one run returns *every* current listing, so listings that are
    #: missing from a run can be marked off-market
    full_snapshot: bool = False

    def api_key(self) -> str:
        if not self.api_key_env:
            return ""
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise SourceUnavailable(f"{self.label} requires the {self.api_key_env} environment variable")
        return key

    @abstractmethod
    def fetch(self, region: Region) -> Iterable:
        """Yield metric tuples or listing dicts for the region."""


def _decode_lines(chunks: Iterable[bytes], gz: bool) -> Iterator[str]:
    decomp = zlib.decompressobj(16 + zlib.MAX_WBITS) if gz else None
    buf = b""
    for chunk in chunks:
        if decomp is not None:
            chunk = decomp.decompress(chunk)
        buf += chunk
        *lines, buf = buf.split(b"\n")
        for line in lines:
            yield line.decode("utf-8", errors="replace").rstrip("\r")
    if decomp is not None:
        buf += decomp.flush()
    if buf:
        yield buf.decode("utf-8", errors="replace").rstrip("\r")


def stream_lines(location: str, gz: bool | None = None) -> Iterator[str]:
    """Stream text lines from a URL or local path without holding the file in memory.

    Some public datasets are hundreds of MB to over 1 GB, so they are filtered
    line by line while downloading.
    """
    if gz is None:
        gz = location.endswith(".gz") or ".gz" in location.rsplit("/", 1)[-1]
    if not location.startswith(("http://", "https://")):
        path = Path(location)
        opener = gzip.open if gz else open
        with opener(path, "rt", encoding="utf-8", errors="replace", newline="") as fh:
            for line in fh:
                yield line.rstrip("\r\n")
        return
    with httpx.stream("GET", location, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT,
                      follow_redirects=True) as resp:
        resp.raise_for_status()
        yield from _decode_lines(resp.iter_bytes(1 << 20), gz)


def parse_csv_line(line: str, delimiter: str = ",") -> list[str]:
    return next(csv.reader(io.StringIO(line), delimiter=delimiter))


def to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace("$", "").replace(",", "")
    if s in ("", "NA", "N/A", "nan", "NaN", "null", "None", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None
