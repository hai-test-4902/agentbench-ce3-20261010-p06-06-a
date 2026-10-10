from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from quakewatch.cache import ResponseCache
from quakewatch.client import (
    QuakeClient,
    SourceError,
    filter_events,
    sort_events,
    sources_for,
    utcnow,
)
from quakewatch.models import Event
from quakewatch.sources import emsc, usgs
from tests.conftest import RECORDED_AT, fixture_json, mock_emsc, mock_usgs

UTC = timezone.utc


def make_client(cache: ResponseCache | None = None, *, retries: int = 2) -> QuakeClient:
    return QuakeClient(cache=cache, retries=retries, now=lambda: RECORDED_AT, sleep=lambda _: None)


def event(
    event_id: str,
    *,
    minutes_ago: float = 0.0,
    magnitude: float | None = 4.0,
    lat: float = 0.0,
    lon: float = 0.0,
    source: str = "usgs",
) -> Event:
    assert source in ("usgs", "emsc")
    return Event(
        id=event_id,
        time=RECORDED_AT - timedelta(minutes=minutes_ago),
        magnitude=magnitude,
        depth_km=10.0,
        lat=lat,
        lon=lon,
        place="test",
        source="usgs" if source == "usgs" else "emsc",
    )


# -- fetching -------------------------------------------------------------------


def test_fetch_usgs_uses_the_feed_for_the_window(api: respx.MockRouter) -> None:
    route = mock_usgs(api, url=usgs.feed_url("day"))
    with make_client() as client:
        events = client.fetch("usgs", "day")
    assert route.call_count == 1
    assert len(events) == len(fixture_json("usgs_2.5_day.geojson")["features"])
    assert [e.time for e in events] == sorted((e.time for e in events), reverse=True)
    request = route.calls.last.request
    assert request.headers["User-Agent"].startswith("quakewatch/")


def test_fetch_usgs_min_mag_picks_a_narrower_feed_and_filters(api: respx.MockRouter) -> None:
    route = mock_usgs(api, url=usgs.feed_url("day", 4.5))
    with make_client() as client:
        events = client.fetch("usgs", "day", min_mag=4.5)
    assert route.call_count == 1
    assert events
    assert all(e.magnitude is not None and e.magnitude >= 4.5 for e in events)
    expected = [
        f for f in fixture_json("usgs_2.5_day.geojson")["features"] if f["properties"]["mag"] >= 4.5
    ]
    assert len(events) == len(expected)


