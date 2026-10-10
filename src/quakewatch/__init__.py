"""quakewatch: monitor recent earthquakes from the USGS and EMSC catalogues."""

from __future__ import annotations

from quakewatch._meta import __version__
from quakewatch.cache import ResponseCache
from quakewatch.client import QuakeClient, SourceError
from quakewatch.models import Event, Source, Window

__all__ = [
    "Event",
    "QuakeClient",
    "ResponseCache",
    "Source",
    "SourceError",
    "Window",
    "__version__",
]
