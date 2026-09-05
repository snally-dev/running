"""Canonical normalization shared by Strava export and API ingestion."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

METERS_PER_MILE = 1609.344
FEET_PER_METER = 3.280839895013123

# Strava's current SportType values that unambiguously represent running.
RUNNING_SPORT_TYPES = frozenset({"Run", "TrailRun", "VirtualRun"})


class NormalizationError(ValueError):
    """A source record cannot be converted to a canonical run."""


@dataclass(frozen=True)
class Run:
    """The single internal representation used for export and API activities."""

    activity_id: int
    start_datetime: datetime
    name: str
    activity_type: str
    distance_m: float
    moving_time_s: int
    elapsed_time_s: int
    elevation_gain_m: float | None
    max_speed_mps: float | None
    average_heart_rate_bpm: float | None
    max_heart_rate_bpm: float | None
    calories: float | None


def is_running_activity(
    activity_type: object, sport_type: object | None = None
) -> bool:
    """Conservatively identify Strava runs, preferring modern sport_type."""
    if isinstance(sport_type, str) and sport_type:
        return sport_type in RUNNING_SPORT_TYPES
    return activity_type == "Run"


def meters_to_miles(distance_m: float) -> float:
    return distance_m / METERS_PER_MILE


def pace_minutes_per_mile(
    moving_time_s: float | None, distance_m: float | None
) -> float | None:
    if moving_time_s is None or distance_m is None:
        return None
    if moving_time_s < 0 or distance_m <= 0:
        return None
    return float(moving_time_s) / 60 / meters_to_miles(distance_m)


def distance_flags(distance_m: float) -> dict[str, bool]:
    return {
        "is_5k_distance": distance_m >= 5_000,
        "is_10k_distance": distance_m >= 10_000,
        "is_half_marathon_distance": distance_m >= 21_097.5,
        "is_marathon_distance": distance_m >= 42_195,
        "is_ultra_distance": distance_m > 42_195,
    }


def _finite_float(value: object, *, field: str, source: str) -> float:
    if isinstance(value, bool):
        raise NormalizationError(f"{source}: {field} must be numeric")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise NormalizationError(f"{source}: {field} must be numeric") from error
    if not math.isfinite(number):
        raise NormalizationError(f"{source}: {field} must be finite")
    return number


def _optional_float(value: object, *, field: str, source: str) -> float | None:
    if value is None or value == "":
        return None
    return _finite_float(value, field=field, source=source)


def _whole_seconds(value: object, *, field: str, source: str) -> int:
    number = _finite_float(value, field=field, source=source)
    if not number.is_integer():
        raise NormalizationError(f"{source}: {field} must be whole seconds")
    return int(number)


def _utc_datetime(value: datetime | str, *, field: str, source: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(value)
        except (AttributeError, ValueError) as error:
            raise NormalizationError(f"{source}: invalid {field}") from error
    if parsed.tzinfo is None:
        raise NormalizationError(f"{source}: {field} must include a timezone")
    return parsed.astimezone(UTC)


def normalize_run(
    *,
    activity_id: object,
    start_datetime: datetime | str,
    name: object,
    distance_m: object,
    moving_time_s: object,
    elapsed_time_s: object,
    elevation_gain_m: object = None,
    max_speed_mps: object = None,
    average_heart_rate_bpm: object = None,
    max_heart_rate_bpm: object = None,
    calories: object = None,
    source: str,
) -> Run:
    """Validate source values and construct the shared canonical model."""
    try:
        normalized_id = int(activity_id)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise NormalizationError(f"{source}: invalid activity ID") from error
    distance = _finite_float(distance_m, field="distance", source=source)
    moving = _whole_seconds(moving_time_s, field="moving time", source=source)
    elapsed = _whole_seconds(elapsed_time_s, field="elapsed time", source=source)
    if distance < 0 or moving < 0 or elapsed < 0:
        raise NormalizationError(f"{source}: distance and times must be non-negative")
    return Run(
        activity_id=normalized_id,
        start_datetime=_utc_datetime(
            start_datetime, field="start datetime", source=source
        ),
        name="" if name is None else str(name),
        activity_type="Run",
        distance_m=distance,
        moving_time_s=moving,
        elapsed_time_s=elapsed,
        elevation_gain_m=_optional_float(
            elevation_gain_m, field="elevation gain", source=source
        ),
        max_speed_mps=_optional_float(max_speed_mps, field="max speed", source=source),
        average_heart_rate_bpm=_optional_float(
            average_heart_rate_bpm, field="average heart rate", source=source
        ),
        max_heart_rate_bpm=_optional_float(
            max_heart_rate_bpm, field="max heart rate", source=source
        ),
        calories=_optional_float(calories, field="calories", source=source),
    )


def normalize_api_activity(activity: Mapping[str, Any]) -> Run:
    """Normalize a Strava SummaryActivity or DetailedActivity."""
    activity_id = activity.get("id")
    source = f"Strava API activity {activity_id!r}"
    if not is_running_activity(activity.get("type"), activity.get("sport_type")):
        raise NormalizationError(f"{source}: activity is not a supported run")
    return normalize_run(
        activity_id=activity_id,
        start_datetime=activity.get("start_date"),  # type: ignore[arg-type]
        name=activity.get("name", ""),
        distance_m=activity.get("distance"),
        moving_time_s=activity.get("moving_time"),
        elapsed_time_s=activity.get("elapsed_time"),
        elevation_gain_m=activity.get("total_elevation_gain"),
        max_speed_mps=activity.get("max_speed"),
        average_heart_rate_bpm=activity.get("average_heartrate"),
        max_heart_rate_bpm=activity.get("max_heartrate"),
        calories=activity.get("calories"),
        source=source,
    )


def _rounded(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def run_to_record(run: Run) -> dict[str, object]:
    """Render one canonical run as a deterministic public record."""
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
        **distance_flags(run.distance_m),
    }


def record_to_run(record: Mapping[str, object]) -> Run:
    """Reload a public record for deterministic API upserts."""
    activity_id = record.get("activity_id")
    return normalize_run(
        activity_id=activity_id,
        start_datetime=record.get("start_datetime"),  # type: ignore[arg-type]
        name=record.get("name", ""),
        distance_m=record.get("distance_m"),
        moving_time_s=record.get("moving_time_s"),
        elapsed_time_s=record.get("elapsed_time_s"),
        elevation_gain_m=record.get("elevation_gain_m"),
        max_speed_mps=record.get("max_speed_mps"),
        average_heart_rate_bpm=record.get("average_heart_rate_bpm"),
        max_heart_rate_bpm=record.get("max_heart_rate_bpm"),
        calories=record.get("calories"),
        source=f"public CSV activity {activity_id!r}",
    )


def merge_api_run(existing: Run | None, current: Run) -> Run:
    """Prefer current API metadata, retaining export-only calories when absent.

    List Athlete Activities supplies every public canonical field except calories.
    Avoiding one DetailedActivity request per run keeps the weekly sync inexpensive;
    historical export calories remain available while new API rows leave them blank.
    """
    if existing is None or current.calories is not None:
        return current
    return Run(**{**current.__dict__, "calories": existing.calories})


def validate_runs(runs: Iterable[Run]) -> list[Run]:
    ordered = list(runs)
    ids = [run.activity_id for run in ordered]
    if len(ids) != len(set(ids)):
        raise ValueError("activity IDs must be unique")
    if any(run.activity_type != "Run" for run in ordered):
        raise ValueError("canonical dataset contains a non-running activity")
    expected = sorted(ordered, key=lambda run: (run.start_datetime, run.activity_id))
    if ordered != expected:
        raise ValueError("runs must be ordered by start_datetime and activity_id")
    return ordered


__all__ = [
    "FEET_PER_METER",
    "METERS_PER_MILE",
    "RUNNING_SPORT_TYPES",
    "NormalizationError",
    "Run",
    "distance_flags",
    "is_running_activity",
    "merge_api_run",
    "meters_to_miles",
    "normalize_api_activity",
    "normalize_run",
    "pace_minutes_per_mile",
    "record_to_run",
    "run_to_record",
    "validate_runs",
]
