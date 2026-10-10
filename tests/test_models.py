from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from quakewatch.models import (
    FIELD_NAMES,
    WINDOW_DELTAS,
    WINDOWS,
    Event,
    format_utc,
    from_epoch_ms,
    parse_iso_utc,
)

UTC = timezone.utc


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-10-10T23:21:38.0Z", datetime(2026, 10, 10, 23, 21, 38, tzinfo=UTC)),
        ("2026-10-09T23:34:12.489Z", datetime(2026, 10, 9, 23, 34, 12, 489000, tzinfo=UTC)),
        ("2026-10-10T23:10:32.56Z", datetime(2026, 10, 10, 23, 10, 32, 560000, tzinfo=UTC)),
        ("2026-10-10T23:42:03.290970Z", datetime(2026, 10, 10, 23, 42, 3, 290970, tzinfo=UTC)),
        ("2026-10-10T23:42:03.2909701234Z", datetime(2026, 10, 10, 23, 42, 3, 290970, tzinfo=UTC)),
        ("2026-10-10T23:21:38", datetime(2026, 10, 10, 23, 21, 38, tzinfo=UTC)),
        ("2026-10-10 23:21:38", datetime(2026, 10, 10, 23, 21, 38, tzinfo=UTC)),
        ("2026-10-11T01:21:38+02:00", datetime(2026, 10, 10, 23, 21, 38, tzinfo=UTC)),
        ("2026-10-10T18:21:38-0500", datetime(2026, 10, 10, 23, 21, 38, tzinfo=UTC)),
        (" 2026-10-10T23:21:38Z ", datetime(2026, 10, 10, 23, 21, 38, tzinfo=UTC)),
    ],
)
def test_parse_iso_utc(text: str, expected: datetime) -> None:
    parsed = parse_iso_utc(text)
    assert parsed == expected
    assert parsed.utcoffset() == timedelta(0)


@pytest.mark.parametrize("text", ["", "yesterday", "2026-10-10", "2026-13-40T00:00:00Z", "12:00"])
def test_parse_iso_utc_rejects_garbage(text: str) -> None:
    with pytest.raises(ValueError, match=r"timestamp|month|day"):
        parse_iso_utc(text)


def test_from_epoch_ms_keeps_milliseconds() -> None:
    assert from_epoch_ms(1791674437720) == datetime(2026, 10, 10, 23, 20, 37, 720000, tzinfo=UTC)


def test_format_utc_converts_and_truncates_to_milliseconds() -> None:
    cest = timezone(timedelta(hours=2))
    moment = datetime(2026, 10, 11, 1, 2, 3, 456789, tzinfo=cest)
    assert format_utc(moment) == "2026-10-10T23:02:03.456Z"


def test_event_normalizes_time_to_utc() -> None:
    event = Event(
        id="x1",
        time=datetime(2026, 10, 11, 1, 0, tzinfo=timezone(timedelta(hours=2))),
        magnitude=4.2,
        depth_km=10.0,
        lat=1.0,
        lon=2.0,
        place="somewhere",
        source="usgs",
    )
    assert event.time == datetime(2026, 10, 10, 23, 0, tzinfo=UTC)
    assert event.time.utcoffset() == timedelta(0)
    assert event.time_iso == "2026-10-10T23:00:00.000Z"


def test_event_rejects_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Event("x", datetime(2026, 1, 1), 1.0, 1.0, 0.0, 0.0, "", "emsc")  # noqa: DTZ001


def test_event_to_dict_uses_canonical_field_order() -> None:
    event = Event(
        "x1", datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC), None, None, 1.5, -2.5, "p", "emsc"
    )
    data = event.to_dict()
    assert tuple(data) == FIELD_NAMES
    assert data == {
        "id": "x1",
        "time": "2026-01-02T03:04:05.000Z",
        "magnitude": None,
        "depth_km": None,
        "lat": 1.5,
        "lon": -2.5,
        "place": "p",
        "source": "emsc",
    }


def test_every_window_has_a_duration() -> None:
    assert set(WINDOW_DELTAS) == set(WINDOWS)
    assert WINDOW_DELTAS["hour"] < WINDOW_DELTAS["day"] < WINDOW_DELTAS["week"]
