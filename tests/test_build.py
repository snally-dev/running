from __future__ import annotations

from datetime import UTC, datetime

import pytest

from running.build import validate_records
from running.normalize import Run, record_to_run, run_to_record


def _run(**changes: object) -> Run:
    values: dict[str, object] = {
        "activity_id": 1,
        "start_datetime": datetime(2024, 1, 2, tzinfo=UTC),
        "name": "Morning Run",
        "distance_m": 5000.0,
        "moving_time_s": 1500,
        "elapsed_time_s": 1600,
        "elevation_gain_m": None,
        "average_heart_rate_bpm": None,
        "max_heart_rate_bpm": None,
        "calories": None,
    }
    values.update(changes)
    return Run(**values)  # type: ignore[arg-type]


def test_run_record_contains_canonical_values_only() -> None:
    record = run_to_record(_run())
    assert record["distance_miles"] == 3.11
    assert record["strava_activity_id"] == 1
    assert record["activity_name"] == "Morning Run"
    assert record["moving_time_seconds"] == 1500
    assert "sport_type" not in record
    for redundant in (
        "activity_type",
        "distance_m",
        "distance_mi",
        "average_pace_min_mi",
        "is_5k_distance",
        "elevation_gain_ft",
    ):
        assert redundant not in record


def test_public_csv_distance_miles_round_trips() -> None:
    record = run_to_record(
        _run(
            distance_m=1609.344,
        )
    )
    assert record["distance_miles"] == 1
    reloaded = record_to_run(record)
    assert reloaded.distance_m == pytest.approx(1609.344)


def test_previous_public_column_names_remain_loadable() -> None:
    current = run_to_record(_run())
    legacy_names = {
        "strava_activity_id": "activity_id",
        "activity_date_local": "local_date",
        "start_datetime_utc": "start_datetime",
        "timezone_iana": "timezone",
        "activity_name": "name",
        "strava_relative_effort": "relative_effort",
        "moving_time_seconds": "moving_time_s",
        "elapsed_time_seconds": "elapsed_time_s",
        "elevation_gain_meters": "elevation_gain_m",
        "calories_kcal": "calories",
        "start_region": "start_state",
        "start_region_code": "start_state_code",
    }
    legacy = {legacy_names.get(field, field): value for field, value in current.items()}

    reloaded = record_to_run(legacy)

    assert reloaded.activity_id == 1
    assert reloaded.name == "Morning Run"
    assert reloaded.moving_time_s == 1500


def test_validation_rejects_duplicate_ids() -> None:
    record = run_to_record(_run())
    with pytest.raises(ValueError, match="activity IDs must be unique"):
        validate_records([record, record.copy()])


def test_validation_rejects_negative_distance() -> None:
    record = run_to_record(_run())
    record["distance_miles"] = -1
    with pytest.raises(ValueError, match="distance_miles must be non-negative"):
        validate_records([record])
