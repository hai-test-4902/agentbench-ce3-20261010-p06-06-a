from __future__ import annotations

import csv
import json
import runpy
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import respx
from click.testing import CliRunner

from quakewatch import __version__
from quakewatch.cli import cli, main
from quakewatch.geo import haversine_km
from quakewatch.models import FIELD_NAMES, parse_iso_utc
from quakewatch.output import EVENT_COLUMNS
from quakewatch.sources import emsc
from tests.conftest import RECORDED_AT, fixture_json, mock_emsc, mock_usgs

pytestmark = pytest.mark.usefixtures("frozen_now", "in_tmp_cwd")

USGS_COUNT = len(fixture_json("usgs_2.5_day.geojson")["features"])
EMSC_COUNT = len(fixture_json("emsc_day_minmag2.5.json")["features"])


@pytest.fixture
def in_tmp_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run each CLI test in an empty directory so the default cache lands there."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QUAKEWATCH_CACHE_DIR", raising=False)
    monkeypatch.delenv("QUAKEWATCH_CACHE_TTL", raising=False)
    return tmp_path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def rows_of(output: str) -> list[dict[str, str]]:
    return list(csv.DictReader(output.splitlines(), delimiter="\t"))


# -- list -----------------------------------------------------------------------


def test_list_plain_when_not_a_tty(runner: CliRunner, api: respx.MockRouter) -> None:
    mock_usgs(api)
    mock_emsc(api)
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split("\t") == list(EVENT_COLUMNS)
    assert len(lines) == 1 + USGS_COUNT + EMSC_COUNT
    assert "\x1b" not in result.output
    rows = rows_of(result.output)
    assert {row["source"] for row in rows} == {"usgs", "emsc"}
    times = [row["time_utc"] for row in rows]
    assert times == sorted(times, reverse=True)


def test_list_rich_table_on_a_tty(
    runner: CliRunner, api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_usgs(api, "usgs_all_hour.geojson")
    monkeypatch.setattr("quakewatch.cli.stdout_is_tty", lambda: True)
    result = runner.invoke(cli, ["list", "--source", "usgs", "--window", "hour"])
    assert result.exit_code == 0, result.output
    assert "Earthquakes, past hour (usgs)" in result.output
    assert "Time (UTC)" in result.output
    assert "7 event(s)" in result.output
    assert "\t" not in result.output


def test_list_plain_flag_overrides_tty(
    runner: CliRunner, api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_usgs(api, "usgs_all_hour.geojson")
    monkeypatch.setattr("quakewatch.cli.stdout_is_tty", lambda: True)
    result = runner.invoke(cli, ["list", "--source", "usgs", "--window", "hour", "--plain"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0].split("\t") == list(EVENT_COLUMNS)


def test_list_source_filter_only_calls_that_api(runner: CliRunner, api: respx.MockRouter) -> None:
    usgs_route = mock_usgs(api)
    emsc_route = mock_emsc(api)
    result = runner.invoke(cli, ["list", "--source", "emsc", "--no-cache"])
    assert result.exit_code == 0, result.output
    assert usgs_route.call_count == 0
    assert emsc_route.call_count == 1
    assert {row["source"] for row in rows_of(result.output)} == {"emsc"}


def test_list_min_mag_and_limit(runner: CliRunner, api: respx.MockRouter) -> None:
    mock_usgs(api)
    mock_emsc(api)
    result = runner.invoke(cli, ["list", "--min-mag", "4.5", "--limit", "3"])
    assert result.exit_code == 0, result.output
    rows = rows_of(result.output)
    assert len(rows) == 3
    assert all(float(row["magnitude"]) >= 4.5 for row in rows)


def test_list_near_accepts_negative_coordinates(runner: CliRunner, api: respx.MockRouter) -> None:
    mock_usgs(api)
    mock_emsc(api)
    result = runner.invoke(cli, ["list", "--near", "-8.3,120.5", "--radius-km", "1500"])
    assert result.exit_code == 0, result.output
    rows = rows_of(result.output)
    assert rows
    for row in rows:
        assert haversine_km(-8.3, 120.5, float(row["lat"]), float(row["lon"])) <= 1500.5


def test_list_near_defaults_to_100_km(runner: CliRunner, api: respx.MockRouter) -> None:
    mock_emsc(api)
    anchor = emsc.parse(fixture_json("emsc_day_minmag2.5.json"))[0]
    result = runner.invoke(
        cli, ["list", "--source", "emsc", "--near", f"{anchor.lat},{anchor.lon}"]
    )
    assert result.exit_code == 0, result.output
    rows = rows_of(result.output)
    assert anchor.id in {row["id"] for row in rows}
    for row in rows:
        assert haversine_km(anchor.lat, anchor.lon, float(row["lat"]), float(row["lon"])) <= 100.5


def test_list_window_choice_is_passed_to_both_sources(
    runner: CliRunner, api: respx.MockRouter
) -> None:
    usgs_route = mock_usgs(api, "usgs_all_hour.geojson")
    emsc_route = mock_emsc(api, "emsc_hour.json")
    result = runner.invoke(cli, ["list", "--window", "hour"])
    assert result.exit_code == 0, result.output
    assert str(usgs_route.calls.last.request.url).endswith("/all_hour.geojson")
    start = emsc_route.calls.last.request.url.params["start"]
    assert parse_iso_utc(start) == RECORDED_AT - timedelta(hours=1)
    assert all(
        parse_iso_utc(row["time_utc"]) >= RECORDED_AT - timedelta(hours=1, seconds=1)
        for row in rows_of(result.output)
    )


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["list", "--radius-km", "50"], "--radius-km requires --near"),
        (["list", "--near", "100,0"], "latitude 100 is outside"),
        (["list", "--near", "tokyo"], "expected LAT,LON"),
        (["list", "--near", "1,2", "--radius-km", "0"], "--radius-km"),
        (["list", "--window", "month"], "'month' is not one of"),
        (["list", "--source", "jma"], "'jma' is not one of"),
        (["list", "--min-mag", "big"], "'big' is not a valid float"),
        (["list", "--limit", "0"], "--limit"),
        (["list", "--cache-ttl", "-1"], "--cache-ttl"),
        (["export", "--format", "csv"], "Missing option '-o' / '--output'"),
        (["export", "-o", "quakes.txt"], "cannot infer the format from 'quakes.txt'"),
        (["export", "-o", "quakes.csv", "--format", "xml"], "'xml' is not one of"),
    ],
)
def test_usage_errors_exit_2_without_calling_the_api(
    runner: CliRunner, api: respx.MockRouter, args: list[str], message: str
) -> None:
    result = runner.invoke(cli, args)
    assert result.exit_code == 2, result.output
    assert message in result.output
    assert not api.calls


