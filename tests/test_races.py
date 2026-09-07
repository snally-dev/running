from __future__ import annotations

import csv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RACES_PATH = PROJECT_ROOT / "data/public/races.csv"
RUNS_PATH = PROJECT_ROOT / "data/public/runs.csv"
RACE_COLUMNS = (
    "race_date",
    "strava_id",
    "race_name",
    "distance_category",
    "race_city",
    "race_region_code",
    "overall_place",
    "overall_field_size",
    "gender_place",
    "gender_field_size",
    "division_place",
    "division_field_size",
    "pace_seconds_per_mile",
    "finish_duration_seconds",
    "bib_number",
)


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        assert tuple(reader.fieldnames or ()) == RACE_COLUMNS
        return list(reader)


def test_race_dataset_has_valid_rankings_and_numeric_durations() -> None:
    races = _rows(RACES_PATH)

    assert len(races) == 59
    for race in races:
        for place, field_size in (
            ("overall_place", "overall_field_size"),
            ("gender_place", "gender_field_size"),
            ("division_place", "division_field_size"),
        ):
            assert 1 <= int(race[place]) <= int(race[field_size])
        for duration in ("pace_seconds_per_mile", "finish_duration_seconds"):
            if race[duration]:
                assert float(race[duration]) > 0


def test_linked_races_reference_runs_on_the_same_local_date() -> None:
    races = _rows(RACES_PATH)
    with RUNS_PATH.open(encoding="utf-8", newline="") as stream:
        runs = {
            row["strava_activity_id"]: row for row in csv.DictReader(stream)
        }

    linked_ids = [race["strava_id"] for race in races if race["strava_id"]]
    assert len(linked_ids) == len(set(linked_ids))
    for race in races:
        if race["strava_id"]:
            assert race["strava_id"] in runs
            assert race["race_date"] == runs[race["strava_id"]][
                "activity_date_local"
            ]

    unlinked = [race for race in races if not race["strava_id"]]
    assert len(unlinked) == 7
    assert any(
        race["race_name"]
        == "Walt Disney World Goofy's Race and a Half Challenge"
        and race["distance_category"] == "CHALLENGE"
        for race in unlinked
    )


def test_race_columns_meet_public_data_density_rules() -> None:
    races = _rows(RACES_PATH)

    for field in RACE_COLUMNS:
        missing = sum(not race[field] for race in races)
        assert missing / len(races) <= 0.5
        populated = [race[field] for race in races if race[field]]
        try:
            zeros = sum(float(value) == 0 for value in populated)
        except ValueError:
            continue
        assert zeros / len(races) < 0.5
