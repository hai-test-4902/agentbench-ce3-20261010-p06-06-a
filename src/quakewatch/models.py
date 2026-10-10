"""The normalized event model shared by every data source."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, get_args

Source = Literal["usgs", "emsc"]
SourceChoice = Literal["usgs", "emsc", "both"]
Window = Literal["hour", "day", "week"]

SOURCES: tuple[Source, ...] = get_args(Source)
SOURCE_CHOICES: tuple[SourceChoice, ...] = get_args(SourceChoice)
WINDOWS: tuple[Window, ...] = get_args(Window)

WINDOW_DELTAS: dict[Window, timedelta] = {
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(days=7),
}

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$"
)


@dataclass(frozen=True)
class Event:
    """One earthquake as reported by one source."""

    id: str
    time: datetime
    magnitude: float | None
    depth_km: float | None
    lat: float
    lon: float
    place: str
    source: Source

    def __post_init__(self) -> None:
        if self.time.tzinfo is None:
            raise ValueError("Event.time must be timezone-aware")
        object.__setattr__(self, "time", self.time.astimezone(timezone.utc))

    @property
    def time_iso(self) -> str:
        """Event time as an ISO-8601 UTC string with millisecond precision."""
        return format_utc(self.time)

    def to_dict(self) -> dict[str, str | float | None]:
        """A JSON-serializable mapping, in the canonical field order."""
        return {
            "id": self.id,
            "time": self.time_iso,
            "magnitude": self.magnitude,
            "depth_km": self.depth_km,
            "lat": self.lat,
            "lon": self.lon,
            "place": self.place,
            "source": self.source,
        }


FIELD_NAMES: tuple[str, ...] = (
    "id",
    "time",
    "magnitude",
    "depth_km",
    "lat",
    "lon",
    "place",
    "source",
)


def format_utc(moment: datetime) -> str:
    """Format an aware datetime as ``YYYY-MM-DDTHH:MM:SS.mmmZ``."""
    utc = moment.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def parse_iso_utc(text: str) -> datetime:
    """Parse an ISO-8601 timestamp into an aware UTC datetime.

    ``datetime.fromisoformat`` on Python 3.10 rejects the ``Z`` suffix and
    fractional seconds that are not exactly 3 or 6 digits, both of which EMSC
    emits (``2026-10-10T23:21:38.0Z``), so this is parsed by hand. Timestamps
    without an offset are taken to be UTC.
    """
    match = _ISO_RE.match(text.strip())
    if match is None:
        raise ValueError(f"unrecognized timestamp: {text!r}")
    year, month, day, hour, minute, second, fraction, offset = match.groups()
    micro = int((fraction or "0")[:6].ljust(6, "0"))
    tz = timezone.utc
    if offset and offset != "Z":
        sign = -1 if offset[0] == "-" else 1
        digits = offset[1:].replace(":", "")
        tz = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
    moment = datetime(
        int(year), int(month), int(day), int(hour), int(minute), int(second), micro, tzinfo=tz
    )
    return moment.astimezone(timezone.utc)


def from_epoch_ms(milliseconds: float) -> datetime:
    """Convert milliseconds since the Unix epoch into an aware UTC datetime."""
    return EPOCH + timedelta(milliseconds=milliseconds)
