from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from quakewatch.compare import (
    Comparison,
    MagnitudeStats,
    Match,
    filter_comparison,
    match_events,
)
from quakewatch.geo import haversine_km
from quakewatch.models import Event
from quakewatch.sources import emsc, usgs
from tests.conftest import fixture_json

T0 = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
KM_PER_DEGREE_LAT = 111.195


def usgs_event(
    event_id: str,
    *,
    seconds: float = 0.0,
    north_km: float = 0.0,
    magnitude: float | None = 5.0,
    lat: float = 10.0,
    lon: float = 20.0,
) -> Event:
    return Event(
        event_id,
        T0 + timedelta(seconds=seconds),
        magnitude,
        10.0,
        lat + north_km / KM_PER_DEGREE_LAT,
        lon,
        f"place of {event_id}",
        "usgs",
    )


def emsc_event(
    event_id: str,
    *,
    seconds: float = 0.0,
    north_km: float = 0.0,
    magnitude: float | None = 5.0,
    lat: float = 10.0,
    lon: float = 20.0,
) -> Event:
    return Event(
        event_id,
        T0 + timedelta(seconds=seconds),
        magnitude,
        10.0,
        lat + north_km / KM_PER_DEGREE_LAT,
        lon,
        f"REGION OF {event_id}",
        "emsc",
    )


def pairs(comparison: Comparison) -> set[tuple[str, str]]:
    return {(match.usgs.id, match.emsc.id) for match in comparison.matches}


# -- matching -------------------------------------------------------------------


def test_same_event_is_matched_with_signed_deltas() -> None:
    result = match_events(
        [usgs_event("u1", seconds=2.5, magnitude=5.3)],
        [emsc_event("e1", north_km=12.0, magnitude=5.1)],
    )
    assert pairs(result) == {("u1", "e1")}
    match = result.matches[0]
    assert match.time_delta_s == pytest.approx(2.5)
    assert match.distance_km == pytest.approx(12.0, abs=0.01)
    assert match.magnitude_diff == pytest.approx(0.2)
    assert result.usgs_only == []
    assert result.emsc_only == []


@pytest.mark.parametrize(
    ("seconds", "north_km", "expected"),
    [
        (60.0, 0.0, True),  # exactly at the time tolerance
        (-60.0, 0.0, True),
        (60.001, 0.0, False),
        (-60.001, 0.0, False),
        (0.0, 49.9, True),
        (0.0, 50.1, False),
        (59.0, 49.0, True),  # both near the limit is still a match
        (61.0, 1.0, False),  # close in space is not enough
        (1.0, 51.0, False),  # close in time is not enough
    ],
)
def test_tolerances(seconds: float, north_km: float, expected: bool) -> None:
    result = match_events(
        [usgs_event("u1", seconds=seconds, north_km=north_km)], [emsc_event("e1")]
    )
    assert bool(result.matches) is expected
    assert len(result.usgs_only) == len(result.emsc_only) == (0 if expected else 1)


def test_distance_tolerance_is_inclusive() -> None:
    a = usgs_event("u1", north_km=30.0)
    b = emsc_event("e1")
    exact = haversine_km(a.lat, a.lon, b.lat, b.lon)
    assert match_events([a], [b], max_km=exact).matches
    assert not match_events([a], [b], max_km=exact - 1e-6).matches


def test_custom_tolerances() -> None:
    usgs_events = [usgs_event("u1", seconds=90.0, north_km=80.0)]
    emsc_events = [emsc_event("e1")]
    assert not match_events(usgs_events, emsc_events).matches
    wide = match_events(usgs_events, emsc_events, max_seconds=120, max_km=100)
    assert pairs(wide) == {("u1", "e1")}
    assert (wide.max_seconds, wide.max_km) == (120, 100)


def test_zero_tolerances_only_match_identical_reports() -> None:
    result = match_events(
        [usgs_event("same"), usgs_event("later", seconds=1.0)],
        [emsc_event("e-same"), emsc_event("e-far", seconds=1.0, north_km=1.0)],
        max_seconds=0,
        max_km=0,
    )
    assert pairs(result) == {("same", "e-same")}


def test_negative_tolerances_are_rejected() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        match_events([], [], max_seconds=-1)
    with pytest.raises(ValueError, match=">= 0"):
        match_events([], [], max_km=-1)


def test_matching_is_one_to_one_and_prefers_the_closest_partner() -> None:
    # An aftershock pair 20 s apart: each USGS report must take its own EMSC report,
    # not both grab the first one inside the window.
    usgs_events = [usgs_event("u-main"), usgs_event("u-after", seconds=20.0, north_km=5.0)]
    emsc_events = [
        emsc_event("e-after", seconds=21.0, north_km=6.0),
        emsc_event("e-main", seconds=1.0, north_km=1.0),
    ]
    result = match_events(usgs_events, emsc_events)
    assert pairs(result) == {("u-main", "e-main"), ("u-after", "e-after")}


