# running

A small local data pipeline for Caleb's personal Strava running history. It reads
a Strava account export and creates one analysis-ready row per running activity in
`data/public/runs.csv`.

Place one export under `data/raw/strava/<export-name>/`. The build needs the
export's `activities.csv` and its referenced files under `activities/`; raw data
is private and ignored by Git.

```console
uv sync
uv run python -m running.build
```

`runs.csv` contains normalized dates, distances, times, pace, speed, elevation,
selected heart-rate and calorie fields, distance-threshold flags, source activity
paths, and start/end coordinates when a GPS track is available. It contains only
activities whose Strava activity type is exactly `Run`.

Run the tests with:

```console
uv run pytest
```
