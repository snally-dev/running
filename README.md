# running

A small personal pipeline for one canonical Strava running history. Historical
account-export records and current API records are normalized to the same `Run`
model and rendered deterministically to `data/public/runs.csv`.

## Historical bootstrap

Place one account export under `data/raw/strava/<export-name>/`. The raw export
is ignored by Git. Build the complete historical dataset without API credentials:

```console
uv sync
uv run python -m running.build
```

The export's `activities.csv` supplies run metadata. Its FIT/GPX/TCX files remain
private raw geographic source data. Precise start/end coordinates are deliberately
not published in `runs.csv`.

## Incremental synchronization

Once Strava credentials are configured, refresh recent activities with:

```console
uv run python -m running.sync
uv run python -m running.sync --lookback-days 45
uv run python -m running.sync --no-streams
```

The default lookback is 30 days (overridable with `--lookback-days` or
`STRAVA_LOOKBACK_DAYS`). The client requests up to 200 activities per page and
continues until Strava returns an empty page. Supported runs are upserted by
activity ID, with current API metadata taking precedence. Historical records
outside the window remain unchanged. Incremental polling does not discover
deleted activities; a future explicit full reconciliation can handle that if
needed.

Required runtime secrets:

- `STRAVA_CLIENT_ID`
- `STRAVA_CLIENT_SECRET`
- `STRAVA_REFRESH_TOKEN` for the first local run

Optional values are `STRAVA_ACCESS_TOKEN`, `STRAVA_ACCESS_TOKEN_EXPIRES_AT`,
`STRAVA_TOKEN_FILE`, and `STRAVA_LOOKBACK_DAYS`. Never commit any of them. The
default ignored local token state is `data/private/strava-token.json`. Access
tokens are reused only while safely valid; otherwise the current refresh token is
exchanged and every returned token pair is saved. Strava can rotate refresh
tokens, so the newest returned refresh token is authoritative.

Authorize only the `activity:read_all` OAuth scope. It includes activity read
access, Only Me activities, privacy-zone data, details, and streams; this pipeline
does not need `activity:write`, profile, route, or segment scopes. See Strava's
[authentication documentation](https://developers.strava.com/docs/authentication/),
[API reference](https://developers.strava.com/docs/reference/), and
[rate-limit documentation](https://developers.strava.com/docs/rate-limits/).

For activities with geographic data, sync requests only `latlng`, `distance`,
`time`, and `altitude` streams. Files are stable-ID keyed under the ignored
`data/private/streams/<activity-id>.json`; coordinates never enter the public CSV.
SummaryActivity already provides the public canonical fields used for ongoing
sync, so DetailedActivity is supported by the client but is not fetched for every
run. Historical export calories are retained during API upserts; new API-only
rows leave calories blank rather than spend one detail request per activity.
Transient server failures receive two short retries. Rate-limit responses are not
blindly retried; sync stops with Strava's read-usage and limit headers.

## Weekly automation scaffold

`.github/workflows/sync.yml` has `workflow_dispatch`, a commented Sunday schedule,
and a disabled job gate (`STRAVA_SYNC_ENABLED`). Do not enable it yet. A GitHub
Actions secret containing the initial refresh token is not durable: after Strava
rotates it, the ignored runner-local token file disappears with the runner. Before
setting the gate or enabling the schedule, integrate a least-privilege durable
secret store that atomically saves the newest returned refresh token between
runs. Artifacts, caches, generated files, and commits are not acceptable token
storage.

When safely enabled, the scaffold tests and validates the result, stages only
`data/public/runs.csv`, and skips an empty commit.

Run local checks with:

```console
uv run pytest
uv run ruff check .
```
