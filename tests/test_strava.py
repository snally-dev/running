from __future__ import annotations

import csv
import gzip
from pathlib import Path

import pytest

from running.strava import (
    METERS_PER_MILE,
    StravaExportError,
    distance_flags,
    load_runs,
    meters_to_miles,
    pace_minutes_per_mile,
)

HEADER = [
    "Activity ID",
    "Activity Date",
    "Activity Name",
    "Activity Type",
    "Filename",
    "Elapsed Time",
    "Elapsed Time",
    "Moving Time",
    "Distance",
    "Distance",
    "Elevation Gain",
    "Max Speed",
    "Max Heart Rate",
    "Max Heart Rate",
    "Average Heart Rate",
    "Calories",
]


def _row(
    activity_id: int,
    *,
    activity_type: str = "Run",
    date: str = "Jan 02, 2024, 03:04:05 PM",
    filename: str = "",
    distance_m: str = "5000",
) -> list[str]:
    return [
        str(activity_id),
        date,
        f"Activity {activity_id}",
        activity_type,
        filename,
        "1800",
        "1800.0",
        "1700.0",
        str(float(distance_m) / 1000),
        distance_m,
        "12.3",
        "4.2",
        "180",
        "180.0",
        "150.0",
        "400.0",
    ]


def _export(tmp_path: Path, rows: list[list[str]]) -> Path:
    export = tmp_path / "export_test"
    export.mkdir()
    with (export / "activities.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows)
    return export


def test_filters_to_strava_runs(tmp_path: Path) -> None:
    export = _export(
        tmp_path,
        [_row(1), _row(2, activity_type="Ride"), _row(3, activity_type="Hike")],
    )
    assert [run.activity_id for run in load_runs(export)] == [1]


def test_meter_to_mile_conversion() -> None:
    assert meters_to_miles(METERS_PER_MILE) == pytest.approx(1)


def test_pace_uses_moving_time_and_distance() -> None:
    assert pace_minutes_per_mile(510, METERS_PER_MILE) == pytest.approx(8.5)
    assert pace_minutes_per_mile(510, 0) is None
    assert pace_minutes_per_mile(None, METERS_PER_MILE) is None


@pytest.mark.parametrize(
    ("distance_m", "expected"),
    [
        (4_999.9, (False, False, False, False, False)),
        (5_000, (True, False, False, False, False)),
        (10_000, (True, True, False, False, False)),
        (21_097.5, (True, True, True, False, False)),
        (42_195, (True, True, True, True, False)),
        (42_195.1, (True, True, True, True, True)),
    ],
)
def test_distance_thresholds(distance_m: float, expected: tuple[bool, ...]) -> None:
    assert tuple(distance_flags(distance_m).values()) == expected


def test_runs_are_deterministically_ordered(tmp_path: Path) -> None:
    export = _export(
        tmp_path,
        [
            _row(3, date="Jan 03, 2024, 01:00:00 PM"),
            _row(2, date="Jan 02, 2024, 01:00:00 PM"),
            _row(1, date="Jan 02, 2024, 01:00:00 PM"),
        ],
    )
    assert [run.activity_id for run in load_runs(export)] == [1, 2, 3]


def test_duplicate_running_activity_ids_fail(tmp_path: Path) -> None:
    export = _export(tmp_path, [_row(1), _row(1)])
    with pytest.raises(StravaExportError, match="duplicate running Activity IDs: 1"):
        load_runs(export)


def test_missing_detail_elapsed_time_uses_summary_value(tmp_path: Path) -> None:
    row = _row(1)
    row[6] = ""
    assert load_runs(_export(tmp_path, [row]))[0].elapsed_time_s == 1800


def test_missing_optional_values_are_preserved(tmp_path: Path) -> None:
    row = _row(1)
    for index in (4, 10, 11, 12, 13, 14, 15):
        row[index] = ""
    run = load_runs(_export(tmp_path, [row]))[0]
    assert run.elevation_gain_m is None
    assert run.max_speed_mps is None
    assert run.average_heart_rate_bpm is None
    assert run.max_heart_rate_bpm is None
    assert run.calories is None
    assert (run.start_lat, run.start_lon, run.end_lat, run.end_lon) == (
        None,
        None,
        None,
        None,
    )


def test_gpx_endpoints_are_loaded(tmp_path: Path) -> None:
    export = _export(tmp_path, [_row(1, filename="activities/1.gpx.gz")])
    track = export / "activities/1.gpx.gz"
    track.parent.mkdir()
    with gzip.open(track, "wb") as stream:
        stream.write(
            b'<gpx><trk><trkseg><trkpt lat="38.1" lon="-77.1"/>'
            b'<trkpt lat="38.2" lon="-77.2"/></trkseg></trk></gpx>'
        )
    run = load_runs(export)[0]
    assert (run.start_lat, run.start_lon) == (38.1, -77.1)
    assert (run.end_lat, run.end_lon) == (38.2, -77.2)
