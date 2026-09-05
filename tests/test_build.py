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
        "activity_type": "Run",
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


def test_run_record_has_derived_values_and_empty_optionals() -> None:
    record = run_to_record(_run())
    assert record["distance_mi"] == pytest.approx(3.106856)
    assert record["average_pace_min_mi"] == pytest.approx(8.04672)
    assert record["is_5k_distance"] is True
    assert record["elevation_gain_ft"] is None


def test_validation_rejects_duplicate_ids() -> None:
    record = run_to_record(_run())
    with pytest.raises(ValueError, match="activity IDs must be unique"):
        validate_records([record, record.copy()])


def test_validation_rejects_incorrect_distance_flags() -> None:
    record = run_to_record(_run())
    record["is_10k_distance"] = True
    with pytest.raises(ValueError, match="distance flags do not match distance_m"):
        validate_records([record])
