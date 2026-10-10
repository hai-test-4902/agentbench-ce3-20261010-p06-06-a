"""Great-circle geometry helpers."""

from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two points given in degrees."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def parse_lat_lon(text: str) -> tuple[float, float]:
    """Parse ``"LAT,LON"`` (degrees) and validate the ranges."""
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 2:
        raise ValueError("expected LAT,LON (for example 35.68,139.69)")
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        raise ValueError("LAT and LON must be numbers (for example 35.68,139.69)") from None
    if not (math.isfinite(lat) and -90.0 <= lat <= 90.0):
        raise ValueError(f"latitude {parts[0]} is outside -90..90")
    if not (math.isfinite(lon) and -180.0 <= lon <= 180.0):
        raise ValueError(f"longitude {parts[1]} is outside -180..180")
    return lat, lon
