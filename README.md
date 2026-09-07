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

The public CSV stores distance in miles, while other source measurements remain
metric, and keeps exact integer seconds. Consumers derive feet, speed, pace,
duration displays, and distance classifications as needed. `start_datetime_utc`
is the canonical instant; IANA timezones are retained alongside derived local
dates when a start coordinate is available.

Useful structured activity metadata from `activities.csv` is retained when
available. Columns that would be more than 50% empty or exactly zero for at least
50% of runs are omitted, as are free-form descriptions and private notes,
athlete weight, source filenames, opaque provider codes, noisy peak measurements,
and measurements that can be reliably derived from existing columns.

The public schema is deliberately small and explicit:

| Column | Purpose |
| --- | --- |
| `strava_activity_id` | Stable source identifier and join key |
| `activity_date_local` | Local calendar date for daily grouping |
| `start_datetime_utc` | Unambiguous activity start instant |
| `timezone_iana` | IANA zone used to interpret the local date |
| `activity_name` | Human context supplied in Strava |
| `strava_relative_effort` | Strava's source-specific training-load score |
| `distance_miles` | Run distance |
| `moving_time_seconds` | Time moving |
| `elapsed_time_seconds` | Total time, including stops |
| `elevation_gain_meters` | Standard measure of route hilliness |
| `average_heart_rate_bpm` | Sustained cardiovascular intensity |
| `max_heart_rate_bpm` | Peak cardiovascular intensity |
| `calories_kcal` | Strava's estimated energy expenditure |
| `start_city` | City-level location grouping |
| `start_locality` | More specific locality or neighborhood grouping |
| `start_region` | Human-readable state, province, or region |
| `start_region_code` | Stable subdivision code |
| `start_country` | Human-readable country |
| `start_country_code` | Stable ISO country code |

The manually curated race-results table at `data/public/races.csv` uses:

| Column | Purpose |
| --- | --- |
| `race_date` | Actual local race date |
| `strava_id` | Optional link to `runs.csv`'s `strava_activity_id` |
| `race_name` | Published event name |
| `distance_category` | Standard race distance or challenge category |
| `race_city` | Event city |
| `race_region_code` | Event state or region code |
| `overall_place` | Overall finishing place |
| `overall_field_size` | Overall finisher field size |
| `gender_place` | Gender-group finishing place |
| `gender_field_size` | Gender-group field size |
| `division_place` | Age-division finishing place |
| `division_field_size` | Age-division field size |
| `pace_seconds_per_mile` | Published average pace as numeric seconds |
| `finish_duration_seconds` | Published finish duration as numeric seconds |
| `bib_number` | Optional published bib identifier |

The Strava ZIP files and private geocoding cache are intentionally ignored by
Git. Commit only the regenerated public CSV and application changes. GitHub
Actions validates committed data and code; it does not contact Strava or download
exports.

Run local checks with:

```console
uv run pytest
uv run ruff check .
```
