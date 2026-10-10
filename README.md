# quakewatch

A Python CLI and library for monitoring recent earthquakes. It reads two public
catalogues and normalizes them into one event model:

| Source | Service | What quakewatch calls |
|---|---|---|
| `usgs` | [USGS GeoJSON summary feeds](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php) | `…/summary/{all,1.0,2.5,4.5}_{hour,day,week}.geojson` |
| `emsc` | [EMSC FDSN event service](https://www.seismicportal.eu/fdsn-wsevent.html) | `…/fdsnws/event/1/query?format=json&start=…` |

Every event, whichever source it came from, has the same eight fields:

| Field | Type | Notes |
|---|---|---|
| `id` | str | the source's own identifier (`nc75447541`, `20261010_0000413`) |
| `time` | datetime | timezone-aware, always UTC |
| `magnitude` | float or null | as reported; the magnitude *type* differs by source and event |
| `depth_km` | float or null | positive down |
| `lat`, `lon` | float | decimal degrees |
| `place` | str | free-text location |
| `source` | `usgs` or `emsc` | |

## Install

Python 3.10 or newer.

```console
# from the GitHub release
$ pip install https://github.com/hai-test-4902/agentbench-ce3-20261010-p06-06-a/releases/download/v0.1.0/quakewatch-0.1.0-py3-none-any.whl

# or from a checkout
$ python -m venv .venv
$ .venv/bin/pip install .
$ .venv/bin/quakewatch --version
quakewatch, version 0.1.0
```

Or run it from the Docker image (non-root, about 160 MB):

```console
$ docker build -t agentbench-quakewatch:local .
$ docker run --rm agentbench-quakewatch:local list --window day
```

## Usage

```console
$ quakewatch --help
$ quakewatch list --help
```

Options shared by every command:

| Option | Default | Meaning |
|---|---|---|
| `--window hour\|day\|week` | `day` | how far back to look |
| `--min-mag MAG` | none | minimum magnitude; events with no magnitude are dropped |
| `--source usgs\|emsc\|both` | `both` | which catalogue(s) to query |
| `--near LAT,LON` | none | only events near a point; negative values work as-is (`--near -33.45,-70.66`) |
| `--radius-km KM` | `100` with `--near` | great-circle radius around `--near` |
| `--no-cache` | off | neither read nor write the response cache |
| `--cache-ttl SECONDS` | `300` | how long a cached response is reused (`QUAKEWATCH_CACHE_TTL`) |
| `--cache-dir DIR` | `./.cache/quakewatch` | where responses are cached (`QUAKEWATCH_CACHE_DIR`) |
| `--timeout SECONDS` | `30` | per-request HTTP timeout |

Exit status is `0` on success, `1` if a source could not be fetched (nothing
partial is printed), and `2` for a usage error.

### `quakewatch list`

Lists events, newest first. On a terminal you get a Rich table; when stdout is
not a TTY (a pipe, a file, `docker run` without `-t`) you get tab-separated text
with a header row. `--plain` forces the tab-separated form, `--limit N` caps
the number of rows.

```console
$ quakewatch list --window day --min-mag 4.5 --near -8.3,120.5 --radius-km 2000
                                             Earthquakes, past day (both)

  Time (UTC)            Mag   Depth km     Lat       Lon   Place                           Source   ID
 ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  2026-10-10 05:32:01   5.0       10.0   1.625   126.389   141 km E of Bitung, Indonesia   usgs     us6000u1cv
  2026-10-10 05:32:01   5.1       10.0   1.496   126.359   MOLUCCA SEA                     emsc     20261010_0000101
  2026-10-10 02:00:42   5.1       10.0   1.416   126.360   133 km WNW of Ternate,          usgs     us6000u1bs
                                                           Indonesia
  2026-10-10 02:00:42   5.1        2.5   1.441   126.317   MOLUCCA SEA                     emsc     20261010_0000036

                                                      4 event(s)
```

With `--source both` an earthquake that both agencies located appears twice,
once per source - the listing does not merge them.

Piped, the same data is easy to post-process:

```console
$ quakewatch list --window hour --source usgs | cut -f1,2,6
time_utc	magnitude	place
2026-10-10T23:48:40Z	0.8	24 km ENE of Ashford, Washington
2026-10-10T23:42:03Z	3.1	43 km NNW of Petersville, Alaska
2026-10-10T23:20:37Z	0.8	13 km NW of The Geysers, CA
```

On terminals narrower than 100 columns the table drops the ID column (and says
so in its caption) rather than truncating values.

### `quakewatch export`

Writes the same selection to a file. The format comes from the file extension,
or from `--format csv|json|geojson`.

```console
$ quakewatch export --window week --min-mag 5 -o quakes.csv
Wrote 91 event(s) to quakes.csv (csv)

$ quakewatch export --source emsc --near 38.0,23.7 --radius-km 300 -o greece.geojson
$ quakewatch export --format json -o today.dat
```

* **CSV** - header `id,time,magnitude,depth_km,lat,lon,place,source`; missing values are empty cells.
* **JSON** - an array of objects with those same keys; missing values are `null`.
* **GeoJSON** - an RFC 7946 `FeatureCollection` of `Point` features. Coordinates
  are `[lon, lat]` only; depth is in `properties.depth_km`, because a third
  GeoJSON coordinate means *elevation in metres*, not depth in kilometres.

Times are ISO-8601 UTC with millisecond precision (`2026-10-10T05:32:01.250Z`).

### Caching

Decoded responses are stored as JSON files in `./.cache/quakewatch` (relative to
the current directory) and reused for `--cache-ttl` seconds, so repeating a
command inside five minutes makes no HTTP request at all. The cache key is the
logical request - source, window and magnitude tier - not the URL, because the
EMSC query's start time moves with the clock. The time window is re-applied
when a cached response is read. If the cache directory is not writable the
commands still work, just uncached.

### As a library

```python
from quakewatch import QuakeClient, ResponseCache

with QuakeClient(cache=ResponseCache(ttl_seconds=600)) as client:
    for event in client.events("both", "day", min_mag=5.0):
        print(event.time_iso, event.magnitude, event.place, event.source)
```

The package ships a `py.typed` marker and passes `mypy --strict`.

## Known differences between USGS and EMSC data

Things that will surprise you if you treat the two catalogues as interchangeable:

* **No shared identifier.** USGS ids are network-prefixed (`nc75447541`,
  `us6000u1cv`); EMSC ids are date-sequenced (`20261010_0000413`). The same
  earthquake can only be paired up by origin time and location.
* **Coverage is regional.** The USGS feed is dense inside the United States
  (down to below M1 in California and Alaska) but only roughly M4.5+ elsewhere.
  EMSC aggregates many national agencies, so it is much denser for Europe, the
  Mediterranean and Indonesia, and has few of the small US events. Most events
  in either catalogue are absent from the other.
* **Magnitudes are not the same quantity.** Each source publishes its preferred
  magnitude, and the type varies per event (`md`, `ml`, `mb`, `mww`, …).
  Differences of 0.1-0.3 between the two for the same earthquake are normal.
  EMSC reports one decimal; USGS regional networks often report two.
* **USGS magnitude tiers are rounded.** The "M2.5+" feed includes an M2.45
  event. `--min-mag` is applied strictly after download, so `--min-mag 2.5`
  will not show it.
* **Depth conventions.** USGS puts depth in the third GeoJSON coordinate, in km,
  positive down - and it can be *negative* for events located above sea level
  (volcanoes). EMSC gives `properties.depth` positive down, while its geometry's
  third coordinate is the same number negated. Both sources often report a
  fixed default depth (10 km is common) when depth is poorly constrained.
* **Time encoding.** USGS uses milliseconds since the epoch; EMSC uses ISO-8601
  strings with a variable number of fractional digits. Origin times for the same
  event typically differ by up to a couple of seconds.
* **Place names.** USGS: "141 km E of Bitung, Indonesia" (distance and bearing to
  a town). EMSC: "MOLUCCA SEA" (upper-case Flinn-Engdahl region).
* **Windows and paging.** USGS serves fixed, pre-built feeds that refresh about
  once a minute. EMSC answers arbitrary queries, pages with a 1-based `offset`,
  and answers HTTP 204 with an empty body when nothing matches.
* **Not everything is an earthquake.** USGS feeds include quarry blasts,
  explosions and similar events (flagged in a `type` property), and EMSC has
  its own `evtype` codes. quakewatch does not filter on either.
* **Both catalogues are revised.** Automatic solutions are reviewed and updated
  for hours or days, so magnitude, location and even existence of a recent
  event can change between two runs.

## Development

```console
$ python -m venv .venv
$ .venv/bin/pip install --require-hashes -r requirements/dev.txt
$ .venv/bin/pip install --no-deps --no-build-isolation -e .

$ .venv/bin/ruff check . && .venv/bin/ruff format --check .
$ .venv/bin/mypy --strict
$ .venv/bin/pytest --cov=quakewatch          # unit tests, HTTP mocked, coverage must be >= 85 %
$ .venv/bin/pytest -m integration            # live tests against the real APIs
```

Unit tests replay recorded responses from both services (`tests/fixtures/`,
re-record with `python scripts/record_fixtures.py`) through
[respx](https://lundberg.github.io/respx/); a fixture also blocks real socket
connections, so a unit test cannot reach the network by accident. Dependencies
are pinned with hashes in `requirements/*.txt` (regenerate with
`scripts/lock.sh`).

### Local check matrix

There is no hosted CI. `scripts/local_checks.py` is the check runner; it needs
only git, Docker and any Python 3 on the host to launch it:

```console
$ python scripts/local_checks.py                 # test HEAD
$ python scripts/local_checks.py --ref v0.1.0    # test any commit, branch or tag
$ python scripts/local_checks.py --build         # also build sdist, wheel and image, and smoke test them
```

For the chosen commit it

1. exports a clean copy with `git archive` (never the working tree; `.git`,
   untracked and ignored files such as credentials are not in it, and the
   runner refuses to continue if it finds any);
2. starts one container per interpreter listed in `local-matrix-images.json`,
   referenced **by digest** - Python 3.10, 3.11 and 3.12 - with only that clean
   copy, the evidence directory and `./.cache/pip` mounted, and no host
   environment passed in;
3. in each: installs the hash-pinned dev requirements, then runs `ruff check`,
   `ruff format --check`, `mypy --strict` and the unit suite with
   `--cov-fail-under=85`; on Python 3.12 only, the live integration tests;
4. with `--build`: builds the sdist and wheel in the 3.12 container
   (`SOURCE_DATE_EPOCH` = commit time), installs the wheel into a fresh
   virtualenv and runs it, builds `agentbench-quakewatch:local` from the same
   clean copy, and checks `docker run agentbench-quakewatch:local list --window day`
   plus that the container is not running as root.

Everything is written to `artifacts/local-checks/<full commit sha>/<run id>/`:

```text
summary.json  SUMMARY.md        overall result, tested sha, image digests, artifact hashes
py3.10/ py3.11/ py3.12/         <step>.log, <step>.exitcode, coverage.xml, coverage.json,
                                .coverage, unit-junit.xml, pip-freeze.txt, result.json
build/                          dist/*.tar.gz, dist/*.whl, SHA256SUMS, build and smoke logs
```

The exit status is 0 only if every stage passed.

### Releasing

```console
$ python scripts/local_checks.py --ref main --build
$ git tag v0.1.0 <sha that was just tested> && git push origin v0.1.0
$ GH_TOKEN=… python scripts/publish_release.py --tag v0.1.0 --repo OWNER/NAME
```

`publish_release.py` refuses to upload unless the tag's commit has a passing
`--build` run on record, the files in that run's `dist/` still match the
recorded SHA-256 values, and the remote tag points at the same commit. After
uploading it downloads both assets again without credentials and compares their
hashes with the local files (`release-verification.json`).