def test_upstream_failure_exits_1_with_a_message(runner: CliRunner, api: respx.MockRouter) -> None:
    mock_usgs(api)
    api.get(emsc.QUERY_URL).mock(return_value=httpx.Response(400, text="bad request"))
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 1
    assert "Error: EMSC: HTTP 400" in result.output
    assert "Traceback" not in result.output


# -- cache options ----------------------------------------------------------------


def test_default_cache_location_and_reuse(
    runner: CliRunner, api: respx.MockRouter, in_tmp_cwd: Path
) -> None:
    usgs_route = mock_usgs(api)
    emsc_route = mock_emsc(api)
    first = runner.invoke(cli, ["list"])
    second = runner.invoke(cli, ["list"])
    assert first.exit_code == second.exit_code == 0
    assert first.output == second.output
    assert (usgs_route.call_count, emsc_route.call_count) == (1, 1)
    assert len(list((in_tmp_cwd / ".cache" / "quakewatch").glob("*.json"))) == 2


def test_no_cache_neither_reads_nor_writes(
    runner: CliRunner, api: respx.MockRouter, in_tmp_cwd: Path
) -> None:
    route = mock_usgs(api)
    runner.invoke(cli, ["list", "--source", "usgs"])  # populate
    assert route.call_count == 1
    for _ in range(2):
        result = runner.invoke(cli, ["list", "--source", "usgs", "--no-cache"])
        assert result.exit_code == 0, result.output
    assert route.call_count == 3
    assert len(list((in_tmp_cwd / ".cache" / "quakewatch").glob("*.json"))) == 1


def test_no_cache_writes_nothing(
    runner: CliRunner, api: respx.MockRouter, in_tmp_cwd: Path
) -> None:
    mock_usgs(api)
    result = runner.invoke(cli, ["list", "--source", "usgs", "--no-cache"])
    assert result.exit_code == 0, result.output
    assert not (in_tmp_cwd / ".cache").exists()


def test_cache_ttl_zero_always_refetches(runner: CliRunner, api: respx.MockRouter) -> None:
    route = mock_usgs(api)
    for _ in range(2):
        result = runner.invoke(cli, ["list", "--source", "usgs", "--cache-ttl", "0"])
        assert result.exit_code == 0, result.output
    assert route.call_count == 2


def test_cache_dir_and_ttl_from_environment(
    runner: CliRunner, api: respx.MockRouter, in_tmp_cwd: Path
) -> None:
    route = mock_usgs(api)
    env = {"QUAKEWATCH_CACHE_DIR": str(in_tmp_cwd / "elsewhere"), "QUAKEWATCH_CACHE_TTL": "3600"}
    for _ in range(2):
        result = runner.invoke(cli, ["list", "--source", "usgs"], env=env)
        assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert len(list((in_tmp_cwd / "elsewhere").glob("*.json"))) == 1
    assert not (in_tmp_cwd / ".cache").exists()


