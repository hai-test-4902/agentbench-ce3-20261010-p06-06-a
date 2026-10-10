"""Fetching, caching and normalizing events from the upstream services."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from types import TracebackType
from typing import Any

import httpx

from quakewatch._meta import PROJECT_URL, __version__
from quakewatch.cache import ResponseCache
from quakewatch.geo import haversine_km
from quakewatch.models import SOURCES, WINDOW_DELTAS, Event, Source, SourceChoice, Window
from quakewatch.sources import PayloadError, emsc, features_of, usgs

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_RETRIES = 2
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_SOURCE_LABELS: dict[Source, str] = {"usgs": "USGS", "emsc": "EMSC"}


class SourceError(RuntimeError):
    """An upstream service could not be reached or returned something unusable."""

    def __init__(self, source: Source, message: str) -> None:
        super().__init__(f"{_SOURCE_LABELS[source]}: {message}")
        self.source: Source = source


def utcnow() -> datetime:
    """The current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def _user_agent() -> str:
    return f"quakewatch/{__version__} (+{PROJECT_URL})"


def sources_for(choice: SourceChoice) -> tuple[Source, ...]:
    """Expand ``both`` into the individual sources."""
    return SOURCES if choice == "both" else (choice,)


def filter_events(
    events: Iterable[Event],
    *,
    min_mag: float | None = None,
    since: datetime | None = None,
    near: tuple[float, float] | None = None,
    radius_km: float | None = None,
) -> list[Event]:
    """Keep events matching every given criterion, newest first.

    Events with no magnitude never satisfy ``min_mag``. ``near`` and
    ``radius_km`` must be given together.
    """
    if (near is None) != (radius_km is None):
        raise ValueError("near and radius_km must be given together")
    kept: list[Event] = []
    for event in events:
        if min_mag is not None and (event.magnitude is None or event.magnitude < min_mag):
            continue
        if since is not None and event.time < since:
            continue
        if (
            near is not None
            and radius_km is not None
            and haversine_km(near[0], near[1], event.lat, event.lon) > radius_km
        ):
            continue
        kept.append(event)
    return sort_events(kept)


def sort_events(events: Iterable[Event]) -> list[Event]:
    """Newest first; ties broken by source then id so output is stable."""
    return sorted(events, key=lambda e: (-e.time.timestamp(), e.source, e.id))


class QuakeClient:
    """Fetches recent events from USGS and EMSC as normalized :class:`Event` objects.

    Decoded responses are stored in ``cache`` (when given) keyed by the logical
    request - source, window and magnitude threshold - so a repeated call within
    the TTL makes no HTTP request at all.
    """

    def __init__(
        self,
        *,
        cache: ResponseCache | None = None,
        http: httpx.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        retries: int = DEFAULT_RETRIES,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cache = cache
        self._owns_http = http is None
        self._http = http or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": _user_agent(), "Accept": "application/json"},
        )
        self._retries = max(0, retries)
        self._now = now or utcnow
        self._sleep = sleep

    def __enter__(self) -> QuakeClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP client if this instance created it."""
        if self._owns_http:
            self._http.close()

    # -- public API ---------------------------------------------------------

    def fetch(self, source: Source, window: Window, min_mag: float | None = None) -> list[Event]:
        """Events from one source within ``window``, newest first."""
        now = self._now()
        try:
            if source == "usgs":
                events = usgs.parse(self._usgs_payload(window, min_mag))
            else:
                events = emsc.parse(self._emsc_payload(window, min_mag, now))
        except PayloadError as error:
            raise SourceError(source, str(error)) from error
        return filter_events(events, min_mag=min_mag, since=now - WINDOW_DELTAS[window])

    def events(
        self, choice: SourceChoice, window: Window, min_mag: float | None = None
    ) -> list[Event]:
        """Events from one or both sources within ``window``, newest first."""
        collected: list[Event] = []
        for source in sources_for(choice):
            collected.extend(self.fetch(source, window, min_mag))
        return sort_events(collected)

    # -- per-source payloads -------------------------------------------------

    def _usgs_payload(self, window: Window, min_mag: float | None) -> Any:
        url = usgs.feed_url(window, min_mag)
        key = f"usgs GET {url}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        payload = self._get_json("usgs", url)
        if payload is None:
            raise SourceError("usgs", f"empty response from {url}")
        self._cache_put(key, payload)
        return payload

    def _emsc_payload(self, window: Window, min_mag: float | None, now: datetime) -> Any:
        # The query's start time moves with the clock, so the cache key is the
        # logical request rather than the URL; the window is re-applied on read.
        threshold = "any" if min_mag is None else f"{min_mag:g}"
        key = f"emsc GET {emsc.QUERY_URL} window={window} minmag={threshold}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        start = now - WINDOW_DELTAS[window]
        features: list[Any] = []
        seen: set[str] = set()
        for page in range(emsc.MAX_PAGES):
            params = emsc.query_params(start, min_mag, offset=1 + page * emsc.PAGE_SIZE)
            body = self._get_json("emsc", emsc.QUERY_URL, params)
            if body is None:  # HTTP 204: nothing (more) matches
                break
            page_features = features_of(body, "EMSC")
            for feature in page_features:
                identity = str(feature.get("id")) if isinstance(feature, dict) else repr(feature)
                if identity not in seen:
                    seen.add(identity)
                    features.append(feature)
            if len(page_features) < emsc.PAGE_SIZE:
                break
        payload = {
            "type": "FeatureCollection",
            "metadata": {"count": len(features)},
            "features": features,
        }
        self._cache_put(key, payload)
        return payload

    # -- plumbing --------------------------------------------------------------

    def _cache_get(self, key: str) -> Any | None:
        return None if self.cache is None else self.cache.get(key)

    def _cache_put(self, key: str, payload: Any) -> None:
        if self.cache is not None:
            self.cache.put(key, payload)

    def _get_json(self, source: Source, url: str, params: dict[str, str] | None = None) -> Any:
        """GET ``url`` and decode JSON. Returns ``None`` for HTTP 204 (no content).

        Transport errors and 429/5xx responses are retried with a short backoff.
        """
        attempts = self._retries + 1
        failure = "no attempt made"
        for attempt in range(attempts):
            if attempt:
                self._sleep(min(4.0, 0.5 * 2 ** (attempt - 1)))
            try:
                response = self._http.get(url, params=params)
            except httpx.HTTPError as error:
                failure = f"request failed ({type(error).__name__}: {error})"
                continue
            if response.status_code == httpx.codes.NO_CONTENT:
                return None
            if response.status_code in _RETRY_STATUS:
                failure = f"HTTP {response.status_code} from {response.url}"
                continue
            if response.status_code != httpx.codes.OK:
                detail = " ".join(response.text.split())[:200]
                raise SourceError(
                    source, f"HTTP {response.status_code} from {response.url}: {detail}"
                )
            try:
                return response.json()
            except ValueError as error:
                raise SourceError(
                    source, f"response from {response.url} is not valid JSON"
                ) from error
        raise SourceError(source, f"{failure} after {attempts} attempt(s)")
