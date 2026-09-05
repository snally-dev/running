from __future__ import annotations

from datetime import UTC, datetime

import pytest

from running.normalize import (
    NormalizationError,
    is_running_activity,
    normalize_api_activity,
    normalize_run,
)


@pytest.mark.parametrize("sport_type", ["Run", "TrailRun", "VirtualRun"])
def test_supported_running_sport_types(sport_type: str) -> None:
    assert is_running_activity("Run", sport_type)


@pytest.mark.parametrize(
    "sport_type", ["Hike", "Walk", "Ride", "Workout", "MountainBikeRide"]
)
def test_unrelated_sport_types_are_not_runs(sport_type: str) -> None:
    assert not is_running_activity("Run", sport_type)


def test_legacy_type_is_used_only_without_sport_type() -> None:
    assert is_running_activity("Run")
    assert not is_running_activity("Hike")


def test_export_and_api_values_share_the_canonical_model() -> None:
    export_run = normalize_run(
        activity_id="123",
        start_datetime=datetime(2024, 1, 2, tzinfo=UTC),
        name="Morning Run",
        distance_m="5000",
        moving_time_s="1500",
        elapsed_time_s="1600",
        elevation_gain_m="12.3",
        max_speed_mps="4.2",
        source="export row",
    )
    api_run = normalize_api_activity(
        {
            "id": 123,
            "start_date": "2024-01-02T00:00:00Z",
            "name": "Morning Run",
            "type": "Run",
            "sport_type": "Run",
            "distance": 5000,
            "moving_time": 1500,
            "elapsed_time": 1600,
            "total_elevation_gain": 12.3,
            "max_speed": 4.2,
        }
    )
    assert export_run == api_run


def test_api_private_run_is_not_excluded() -> None:
    run = normalize_api_activity(_api_activity(private=True))
    assert run.activity_id == 123


def test_api_missing_optional_fields_are_none() -> None:
    run = normalize_api_activity(_api_activity())
    assert run.elevation_gain_m is None
    assert run.max_speed_mps is None
    assert run.average_heart_rate_bpm is None
    assert run.calories is None


def test_non_run_cannot_be_normalized() -> None:
    with pytest.raises(NormalizationError, match="not a supported run"):
        normalize_api_activity(_api_activity(sport_type="Hike"))


def _api_activity(**changes: object) -> dict[str, object]:
    activity: dict[str, object] = {
        "id": 123,
        "start_date": "2024-01-02T00:00:00Z",
        "name": "Morning Run",
        "type": "Run",
        "sport_type": "Run",
        "distance": 5000,
        "moving_time": 1500,
        "elapsed_time": 1600,
    }
    activity.update(changes)
    return activity
