from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quakewatch import export
from quakewatch.models import FIELD_NAMES, Event

EVENTS = [
    Event(
        "us6000u1cv",
        datetime(2026, 10, 10, 5, 32, 1, 250000, tzinfo=timezone.utc),
        5.0,
        10.0,
        1.625,
        126.389,
        '141 km E of Bitung, "Indonesia"',
        "usgs",
    ),
    Event(
        "20261010_0000101",
        datetime(2026, 10, 10, 5, 32, 1, tzinfo=timezone.utc),
        None,
        None,
        1.496,
        126.359,
        "MOLUCCA SEA",
        "emsc",
    ),
]


def test_csv_round_trips_through_the_csv_module() -> None:
    text = export.to_csv(EVENTS)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert text.splitlines()[0] == ",".join(FIELD_NAMES)
    assert "\r" not in text
    assert len(rows) == 2
    assert rows[0] == {
        "id": "us6000u1cv",
        "time": "2026-10-10T05:32:01.250Z",
        "magnitude": "5.0",
        "depth_km": "10.0",
        "lat": "1.625",
        "lon": "126.389",
        "place": '141 km E of Bitung, "Indonesia"',
        "source": "usgs",
    }
    assert rows[1]["magnitude"] == ""
    assert rows[1]["depth_km"] == ""


def test_json_is_a_list_of_event_dicts() -> None:
    data = json.loads(export.to_json(EVENTS))
    assert data == [event.to_dict() for event in EVENTS]
    assert data[1]["magnitude"] is None


def test_geojson_structure() -> None:
    data = json.loads(export.to_geojson(EVENTS))
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 2
    first = data["features"][0]
    assert first["type"] == "Feature"
    assert first["id"] == "usgs:us6000u1cv"
    assert first["geometry"] == {"type": "Point", "coordinates": [126.389, 1.625]}
    assert first["properties"] == {
        "id": "us6000u1cv",
        "time": "2026-10-10T05:32:01.250Z",
        "magnitude": 5.0,
        "depth_km": 10.0,
        "place": '141 km E of Bitung, "Indonesia"',
        "source": "usgs",
    }
    assert data["features"][1]["properties"]["depth_km"] is None


@pytest.mark.parametrize("fmt", export.EXPORT_FORMATS)
def test_empty_exports_are_still_valid(fmt: export.ExportFormat) -> None:
    text = export.dumps([], fmt)
    if fmt == "csv":
        assert text == ",".join(FIELD_NAMES) + "\n"
    elif fmt == "json":
        assert json.loads(text) == []
    else:
        assert json.loads(text) == {"type": "FeatureCollection", "features": []}


def test_non_ascii_place_names_survive(tmp_path: Path) -> None:
    event = Event("x", EVENTS[0].time, 3.0, 5.0, 38.0, 23.0, "Αθήνα, Ελλάδα", "emsc")
    for fmt in export.EXPORT_FORMATS:
        path = export.write([event], tmp_path / f"out.{fmt}", fmt)
        assert "Αθήνα, Ελλάδα" in path.read_text(encoding="utf-8")


def test_write_creates_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "quakes.csv"
    assert export.write(EVENTS, target, "csv") == target
    assert target.read_bytes() == export.to_csv(EVENTS).encode("utf-8")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("quakes.csv", "csv"),
        ("QUAKES.JSON", "json"),
        ("dir/quakes.geojson", "geojson"),
        ("quakes.txt", None),
        ("quakes", None),
    ],
)
def test_infer_format(name: str, expected: str | None) -> None:
    assert export.infer_format(name) == expected
