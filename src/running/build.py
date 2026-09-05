"""Build the canonical public running-activity CSV."""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Iterable
from pathlib import Path

from running.strava import (
    FEET_PER_METER,
    Run,
    discover_export,
    distance_flags,
    load_runs,
    meters_to_miles,
    pace_minutes_per_mile,
)

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
    "start_lat",
    "start_lon",
    "end_lat",
    "end_lon",
    "is_5k_distance",
    "is_10k_distance",
    "is_half_marathon_distance",
    "is_marathon_distance",
    "is_ultra_distance",
    "source_activity_file",
)


def _rounded(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def run_to_record(run: Run) -> dict[str, object]:
    distance_mi = meters_to_miles(run.distance_m)
    average_speed = (
        run.distance_m / run.moving_time_s if run.moving_time_s > 0 else None
    )
    elevation_gain_ft = (
        run.elevation_gain_m * FEET_PER_METER
        if run.elevation_gain_m is not None
        else None
    )
    timestamp = run.start_datetime.isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "activity_id": run.activity_id,
        "date": run.start_datetime.date().isoformat(),
        "start_datetime": timestamp,
        "name": run.name,
        "activity_type": run.activity_type,
        "distance_m": _rounded(run.distance_m, 1),
        "distance_mi": _rounded(distance_mi, 6),
        "moving_time_s": run.moving_time_s,
        "elapsed_time_s": run.elapsed_time_s,
        "elevation_gain_m": _rounded(run.elevation_gain_m, 1),
        "elevation_gain_ft": _rounded(elevation_gain_ft, 3),
        "average_speed_mps": _rounded(average_speed, 6),
        "average_pace_min_mi": _rounded(
            pace_minutes_per_mile(run.moving_time_s, run.distance_m), 6
        ),
        "max_speed_mps": _rounded(run.max_speed_mps, 6),
        "average_heart_rate_bpm": _rounded(run.average_heart_rate_bpm, 1),
        "max_heart_rate_bpm": _rounded(run.max_heart_rate_bpm, 1),
        "calories": _rounded(run.calories, 1),
        "start_lat": _rounded(run.start_lat, 7),
        "start_lon": _rounded(run.start_lon, 7),
        "end_lat": _rounded(run.end_lat, 7),
        "end_lon": _rounded(run.end_lon, 7),
        **distance_flags(run.distance_m),
        "source_activity_file": run.source_activity_file,
    }


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
        for field in ("start_lat", "end_lat"):
            value = record[field]
            if value is not None and not -90 <= float(value) <= 90:
                raise ValueError(f"{label}: {field} is outside [-90, 90]")
        for field in ("start_lon", "end_lon"):
            value = record[field]
            if value is not None and not -180 <= float(value) <= 180:
                raise ValueError(f"{label}: {field} is outside [-180, 180]")

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


def build(
    export_directory: Path | None = None, *, output_path: Path = OUTPUT_PATH
) -> tuple[Path, list[dict[str, object]]]:
    export_directory = export_directory or discover_export(RAW_ROOT)
    runs = load_runs(export_directory, repository_root=PROJECT_ROOT)
    records = [run_to_record(run) for run in runs]
    validate_records(records)
    text = _csv_text(records)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not output_path.exists() or output_path.read_text(encoding="utf-8") != text:
        output_path.write_text(text, encoding="utf-8")
    return output_path, records


def main() -> None:
    output_path, records = build()
    total_miles = sum(float(record["distance_mi"]) for record in records)
    print(f"Wrote {len(records):,} runs to {output_path.relative_to(PROJECT_ROOT)}")
    print(f"Total running miles: {total_miles:,.2f}")


if __name__ == "__main__":
    main()