def test_surplus_reports_stay_unmatched() -> None:
    usgs_events = [usgs_event("u1"), usgs_event("u2", seconds=5.0)]
    emsc_events = [emsc_event("e1", seconds=4.0)]
    result = match_events(usgs_events, emsc_events)
    assert pairs(result) == {("u2", "e1")}  # u2 is 1 s away, u1 is 4 s away
    assert [e.id for e in result.usgs_only] == ["u1"]
    assert result.emsc_only == []
    assert (result.usgs_total, result.emsc_total) == (2, 1)


def test_result_does_not_depend_on_input_order() -> None:
    usgs_events = [usgs_event(f"u{i}", seconds=i * 15.0, north_km=i * 3.0) for i in range(6)]
    emsc_events = [
        emsc_event(f"e{i}", seconds=i * 15.0 + 2, north_km=i * 3.0 + 1) for i in range(6)
    ]
    forward = match_events(usgs_events, emsc_events)
    backward = match_events(usgs_events[::-1], emsc_events[::-1])
    assert pairs(forward) == pairs(backward) == {(f"u{i}", f"e{i}") for i in range(6)}
    assert [m.usgs.id for m in forward.matches] == [m.usgs.id for m in backward.matches]


def test_matches_and_leftovers_are_newest_first() -> None:
    usgs_events = [
        usgs_event("u-old"),
        usgs_event("u-new", seconds=600.0),
        usgs_event("u-lonely", seconds=3000.0),
    ]
    emsc_events = [
        emsc_event("e-old"),
        emsc_event("e-new", seconds=600.0),
        emsc_event("e-lonely", seconds=-3000.0),
    ]
    result = match_events(usgs_events, emsc_events)
    assert [m.usgs.id for m in result.matches] == ["u-new", "u-old"]
    assert [e.id for e in result.usgs_only] == ["u-lonely"]
    assert [e.id for e in result.emsc_only] == ["e-lonely"]


def test_matching_across_the_antimeridian() -> None:
    result = match_events(
        [usgs_event("u1", lat=-17.0, lon=179.9)], [emsc_event("e1", lat=-17.0, lon=-179.9)]
    )
    assert pairs(result) == {("u1", "e1")}
    assert result.matches[0].distance_km == pytest.approx(21.3, abs=0.2)


def test_empty_inputs() -> None:
    assert match_events([], []) == Comparison()
    only_usgs = match_events([usgs_event("u1")], [])
    assert (len(only_usgs.matches), len(only_usgs.usgs_only), len(only_usgs.emsc_only)) == (0, 1, 0)
    assert only_usgs.magnitude_stats is None


# -- magnitude differences ----------------------------------------------------------


def test_magnitude_diff_is_usgs_minus_emsc_and_none_when_unknown() -> None:
    assert Match(
        usgs_event("u", magnitude=4.4), emsc_event("e", magnitude=4.9), 0, 0
    ).magnitude_diff == (pytest.approx(-0.5))
    assert Match(usgs_event("u", magnitude=None), emsc_event("e"), 0, 0).magnitude_diff is None
    assert Match(usgs_event("u"), emsc_event("e", magnitude=None), 0, 0).magnitude_diff is None


def test_magnitude_stats() -> None:
    usgs_events = [
        usgs_event("u1", seconds=0, magnitude=5.0),
        usgs_event("u2", seconds=1000, magnitude=4.0),
        usgs_event("u3", seconds=2000, magnitude=6.0),
        usgs_event("u4", seconds=3000, magnitude=None),
    ]
    emsc_events = [
        emsc_event("e1", seconds=0, magnitude=5.2),  # -0.2
        emsc_event("e2", seconds=1000, magnitude=3.5),  # +0.5
        emsc_event("e3", seconds=2000, magnitude=6.0),  # 0.0
        emsc_event("e4", seconds=3000, magnitude=4.0),  # unknown
    ]
    result = match_events(usgs_events, emsc_events)
    assert len(result.matches) == 4
    assert sorted(result.magnitude_diffs) == pytest.approx([-0.2, 0.0, 0.5])
    stats = result.magnitude_stats
    assert stats is not None
    assert stats.count == 3
    assert stats.mean == pytest.approx(0.1)
    assert stats.mean_abs == pytest.approx(0.7 / 3)
    assert stats.median == pytest.approx(0.0)
    assert stats.max_abs == pytest.approx(0.5)


def test_magnitude_stats_of_nothing_is_none() -> None:
    assert MagnitudeStats.of([]) is None


# -- filtering after matching ---------------------------------------------------------


