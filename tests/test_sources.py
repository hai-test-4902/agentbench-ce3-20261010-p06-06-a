from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from quakewatch.sources import PayloadError, as_float, emsc, features_of, usgs
from tests.conftest import fixture_json

UTC = timezone.utc


# -- USGS ---------------------------------------------------------------------


def test_usgs_fixture_is_fully_normalized() -> None:
    payload = fixture_json("usgs_2.5_day.geojson")
    events = usgs.parse(payload)
    assert len(events) == len(payload["features"]) == payload["metadata"]["count"]
    assert {event.source for event in events} == {"usgs"}
    assert all(event.time.tzinfo is UTC for event in events)
    assert all(-90 <= event.lat <= 90 and -180 <= event.lon <= 180 for event in events)
    # The "M2.5+" feed is bucketed on the rounded magnitude, so it includes e.g. 2.45.
    assert all(event.magnitude is not None and event.magnitude >= 2.45 for event in events)


def test_usgs_first_feature_field_mapping() -> None:
    feature = fixture_json("usgs_2.5_day.geojson")["features"][0]
    event = usgs.parse_feature(feature)
    assert event is not None
    lon, lat, depth = feature["geometry"]["coordinates"]
    assert event.id == feature["id"]
    assert event.lat == lat
    assert event.lon == lon
    assert event.depth_km == depth
    assert event.magnitude == feature["properties"]["mag"]
    assert event.place == feature["properties"]["place"]
    assert event.time.timestamp() * 1000 == pytest.approx(feature["properties"]["time"])


def test_usgs_null_magnitude_place_and_depth_are_tolerated() -> None:
    feature = {
        "id": "us1",
        "properties": {"mag": None, "place": None, "time": 1791674437720},
        "geometry": {"type": "Point", "coordinates": [10.5, -20.25]},
    }
    event = usgs.parse_feature(feature)
    assert event is not None
    assert event.magnitude is None
    assert event.depth_km is None
    assert event.place == ""
    assert event.time == datetime(2026, 10, 10, 23, 20, 37, 720000, tzinfo=UTC)


@pytest.mark.parametrize(
    "feature",
    [
        None,
        "feature",
        {"id": "x", "properties": None, "geometry": {"coordinates": [1, 2, 3]}},
        {"id": "x", "properties": {"time": 1}, "geometry": None},
        {"id": "", "properties": {"time": 1}, "geometry": {"coordinates": [1, 2, 3]}},
        {"id": "x", "properties": {"time": 1}, "geometry": {"coordinates": [1]}},
        {"id": "x", "properties": {"time": 1}, "geometry": {"coordinates": "1,2"}},
        {"id": "x", "properties": {"time": None}, "geometry": {"coordinates": [1, 2, 3]}},
        {"id": "x", "properties": {"time": 1}, "geometry": {"coordinates": [None, 2, 3]}},
    ],
)
def test_usgs_unusable_features_are_skipped(feature: Any) -> None:
    assert usgs.parse_feature(feature) is None
    assert usgs.parse({"features": [feature]}) == []


@pytest.mark.parametrize(
    ("min_mag", "level"),
    [
        (None, "all"),
        (0.0, "all"),
        (0.99, "all"),
        (1.0, "1.0"),
        (2.4, "1.0"),
        (2.5, "2.5"),
        (4.49, "2.5"),
        (4.5, "4.5"),
        (7.0, "4.5"),
    ],
)
def test_usgs_feed_level_never_excludes_requested_magnitudes(
    min_mag: float | None, level: str
) -> None:
    assert usgs.feed_level(min_mag) == level


def test_usgs_feed_url() -> None:
    base = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary"
    assert usgs.feed_url("day") == f"{base}/all_day.geojson"
    assert usgs.feed_url("week", 5.0) == f"{base}/4.5_week.geojson"
    assert usgs.feed_url("hour", 3) == f"{base}/2.5_hour.geojson"


# -- EMSC ---------------------------------------------------------------------


