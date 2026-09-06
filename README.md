# running

A small personal pipeline that turns Strava bulk exports into one canonical,
analysis-ready running history at `data/public/runs.csv`.

## Update the dataset

Periodically request a bulk export from Strava and place the downloaded ZIP in:

```text
data/raw/strava/
```

Keep older ZIPs in place. The pipeline discovers every archive recursively,
filters for running activities, and deduplicates overlapping cumulative exports
by Strava Activity ID. Indoor activities are excluded when Strava labels them as
`VirtualRun` or their name explicitly contains `treadmill`, `indoor`, or `Zwift`.
If two exports contain the same activity, the export with
the newer embedded `activities.csv` timestamp takes precedence. Missing optional
metadata and rows absent from all available exports are retained from the current
public dataset.

Run:

```console
uv sync
uv run python -m running.sync
```

To deliberately refresh all cached locations after adding new provider-backed
columns, run `uv run python -m running.sync --refresh-location-metadata`. This
uses one BigDataCloud request per unique cached coordinate.

The sync command validates each ZIP, parses `activities.csv` and referenced
FIT/GPX/TCX tracks, enriches uncached start locations, and updates
`data/public/runs.csv`. Reprocessing an archive is safe and deterministic. An
empty `data/raw/strava/` preserves the existing eligible public rows.

Precise start/end coordinates stay in ignored raw data and are not written to the
public CSV. New BigDataCloud location results and complete provider responses are
cached in the ignored
`data/private/geocoding.csv`; existing public locations and cached coordinates
are reused. Set `BIGDATACLOUD_API_KEY` only when an exported run has coordinates
whose location is not already known.

The public CSV keeps canonical metric measurements and exact integer seconds.
Consumers derive miles, feet, speed, pace, duration displays, and distance
classifications as needed. UTC timestamps are retained alongside IANA timezones
and derived local timestamps when a start coordinate is available.

The Strava ZIP files and private geocoding cache are intentionally ignored by
Git. Commit only the regenerated public CSV and application changes. GitHub
Actions validates committed data and code; it does not contact Strava or download
exports.

Run local checks with:

```console
uv run pytest
uv run ruff check .
```
