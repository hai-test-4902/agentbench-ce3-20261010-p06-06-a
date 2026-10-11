"""Live tests against the real USGS and EMSC services.

Deselected by default; run with ``pytest -m integration``. They assert on shape
and invariants only, since the catalogue contents change by the minute.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from quakewatch.cli import cli
from quakewatch.client import QuakeClient
from quakewatch.models import FIELD_NAMES, Source
from quakewatch.output import EVENT_COLUMNS, MATCH_COLUMNS

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("source", ["usgs", "emsc"])
def test_live_day_window(source: Source) -> None:
    before = datetime.now(timezone.utc)
    with QuakeClient() as client:
        events = client.fetch(source, "day")
    # Both catalogues record far more than one event per day worldwide.
    assert events, f"{source} returned no events for the past day"
    assert {event.source for event in events} == {source}
    assert len({event.id for event in events}) == len(events)
    for event in events:
        assert before - timedelta(days=1, minutes=1) <= event.time <= before + timedelta(minutes=5)
        assert -90 <= event.lat <= 90
        assert -180 <= event.lon <= 180
        assert event.magnitude is None or -2 <= event.magnitude <= 10
        assert event.depth_km is None or -10 <= event.depth_km <= 800


@pytest.mark.parametrize("source", ["usgs", "emsc"])
def test_live_min_mag_is_respected(source: Source) -> None:
    with QuakeClient() as client:
        events = client.fetch(source, "week", min_mag=4.5)
    assert events, f"{source} returned no M4.5+ events for the past week"
    assert all(event.magnitude is not None and event.magnitude >= 4.5 for event in events)


def test_live_cli_list_and_cache(tmp_path: Path) -> None:
    runner = CliRunner()
    args = ["list", "--window", "day", "--min-mag", "4", "--cache-dir", str(tmp_path / "cache")]
    first = runner.invoke(cli, args)
    assert first.exit_code == 0, first.output
    assert first.output.splitlines()[0].split("\t") == list(EVENT_COLUMNS)
    assert len(first.output.splitlines()) > 1
    assert len(list((tmp_path / "cache").glob("*.json"))) == 2
    second = runner.invoke(cli, args)
    assert second.exit_code == 0, second.output
    assert second.output == first.output


def test_live_cli_export_geojson(tmp_path: Path) -> None:
    target = tmp_path / "quakes.geojson"
    result = CliRunner().invoke(
        cli, ["export", "--window", "day", "--min-mag", "4", "--no-cache", "-o", str(target)]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["type"] == "FeatureCollection"
    assert data["features"]
    expected = set(FIELD_NAMES) - {"lat", "lon"}
    assert all(set(feature["properties"]) == expected for feature in data["features"])


def test_live_cli_compare_day(tmp_path: Path) -> None:
    args = ["compare", "--window", "day", "--cache-dir", str(tmp_path / "cache")]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    summary_block, table_block = result.output.split("\n\n")
    summary = dict(line.split("\t") for line in summary_block.splitlines())
    rows = table_block.splitlines()
    assert rows[0].split("\t") == list(MATCH_COLUMNS)
    matched = int(summary["matched"])
    assert matched == len(rows) - 1
    # On any ordinary day the two global catalogues share at least a few M4+ events.
    assert matched > 0
    assert matched + int(summary["usgs_only"]) == int(summary["usgs_events"])
    assert matched + int(summary["emsc_only"]) == int(summary["emsc_events"])
    for row in rows[1:]:
        cells = dict(zip(MATCH_COLUMNS, row.split("\t"), strict=True))
        assert abs(float(cells["dt_s"])) <= 60
        assert float(cells["dist_km"]) <= 50
