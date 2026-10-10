"""Serializing events to CSV, JSON and GeoJSON."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, get_args

from quakewatch.models import FIELD_NAMES, Event

ExportFormat = Literal["csv", "json", "geojson"]
EXPORT_FORMATS: tuple[ExportFormat, ...] = get_args(ExportFormat)

_SUFFIXES: dict[str, ExportFormat] = {".csv": "csv", ".json": "json", ".geojson": "geojson"}


def infer_format(path: Path | str) -> ExportFormat | None:
    """Guess the export format from a file name's extension."""
    return _SUFFIXES.get(Path(path).suffix.lower())


def to_csv(events: Sequence[Event]) -> str:
    """One header row plus one row per event; missing values are empty cells."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(FIELD_NAMES)
    for event in events:
        row = event.to_dict()
        writer.writerow(["" if row[name] is None else row[name] for name in FIELD_NAMES])
    return buffer.getvalue()


def to_json(events: Sequence[Event]) -> str:
    """A JSON array of event objects."""
    return json.dumps([event.to_dict() for event in events], indent=2, ensure_ascii=False) + "\n"


def to_geojson(events: Sequence[Event]) -> str:
    """An RFC 7946 FeatureCollection of points.

    Coordinates are ``[lon, lat]`` only: RFC 7946 defines a third coordinate as
    metres of *elevation*, so depth is carried in ``properties.depth_km``
    instead of being smuggled in as a positive-down kilometre value.
    """
    features: list[dict[str, Any]] = []
    for event in events:
        properties = event.to_dict()
        del properties["lat"], properties["lon"]
        features.append(
            {
                "type": "Feature",
                "id": f"{event.source}:{event.id}",
                "geometry": {"type": "Point", "coordinates": [event.lon, event.lat]},
                "properties": properties,
            }
        )
    collection = {"type": "FeatureCollection", "features": features}
    return json.dumps(collection, indent=2, ensure_ascii=False) + "\n"


def dumps(events: Sequence[Event], fmt: ExportFormat) -> str:
    """Serialize ``events`` in the requested format."""
    if fmt == "csv":
        return to_csv(events)
    if fmt == "json":
        return to_json(events)
    return to_geojson(events)


def write(events: Sequence[Event], path: Path | str, fmt: ExportFormat) -> Path:
    """Write ``events`` to ``path`` (creating parent directories) and return the path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # newline="" keeps the CSV line endings exactly as the csv module wrote them.
    with target.open("w", encoding="utf-8", newline="") as handle:
        handle.write(dumps(events, fmt))
    return target
