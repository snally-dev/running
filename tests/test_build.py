from __future__ import annotations

from datetime import UTC, datetime

import pytest

from running.build import validate_records
from running.normalize import Run, run_to_record


def _run(**changes: object) -> Run:
    values: dict[str, object] = {
        "activity_id": 1,
        "start_datetime": datetime(2024, 1, 2, tzinfo=UTC),
        "name": "Morning Run",
        "distance_m": 5000.0,
        "moving_time_s": 1500,
        "elapsed_time_s": 1600,
        "elevation_gain_m": None,
        "max_speed_mps": None,
        "average_heart_rate_bpm": None,
        "max_heart_rate_bpm": None,
        "calories": None,
    }
    values.update(changes)
    return Run(**values)  # type: ignore[arg-type]


def test_run_record_contains_canonical_values_only() -> None:
    record = run_to_record(_run())
    assert record["distance_m"] == 5000
    assert record["sport_type"] == "Run"
    for redundant in (
        "activity_type",
        "distance_mi",
        "average_pace_min_mi",
        "is_5k_distance",
        "elevation_gain_ft",
    ):
        assert redundant not in record


def test_validation_rejects_duplicate_ids() -> None:
    record = run_to_record(_run())
    with pytest.raises(ValueError, match="activity IDs must be unique"):
        validate_records([record, record.copy()])


def test_validation_rejects_non_running_sport_type() -> None:
    record = run_to_record(_run())
    record["sport_type"] = "Ride"
    with pytest.raises(ValueError, match="non-running sport type"):
        validate_records([record])