def test_emsc_fixture_is_fully_normalized() -> None:
    payload = fixture_json("emsc_day_minmag2.5.json")
    events = emsc.parse(payload)
    assert len(events) == len(payload["features"])
    assert {event.source for event in events} == {"emsc"}
    assert all(event.time.tzinfo is UTC for event in events)
    assert all(event.depth_km is None or event.depth_km >= 0 for event in events)
    assert all(event.magnitude is not None and event.magnitude >= 2.5 for event in events)


def test_emsc_first_feature_field_mapping() -> None:
    feature = fixture_json("emsc_hour.json")["features"][0]
    props = feature["properties"]
    event = emsc.parse_feature(feature)
    assert event is not None
    assert event.id == props["unid"]
    assert event.lat == props["lat"]
    assert event.lon == props["lon"]
    assert event.depth_km == props["depth"]
    assert event.depth_km == -feature["geometry"]["coordinates"][2]
    assert event.magnitude == props["mag"]
    assert event.place == props["flynn_region"]


def test_emsc_falls_back_to_geometry_for_position_and_depth() -> None:
    feature = {
        "id": "20261010_0000001",
        "geometry": {"type": "Point", "coordinates": [120.55, -8.35, -33.0]},
        "properties": {"time": "2026-10-10T23:21:38.0Z", "mag": 2.5, "flynn_region": " FLORES "},
    }
    event = emsc.parse_feature(feature)
    assert event is not None
    assert (event.lat, event.lon, event.depth_km) == (-8.35, 120.55, 33.0)
    assert event.id == "20261010_0000001"
    assert event.place == "FLORES"
    assert event.time == datetime(2026, 10, 10, 23, 21, 38, tzinfo=UTC)


def test_emsc_missing_depth_and_place() -> None:
    feature = {
        "id": "e1",
        "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
        "properties": {"time": "2026-10-10T23:21:38Z", "lat": 2.0, "lon": 1.0, "mag": None},
    }
    event = emsc.parse_feature(feature)
    assert event is not None
    assert event.depth_km is None
    assert event.magnitude is None
    assert event.place == ""


@pytest.mark.parametrize(
    "feature",
    [
        None,
        {"id": "e1", "properties": None},
        {"id": "e1", "properties": {"time": None, "lat": 1, "lon": 2}},
        {"id": "e1", "properties": {"time": "garbage", "lat": 1, "lon": 2}},
        {"id": None, "properties": {"time": "2026-10-10T23:21:38Z", "lat": 1, "lon": 2}},
        {"id": "e1", "properties": {"time": "2026-10-10T23:21:38Z"}},
        {"id": "e1", "properties": {"time": "2026-10-10T23:21:38Z"}, "geometry": {}},
        {"id": "e1", "properties": {"time": "2026-10-10T23:21:38Z", "lat": 1}},
    ],
)
def test_emsc_unusable_features_are_skipped(feature: Any) -> None:
    assert emsc.parse_feature(feature) is None
    assert emsc.parse({"features": [feature]}) == []


def test_emsc_query_params() -> None:
    start = datetime(2026, 10, 9, 23, 46, 9, 123456, tzinfo=UTC)
    assert emsc.query_params(start) == {
        "format": "json",
        "start": "2026-10-09T23:46:09",
        "orderby": "time",
        "limit": str(emsc.PAGE_SIZE),
        "offset": "1",
    }
    paged = emsc.query_params(start, 4.5, offset=5001, limit=100)
    assert paged["minmag"] == "4.5"
    assert paged["offset"] == "5001"
    assert paged["limit"] == "100"


# -- shared helpers -------------------------------------------------------------


@pytest.mark.parametrize("payload", [None, [], "text", {}, {"features": None}, {"features": {}}])
def test_features_of_rejects_non_collections(payload: Any) -> None:
    with pytest.raises(PayloadError, match="USGS response"):
        features_of(payload, "USGS")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1.0),
        (2.5, 2.5),
        ("3.25", 3.25),
        (None, None),
        (True, None),
        ("n/a", None),
        ([1], None),
        (float("nan"), None),
        (float("inf"), None),
    ],
)
def test_as_float(value: Any, expected: float | None) -> None:
    assert as_float(value) == expected
