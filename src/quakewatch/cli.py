"""The ``quakewatch`` command-line interface."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import click

from quakewatch import export as export_module
from quakewatch._meta import __version__
from quakewatch.cache import DEFAULT_CACHE_DIR, DEFAULT_TTL_SECONDS, ResponseCache
from quakewatch.client import (
    DEFAULT_TIMEOUT_SECONDS,
    QuakeClient,
    SourceError,
    filter_events,
)
from quakewatch.geo import parse_lat_lon
from quakewatch.models import SOURCE_CHOICES, WINDOWS, Event, SourceChoice, Window
from quakewatch.output import render_events

DEFAULT_RADIUS_KM = 100.0

F = TypeVar("F", bound=Callable[..., Any])


def parse_near(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> tuple[float, float] | None:
    """Option callback turning ``"LAT,LON"`` into a pair of floats."""
    if value is None:
        return None
    try:
        return parse_lat_lon(value)
    except ValueError as error:
        raise click.BadParameter(str(error), ctx=ctx, param=param) from error


@dataclass(frozen=True)
class Query:
    """The filters shared by every command."""

    min_mag: float | None
    window: Window
    source: SourceChoice
    near: tuple[float, float] | None
    radius_km: float | None


def _apply(options: list[Callable[[F], F]], func: F) -> F:
    for option in reversed(options):
        func = option(func)
    return func


def window_option(func: F) -> F:
    return click.option(
        "--window",
        type=click.Choice(WINDOWS),
        default="day",
        show_default=True,
        help="How far back to look.",
    )(func)


def min_mag_option(func: F) -> F:
    return click.option(
        "--min-mag",
        type=float,
        default=None,
        metavar="MAG",
        help="Only events of at least this magnitude (events with no magnitude are dropped).",
    )(func)


def source_option(func: F) -> F:
    return click.option(
        "--source",
        type=click.Choice(SOURCE_CHOICES),
        default="both",
        show_default=True,
        help="Which catalogue(s) to query.",
    )(func)


def near_options(func: F) -> F:
    """--near / --radius-km."""
    return _apply(
        [
            click.option(
                "--near",
                metavar="LAT,LON",
                default=None,
                callback=parse_near,
                help="Only events near this point, e.g. --near 35.68,139.69 (negatives work too).",
            ),
            click.option(
                "--radius-km",
                type=click.FloatRange(min=0, min_open=True),
                default=None,
                metavar="KM",
                help=f"Radius around --near [default: {DEFAULT_RADIUS_KM:g} when --near is given].",
            ),
        ],
        func,
    )


def cache_options(func: F) -> F:
    """--no-cache / --cache-ttl / --cache-dir / --timeout."""
    return _apply(
        [
            click.option(
                "--no-cache",
                is_flag=True,
                help="Neither read nor write the on-disk response cache.",
            ),
            click.option(
                "--cache-ttl",
                type=click.FloatRange(min=0),
                default=DEFAULT_TTL_SECONDS,
                show_default=True,
                envvar="QUAKEWATCH_CACHE_TTL",
                metavar="SECONDS",
                help="How long a cached response stays fresh [env: QUAKEWATCH_CACHE_TTL].",
            ),
            click.option(
                "--cache-dir",
                type=click.Path(file_okay=False, path_type=Path),
                default=DEFAULT_CACHE_DIR,
                show_default=True,
                envvar="QUAKEWATCH_CACHE_DIR",
                help="Where cached responses are stored [env: QUAKEWATCH_CACHE_DIR].",
            ),
            click.option(
                "--timeout",
                type=click.FloatRange(min=0, min_open=True),
                default=DEFAULT_TIMEOUT_SECONDS,
                show_default=True,
                metavar="SECONDS",
                help="Per-request HTTP timeout.",
            ),
        ],
        func,
    )


def plain_option(func: F) -> F:
    return click.option(
        "--plain",
        is_flag=True,
        help="Force tab-separated output even on a terminal (automatic when piped).",
    )(func)


def build_query(
    *,
    min_mag: float | None,
    window: Window,
    source: SourceChoice,
    near: tuple[float, float] | None,
    radius_km: float | None,
) -> Query:
    """Validate the --near/--radius-km pairing and apply the default radius."""
    if near is None and radius_km is not None:
        raise click.UsageError("--radius-km requires --near LAT,LON")
    if near is not None and radius_km is None:
        radius_km = DEFAULT_RADIUS_KM
    return Query(min_mag=min_mag, window=window, source=source, near=near, radius_km=radius_km)


def build_client(
    *, no_cache: bool, cache_ttl: float, cache_dir: Path, timeout: float
) -> QuakeClient:
    cache = None if no_cache else ResponseCache(cache_dir, cache_ttl)
    return QuakeClient(cache=cache, timeout=timeout)


def load_events(client: QuakeClient, query: Query) -> list[Event]:
    """Fetch and filter events for ``query``; upstream failures become CLI errors."""
    try:
        events = client.events(query.source, query.window, query.min_mag)
    except SourceError as error:
        raise click.ClickException(str(error)) from error
    return filter_events(events, near=query.near, radius_km=query.radius_km)


def stdout_is_tty() -> bool:
    """Whether stdout is an interactive terminal."""
    return sys.stdout.isatty()


def use_rich(plain: bool) -> bool:
    return not plain and stdout_is_tty()


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version", prog_name="quakewatch")
def cli() -> None:
    """Monitor recent earthquakes reported by USGS and EMSC."""


@cli.command("list")
@min_mag_option
@window_option
@source_option
@near_options
@click.option("--limit", type=click.IntRange(min=1), default=None, help="Show at most N events.")
@plain_option
@cache_options
def list_command(
    min_mag: float | None,
    window: Window,
    source: SourceChoice,
    near: tuple[float, float] | None,
    radius_km: float | None,
    limit: int | None,
    plain: bool,
    no_cache: bool,
    cache_ttl: float,
    cache_dir: Path,
    timeout: float,
) -> None:
    """List recent earthquakes, newest first."""
    query = build_query(
        min_mag=min_mag, window=window, source=source, near=near, radius_km=radius_km
    )
    with build_client(
        no_cache=no_cache, cache_ttl=cache_ttl, cache_dir=cache_dir, timeout=timeout
    ) as client:
        events = load_events(client, query)
    if limit is not None:
        events = events[:limit]
    title = f"Earthquakes, past {window} ({source})"
    render_events(events, rich=use_rich(plain), stream=sys.stdout, title=title)


@cli.command("export")
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="File to write.",
)
@click.option(
    "-f",
    "--format",
    "fmt",
    type=click.Choice(export_module.EXPORT_FORMATS),
    default=None,
    help="Output format [default: inferred from the --output extension].",
)
@min_mag_option
@window_option
@source_option
@near_options
@cache_options
def export_command(
    output: Path,
    fmt: export_module.ExportFormat | None,
    min_mag: float | None,
    window: Window,
    source: SourceChoice,
    near: tuple[float, float] | None,
    radius_km: float | None,
    no_cache: bool,
    cache_ttl: float,
    cache_dir: Path,
    timeout: float,
) -> None:
    """Export recent earthquakes to a CSV, JSON or GeoJSON file."""
    resolved = fmt or export_module.infer_format(output)
    if resolved is None:
        raise click.UsageError(
            f"cannot infer the format from '{output.name}'; "
            "use a .csv, .json or .geojson extension or pass --format"
        )
    query = build_query(
        min_mag=min_mag, window=window, source=source, near=near, radius_km=radius_km
    )
    with build_client(
        no_cache=no_cache, cache_ttl=cache_ttl, cache_dir=cache_dir, timeout=timeout
    ) as client:
        events = load_events(client, query)
    try:
        export_module.write(events, output, resolved)
    except OSError as error:
        raise click.ClickException(f"cannot write {output}: {error.strerror or error}") from error
    click.echo(f"Wrote {len(events)} event(s) to {output} ({resolved})")


def main() -> None:
    """Console-script entry point."""
    cli(prog_name="quakewatch")
