"""Shared fixtures: recorded API responses, a frozen clock and a network guard."""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from quakewatch.models import parse_iso_utc
from quakewatch.sources import emsc

FIXTURES = Path(__file__).parent / "fixtures"
USGS_FEED_RE = r"^https://earthquake\.usgs\.gov/earthquakes/feed/v1\.0/summary/[^/]+\.geojson$"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_json(name: str) -> Any:
    return json.loads(fixture_bytes(name))


RECORDED_AT: datetime = parse_iso_utc(fixture_json("recording.json")["recorded_at"])


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests must never open a real connection; only integration tests may."""
    if request.node.get_closest_marker("integration") is not None:
        return

    def refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("unit tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


@pytest.fixture
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Pin the client's clock to the moment the fixtures were recorded."""
    monkeypatch.setattr("quakewatch.client.utcnow", lambda: RECORDED_AT)
    return RECORDED_AT


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    """A respx router; any request without a matching route fails the test."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router


def mock_usgs(
    router: respx.MockRouter, fixture: str = "usgs_2.5_day.geojson", *, url: str | None = None
) -> respx.Route:
    route = router.get(url) if url else router.get(url__regex=USGS_FEED_RE)
    route.mock(
        return_value=httpx.Response(
            200, content=fixture_bytes(fixture), headers={"Content-Type": "application/json"}
        )
    )
    return route


def mock_emsc(router: respx.MockRouter, fixture: str = "emsc_day_minmag2.5.json") -> respx.Route:
    route = router.get(emsc.QUERY_URL)
    route.mock(
        return_value=httpx.Response(
            200, content=fixture_bytes(fixture), headers={"Content-Type": "application/json"}
        )
    )
    return route
