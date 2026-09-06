"""Canonical normalization for Strava export ingestion."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

METERS_PER_MILE = 1609.344
FEET_PER_METER = 3.280839895013123

# Strava's current SportType values that unambiguously represent running.
RUNNING_SPORT_TYPES = frozenset({"Run", "TrailRun", "VirtualRun"})
INDOOR_RUN_NAME_PATTERN = re.compile(
    r"\b(?:indoor|treadmill|zwift|tm)\b", re.IGNORECASE
)


class NormalizationError(ValueError):
    """A source record cannot be converted to a canonical run."""


@dataclass(frozen=True)
class Run:
    """The internal representation of a running activity."""

    activity_id: int
    start_datetime: datetime
    name: str
    distance_m: float
    moving_time_s: int
    elapsed_time_s: int
    elevation_gain_m: float | None
    max_speed_mps: float | None
    average_heart_rate_bpm: float | None
    max_heart_rate_bpm: float | None
    calories: float | None
    sport_type: str = "Run"
    timezone: str | None = None
    start_lat: float | None = None
    start_lon: float | None = None
    end_lat: float | None = None
    end_lon: float | None = None
    start_city: str | None = None
    start_locality: str | None = None
    start_state: str | None = None
    start_state_code: str | None = None
    start_country: str | None = None
    start_country_code: str | None = None


def is_running_activity(
    activity_type: object, sport_type: object | None = None
) -> bool:
    """Conservatively identify Strava runs, preferring modern sport_type."""
    if isinstance(sport_type, str) and sport_type:
        return sport_type in RUNNING_SPORT_TYPES
    return activity_type == "Run"


def is_indoor_run(name: object, sport_type: object | None = None) -> bool:
    """Identify indoor runs using Strava's type or an explicit activity name."""
    if sport_type == "VirtualRun":
        return True
    return bool(INDOOR_RUN_NAME_PATTERN.search("" if name is None else str(name)))


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


def _coordinate(
    value: object, *, minimum: float, maximum: float, field: str, source: str
) -> float | None:
    if value is None or value == "":
        return None
    number = _optional_float(value, field=field, source=source)
    return number if number is not None and minimum <= number <= maximum else None


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _timezone(value: object, *, source: str) -> str | None:
    name = _optional_text(value)
    if name is None:
        return None
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as error:
        raise NormalizationError(f"{source}: invalid timezone {name!r}") from error
    return name


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
    sport_type: object = None,
    timezone: object = None,
    start_lat: object = None,
    start_lon: object = None,
    end_lat: object = None,
    end_lon: object = None,
    start_city: object = None,
    start_locality: object = None,
    start_state: object = None,
    start_state_code: object = None,
    start_country: object = None,
    start_country_code: object = None,
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
    normalized_start_lat = _coordinate(
        start_lat, minimum=-90, maximum=90, field="start latitude", source=source
    )
    normalized_start_lon = _coordinate(
        start_lon, minimum=-180, maximum=180, field="start longitude", source=source
    )
    if normalized_start_lat is None or normalized_start_lon is None:
        normalized_start_lat = normalized_start_lon = None
    normalized_end_lat = _coordinate(
        end_lat, minimum=-90, maximum=90, field="end latitude", source=source
    )
    normalized_end_lon = _coordinate(
        end_lon, minimum=-180, maximum=180, field="end longitude", source=source
    )
    if normalized_end_lat is None or normalized_end_lon is None:
        normalized_end_lat = normalized_end_lon = None
    return Run(
        activity_id=normalized_id,
        start_datetime=_utc_datetime(
            start_datetime, field="start datetime", source=source
        ),
        name="" if name is None else str(name),
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
        sport_type=_optional_text(sport_type) or "Run",
        timezone=_timezone(timezone, source=source),
        start_lat=normalized_start_lat,
        start_lon=normalized_start_lon,
        end_lat=normalized_end_lat,
        end_lon=normalized_end_lon,
        start_city=_optional_text(start_city),
        start_locality=_optional_text(start_locality),
        start_state=_optional_text(start_state),
        start_state_code=_optional_text(start_state_code),
        start_country=_optional_text(start_country),
        start_country_code=_optional_text(start_country_code),
    )


def _rounded(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def run_to_record(run: Run) -> dict[str, object]:
    """Render one canonical run as a deterministic public record."""
    timestamp = run.start_datetime.isoformat(timespec="seconds").replace("+00:00", "Z")
    local_start = (
        run.start_datetime.astimezone(ZoneInfo(run.timezone))
        if run.timezone is not None
        else None
    )
    return {
        "activity_id": run.activity_id,
        "local_date": local_start.date().isoformat() if local_start else None,
        "start_datetime": timestamp,
        "local_start_datetime": (
            local_start.isoformat(timespec="seconds") if local_start else None
        ),
        "timezone": run.timezone,
        "name": run.name,
        "sport_type": run.sport_type,
        "distance_m": _rounded(run.distance_m, 1),
        "moving_time_s": run.moving_time_s,
        "elapsed_time_s": run.elapsed_time_s,
        "elevation_gain_m": _rounded(run.elevation_gain_m, 1),
        "max_speed_mps": _rounded(run.max_speed_mps, 6),
        "average_heart_rate_bpm": _rounded(run.average_heart_rate_bpm, 1),
        "max_heart_rate_bpm": _rounded(run.max_heart_rate_bpm, 1),
        "calories": _rounded(run.calories, 1),
        "start_city": run.start_city,
        "start_locality": run.start_locality,
        "start_state": run.start_state,
        "start_state_code": run.start_state_code,
        "start_country": run.start_country,
        "start_country_code": run.start_country_code,
    }


def record_to_run(record: Mapping[str, object]) -> Run:
    """Reload canonical values from the public CSV."""
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
        sport_type=record.get("sport_type") or record.get("activity_type"),
        timezone=record.get("timezone"),
        start_city=record.get("start_city"),
        start_locality=record.get("start_locality"),
        start_state=record.get("start_state"),
        start_state_code=record.get("start_state_code"),
        start_country=record.get("start_country"),
        start_country_code=record.get("start_country_code"),
        source=f"public CSV activity {activity_id!r}",
    )


def validate_runs(runs: Iterable[Run]) -> list[Run]:
    ordered = list(runs)
    ids = [run.activity_id for run in ordered]
    if len(ids) != len(set(ids)):
        raise ValueError("activity IDs must be unique")
    if any(run.sport_type not in RUNNING_SPORT_TYPES for run in ordered):
        raise ValueError("canonical dataset contains a non-running sport type")
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
    "is_indoor_run",
    "is_running_activity",
    "meters_to_miles",
    "normalize_run",
    "pace_minutes_per_mile",
    "record_to_run",
    "run_to_record",
    "validate_runs",
]