def test_cache_dir_option(runner: CliRunner, api: respx.MockRouter, in_tmp_cwd: Path) -> None:
    mock_usgs(api)
    result = runner.invoke(cli, ["list", "--source", "usgs", "--cache-dir", "my-cache"])
    assert result.exit_code == 0, result.output
    assert len(list((in_tmp_cwd / "my-cache").glob("*.json"))) == 1


# -- export -----------------------------------------------------------------------


def test_export_csv(runner: CliRunner, api: respx.MockRouter, in_tmp_cwd: Path) -> None:
    mock_usgs(api)
    mock_emsc(api)
    result = runner.invoke(cli, ["export", "-o", "out/quakes.csv", "--min-mag", "4.5"])
    assert result.exit_code == 0, result.output
    with (in_tmp_cwd / "out" / "quakes.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert tuple(rows[0]) == FIELD_NAMES
    assert all(float(row["magnitude"]) >= 4.5 for row in rows)
    assert result.output == f"Wrote {len(rows)} event(s) to out/quakes.csv (csv)\n"


def test_export_json(runner: CliRunner, api: respx.MockRouter, in_tmp_cwd: Path) -> None:
    mock_usgs(api)
    result = runner.invoke(cli, ["export", "--source", "usgs", "--output", "quakes.json"])
    assert result.exit_code == 0, result.output
    data = json.loads((in_tmp_cwd / "quakes.json").read_text(encoding="utf-8"))
    assert len(data) == USGS_COUNT
    assert tuple(data[0]) == FIELD_NAMES
    assert all(item["source"] == "usgs" for item in data)
    assert all(item["time"].endswith("Z") for item in data)


def test_export_geojson(runner: CliRunner, api: respx.MockRouter, in_tmp_cwd: Path) -> None:
    mock_emsc(api)
    result = runner.invoke(cli, ["export", "--source", "emsc", "-o", "quakes.geojson"])
    assert result.exit_code == 0, result.output
    data = json.loads((in_tmp_cwd / "quakes.geojson").read_text(encoding="utf-8"))
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == EMSC_COUNT
    feature = data["features"][0]
    assert feature["geometry"]["type"] == "Point"
    assert len(feature["geometry"]["coordinates"]) == 2
    assert feature["properties"]["source"] == "emsc"


def test_export_format_flag_overrides_extension(
    runner: CliRunner, api: respx.MockRouter, in_tmp_cwd: Path
) -> None:
    mock_usgs(api, "usgs_all_hour.geojson")
    args = ["export", "--source", "usgs", "--window", "hour", "-o", "data.txt", "-f", "geojson"]
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert json.loads((in_tmp_cwd / "data.txt").read_text())["type"] == "FeatureCollection"


def test_export_near_filter(runner: CliRunner, api: respx.MockRouter, in_tmp_cwd: Path) -> None:
    mock_usgs(api)
    mock_emsc(api)
    args = ["export", "-o", "near.json", "--near", "-8.3,120.5", "--radius-km", "1500"]
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    data = json.loads((in_tmp_cwd / "near.json").read_text(encoding="utf-8"))
    assert data
    assert all(haversine_km(-8.3, 120.5, item["lat"], item["lon"]) <= 1500 for item in data)


def test_export_unwritable_target_exits_1(
    runner: CliRunner, api: respx.MockRouter, in_tmp_cwd: Path
) -> None:
    mock_usgs(api, "usgs_all_hour.geojson")
    (in_tmp_cwd / "blocker").write_text("a file, not a directory", encoding="utf-8")
    args = ["export", "--source", "usgs", "--window", "hour", "-o", "blocker/quakes.csv"]
    result = runner.invoke(cli, args)
    assert result.exit_code == 1
    assert "Error: cannot write blocker/quakes.csv" in result.output


# -- entry points -------------------------------------------------------------------


def test_version(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert result.output == f"quakewatch, version {__version__}\n"


def test_help_lists_commands(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["-h"])
    assert result.exit_code == 0
    for name in ("list", "export"):
        assert name in result.output


def test_main_entry_point(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["quakewatch", "--version"])
    with pytest.raises(SystemExit) as raised:
        main()
    assert raised.value.code == 0
    assert capsys.readouterr().out == f"quakewatch, version {__version__}\n"


def test_python_dash_m(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["quakewatch", "--help"])
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("quakewatch", run_name="__main__")
    assert raised.value.code == 0
    assert "Usage: quakewatch" in capsys.readouterr().out
