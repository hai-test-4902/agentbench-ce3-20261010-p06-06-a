from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest
from rich.console import Console

from quakewatch.models import Event
from quakewatch.output import (
    EVENT_COLUMNS,
    events_table,
    fmt_number,
    magnitude_style,
    plain_events,
    render_events,
)

EVENTS = [
    Event(
        "us6000u1cv",
        datetime(2026, 10, 10, 5, 32, 1, 250000, tzinfo=timezone.utc),
        5.04,
        10.0,
        1.6254,
        126.3891,
        "141 km E of\tBitung,\nIndonesia",
        "usgs",
    ),
    Event(
        "20261010_0000101",
        datetime(2026, 10, 10, 5, 32, 1, tzinfo=timezone.utc),
        None,
        None,
        -1.496,
        126.359,
        "MOLUCCA SEA",
        "emsc",
    ),
]


def test_plain_output_is_tab_separated_with_header() -> None:
    lines = plain_events(EVENTS).splitlines()
    assert lines[0].split("\t") == list(EVENT_COLUMNS)
    assert lines[1].split("\t") == [
        "2026-10-10T05:32:01Z",
        "5.0",
        "10.0",
        "1.625",
        "126.389",
        "141 km E of Bitung, Indonesia",
        "usgs",
        "us6000u1cv",
    ]
    assert lines[2].split("\t") == [
        "2026-10-10T05:32:01Z",
        "",
        "",
        "-1.496",
        "126.359",
        "MOLUCCA SEA",
        "emsc",
        "20261010_0000101",
    ]
    assert len(lines) == 3


def test_plain_output_for_no_events_is_just_the_header() -> None:
    assert plain_events([]) == "\t".join(EVENT_COLUMNS) + "\n"


def test_render_plain_has_no_ansi_or_box_drawing() -> None:
    stream = io.StringIO()
    render_events(EVENTS, rich=False, stream=stream)
    assert stream.getvalue() == plain_events(EVENTS)
    assert "\x1b" not in stream.getvalue()
    assert "│" not in stream.getvalue()


def test_rich_table_contents() -> None:
    stream = io.StringIO()
    console = Console(file=stream, width=160, force_terminal=True, color_system="standard")
    console.print(events_table(EVENTS, title="Earthquakes, past day (both)"))
    text = stream.getvalue()
    assert "Earthquakes, past day (both)" in text
    assert "2 event(s)" in text
    assert "Time (UTC)" in text
    assert "2026-10-10 05:32:01" in text
    assert "us6000u1cv" in text
    assert "MOLUCCA SEA" in text
    assert "\x1b[" in text  # styled for a terminal
    assert "─" in text or "━" in text


def test_render_rich_writes_a_table_to_the_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "160")
    stream = io.StringIO()
    render_events(EVENTS, rich=True, stream=stream, title="T")
    text = stream.getvalue()
    assert "Time (UTC)" in text
    assert "20261010_0000101" in text


def test_rich_table_does_not_interpret_markup_in_place_names() -> None:
    event = Event("x[1]", EVENTS[0].time, 1.0, 1.0, 0.0, 0.0, "[bold]not markup[/bold]", "usgs")
    stream = io.StringIO()
    Console(file=stream, width=160).print(events_table([event]))
    assert "[bold]not markup[/bold]" in stream.getvalue()
    assert "x[1]" in stream.getvalue()


@pytest.mark.parametrize(
    ("magnitude", "style"),
    [
        (None, "dim"),
        (1.0, "dim"),
        (2.5, "green"),
        (4.5, "yellow"),
        (6.0, "bold red"),
        (8.1, "bold red"),
    ],
)
def test_magnitude_style(magnitude: float | None, style: str) -> None:
    assert magnitude_style(magnitude) == style


def test_number_formatting() -> None:
    assert fmt_number(1.26, 1) == "1.3"
    assert fmt_number(None, 1) == ""
    assert fmt_number(None, 1, missing="-") == "-"


def test_narrow_terminal_drops_the_id_column_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    stream = io.StringIO()
    render_events(EVENTS, rich=True, stream=stream)
    text = stream.getvalue()
    assert max(len(line) for line in text.splitlines()) <= 80
    assert "us6000u1cv" not in text
    assert "ID column hidden" in text
    assert "2026-10-10 05:32:01" in text
    assert "MOLUCCA" in text  # the place column may wrap, but is never cut short
    assert "…" not in text
