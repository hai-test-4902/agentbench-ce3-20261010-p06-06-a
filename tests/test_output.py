from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest
from rich.console import Console

from quakewatch.compare import Comparison, Match
from quakewatch.models import Event
from quakewatch.output import (
    EVENT_COLUMNS,
    MATCH_COLUMNS,
    diff_style,
    events_table,
    fmt_number,
    fmt_signed,
    magnitude_style,
    plain_comparison,
    plain_events,
    render_comparison,
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


# -- compare ---------------------------------------------------------------------


def comparison() -> Comparison:
    usgs_small = Event("us2", EVENTS[0].time, 4.45, 30.0, 2.0, 3.0, "", "usgs")
    emsc_small = Event("em2", EVENTS[0].time, None, 30.0, 2.0, 3.0, "OFF  THE\tCOAST", "emsc")
    emsc_main = Event("em1", EVENTS[0].time, 5.5, 12.0, 1.7, 126.4, "MOLUCCA SEA", "emsc")
    return Comparison(
        matches=[
            Match(EVENTS[0], emsc_main, time_delta_s=-1.26, distance_km=8.44),
            Match(usgs_small, emsc_small, time_delta_s=-0.04, distance_km=0.0),
        ],
        usgs_only=[usgs_small],
        emsc_only=[emsc_small, emsc_main, emsc_main],
    )


def test_plain_comparison_summary_then_pairs() -> None:
    text = plain_comparison(comparison(), "day")
    summary_block, table_block = text.split("\n\n")
    summary = dict(line.split("\t") for line in summary_block.splitlines())
    assert summary == {
        "window": "day",
        "max_seconds": "60",
        "max_km": "50",
        "usgs_events": "3",
        "emsc_events": "5",
        "matched": "2",
        "usgs_only": "1",
        "emsc_only": "3",
        "mag_diff_pairs": "1",
        "mag_diff_mean": "-0.460",
        "mag_diff_mean_abs": "0.460",
        "mag_diff_median": "-0.460",
        "mag_diff_max_abs": "0.460",
    }
    rows = [line.split("\t") for line in table_block.splitlines()]
    assert rows[0] == list(MATCH_COLUMNS)
    assert rows[1] == [
        "2026-10-10T05:32:01Z",
        "5.04",
        "5.50",
        "-0.46",
        "-1.3",
        "8.4",
        "us6000u1cv",
        "em1",
        "141 km E of Bitung, Indonesia",
    ]
    # Unknown magnitude -> empty cells; "-0.04 s" rounds to +0.0, not "-0.0";
    # the EMSC region stands in when USGS has no place.
    assert rows[2] == [
        "2026-10-10T05:32:01Z",
        "4.45",
        "",
        "",
        "+0.0",
        "0.0",
        "us2",
        "em2",
        "OFF THE COAST",
    ]
    assert text.endswith("\n")


def test_plain_comparison_with_no_matches_omits_statistics() -> None:
    text = plain_comparison(Comparison(usgs_only=[EVENTS[0]]), "hour")
    summary_block, table_block = text.split("\n\n")
    summary = dict(line.split("\t") for line in summary_block.splitlines())
    assert summary["matched"] == "0"
    assert summary["usgs_only"] == "1"
    assert summary["mag_diff_pairs"] == "0"
    assert "mag_diff_mean" not in summary
    assert table_block == "\t".join(MATCH_COLUMNS) + "\n"


def test_render_comparison_plain() -> None:
    stream = io.StringIO()
    render_comparison(comparison(), window="day", rich=False, stream=stream)
    assert stream.getvalue() == plain_comparison(comparison(), "day")


def test_render_comparison_rich(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "160")
    stream = io.StringIO()
    render_comparison(comparison(), window="week", rich=True, stream=stream)
    text = stream.getvalue()
    assert "USGS vs EMSC, past week" in text
    assert "within 60 s" in text
    assert "within 50 km" in text
    for label in ("Matched (reported by both)", "Unmatched, USGS only", "Unmatched, EMSC only"):
        assert label in text
    assert "Mean magnitude difference (USGS - EMSC)" in text
    assert "Matched events" in text
    assert "-0.46" in text
    assert "us6000u1cv" in text
    assert "em1" in text
    assert "2 matched pair(s)" in text


def test_render_comparison_rich_narrow_terminal_hides_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "80")
    stream = io.StringIO()
    render_comparison(comparison(), window="day", rich=True, stream=stream)
    text = stream.getvalue()
    assert max(len(line) for line in text.splitlines()) <= 80
    assert "ID columns hidden" in text
    assert "us6000u1cv" not in text
    assert "-0.46" in text


def test_render_comparison_rich_without_matches_prints_only_the_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "160")
    stream = io.StringIO()
    render_comparison(Comparison(emsc_only=[EVENTS[1]]), window="hour", rich=True, stream=stream)
    text = stream.getvalue()
    assert "USGS vs EMSC, past hour" in text
    assert "Matched events" not in text
    assert "Mean magnitude difference" not in text


@pytest.mark.parametrize(
    ("diff", "style"),
    [(None, "dim"), (0.0, "green"), (-0.19, "green"), (0.2, "yellow"), (-0.5, "bold red")],
)
def test_diff_style(diff: float | None, style: str) -> None:
    assert diff_style(diff) == style


def test_signed_formatting_never_prints_negative_zero() -> None:
    assert fmt_signed(0.2, 1) == "+0.2"
    assert fmt_signed(-0.25, 2) == "-0.25"
    assert fmt_signed(-0.0, 2) == "+0.00"
    assert fmt_signed(-0.004, 2) == "+0.00"
    assert fmt_signed(None, 1, missing="-") == "-"
