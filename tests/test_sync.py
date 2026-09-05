from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from running.build import write_runs
from running.normalize import Run
from running.sync import synchronize


class FakeClient:
    def __init__(self, activities: list[dict[str, Any]]) -> None:
        self.activities = activities
        self.stream_calls: list[int] = []

    def iter_activities(self, *, after: int, before: int | None = None):
        assert after == 100
        assert before == 200
        yield from self.activities

    def get_activity_streams(self, activity_id: int) -> dict[str, object]:
        self.stream_calls.append(activity_id)
        return {"latlng": {"data": [[38.1, -77.1]]}, "time": {"data": [0]}}


def _run(**changes: object) -> Run:
    values: dict[str, object] = {
        "activity_id": 1,
        "start_datetime": datetime(2024, 1, 2, tzinfo=UTC),
        "name": "Morning Run",
        "activity_type": "Run",
        "distance_m": 5000.0,
        "moving_time_s": 1500,
        "elapsed_time_s": 1600,
        "elevation_gain_m": 12.0,
        "max_speed_mps": 4.2,
        "average_heart_rate_bpm": None,
        "max_heart_rate_bpm": None,
        "calories": 400.0,
    }
    values.update(changes)
    return Run(**values)  # type: ignore[arg-type]


def _activity(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": 2,
        "start_date": "2024-01-03T00:00:00Z",
        "name": "Evening Run",
        "type": "Run",
        "sport_type": "Run",
        "distance": 6000.0,
        "moving_time": 1800,
        "elapsed_time": 1900,
        "total_elevation_gain": 20.0,
        "max_speed": 4.5,
        "private": True,
    }
    values.update(changes)
    return values


def _sync(
    client: FakeClient, existing: list[Run], stream_root: Path
) -> tuple[list[Run], object]:
    return synchronize(
        client,
        existing,
        after=100,
        before=200,
        stream_root=stream_root,
    )


def test_no_new_activities(tmp_path: Path) -> None:
    runs, result = _sync(FakeClient([]), [_run()], tmp_path)
    assert runs == [_run()]
    assert result.added == result.updated == 0


def test_new_private_run_is_added_and_non_run_filtered(tmp_path: Path) -> None:
    client = FakeClient([_activity(), _activity(id=3, sport_type="Hike")])
    runs, result = _sync(client, [_run()], tmp_path)
    assert [run.activity_id for run in runs] == [1, 2]
    assert result.fetched == 2
    assert result.runs == result.added == 1


def test_existing_activity_is_updated_by_id(tmp_path: Path) -> None:
    client = FakeClient(
        [_activity(id=1, name="Edited name", start_date="2024-01-02T00:00:00Z")]
    )
    runs, result = _sync(client, [_run()], tmp_path)
    assert len(runs) == 1
    assert runs[0].name == "Edited name"
    assert runs[0].calories == 400.0
    assert result.updated == 1


def test_geographic_stream_is_stored_once_deterministically(tmp_path: Path) -> None:
    client = FakeClient([_activity(start_latlng=[38.1, -77.1])])
    first, first_result = _sync(client, [], tmp_path)
    second, second_result = _sync(client, first, tmp_path)
    path = tmp_path / "2.json"
    assert path.read_text(encoding="utf-8") == (
        '{"latlng":{"data":[[38.1,-77.1]]},"time":{"data":[0]}}\n'
    )
    assert client.stream_calls == [2]
    assert first_result.streams_added == 1
    assert second_result.streams_added == 0
    assert first == second


def test_repeated_sync_produces_identical_csv(tmp_path: Path) -> None:
    client = FakeClient([_activity()])
    first, _ = _sync(client, [_run()], tmp_path / "streams")
    first_path = tmp_path / "first.csv"
    second_path = tmp_path / "second.csv"
    write_runs(first, output_path=first_path)
    second, result = _sync(client, first, tmp_path / "streams")
    write_runs(second, output_path=second_path)
    assert result.added == result.updated == 0
    assert first_path.read_bytes() == second_path.read_bytes()


def test_public_csv_has_no_coordinates(tmp_path: Path) -> None:
    path = tmp_path / "runs.csv"
    write_runs([_run()], output_path=path)
    with path.open(encoding="utf-8", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert "start_lat" not in row
    assert "end_lon" not in row
    assert "latlng" not in json.dumps(row)
