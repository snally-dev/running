from __future__ import annotations

from datetime import UTC, datetime

import pytest

from running.normalize import (
    is_indoor_run,
    is_running_activity,
    normalize_run,
    run_to_record,
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


@pytest.mark.parametrize(
    ("name", "sport_type"),
    [
        ("Morning Run", "VirtualRun"),
        ("Treadmill intervals", "Run"),
        ("Indoor Track", "Run"),
        ("Zwift Run", None),
    ],
)
def test_indoor_runs_are_identified(name: str, sport_type: str | None) -> None:
    assert is_indoor_run(name, sport_type)


def test_virtual_race_name_alone_is_not_indoor() -> None:
    assert not is_indoor_run("Virtual 10K", "Run")


def test_export_values_are_normalized() -> None:
    run = normalize_run(
        activity_id="123",
        start_datetime=datetime(2024, 1, 2, tzinfo=UTC),
        name="Morning Run",
        distance_m="5000",
        moving_time_s="1500",
        elapsed_time_s="1600",
        elevation_gain_m="12.3",
        sport_type="TrailRun",
        timezone="America/New_York",
        source="export row",
    )
    assert run.activity_id == 123
    assert run.distance_m == 5000
    assert run.moving_time_s == 1500
    assert run.sport_type == "TrailRun"
    record = run_to_record(run)
    assert record["distance_miles"] == 3.11
    assert record["start_datetime_utc"] == "2024-01-02T00:00:00Z"
    assert record["activity_date_local"] == "2024-01-01"
    assert "local_start_datetime" not in record
