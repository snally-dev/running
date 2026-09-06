from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest

from running.build import write_runs
from running.normalize import Run
from running.strava.export import StravaExportError, discover_archives
from running.sync import process_exports, synchronize

HEADER = [
    "Activity ID",
    "Activity Date",
    "Activity Name",
    "Activity Type",
    "Filename",
    "Elapsed Time",
    "Moving Time",
    "Distance",
    "Elevation Gain",
    "Max Speed",
    "Max Heart Rate",
    "Average Heart Rate",
    "Calories",
    "Type",
]


def _row(
    activity_id: int,
    *,
    name: str | None = None,
    activity_type: str = "Run",
    calories: str = "400",
    sport_type: str = "",
) -> list[str]:
    return [
        str(activity_id),
        "Jan 02, 2024, 03:04:05 PM",
        name or f"Activity {activity_id}",
        activity_type,
        "",
        "1800",
        "1700",
        "5.0",
        "12.3",
        "4.2",
        "180",
        "150",
        calories,
        sport_type,
    ]


def _archive(
    path: Path,
    rows: list[list[str]],
    *,
    timestamp: tuple[int, int, int, int, int, int] = (2024, 1, 1, 0, 0, 0),
) -> Path:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(HEADER)
    writer.writerows(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    info = ZipInfo("export/activities.csv", date_time=timestamp)
    with ZipFile(path, "w") as archive:
        archive.writestr(info, output.getvalue())
    return path


def _run(**changes: object) -> Run:
    values: dict[str, object] = {
        "activity_id": 1,
        "start_datetime": datetime(2024, 1, 2, 15, 4, 5, tzinfo=UTC),
        "name": "Existing name",
        "distance_m": 5000.0,
        "moving_time_s": 1700,
        "elapsed_time_s": 1800,
        "elevation_gain_m": 12.3,
        "max_speed_mps": 4.2,
        "average_heart_rate_bpm": 150.0,
        "max_heart_rate_bpm": 180.0,
        "calories": 400.0,
        "start_city": "Frederick",
        "start_state": "Maryland",
        "start_country": "United States",
        "start_country_code": "US",
    }
    values.update(changes)
    return Run(**values)  # type: ignore[arg-type]


def test_multiple_overlapping_exports_are_order_independent(tmp_path: Path) -> None:
    old = _archive(tmp_path / "old.zip", [_row(1), _row(2, name="Old name")])
    new = _archive(
        tmp_path / "new.zip",
        [_row(2, name="Edited name"), _row(3)],
        timestamp=(2025, 1, 1, 0, 0, 0),
    )

    forward, forward_result = synchronize([old, new], [])
    backward, backward_result = synchronize([new, old], [])

    assert forward == backward
    assert forward_result == backward_result
    assert [run.activity_id for run in forward] == [1, 2, 3]
    assert next(run for run in forward if run.activity_id == 2).name == "Edited name"
    assert forward_result.duplicates_removed == 1


def test_duplicate_ids_inside_archive_are_removed(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "duplicate.zip", [_row(1), _row(1)])
    runs, result = synchronize([archive], [])
    assert [run.activity_id for run in runs] == [1]
    assert result.duplicates_removed == 1


def test_existing_rows_and_omitted_values_are_preserved(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "export.zip", [_row(1, name="Edited", calories="")])
    absent_from_export = _run(
        activity_id=99,
        start_datetime=datetime(2023, 1, 1, tzinfo=UTC),
        name="Historical row",
    )

    runs, result = synchronize([archive], [_run(), absent_from_export])

    by_id = {run.activity_id: run for run in runs}
    assert by_id[1].name == "Edited"
    assert by_id[1].calories == 400
    assert by_id[1].start_city == "Frederick"
    assert by_id[99] == absent_from_export
    assert result.existing_preserved == 1


def test_indoor_run_in_export_removes_existing_row(tmp_path: Path) -> None:
    archive = _archive(
        tmp_path / "export.zip",
        [_row(1, sport_type="VirtualRun"), _row(2, name="Treadmill")],
    )

    runs, result = synchronize(
        archive_paths=[archive],
        existing=[_run(), _run(activity_id=2, name="Previously generic")],
    )

    assert runs == []
    assert result.indoor_excluded == 2
    assert result.existing_preserved == 0


def test_repeated_processing_is_idempotent(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    output = tmp_path / "runs.csv"
    cache = tmp_path / "cache.csv"
    _archive(raw / "export.zip", [_row(1), _row(2)])

    process_exports(raw_root=raw, output_path=output, cache_path=cache)
    first = output.read_bytes()
    _, second_result, _ = process_exports(
        raw_root=raw, output_path=output, cache_path=cache
    )

    assert output.read_bytes() == first
    assert second_result.added == 0
    assert second_result.total == 2


def test_no_archives_preserves_existing_csv(tmp_path: Path) -> None:
    raw = tmp_path / "missing-raw"
    output = tmp_path / "runs.csv"
    write_runs([_run()], output_path=output)
    before = output.read_bytes()

    _, result, geocoding = process_exports(
        raw_root=raw,
        output_path=output,
        cache_path=tmp_path / "cache.csv",
    )

    assert output.read_bytes() == before
    assert result.archives_found == 0
    assert result.existing_preserved == result.total == 1
    assert geocoding.api_requests == 0


def test_no_archives_removes_named_indoor_run(tmp_path: Path) -> None:
    runs, result = synchronize([], [_run(name="Indoor track run")])

    assert runs == []
    assert result.indoor_excluded == 1
    assert result.existing_preserved == 0


@pytest.mark.parametrize("contents", [b"not a zip", b""])
def test_malformed_archive_fails_clearly(tmp_path: Path, contents: bytes) -> None:
    path = tmp_path / "broken.zip"
    path.write_bytes(contents)
    with pytest.raises(StravaExportError, match="could not read Strava archive"):
        synchronize([path], [])


def test_archive_without_activities_csv_fails_clearly(tmp_path: Path) -> None:
    path = tmp_path / "missing.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("readme.txt", "missing")
    with pytest.raises(StravaExportError, match="exactly one activities.csv"):
        synchronize([path], [])


def test_archive_discovery_is_recursive_and_case_insensitive(tmp_path: Path) -> None:
    first = _archive(tmp_path / "one.zip", [_row(1)])
    second = _archive(tmp_path / "history" / "two.ZIP", [_row(2)])
    (tmp_path / "ignore.txt").write_text("no", encoding="utf-8")
    assert discover_archives(tmp_path) == [second, first]
