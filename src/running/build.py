"""Build the canonical public running-activity CSV."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from pathlib import Path

from running.normalize import (
    RUNNING_SPORT_TYPES,
    Run,
    record_to_run,
    run_to_record,
    validate_runs,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = PROJECT_ROOT / "data/public/runs.csv"

CSV_COLUMNS = (
    "activity_id",
    "local_date",
    "start_datetime",
    "local_start_datetime",
    "timezone",
    "name",
    "sport_type",
    "distance_m",
    "moving_time_s",
    "elapsed_time_s",
    "elevation_gain_m",
    "max_speed_mps",
    "average_heart_rate_bpm",
    "max_heart_rate_bpm",
    "calories",
    "start_city",
    "start_locality",
    "start_state",
    "start_state_code",
    "start_country",
    "start_country_code",
)


def validate_records(records: Iterable[dict[str, object]]) -> None:
    records = list(records)
    ids = [record["activity_id"] for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("activity IDs must be unique")

    for record in records:
        label = f"activity {record['activity_id']}"
        if record["sport_type"] not in RUNNING_SPORT_TYPES:
            raise ValueError(f"{label}: non-running sport type in output")
        for field in ("distance_m", "moving_time_s", "elapsed_time_s"):
            value = record[field]
            if value is not None and float(value) < 0:
                raise ValueError(f"{label}: {field} must be non-negative")

    order = [(record["start_datetime"], record["activity_id"]) for record in records]
    if order != sorted(order):
        raise ValueError("records must be ordered by start_datetime and activity_id")


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
        # The newer locality/time columns are optional while migrating an older
        # public CSV; write_runs always emits the complete current schema.
        migration_columns = {
            "local_date",
            "local_start_datetime",
            "timezone",
            "sport_type",
            "start_locality",
            "start_state_code",
        }
        missing = set(CSV_COLUMNS) - migration_columns - set(reader.fieldnames or ())
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
