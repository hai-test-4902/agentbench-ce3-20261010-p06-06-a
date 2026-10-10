#!/usr/bin/env python3
"""Re-record the HTTP fixtures in tests/fixtures/ from the live USGS and EMSC services.

The response bodies are saved byte-for-byte. ``recording.json`` stores when and
from where they were captured; the unit tests freeze "now" to that instant so
window filtering stays deterministic.

Usage: python scripts/record_fixtures.py
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
USGS = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary"
EMSC = "https://www.seismicportal.eu/fdsnws/event/1/query"


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "quakewatch-fixture-recorder"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        body: bytes = response.read()
    return body


def emsc_url(start: datetime, min_mag: float | None) -> str:
    params = {
        "format": "json",
        "start": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "orderby": "time",
        "limit": "5000",
        "offset": "1",
    }
    if min_mag is not None:
        params["minmag"] = f"{min_mag:g}"
    return f"{EMSC}?{urllib.parse.urlencode(params)}"


def main() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    targets = {
        "usgs_all_hour.geojson": f"{USGS}/all_hour.geojson",
        "usgs_2.5_day.geojson": f"{USGS}/2.5_day.geojson",
        "emsc_hour.json": emsc_url(now - timedelta(hours=1), None),
        "emsc_day_minmag2.5.json": emsc_url(now - timedelta(days=1), 2.5),
    }
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, url in targets.items():
        body = fetch(url)
        json.loads(body)  # refuse to record something that is not JSON
        (FIXTURES / name).write_bytes(body)
        print(f"{name}: {len(body)} bytes")
    meta = {"recorded_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "requests": targets}
    (FIXTURES / "recording.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
