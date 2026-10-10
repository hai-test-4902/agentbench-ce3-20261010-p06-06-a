"""Terminal rendering: Rich tables on a TTY, tab-separated text otherwise."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TextIO

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from quakewatch.models import Event

# Below this width the eight-column table cannot fit without mangling a column.
WIDE_TERMINAL_COLUMNS = 100

EVENT_COLUMNS: tuple[str, ...] = (
    "time_utc",
    "magnitude",
    "depth_km",
    "lat",
    "lon",
    "place",
    "source",
    "id",
)


def fmt_number(value: float | None, digits: int, *, missing: str = "") -> str:
    """Fixed-precision number, or ``missing`` when there is no value."""
    return missing if value is None else f"{value:.{digits}f}"


def clean(text: str) -> str:
    """Collapse whitespace so a value can never break a tab-separated row."""
    return " ".join(text.split())


def magnitude_style(magnitude: float | None) -> str:
    """Rich style used to colour a magnitude by severity."""
    if magnitude is None:
        return "dim"
    if magnitude >= 6.0:
        return "bold red"
    if magnitude >= 4.5:
        return "yellow"
    if magnitude >= 2.5:
        return "green"
    return "dim"


def tsv(rows: Sequence[Sequence[str]]) -> str:
    """Join rows as tab-separated lines with a trailing newline."""
    return "".join("\t".join(row) + "\n" for row in rows)


def plain_events(events: Sequence[Event]) -> str:
    """Tab-separated listing with a header row, suitable for cut/awk/grep."""
    rows: list[Sequence[str]] = [EVENT_COLUMNS]
    for event in events:
        rows.append(
            (
                event.time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                fmt_number(event.magnitude, 1),
                fmt_number(event.depth_km, 1),
                fmt_number(event.lat, 3),
                fmt_number(event.lon, 3),
                clean(event.place),
                event.source,
                event.id,
            )
        )
    return tsv(rows)


def events_table(
    events: Sequence[Event], *, title: str | None = None, show_id: bool = True
) -> Table:
    """A Rich table of events; ``show_id=False`` drops the ID column to save width."""
    caption = f"{len(events)} event(s)"
    if not show_id:
        caption += " - ID column hidden, widen the terminal or use --plain to see it"
    table = Table(title=title, caption=caption, header_style="bold", box=box.SIMPLE_HEAD)
    table.add_column("Time (UTC)", no_wrap=True)
    table.add_column("Mag", justify="right", no_wrap=True)
    table.add_column("Depth km", justify="right", no_wrap=True)
    table.add_column("Lat", justify="right", no_wrap=True)
    table.add_column("Lon", justify="right", no_wrap=True)
    table.add_column("Place", overflow="fold")
    table.add_column("Source", no_wrap=True)
    if show_id:
        table.add_column("ID", no_wrap=True)
    for event in events:
        cells: list[str | Text] = [
            event.time.strftime("%Y-%m-%d %H:%M:%S"),
            Text(fmt_number(event.magnitude, 1, missing="-"), magnitude_style(event.magnitude)),
            fmt_number(event.depth_km, 1, missing="-"),
            fmt_number(event.lat, 3),
            fmt_number(event.lon, 3),
            Text(event.place),
            event.source,
        ]
        if show_id:
            cells.append(Text(event.id))
        table.add_row(*cells)
    return table


def make_console(stream: TextIO) -> Console:
    """A Console that writes Rich output to ``stream``."""
    return Console(file=stream, highlight=False)


def render_events(
    events: Sequence[Event], *, rich: bool, stream: TextIO, title: str | None = None
) -> None:
    """Write the listing to ``stream`` as a Rich table or as plain TSV."""
    if rich:
        console = make_console(stream)
        show_id = console.width >= WIDE_TERMINAL_COLUMNS
        console.print(events_table(events, title=title, show_id=show_id))
    else:
        stream.write(plain_events(events))
