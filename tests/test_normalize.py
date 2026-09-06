from __future__ import annotations

from datetime import UTC, datetime

import pytest

from running.normalize import (
    is_running_activity,
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


def test_export_values_are_normalized() -> None:
    run = normalize_run(
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
    assert run.activity_id == 123
    assert run.distance_m == 5000
    assert run.moving_time_s == 1500
