"""Synchronize Strava bulk exports into the public running dataset."""

from __future__ import annotations

import argparse
import os
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from running.build import OUTPUT_PATH, PROJECT_ROOT, load_public_runs, write_runs
from running.geography import GeocodingError, GeocodingStats, enrich_runs
from running.normalize import Run, is_indoor_run
from running.strava.export import (
    StravaExportError,
    discover_archives,
    load_archive,
)

RAW_ROOT = PROJECT_ROOT / "data/raw/strava"
GEOCODING_CACHE_PATH = PROJECT_ROOT / "data/private/geocoding.csv"
SOURCE_METADATA_FIELDS = (
    "relative_effort",
)


@dataclass(frozen=True)
class SyncResult:
    archives_found: int
    activities_parsed: int
    runs_retained: int
    duplicates_removed: int
    indoor_excluded: int
    added: int
    updated: int
    existing_preserved: int
    total: int


def _merge_run(existing: Run | None, exported: Run) -> Run:
    """Prefer export data while retaining useful values the export omitted."""
    if existing is None:
        return exported
    source_metadata = {
        field: (
            getattr(exported, field)
            if getattr(exported, field) is not None
            else getattr(existing, field)
        )
        for field in SOURCE_METADATA_FIELDS
    }
    return replace(
        exported,
        name=exported.name or existing.name,
        sport_type=exported.sport_type or existing.sport_type,
        timezone=exported.timezone or existing.timezone,
        elevation_gain_m=(
            exported.elevation_gain_m
            if exported.elevation_gain_m is not None
            else existing.elevation_gain_m
        ),
        average_heart_rate_bpm=(
            exported.average_heart_rate_bpm
            if exported.average_heart_rate_bpm is not None
            else existing.average_heart_rate_bpm
        ),
        max_heart_rate_bpm=(
            exported.max_heart_rate_bpm
            if exported.max_heart_rate_bpm is not None
            else existing.max_heart_rate_bpm
        ),
        calories=(
            exported.calories if exported.calories is not None else existing.calories
        ),
        **source_metadata,
        start_lat=(
            exported.start_lat if exported.start_lat is not None else existing.start_lat
        ),
        start_lon=(
            exported.start_lon if exported.start_lon is not None else existing.start_lon
        ),
        end_lat=exported.end_lat if exported.end_lat is not None else existing.end_lat,
        end_lon=exported.end_lon if exported.end_lon is not None else existing.end_lon,
        start_city=existing.start_city,
        start_locality=existing.start_locality,
        start_state=existing.start_state,
        start_state_code=existing.start_state_code,
        start_country=existing.start_country,
        start_country_code=existing.start_country_code,
    )


def synchronize(
    archive_paths: Iterable[Path], existing: Iterable[Run]
) -> tuple[list[Run], SyncResult]:
    """Parse all exports and deterministically upsert runs by Activity ID."""
    archives = [load_archive(path) for path in archive_paths]
    archives.sort(key=lambda archive: archive.preference_key)

    exported_by_id: dict[int, Run] = {}
    indoor_run_ids: set[int] = set()
    activities_parsed = runs_retained = 0
    for archive in archives:
        activities_parsed += archive.parsed.activities_parsed
        runs_retained += archive.parsed.runs_parsed
        for activity_id in archive.parsed.indoor_run_ids:
            exported_by_id.pop(activity_id, None)
            indoor_run_ids.add(activity_id)
        for run in archive.parsed.runs:
            indoor_run_ids.discard(run.activity_id)
            exported_by_id[run.activity_id] = _merge_run(
                exported_by_id.get(run.activity_id), run
            )

    existing_runs = list(existing)
    existing_by_id = {run.activity_id: run for run in existing_runs}
    if len(existing_by_id) != len(existing_runs):
        raise ValueError("existing activity IDs must be unique")
    existing_indoor_ids = {
        run.activity_id
        for run in existing_runs
        if is_indoor_run(run.name, run.sport_type)
    }
    excluded_ids = indoor_run_ids | (existing_indoor_ids - exported_by_id.keys())
    combined = {
        activity_id: run
        for activity_id, run in existing_by_id.items()
        if activity_id not in excluded_ids
    }
    added = updated = 0
    for activity_id, exported in exported_by_id.items():
        previous = existing_by_id.get(activity_id)
        merged = _merge_run(previous, exported)
        combined[activity_id] = merged
        if previous is None:
            added += 1
        elif previous != merged:
            updated += 1

    ordered = sorted(
        combined.values(), key=lambda run: (run.start_datetime, run.activity_id)
    )
    return ordered, SyncResult(
        archives_found=len(archives),
        activities_parsed=activities_parsed,
        runs_retained=runs_retained,
        duplicates_removed=runs_retained - len(exported_by_id),
        indoor_excluded=len(excluded_ids),
        added=added,
        updated=updated,
        existing_preserved=len(
            existing_by_id.keys() - exported_by_id.keys() - excluded_ids
        ),
        total=len(ordered),
    )


def process_exports(
    *,
    raw_root: Path = RAW_ROOT,
    output_path: Path = OUTPUT_PATH,
    cache_path: Path = GEOCODING_CACHE_PATH,
    api_key: str | None = None,
    refresh_location_metadata: bool = False,
) -> tuple[Path, SyncResult, GeocodingStats]:
    """Run export discovery, merge, location enrichment, and CSV writing."""
    archives = discover_archives(raw_root)
    existing = load_public_runs(output_path)
    runs, result = synchronize(archives, existing)
    runs, geocoding = enrich_runs(
        runs,
        cache_path=cache_path,
        api_key=api_key,
        refresh_cached_metadata=refresh_location_metadata,
    )
    output_path, _ = write_runs(runs, output_path=output_path)
    return output_path, result, geocoding


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Update runs.csv from Strava bulk-export ZIPs."
    )
    parser.add_argument(
        "--refresh-location-metadata",
        action="store_true",
        help="re-query cached coordinates for all BigDataCloud metadata",
    )
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    try:
        output_path, result, geocoding = process_exports(
            api_key=os.environ.get("BIGDATACLOUD_API_KEY"),
            refresh_location_metadata=args.refresh_location_metadata,
        )
    except (GeocodingError, StravaExportError, ValueError) as error:
        parser.exit(2, f"running.sync: {error}\n")

    print(f"Archives found: {result.archives_found:,}")
    print(
        f"Activities parsed: {result.activities_parsed:,}; "
        f"runs retained: {result.runs_retained:,}"
    )
    print(f"Duplicate activities removed: {result.duplicates_removed:,}")
    print(f"Indoor runs excluded: {result.indoor_excluded:,}")
    print(
        f"Dataset changes: {result.added:,} added, {result.updated:,} updated, "
        f"{result.existing_preserved:,} existing-only preserved"
    )
    print(f"New locations geocoded: {geocoding.api_requests:,}")
    print(
        f"Total rows written: {result.total:,} "
        f"({output_path.relative_to(PROJECT_ROOT)})"
    )


if __name__ == "__main__":
    main()
