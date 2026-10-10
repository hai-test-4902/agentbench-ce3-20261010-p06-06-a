"""EMSC FDSN event web service (``format=json``).

See https://www.seismicportal.eu/fdsn-wsevent.html
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from quakewatch.models import Event, parse_iso_utc
from quakewatch.sources import as_float, features_of

QUERY_URL = "https://www.seismicportal.eu/fdsnws/event/1/query"

# The service pages with a 1-based ``offset``; a busy week is a few thousand events.
PAGE_SIZE = 5000
MAX_PAGES = 20


def query_params(
    start: datetime, min_mag: float | None = None, *, offset: int = 1, limit: int = PAGE_SIZE
) -> dict[str, str]:
    """Query-string parameters for one page of events since ``start``."""
    params = {
        "format": "json",
        "start": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "orderby": "time",
        "limit": str(limit),
        "offset": str(offset),
    }
    if min_mag is not None:
        params["minmag"] = f"{min_mag:g}"
    return params


def parse_feature(feature: Any) -> Event | None:
    """Normalize one EMSC feature; ``None`` if it lacks an id, time or position."""
    if not isinstance(feature, dict):
        return None
    properties = feature.get("properties")
    if not isinstance(properties, dict):
        return None
    event_id = properties.get("unid") or feature.get("id")
    raw_time = properties.get("time")
    if not event_id or not isinstance(raw_time, str):
        return None
    try:
        moment = parse_iso_utc(raw_time)
    except ValueError:
        return None

    geometry = feature.get("geometry")
    coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if not isinstance(coordinates, list):
        coordinates = []
    lat = as_float(properties.get("lat"))
    lon = as_float(properties.get("lon"))
    if lat is None and len(coordinates) > 1:
        lat = as_float(coordinates[1])
    if lon is None and len(coordinates) > 0:
        lon = as_float(coordinates[0])
    if lat is None or lon is None:
        return None

    # ``properties.depth`` is positive-down kilometres; the geometry's third
    # coordinate is the same value as an elevation (negative-down).
    depth = as_float(properties.get("depth"))
    if depth is None and len(coordinates) > 2:
        elevation = as_float(coordinates[2])
        depth = None if elevation is None else -elevation

    place = properties.get("flynn_region")
    return Event(
        id=str(event_id),
        time=moment,
        magnitude=as_float(properties.get("mag")),
        depth_km=depth,
        lat=lat,
        lon=lon,
        place=place.strip() if isinstance(place, str) else "",
        source="emsc",
    )


def parse(payload: Any) -> list[Event]:
    """Normalize an EMSC FeatureCollection, skipping features with no time or position."""
    events = (parse_feature(feature) for feature in features_of(payload, "EMSC"))
    return [event for event in events if event is not None]