def test_fetch_emsc_sends_the_fdsn_query(api: respx.MockRouter) -> None:
    route = mock_emsc(api)
    with make_client() as client:
        events = client.fetch("emsc", "day", min_mag=2.5)
    assert route.call_count == 1
    query = parse_qs(urlparse(str(route.calls.last.request.url)).query)
    assert query == {
        "format": ["json"],
        "start": [(RECORDED_AT - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")],
        "orderby": ["time"],
        "limit": [str(emsc.PAGE_SIZE)],
        "offset": ["1"],
        "minmag": ["2.5"],
    }
    assert len(events) == len(fixture_json("emsc_day_minmag2.5.json")["features"])
    assert {e.source for e in events} == {"emsc"}


def test_window_is_applied_client_side(api: respx.MockRouter) -> None:
    # A day-long response served for an hour-long request must be trimmed.
    mock_emsc(api, "emsc_day_minmag2.5.json")
    with make_client() as client:
        events = client.fetch("emsc", "hour")
    assert events
    assert all(e.time >= RECORDED_AT - timedelta(hours=1) for e in events)
    assert len(events) < len(fixture_json("emsc_day_minmag2.5.json")["features"])


def test_emsc_no_content_means_no_events(api: respx.MockRouter) -> None:
    api.get(emsc.QUERY_URL).mock(return_value=httpx.Response(204))
    with make_client() as client:
        assert client.fetch("emsc", "hour", min_mag=9.0) == []


def test_emsc_pagination_follows_offsets_and_deduplicates(
    api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    features = fixture_json("emsc_hour.json")["features"]
    assert len(features) >= 5
    monkeypatch.setattr(emsc, "PAGE_SIZE", 2)
    pages = {
        "1": features[0:2],
        "3": features[1:3],  # overlaps page 1, as happens when an event arrives mid-paging
        "5": features[3:4],  # short page: last one
    }

    def respond(request: httpx.Request) -> httpx.Response:
        offset = parse_qs(urlparse(str(request.url)).query)["offset"][0]
        return httpx.Response(200, json={"type": "FeatureCollection", "features": pages[offset]})

    route = api.get(emsc.QUERY_URL).mock(side_effect=respond)
    with make_client() as client:
        events = client.fetch("emsc", "hour")
    assert route.call_count == 3
    assert sorted(e.id for e in events) == sorted(f["id"] for f in features[0:4])


def test_emsc_pagination_stops_on_no_content(
    api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    features = fixture_json("emsc_hour.json")["features"][:2]
    monkeypatch.setattr(emsc, "PAGE_SIZE", 2)
    route = api.get(emsc.QUERY_URL).mock(
        side_effect=[
            httpx.Response(200, json={"type": "FeatureCollection", "features": features}),
            httpx.Response(204),
        ]
    )
    with make_client() as client:
        assert len(client.fetch("emsc", "hour")) == 2
    assert route.call_count == 2


def test_events_both_merges_and_sorts(api: respx.MockRouter) -> None:
    mock_usgs(api, "usgs_all_hour.geojson")
    mock_emsc(api, "emsc_hour.json")
    with make_client() as client:
        merged = client.events("both", "hour")
        only_usgs = client.events("usgs", "hour")
    assert {e.source for e in merged} == {"usgs", "emsc"}
    assert {e.source for e in only_usgs} == {"usgs"}
    assert [e.time for e in merged] == sorted((e.time for e in merged), reverse=True)


# -- caching --------------------------------------------------------------------


def test_second_call_is_served_from_the_cache(api: respx.MockRouter, tmp_path: Path) -> None:
    usgs_route = mock_usgs(api)
    emsc_route = mock_emsc(api)
    cache = ResponseCache(tmp_path / "cache", ttl_seconds=300)
    with make_client(cache) as client:
        first = client.events("both", "day", 2.5)
    with make_client(cache) as client:
        second = client.events("both", "day", 2.5)
    assert first == second
    assert usgs_route.call_count == 1
    assert emsc_route.call_count == 1
    assert len(list((tmp_path / "cache").glob("*.json"))) == 2


def test_cache_is_keyed_by_window_and_magnitude(api: respx.MockRouter, tmp_path: Path) -> None:
    emsc_route = mock_emsc(api)
    cache = ResponseCache(tmp_path, ttl_seconds=300)
    with make_client(cache) as client:
        client.fetch("emsc", "day", 2.5)
        client.fetch("emsc", "day", 2.5)
        client.fetch("emsc", "day", 4.0)
        client.fetch("emsc", "week", 2.5)
        client.fetch("emsc", "day")
    assert emsc_route.call_count == 4


def test_expired_entries_are_refetched(api: respx.MockRouter, tmp_path: Path) -> None:
    route = mock_usgs(api)
    moment = [1000.0]
    cache = ResponseCache(tmp_path, ttl_seconds=60, clock=lambda: moment[0])
    with make_client(cache) as client:
        client.fetch("usgs", "day")
        moment[0] += 30
        client.fetch("usgs", "day")
        assert route.call_count == 1
        moment[0] += 31
        client.fetch("usgs", "day")
    assert route.call_count == 2


def test_without_a_cache_every_call_hits_the_api(api: respx.MockRouter, tmp_path: Path) -> None:
    route = mock_usgs(api)
    with make_client(None) as client:
        client.fetch("usgs", "day")
        client.fetch("usgs", "day")
    assert route.call_count == 2
    assert list(tmp_path.iterdir()) == []


def test_failed_responses_are_not_cached(api: respx.MockRouter, tmp_path: Path) -> None:
    api.get(url__regex=r".*usgs.*").mock(return_value=httpx.Response(404, text="nope"))
    cache = ResponseCache(tmp_path, ttl_seconds=300)
    with make_client(cache) as client, pytest.raises(SourceError):
        client.fetch("usgs", "day")
    assert list(tmp_path.glob("*.json")) == []


# -- failures -------------------------------------------------------------------


def test_transient_errors_are_retried(api: respx.MockRouter) -> None:
    body = json.dumps(fixture_json("usgs_all_hour.geojson"))
    route = api.get(usgs.feed_url("hour")).mock(
        side_effect=[
            httpx.Response(503),
            httpx.ConnectTimeout("timed out"),
            httpx.Response(200, text=body),
        ]
    )
    sleeps: list[float] = []
    client = QuakeClient(retries=2, now=lambda: RECORDED_AT, sleep=sleeps.append)
    with client:
        assert len(client.fetch("usgs", "hour")) == 7
    assert route.call_count == 3
    assert sleeps == [0.5, 1.0]


def test_gives_up_after_the_retry_budget(api: respx.MockRouter) -> None:
    route = api.get(usgs.feed_url("hour")).mock(return_value=httpx.Response(502))
    with make_client(retries=1) as client, pytest.raises(SourceError) as raised:
        client.fetch("usgs", "hour")
    assert route.call_count == 2
    assert raised.value.source == "usgs"
    assert "USGS: HTTP 502" in str(raised.value)
    assert "after 2 attempt(s)" in str(raised.value)


def test_connection_failure_message_names_the_source(api: respx.MockRouter) -> None:
    api.get(emsc.QUERY_URL).mock(side_effect=httpx.ConnectError("name resolution failed"))
    with make_client(retries=0) as client, pytest.raises(SourceError) as raised:
        client.fetch("emsc", "hour")
    assert raised.value.source == "emsc"
    assert "EMSC: request failed (ConnectError: name resolution failed)" in str(raised.value)


def test_client_errors_are_not_retried(api: respx.MockRouter) -> None:
    route = api.get(emsc.QUERY_URL).mock(
        return_value=httpx.Response(400, json={"detail": "Error 400:\n bad   request"})
    )
    with make_client() as client, pytest.raises(SourceError, match=r"EMSC: HTTP 400 .*bad request"):
        client.fetch("emsc", "hour")
    assert route.call_count == 1


def test_invalid_json_is_reported(api: respx.MockRouter) -> None:
    api.get(usgs.feed_url("hour")).mock(return_value=httpx.Response(200, text="<html>oops</html>"))
    with make_client() as client, pytest.raises(SourceError, match="not valid JSON"):
        client.fetch("usgs", "hour")


def test_unexpected_shape_is_reported(api: respx.MockRouter) -> None:
    api.get(usgs.feed_url("hour")).mock(return_value=httpx.Response(200, json={"error": "x"}))
    api.get(emsc.QUERY_URL).mock(return_value=httpx.Response(200, json=[1, 2]))
    with make_client() as client:
        with pytest.raises(SourceError, match="USGS: USGS response has no 'features' list"):
            client.fetch("usgs", "hour")
        with pytest.raises(SourceError, match="EMSC: EMSC response is not a JSON object"):
            client.fetch("emsc", "hour")


def test_usgs_no_content_is_an_error(api: respx.MockRouter) -> None:
    api.get(usgs.feed_url("hour")).mock(return_value=httpx.Response(204))
    with make_client() as client, pytest.raises(SourceError, match="empty response"):
        client.fetch("usgs", "hour")


def test_injected_http_client_is_not_closed(api: respx.MockRouter) -> None:
    mock_usgs(api, "usgs_all_hour.geojson")
    with httpx.Client() as http:
        with QuakeClient(http=http, now=lambda: RECORDED_AT) as client:
            client.fetch("usgs", "hour")
        assert not http.is_closed


def test_default_clock_is_the_module_utcnow(
    api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_usgs(api, "usgs_all_hour.geojson")
    monkeypatch.setattr("quakewatch.client.utcnow", lambda: RECORDED_AT)
    with QuakeClient() as client:
        assert len(client.fetch("usgs", "hour")) == 7
    monkeypatch.setattr("quakewatch.client.utcnow", lambda: RECORDED_AT + timedelta(hours=2))
    with QuakeClient() as client:
        assert client.fetch("usgs", "hour") == []


def test_utcnow_is_timezone_aware() -> None:
    now = utcnow()
    assert now.utcoffset() == timedelta(0)
    assert abs(now - datetime.now(UTC)) < timedelta(seconds=5)


def test_min_mag_filter_is_strict_even_when_the_feed_rounds(api: respx.MockRouter) -> None:
    # The recorded M2.5+ feed contains an M2.45 event; --min-mag 2.5 must not show it.
    magnitudes = [f["properties"]["mag"] for f in fixture_json("usgs_2.5_day.geojson")["features"]]
    assert min(magnitudes) < 2.5
    mock_usgs(api)
    with make_client() as client:
        events = client.fetch("usgs", "day", min_mag=2.5)
    assert len(events) == sum(1 for m in magnitudes if m >= 2.5)


# -- filtering helpers ------------------------------------------------------------


def test_filter_min_mag_drops_unknown_magnitudes() -> None:
    events = [event("a", magnitude=5.0), event("b", magnitude=None), event("c", magnitude=3.9)]
    assert [e.id for e in filter_events(events, min_mag=4.0)] == ["a"]
    assert {e.id for e in filter_events(events)} == {"a", "b", "c"}


def test_filter_since() -> None:
    events = [event("new", minutes_ago=5), event("old", minutes_ago=90)]
    kept = filter_events(events, since=RECORDED_AT - timedelta(hours=1))
    assert [e.id for e in kept] == ["new"]


def test_filter_near_radius() -> None:
    events = [
        event("tokyo", lat=35.68, lon=139.69),
        event("yokohama", lat=35.44, lon=139.64),  # ~27 km from Tokyo
        event("osaka", lat=34.69, lon=135.50),  # ~400 km from Tokyo
    ]
    assert {e.id for e in filter_events(events, near=(35.68, 139.69), radius_km=50)} == {
        "tokyo",
        "yokohama",
    }
    assert {e.id for e in filter_events(events, near=(35.68, 139.69), radius_km=500)} == {
        "tokyo",
        "yokohama",
        "osaka",
    }


def test_filter_near_requires_radius() -> None:
    with pytest.raises(ValueError, match="together"):
        filter_events([], near=(0.0, 0.0))
    with pytest.raises(ValueError, match="together"):
        filter_events([], radius_km=10.0)


def test_sort_is_newest_first_and_stable_across_sources() -> None:
    events = [
        event("b", minutes_ago=1, source="usgs"),
        event("a", minutes_ago=1, source="usgs"),
        event("z", minutes_ago=1, source="emsc"),
        event("newest", minutes_ago=0),
    ]
    assert [e.id for e in sort_events(events)] == ["newest", "z", "a", "b"]


def test_sources_for() -> None:
    assert sources_for("both") == ("usgs", "emsc")
    assert sources_for("emsc") == ("emsc",)