def test_min_mag_keeps_pairs_that_straddle_the_threshold() -> None:
    result = match_events(
        [
            usgs_event("u-straddle", seconds=0, magnitude=4.6),
            usgs_event("u-small", seconds=1000, magnitude=4.0),
            usgs_event("u-alone-big", seconds=2000, magnitude=5.0),
            usgs_event("u-alone-small", seconds=3000, magnitude=3.0),
            usgs_event("u-alone-unknown", seconds=4000, magnitude=None),
        ],
        [
            emsc_event("e-straddle", seconds=0, magnitude=4.4),
            emsc_event("e-small", seconds=1000, magnitude=4.2),
            emsc_event("e-alone-big", seconds=9000, magnitude=4.5),
            emsc_event("e-alone-small", seconds=9500, magnitude=4.49),
        ],
    )
    filtered = filter_comparison(result, min_mag=4.5)
    assert pairs(filtered) == {("u-straddle", "e-straddle")}
    assert [e.id for e in filtered.usgs_only] == ["u-alone-big"]
    assert [e.id for e in filtered.emsc_only] == ["e-alone-big"]
    assert (filtered.max_seconds, filtered.max_km) == (result.max_seconds, result.max_km)


def test_near_keeps_pairs_with_either_epicentre_inside_the_radius() -> None:
    result = match_events(
        [
            usgs_event("u-edge", seconds=0, north_km=95.0),
            usgs_event("u-far", seconds=1000, north_km=500.0),
            usgs_event("u-alone-near", seconds=2000, north_km=10.0),
            usgs_event("u-alone-far", seconds=3000, north_km=300.0),
        ],
        [
            emsc_event("e-edge", seconds=0, north_km=105.0),
            emsc_event("e-far", seconds=1000, north_km=510.0),
        ],
    )
    assert len(result.matches) == 2
    filtered = filter_comparison(result, near=(10.0, 20.0), radius_km=100.0)
    assert pairs(filtered) == {("u-edge", "e-edge")}
    assert [e.id for e in filtered.usgs_only] == ["u-alone-near"]


def test_filters_combine() -> None:
    result = match_events(
        [
            usgs_event("u1", magnitude=5.0, north_km=10.0),
            usgs_event("u2", seconds=900, magnitude=3.0),
        ],
        [
            emsc_event("e1", magnitude=5.0, north_km=10.0),
            emsc_event("e2", seconds=900, magnitude=3.0),
        ],
    )
    assert pairs(filter_comparison(result, min_mag=4.0, near=(10.0, 20.0), radius_km=50)) == {
        ("u1", "e1")
    }
    assert not filter_comparison(result, min_mag=6.0, near=(10.0, 20.0), radius_km=50).matches
    assert not filter_comparison(result, min_mag=4.0, near=(60.0, 20.0), radius_km=50).matches
    assert filter_comparison(result) == result


def test_filter_near_requires_radius() -> None:
    with pytest.raises(ValueError, match="together"):
        filter_comparison(Comparison(), near=(0.0, 0.0))


# -- recorded data --------------------------------------------------------------------


def test_recorded_catalogues_match_consistently() -> None:
    usgs_events = usgs.parse(fixture_json("usgs_2.5_day.geojson"))
    emsc_events = emsc.parse(fixture_json("emsc_day_minmag2.5.json"))
    result = match_events(usgs_events, emsc_events)

    # Every event is accounted for exactly once.
    assert result.usgs_total == len(usgs_events)
    assert result.emsc_total == len(emsc_events)
    assert len({m.usgs.id for m in result.matches}) == len(result.matches)
    assert len({m.emsc.id for m in result.matches}) == len(result.matches)

    # Every pair really is within tolerance, recomputed independently.
    for match in result.matches:
        assert abs((match.usgs.time - match.emsc.time).total_seconds()) <= 60
        separation = haversine_km(match.usgs.lat, match.usgs.lon, match.emsc.lat, match.emsc.lon)
        assert separation <= 50
        assert match.distance_km == pytest.approx(separation)

    # Greedy one-to-one matching found a partner for every event that has one:
    # no unmatched USGS event is still within tolerance of an unmatched EMSC event.
    for left in result.usgs_only:
        for right in result.emsc_only:
            close_in_time = abs((left.time - right.time).total_seconds()) <= 60
            assert not (
                close_in_time and haversine_km(left.lat, left.lon, right.lat, right.lon) <= 50
            )

    # Values pinned from the recording (63 USGS M2.5+ events, 319 EMSC M2.5+ events).
    assert (len(result.matches), len(result.usgs_only), len(result.emsc_only)) == (60, 3, 259)
    stats = result.magnitude_stats
    assert stats is not None
    assert stats.count == 60
    assert stats.max_abs == pytest.approx(0.9)
    assert stats.mean == pytest.approx(-0.0048, abs=1e-4)
