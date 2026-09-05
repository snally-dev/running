"""Incrementally synchronize recent Strava activities into the public dataset."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from running.build import OUTPUT_PATH, PROJECT_ROOT, load_public_runs, write_runs
from running.normalize import (
    Run,
    is_running_activity,
    merge_api_run,
    normalize_api_activity,
)
from running.strava.api import StravaAPIError, StravaClient
from running.strava.auth import (
    AccessTokenProvider,
    OAuthConfig,
    StravaAuthError,
    TokenStore,
)

DEFAULT_LOOKBACK_DAYS = 30
PRIVATE_ROOT = PROJECT_ROOT / "data/private"
STREAM_ROOT = PRIVATE_ROOT / "streams"
DEFAULT_TOKEN_PATH = PRIVATE_ROOT / "strava-token.json"


@dataclass(frozen=True)
class SyncResult:
    fetched: int
    runs: int
    added: int
    updated: int
    streams_added: int
    total: int


def _has_geographic_data(activity: dict[str, Any]) -> bool:
    start = activity.get("start_latlng")
    activity_map = activity.get("map")
    return bool(start) or bool(
        isinstance(activity_map, dict) and activity_map.get("summary_polyline")
    )


def _write_stream(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    temporary = path.with_suffix(".json.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def synchronize(
    client: StravaClient,
    existing: list[Run],
    *,
    after: int,
    before: int,
    stream_root: Path = STREAM_ROOT,
    fetch_streams: bool = True,
) -> tuple[list[Run], SyncResult]:
    """Fetch a bounded window and deterministically upsert by Strava activity ID."""
    by_id = {run.activity_id: run for run in existing}
    fetched = run_count = added = updated = streams_added = 0

    for activity in client.iter_activities(after=after, before=before):
        fetched += 1
        if not is_running_activity(activity.get("type"), activity.get("sport_type")):
            continue
        run_count += 1
        current = normalize_api_activity(activity)
        previous = by_id.get(current.activity_id)
        merged = merge_api_run(previous, current)
        if previous is None:
            added += 1
        elif previous != merged:
            updated += 1
        by_id[current.activity_id] = merged

        stream_path = stream_root / f"{current.activity_id}.json"
        if (
            fetch_streams
            and _has_geographic_data(activity)
            and not stream_path.exists()
        ):
            _write_stream(stream_path, client.get_activity_streams(current.activity_id))
            streams_added += 1

    runs = sorted(by_id.values(), key=lambda run: (run.start_datetime, run.activity_id))
    return runs, SyncResult(
        fetched=fetched,
        runs=run_count,
        added=added,
        updated=updated,
        streams_added=streams_added,
        total=len(runs),
    )


def _default_lookback(environ: Mapping[str, str]) -> int:
    raw = environ.get("STRAVA_LOOKBACK_DAYS", str(DEFAULT_LOOKBACK_DAYS))
    try:
        value = int(raw)
    except ValueError as error:
        raise StravaAuthError(
            "STRAVA_LOOKBACK_DAYS must be a positive integer"
        ) from error
    if value <= 0:
        raise StravaAuthError("STRAVA_LOOKBACK_DAYS must be a positive integer")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Incrementally sync recent runs from the Strava API."
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help="days to refresh (default: STRAVA_LOOKBACK_DAYS or 30)",
    )
    parser.add_argument(
        "--no-streams",
        action="store_true",
        help="skip private geographic stream downloads",
    )
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    try:
        lookback_days = (
            args.lookback_days
            if args.lookback_days is not None
            else _default_lookback(os.environ)
        )
        if lookback_days <= 0:
            parser.error("--lookback-days must be positive")
        token_path = Path(os.environ.get("STRAVA_TOKEN_FILE", DEFAULT_TOKEN_PATH))
        provider = AccessTokenProvider(
            OAuthConfig.from_environment(), TokenStore(token_path)
        )
        client = StravaClient(
            provider.access_token,
            refresh_access_token=provider.refresh_access_token,
        )
        client.get_authenticated_athlete()
        provider.require_scopes()
        now = datetime.now(UTC)
        after = int((now - timedelta(days=lookback_days)).timestamp())
        before = int(now.timestamp())
        runs, result = synchronize(
            client,
            load_public_runs(),
            after=after,
            before=before,
            fetch_streams=not args.no_streams,
        )
        output_path, _ = write_runs(runs, output_path=OUTPUT_PATH)
    except (StravaAuthError, StravaAPIError, ValueError) as error:
        parser.exit(2, f"running.sync: {error}\n")

    print(
        f"Fetched {result.fetched} activities ({result.runs} runs): "
        f"{result.added} added, {result.updated} updated"
    )
    scope_summary = ", ".join(provider.scopes) if provider.scopes else "not returned"
    print(f"Authentication succeeded; OAuth scopes: {scope_summary}")
    print(f"Access token refreshed: {'yes' if provider.refreshed else 'no'}")
    print(f"Stored {result.streams_added} new private stream files")
    print(f"Wrote {result.total:,} runs to {output_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
