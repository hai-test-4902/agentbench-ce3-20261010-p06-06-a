"""Per-source request building and response normalization."""

from __future__ import annotations

import math
from typing import Any


class PayloadError(ValueError):
    """A response decoded as JSON but does not have the expected shape."""


def features_of(payload: Any, source_name: str) -> list[Any]:
    """Return the ``features`` list of a GeoJSON FeatureCollection, or raise."""
    if not isinstance(payload, dict):
        raise PayloadError(f"{source_name} response is not a JSON object")
    features = payload.get("features")
    if not isinstance(features, list):
        raise PayloadError(f"{source_name} response has no 'features' list")
    return features


def as_float(value: Any) -> float | None:
    """Coerce a JSON number (or numeric string) to float; anything else is ``None``."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None
