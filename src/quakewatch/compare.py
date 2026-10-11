"""Pairing up the same earthquake as reported by USGS and by EMSC."""

from __future__ import annotations

import bisect
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from quakewatch.client import sort_events
from quakewatch.geo import haversine_km
from quakewatch.models import Event

DEFAULT_MAX_SECONDS = 60.0
DEFAULT_MAX_KM = 50.0


@dataclass(frozen=True)
class Match:
    """One earthquake located by both agencies."""

    usgs: Event
    emsc: Event
    time_delta_s: float
    """USGS origin time minus EMSC origin time, in seconds."""
    distance_km: float
    """Great-circle distance between the two epicentres."""

    @property
    def magnitude_diff(self) -> float | None:
        """USGS magnitude minus EMSC magnitude; ``None`` if either is unknown."""
        if self.usgs.magnitude is None or self.emsc.magnitude is None:
            return None
        return self.usgs.magnitude - self.emsc.magnitude


@dataclass(frozen=True)
class MagnitudeStats:
    """Summary of USGS-minus-EMSC magnitude differences over matched pairs."""

    count: int
    mean: float
    mean_abs: float
    median: float
    max_abs: float

    @classmethod
    def of(cls, differences: Sequence[float]) -> MagnitudeStats | None:
        if not differences:
            return None
        return cls(
            count=len(differences),
            mean=statistics.fmean(differences),
            mean_abs=statistics.fmean(abs(d) for d in differences),
            median=statistics.median(differences),
            max_abs=max(abs(d) for d in differences),
        )


@dataclass(frozen=True)
class Comparison:
    """Result of matching one list of USGS events against one list of EMSC events."""

    matches: list[Match] = field(default_factory=list)
    usgs_only: list[Event] = field(default_factory=list)
    emsc_only: list[Event] = field(default_factory=list)
    max_seconds: float = DEFAULT_MAX_SECONDS
    max_km: float = DEFAULT_MAX_KM

    @property
    def usgs_total(self) -> int:
        return len(self.matches) + len(self.usgs_only)

    @property
    def emsc_total(self) -> int:
        return len(self.matches) + len(self.emsc_only)

    @property
    def magnitude_diffs(self) -> list[float]:
        """Differences for the pairs where both sources report a magnitude."""
        diffs = (match.magnitude_diff for match in self.matches)
        return [diff for diff in diffs if diff is not None]

    @property
    def magnitude_stats(self) -> MagnitudeStats | None:
        return MagnitudeStats.of(self.magnitude_diffs)


def match_events(
    usgs_events: Iterable[Event],
    emsc_events: Iterable[Event],
    *,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    max_km: float = DEFAULT_MAX_KM,
) -> Comparison:
    """Pair events whose origin times differ by at most ``max_seconds`` and whose
    epicentres are at most ``max_km`` apart (both bounds inclusive).

    Matching is one-to-one. Where several pairings are possible - aftershock
    sequences are the usual case - the closest candidates in combined time and
    distance are paired first, so each event ends up with its best available
    partner rather than the first one encountered.
    """
    if max_seconds < 0 or max_km < 0:
        raise ValueError("max_seconds and max_km must be >= 0")
    usgs_list = list(usgs_events)
    emsc_list = sorted(emsc_events, key=lambda event: event.time)
    emsc_times = [event.time.timestamp() for event in emsc_list]

    candidates: list[tuple[float, str, str, int, int, float, float]] = []
    for u_index, usgs_event in enumerate(usgs_list):
        moment = usgs_event.time.timestamp()
        low = bisect.bisect_left(emsc_times, moment - max_seconds)
        high = bisect.bisect_right(emsc_times, moment + max_seconds)
        for e_index in range(low, high):
            emsc_event = emsc_list[e_index]
            delta = moment - emsc_times[e_index]
            distance = haversine_km(usgs_event.lat, usgs_event.lon, emsc_event.lat, emsc_event.lon)
            if distance > max_km:
                continue
            score = _ratio(abs(delta), max_seconds) + _ratio(distance, max_km)
            candidates.append(
                (score, usgs_event.id, emsc_event.id, u_index, e_index, delta, distance)
            )

    candidates.sort()
    used_usgs: set[int] = set()
    used_emsc: set[int] = set()
    matches: list[Match] = []
    for _score, _usgs_id, _emsc_id, u_index, e_index, delta, distance in candidates:
        if u_index in used_usgs or e_index in used_emsc:
            continue
        used_usgs.add(u_index)
        used_emsc.add(e_index)
        matches.append(Match(usgs_list[u_index], emsc_list[e_index], delta, distance))

    matches.sort(key=lambda m: (-m.usgs.time.timestamp(), m.usgs.id))
    return Comparison(
        matches=matches,
        usgs_only=sort_events(e for i, e in enumerate(usgs_list) if i not in used_usgs),
        emsc_only=sort_events(e for i, e in enumerate(emsc_list) if i not in used_emsc),
        max_seconds=max_seconds,
        max_km=max_km,
    )


def _ratio(value: float, limit: float) -> float:
    return value / limit if limit > 0 else 0.0


def _passes(
    event: Event,
    min_mag: float | None,
    near: tuple[float, float] | None,
    radius_km: float | None,
) -> tuple[bool, bool]:
    """Whether ``event`` satisfies the magnitude filter and the location filter."""
    mag_ok = min_mag is None or (event.magnitude is not None and event.magnitude >= min_mag)
    near_ok = (
        near is None
        or radius_km is None
        or haversine_km(near[0], near[1], event.lat, event.lon) <= radius_km
    )
    return mag_ok, near_ok


def filter_comparison(
    comparison: Comparison,
    *,
    min_mag: float | None = None,
    near: tuple[float, float] | None = None,
    radius_km: float | None = None,
) -> Comparison:
    """Apply the user's filters *after* matching.

    Filtering each catalogue first would break pairs that straddle a threshold:
    an earthquake that USGS calls M4.6 and EMSC calls M4.4 would show up as
    "USGS only" under ``--min-mag 4.5``. So a matched pair is kept when either
    side satisfies each filter, and an unmatched event when it satisfies them
    itself.
    """
    if (near is None) != (radius_km is None):
        raise ValueError("near and radius_km must be given together")

    def keep_event(event: Event) -> bool:
        return all(_passes(event, min_mag, near, radius_km))

    def keep_match(match: Match) -> bool:
        usgs_mag, usgs_near = _passes(match.usgs, min_mag, near, radius_km)
        emsc_mag, emsc_near = _passes(match.emsc, min_mag, near, radius_km)
        return (usgs_mag or emsc_mag) and (usgs_near or emsc_near)

    return Comparison(
        matches=[match for match in comparison.matches if keep_match(match)],
        usgs_only=[event for event in comparison.usgs_only if keep_event(event)],
        emsc_only=[event for event in comparison.emsc_only if keep_event(event)],
        max_seconds=comparison.max_seconds,
        max_km=comparison.max_km,
    )
