from __future__ import annotations

import pytest

from quakewatch.geo import haversine_km, parse_lat_lon


def test_haversine_zero_for_identical_points() -> None:
    assert haversine_km(35.0, 139.0, 35.0, 139.0) == 0.0


def test_haversine_one_degree_of_latitude() -> None:
    assert haversine_km(0.0, 0.0, 1.0, 0.0) == pytest.approx(111.195, abs=0.01)


def test_haversine_known_city_pair() -> None:
    # Paris -> New York is about 5,837 km on a spherical Earth.
    assert haversine_km(48.8566, 2.3522, 40.7128, -74.0060) == pytest.approx(5837, abs=10)


def test_haversine_crosses_the_antimeridian() -> None:
    assert haversine_km(0.0, 179.5, 0.0, -179.5) == pytest.approx(111.195, abs=0.01)


def test_haversine_antipodes_is_half_the_circumference() -> None:
    assert haversine_km(0.0, 0.0, 0.0, 180.0) == pytest.approx(20015.1, abs=0.5)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("35.68,139.69", (35.68, 139.69)),
        ("-33.45,-70.66", (-33.45, -70.66)),
        (" 0 , 0 ", (0.0, 0.0)),
        ("90,-180", (90.0, -180.0)),
    ],
)
def test_parse_lat_lon(text: str, expected: tuple[float, float]) -> None:
    assert parse_lat_lon(text) == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("35.68", "expected LAT,LON"),
        ("1,2,3", "expected LAT,LON"),
        ("north,east", "must be numbers"),
        ("91,0", "latitude 91 is outside"),
        ("0,181", "longitude 181 is outside"),
        ("nan,0", "latitude nan is outside"),
        ("0,inf", "longitude inf is outside"),
    ],
)
def test_parse_lat_lon_errors(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_lat_lon(text)
