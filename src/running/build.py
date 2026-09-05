"""Build the canonical public running-activity CSV."""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Iterable
from pathlib import Path

from running.normalize import (
    Run,
    distance_flags,
    meters_to_miles,
    record_to_run,
    run_to_record,
    validate_runs,
)
from running.strava.export import discover_export, load_runs

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = PROJECT_ROOT / "data/raw/strava"
OUTPUT_PATH = PROJECT_ROOT / "data/public/runs.csv"

CSV_COLUMNS = (
    "activity_id",
    "date",
    "start_datetime",
    "name",
    "activity_type",
    "distance_m",
    "distance_mi",
    "moving_time_s",
    "elapsed_time_s",
    "elevation_gain_m",
    "elevation_gain_ft",
    "average_speed_mps",
    "average_pace_min_mi",
    "max_speed_mps",
    "average_heart_rate_bpm",
    "max_heart_rate_bpm",
    "calories",
    "is_5k_distance",
    "is_10k_distance",
    "is_half_marathon_distance",
    "is_marathon_distance",
    "is_ultra_distance",
)


def validate_records(records: Iterable[dict[str, object]]) -> None:
    records = list(records)
    ids = [record["activity_id"] for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("activity IDs must be unique")

    for record in records:
        label = f"activity {record['activity_id']}"
        if record["activity_type"] != "Run":
            raise ValueError(f"{label}: non-running activity in output")
        for field in ("distance_m", "moving_time_s", "elapsed_time_s"):
            value = record[field]
            if value is not None and float(value) < 0:
                raise ValueError(f"{label}: {field} must be non-negative")
        expected_miles = meters_to_miles(float(record["distance_m"]))
        if not math.isclose(float(record["distance_mi"]), expected_miles, abs_tol=1e-6):
            raise ValueError(f"{label}: distance_mi does not match distance_m")

        flags = {key: bool(record[key]) for key in distance_flags(0)}
        if flags != distance_flags(float(record["distance_m"])):
            raise ValueError(f"{label}: distance flags do not match distance_m")
        if flags["is_half_marathon_distance"] and not (
            flags["is_10k_distance"] and flags["is_5k_distance"]
        ):
            raise ValueError(f"{label}: half-marathon distance flags are inconsistent")
        if flags["is_marathon_distance"] and not (
            flags["is_half_marathon_distance"]
            and flags["is_10k_distance"]
            and flags["is_5k_distance"]
        ):
            raise ValueError(f"{label}: marathon distance flags are inconsistent")
        if flags["is_ultra_distance"] and not flags["is_marathon_distance"]:
            raise ValueError(f"{label}: ultra distance flag is inconsistent")

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
        missing = set(CSV_COLUMNS) - set(reader.fieldnames or ())
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


def build(
    export_directory: Path | None = None, *, output_path: Path = OUTPUT_PATH
) -> tuple[Path, list[dict[str, object]]]:
    export_directory = export_directory or discover_export(RAW_ROOT)
    runs = load_runs(export_directory, repository_root=PROJECT_ROOT)
    return write_runs(runs, output_path=output_path)


def main() -> None:
    output_path, records = build()
    total_miles = sum(float(record["distance_mi"]) for record in records)
    print(f"Wrote {len(records):,} runs to {output_path.relative_to(PROJECT_ROOT)}")
    print(f"Total running miles: {total_miles:,.2f}")


if __name__ == "__main__":
    main()
