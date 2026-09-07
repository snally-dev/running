from __future__ import annotations

import runpy
from pathlib import Path

import polars as pl

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_run_metric_frames_are_consistent() -> None:
    metrics = runpy.run_path(str(PROJECT_ROOT / "scripts/run_metrics.py"))
    overview = metrics["overview_df"].row(0, named=True)

    assert overview["run_count"] == metrics["runs_by_year_df"]["run_count"].sum()
    assert overview["run_count"] == metrics["runs_by_month_df"]["run_count"].sum()
    assert overview["locality_count"] == metrics["runs_by_locality_df"].height
    assert overview["city_count"] == metrics["runs_by_city_df"].height
    assert overview["region_count"] == metrics["runs_by_region_df"].height
    assert overview["country_count"] == metrics["runs_by_country_df"].height

    grouped_frames = (
        metrics["runs_by_locality_df"],
        metrics["runs_by_city_df"],
        metrics["runs_by_region_df"],
        metrics["runs_by_country_df"],
        metrics["runs_by_year_df"],
        metrics["runs_by_month_df"],
    )
    for frame in grouped_frames:
        assert "aggregate_moving_pace_seconds_per_mile" in frame.columns
        assert "average_heart_rate_bpm" not in frame.columns

    assert metrics["runs_by_month_df"].height <= metrics["runs_by_year_df"].height * 12


def test_race_personal_records_are_timed_and_uncluttered() -> None:
    metrics = runpy.run_path(str(PROJECT_ROOT / "scripts/race_metrics.py"))
    personal_records = metrics["personal_records_df"]
    over_40_personal_records = metrics["over_40_personal_records_df"]

    assert personal_records["distance_category"].n_unique() == personal_records.height
    assert personal_records["finish_duration_seconds"].null_count() == 0
    assert "bib_number" not in personal_records.columns
    assert metrics["overview_df"]["region_count"].item() == 12
    assert personal_records.select(pl.all().is_null().sum()).sum_horizontal().item() == 0
    assert over_40_personal_records["race_date"].min() >= metrics[
        "AGE_40_START_DATE"
    ]
    assert (
        over_40_personal_records["distance_category"].n_unique()
        == over_40_personal_records.height
    )
    assert over_40_personal_records["finish_duration_seconds"].null_count() == 0
