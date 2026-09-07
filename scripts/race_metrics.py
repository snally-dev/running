from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RACES_PATH = PROJECT_ROOT / "data/public/races.csv"
OUTPUT_DIR = PROJECT_ROOT / "data/derived"
AGE_40_START_DATE = date(2023, 11, 9)

DISTANCE_CATEGORY_ORDER = (
    "MILE",
    "5K",
    "6K",
    "8K",
    "5_MILE",
    "10K",
    "15K",
    "10_MILE",
    "HALF_MARATHON",
    "METRIC_MARATHON",
    "MARATHON",
    "CHALLENGE",
)

races_df = pl.read_csv(RACES_PATH, try_parse_dates=True).with_columns(
    pl.col("distance_category").cast(pl.Enum(DISTANCE_CATEGORY_ORDER))
)

overview_df = races_df.select(
    pl.len().alias("race_count"),
    pl.col("race_date").min().alias("first_race_date"),
    pl.col("race_date").max().alias("latest_race_date"),
    pl.col("distance_category").n_unique().alias("distance_category_count"),
    pl.col("race_city").drop_nulls().n_unique().alias("city_count"),
    pl.col("race_region_code").drop_nulls().n_unique().alias("region_count"),
    (pl.col("finish_duration_seconds").sum() / 3600).alias(
        "total_official_finish_hours"
    ),
    pl.col("pace_seconds_per_mile")
    .mean()
    .alias("average_published_pace_seconds_per_mile"),
    pl.col("pace_seconds_per_mile")
    .median()
    .alias("median_published_pace_seconds_per_mile"),
    pl.col("pace_seconds_per_mile")
    .min()
    .alias("fastest_published_pace_seconds_per_mile"),
)


def get_personal_records(races_df: pl.DataFrame) -> pl.DataFrame:
    return (
        races_df.filter(pl.col("finish_duration_seconds").is_not_null())
        .sort("finish_duration_seconds")
        .group_by("distance_category", maintain_order=True)
        .first()
        .select(
            "distance_category",
            "race_date",
            "race_name",
            "finish_duration_seconds",
            "pace_seconds_per_mile",
            "strava_id",
        )
    )


personal_records_df = get_personal_records(races_df)

over_40_personal_records_df = get_personal_records(
    races_df.filter(pl.col("race_date") >= AGE_40_START_DATE)
)

personal_records_comparison_df = (
    personal_records_df.join(
        over_40_personal_records_df,
        on="distance_category",
        how="left",
        suffix="_over_40",
    )
    .with_columns(
        (
            pl.col("finish_duration_seconds_over_40")
            - pl.col("finish_duration_seconds")
        ).alias("gap_seconds")
    )
    .sort("distance_category")
)


def write_outputs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {
        "race_overview.csv": overview_df,
        "race_personal_records.csv": personal_records_comparison_df,
    }
    for filename, frame in outputs.items():
        frame.with_columns(pl.col(pl.Float64).round(2)).write_csv(OUTPUT_DIR / filename)


if __name__ == "__main__":
    write_outputs()
