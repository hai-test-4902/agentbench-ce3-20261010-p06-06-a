"""Terminal rendering: Rich tables on a TTY, tab-separated text otherwise."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TextIO

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from quakewatch.compare import Comparison, Match
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


def fmt_signed(value: float | None, digits: int, *, missing: str = "") -> str:
    """Like :func:`fmt_number` but always signed; values that round to zero are ``+0``."""
    if value is None:
        return missing
    rounded = round(value, digits)
    return f"{rounded if rounded else 0.0:+.{digits}f}"


def clean(text: str) -> str:
    """Collapse whitespace so a value can never break a tab-separated row."""
    return " ".join(text.split())


MATCH_COLUMNS: tuple[str, ...] = (
    "time_utc",
    "usgs_mag",
    "emsc_mag",
    "mag_diff",
    "dt_s",
    "dist_km",
    "usgs_id",
    "emsc_id",
    "place",
)


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


# -- compare -----------------------------------------------------------------


def diff_style(diff: float | None) -> str:
    """Rich style for a magnitude difference, by absolute size."""
    if diff is None:
        return "dim"
    if abs(diff) >= 0.5:
        return "bold red"
    if abs(diff) >= 0.2:
        return "yellow"
    return "green"


def match_place(match: Match) -> str:
    """USGS's "distance from a town" description, falling back to EMSC's region."""
    return match.usgs.place or match.emsc.place


def comparison_summary(comparison: Comparison, window: str) -> list[tuple[str, str]]:
    """Key/value summary lines: counts first, then magnitude-difference statistics."""
    rows = [
        ("window", window),
        ("max_seconds", f"{comparison.max_seconds:g}"),
        ("max_km", f"{comparison.max_km:g}"),
        ("usgs_events", str(comparison.usgs_total)),
        ("emsc_events", str(comparison.emsc_total)),
        ("matched", str(len(comparison.matches))),
        ("usgs_only", str(len(comparison.usgs_only))),
        ("emsc_only", str(len(comparison.emsc_only))),
    ]
    stats = comparison.magnitude_stats
    rows.append(("mag_diff_pairs", str(stats.count if stats else 0)))
    if stats:
        rows += [
            ("mag_diff_mean", fmt_signed(stats.mean, 3)),
            ("mag_diff_mean_abs", fmt_number(stats.mean_abs, 3)),
            ("mag_diff_median", fmt_signed(stats.median, 3)),
            ("mag_diff_max_abs", fmt_number(stats.max_abs, 3)),
        ]
    return rows


def plain_comparison(comparison: Comparison, window: str) -> str:
    """Summary as ``key<TAB>value`` lines, a blank line, then one row per matched pair.

    ``mag_diff`` is the USGS magnitude minus the EMSC magnitude and ``dt_s`` the
    USGS origin time minus the EMSC origin time.
    """
    rows: list[Sequence[str]] = [MATCH_COLUMNS]
    for match in comparison.matches:
        rows.append(
            (
                match.usgs.time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                fmt_number(match.usgs.magnitude, 2),
                fmt_number(match.emsc.magnitude, 2),
                fmt_signed(match.magnitude_diff, 2),
                fmt_signed(match.time_delta_s, 1),
                fmt_number(match.distance_km, 1),
                match.usgs.id,
                match.emsc.id,
                clean(match_place(match)),
            )
        )
    return tsv(comparison_summary(comparison, window)) + "\n" + tsv(rows)


_SUMMARY_LABELS: dict[str, str] = {
    "usgs_events": "USGS events",
    "emsc_events": "EMSC events",
    "matched": "Matched (reported by both)",
    "usgs_only": "Unmatched, USGS only",
    "emsc_only": "Unmatched, EMSC only",
    "mag_diff_pairs": "Pairs with both magnitudes",
    "mag_diff_mean": "Mean magnitude difference (USGS - EMSC)",
    "mag_diff_mean_abs": "Mean absolute difference",
    "mag_diff_median": "Median difference",
    "mag_diff_max_abs": "Largest absolute difference",
}


def comparison_summary_table(comparison: Comparison, window: str) -> Table:
    """A Rich table of the match counts and magnitude-difference statistics."""
    table = Table(
        title=f"USGS vs EMSC, past {window}",
        caption=(
            f"matched = origin times within {comparison.max_seconds:g} s "
            f"and epicentres within {comparison.max_km:g} km"
        ),
        show_header=False,
        box=box.SIMPLE_HEAD,
    )
    table.add_column("what")
    table.add_column("value", justify="right", no_wrap=True)
    for key, value in comparison_summary(comparison, window):
        if key in _SUMMARY_LABELS:
            table.add_row(_SUMMARY_LABELS[key], value, style="bold" if key == "matched" else None)
    return table


def matches_table(comparison: Comparison, *, show_ids: bool = True) -> Table:
    """A Rich table with one row per matched pair."""
    caption = f"{len(comparison.matches)} matched pair(s); differences are USGS minus EMSC"
    if not show_ids:
        caption += " - ID columns hidden, widen the terminal or use --plain to see them"
    table = Table(title="Matched events", caption=caption, header_style="bold", box=box.SIMPLE_HEAD)
    table.add_column("Time (UTC)", no_wrap=True)
    table.add_column("USGS", justify="right", no_wrap=True)
    table.add_column("EMSC", justify="right", no_wrap=True)
    table.add_column("Diff", justify="right", no_wrap=True)
    table.add_column("dt s", justify="right", no_wrap=True)
    table.add_column("km", justify="right", no_wrap=True)
    table.add_column("Place", overflow="fold")
    if show_ids:
        table.add_column("USGS ID", no_wrap=True)
        table.add_column("EMSC ID", no_wrap=True)
    for match in comparison.matches:
        diff = match.magnitude_diff
        cells: list[str | Text] = [
            match.usgs.time.strftime("%Y-%m-%d %H:%M:%S"),
            fmt_number(match.usgs.magnitude, 2, missing="-"),
            fmt_number(match.emsc.magnitude, 2, missing="-"),
            Text(fmt_signed(diff, 2, missing="-"), diff_style(diff)),
            fmt_signed(match.time_delta_s, 1),
            fmt_number(match.distance_km, 1),
            Text(match_place(match)),
        ]
        if show_ids:
            cells += [Text(match.usgs.id), Text(match.emsc.id)]
        table.add_row(*cells)
    return table


def render_comparison(comparison: Comparison, *, window: str, rich: bool, stream: TextIO) -> None:
    """Write the comparison to ``stream`` as Rich tables or as plain TSV."""
    if not rich:
        stream.write(plain_comparison(comparison, window))
        return
    console = make_console(stream)
    console.print(comparison_summary_table(comparison, window))
    if comparison.matches:
        show_ids = console.width >= WIDE_TERMINAL_COLUMNS + 20
        console.print(matches_table(comparison, show_ids=show_ids))
