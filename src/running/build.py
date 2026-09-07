"""Build the canonical public running-activity CSV."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from pathlib import Path

from running.normalize import (
    Run,
    record_to_run,
    run_to_record,
    validate_runs,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = PROJECT_ROOT / "data/public/runs.csv"

CSV_COLUMNS = (
    "strava_activity_id",
    "activity_date_local",
    "start_datetime_utc",
    "timezone_iana",
    "activity_name",
    "strava_relative_effort",
    "distance_miles",
    "moving_time_seconds",
    "elapsed_time_seconds",
    "elevation_gain_meters",
    "average_heart_rate_bpm",
    "max_heart_rate_bpm",
    "calories_kcal",
    "start_city",
    "start_locality",
    "start_region",
    "start_region_code",
    "start_country",
    "start_country_code",
)


def validate_records(records: Iterable[dict[str, object]]) -> None:
    records = list(records)
    ids = [record["strava_activity_id"] for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("activity IDs must be unique")

    for record in records:
        label = f"activity {record['strava_activity_id']}"
        for field in (
            "distance_miles",
            "moving_time_seconds",
            "elapsed_time_seconds",
            "elevation_gain_meters",
            "strava_relative_effort",
        ):
            value = record[field]
            if value is not None and float(value) < 0:
                raise ValueError(f"{label}: {field} must be non-negative")

    order = [
        (record["start_datetime_utc"], record["strava_activity_id"])
        for record in records
    ]
    if order != sorted(order):
        raise ValueError(
            "records must be ordered by start_datetime_utc and strava_activity_id"
        )


def _csv_text(records: list[dict[str, object]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for record in records:
        writer.writerow(
            {
                key: (
                    str(value).lower()
                    if isinstance(value, bool)
                    else ""
                    if value is None
                    else value
                )
                for key, value in record.items()
            }
        )
    return stream.getvalue()


def load_public_runs(path: Path = OUTPUT_PATH) -> list[Run]:
    """Load the canonical values from an existing public CSV."""
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        # Public names have evolved; accept the previous schema while migration
        # rewrites it to the complete current schema.
        migration_columns = {
            "activity_date_local",
            "activity_name",
            "calories_kcal",
            "distance_miles",
            "elapsed_time_seconds",
            "elevation_gain_meters",
            "moving_time_seconds",
            "start_locality",
            "start_region",
            "start_region_code",
            "start_datetime_utc",
            "strava_activity_id",
            "strava_relative_effort",
            "timezone_iana",
        }
        missing = set(CSV_COLUMNS) - migration_columns - set(reader.fieldnames or ())
        if "distance_miles" not in (reader.fieldnames or ()) and "distance_m" not in (
            reader.fieldnames or ()
        ):
            missing.add("distance_miles")
        if "start_datetime_utc" not in (
            reader.fieldnames or ()
        ) and "start_datetime" not in (reader.fieldnames or ()):
            missing.add("start_datetime_utc")
        renamed_required_columns = {
            "strava_activity_id": "activity_id",
            "activity_name": "name",
            "moving_time_seconds": "moving_time_s",
            "elapsed_time_seconds": "elapsed_time_s",
            "elevation_gain_meters": "elevation_gain_m",
            "calories_kcal": "calories",
        }
        for current, legacy in renamed_required_columns.items():
            if current not in (reader.fieldnames or ()) and legacy not in (
                reader.fieldnames or ()
            ):
                missing.add(current)
        if missing:
            raise ValueError(
                "public CSV is missing columns: " + ", ".join(sorted(missing))
            )
        runs = [record_to_run(record) for record in reader]
    return validate_runs(runs)


def write_runs(
    runs: Iterable[Run], *, output_path: Path = OUTPUT_PATH
) -> tuple[Path, list[dict[str, object]]]:
    """Validate and deterministically render canonical runs."""
    ordered = sorted(runs, key=lambda run: (run.start_datetime, run.activity_id))
    validate_runs(ordered)
    records = [run_to_record(run) for run in ordered]
    validate_records(records)
    text = _csv_text(records)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not output_path.exists() or output_path.read_text(encoding="utf-8") != text:
        output_path.write_text(text, encoding="utf-8")
    return output_path, records
