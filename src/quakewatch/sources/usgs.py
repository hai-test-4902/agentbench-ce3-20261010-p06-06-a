"""USGS GeoJSON summary feeds.

See https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php
"""

from __future__ import annotations

from typing import Any

from quakewatch.models import Event, Window, from_epoch_ms
from quakewatch.sources import as_float, features_of

FEED_BASE_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary"

# Feed magnitude tiers, highest first. "all" has no lower bound.
_FEED_LEVELS: tuple[tuple[float, str], ...] = ((4.5, "4.5"), (2.5, "2.5"), (1.0, "1.0"))


def feed_level(min_mag: float | None) -> str:
    """The most selective feed tier that still contains every event >= ``min_mag``."""
    if min_mag is not None:
        for threshold, level in _FEED_LEVELS:
            if min_mag >= threshold:
                return level
    return "all"


def feed_url(window: Window, min_mag: float | None = None) -> str:
    """URL of the summary feed covering ``window`` at the tier chosen for ``min_mag``."""
    return f"{FEED_BASE_URL}/{feed_level(min_mag)}_{window}.geojson"


def parse_feature(feature: Any) -> Event | None:
    """Normalize one USGS feature; ``None`` if it lacks an id, time or position."""
    if not isinstance(feature, dict):
        return None
    properties = feature.get("properties")
    geometry = feature.get("geometry")
    event_id = feature.get("id")
    if not isinstance(properties, dict) or not isinstance(geometry, dict) or not event_id:
        return None
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        return None
    lon, lat = as_float(coordinates[0]), as_float(coordinates[1])
    time_ms = as_float(properties.get("time"))
    if lon is None or lat is None or time_ms is None:
        return None
    place = properties.get("place")
    return Event(
        id=str(event_id),
        time=from_epoch_ms(time_ms),
        magnitude=as_float(properties.get("mag")),
        depth_km=as_float(coordinates[2]) if len(coordinates) > 2 else None,
        lat=lat,
        lon=lon,
        place=place.strip() if isinstance(place, str) else "",
        source="usgs",
    )


def parse(payload: Any) -> list[Event]:
    """Normalize a USGS FeatureCollection, skipping features with no time or position."""
    events = (parse_feature(feature) for feature in features_of(payload, "USGS"))
    return [event for event in events if event is not None]
