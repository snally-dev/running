from __future__ import annotations

from pathlib import Path

import polars as pl

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNS_PATH = PROJECT_ROOT / "data/public/runs.csv"
OUTPUT_DIR = PROJECT_ROOT / "data/derived"
FEET_PER_METER = 3.280839895013123

runs_df = (
    pl.read_csv(RUNS_PATH)
    .with_columns(
        pl.col("activity_date_local").str.to_date(),
        pl.col("start_datetime_utc")
        .str.slice(0, 10)
        .str.to_date()
        .alias("activity_date_utc"),
        (pl.col("elevation_gain_meters") * FEET_PER_METER).alias("elevation_gain_feet"),
    )
    .with_columns(
        pl.when(pl.col("distance_miles") > 0)
        .then(pl.col("moving_time_seconds") / pl.col("distance_miles"))
        .alias("moving_pace_seconds_per_mile"),
    )
    .with_columns(
        pl.coalesce("activity_date_local", "activity_date_utc").alias("activity_date")
    )
)

positive_distance = pl.col("distance_miles") > 0
positive_distance_sum = pl.col("distance_miles").filter(positive_distance).sum()
aggregate_moving_pace_seconds_per_mile = pl.when(positive_distance_sum > 0).then(
    pl.col("moving_time_seconds").filter(positive_distance).sum()
    / positive_distance_sum
)

overview_df = runs_df.select(
    pl.len().alias("run_count"),
    pl.col("activity_date").n_unique().alias("active_day_count"),
    pl.col("activity_date").min().alias("first_run_date"),
    pl.col("activity_date").max().alias("latest_run_date"),
    pl.col("distance_miles").sum().alias("total_distance_miles"),
    pl.col("distance_miles").mean().alias("average_distance_miles"),
    pl.col("distance_miles").median().alias("median_distance_miles"),
    pl.col("distance_miles").max().alias("longest_distance_miles"),
    (pl.col("moving_time_seconds").sum() / 3600).alias("total_moving_hours"),
    (pl.col("elapsed_time_seconds").sum() / 3600).alias("total_elapsed_hours"),
    pl.col("elevation_gain_feet").sum().alias("total_elevation_gain_feet"),
    aggregate_moving_pace_seconds_per_mile.alias(
        "aggregate_moving_pace_seconds_per_mile"
    ),
    pl.struct(
        "start_country_code",
        "start_region_code",
        "start_city",
        "start_locality",
    )
    .filter(pl.col("start_locality").is_not_null())
    .n_unique()
    .alias("locality_count"),
    pl.struct("start_country_code", "start_region_code", "start_city")
    .filter(pl.col("start_city").is_not_null())
    .n_unique()
    .alias("city_count"),
    pl.struct("start_country_code", "start_region_code")
    .filter(pl.col("start_region_code").is_not_null())
    .n_unique()
    .alias("region_count"),
    pl.col("start_region_code")
    .filter(pl.col("start_region_code").str.starts_with("US"))
    .drop_nulls()
    .n_unique()
    .alias("us_state_count"),
    pl.col("start_country_code").drop_nulls().n_unique().alias("country_count"),
    (pl.col("start_country_code").is_not_null().mean() * 100).alias(
        "location_coverage_percent"
    ),
    (
        (
            pl.col("average_heart_rate_bpm").is_not_null()
            | pl.col("max_heart_rate_bpm").is_not_null()
        ).mean()
        * 100
    ).alias("heart_rate_coverage_percent"),
)


location_aggregations = (
    pl.len().alias("run_count"),
    pl.col("activity_date").n_unique().alias("active_day_count"),
    pl.col("distance_miles").sum().alias("total_distance_miles"),
    pl.col("distance_miles").mean().alias("average_distance_miles"),
    pl.col("distance_miles").median().alias("median_distance_miles"),
    (pl.col("moving_time_seconds").sum() / 3600).alias("total_moving_hours"),
    (pl.col("elapsed_time_seconds").sum() / 3600).alias("total_elapsed_hours"),
    aggregate_moving_pace_seconds_per_mile.alias(
        "aggregate_moving_pace_seconds_per_mile"
    ),
    pl.col("elevation_gain_feet").sum().alias("total_elevation_gain_feet"),
)


runs_by_locality_df = (
    runs_df.filter(pl.col("start_locality").is_not_null())
    .group_by(
        "start_country_code",
        "start_country",
        "start_region_code",
        "start_region",
        "start_city",
        "start_locality",
    )
    .agg(*location_aggregations)
    .sort(
        [
            "total_distance_miles",
            "start_country_code",
            "start_region_code",
            "start_city",
            "start_locality",
        ],
        descending=[True, False, False, False, False],
        nulls_last=True,
    )
)

runs_by_city_df = (
    runs_df.filter(pl.col("start_city").is_not_null())
    .group_by(
        "start_country_code",
        "start_country",
        "start_region_code",
        "start_region",
        "start_city",
    )
    .agg(*location_aggregations)
    .sort(
        [
            "total_distance_miles",
            "start_country_code",
            "start_region_code",
            "start_city",
        ],
        descending=[True, False, False, False],
        nulls_last=True,
    )
)

runs_by_region_df = (
    runs_df.filter(pl.col("start_region").is_not_null())
    .group_by(
        "start_country_code",
        "start_country",
        "start_region_code",
        "start_region",
    )
    .agg(*location_aggregations)
    .sort(
        ["total_distance_miles", "start_country_code", "start_region_code"],
        descending=[True, False, False],
        nulls_last=True,
    )
)

runs_by_country_df = (
    runs_df.filter(pl.col("start_country").is_not_null())
    .group_by("start_country_code", "start_country")
    .agg(*location_aggregations)
    .sort(
        ["total_distance_miles", "start_country_code"],
        descending=[True, False],
        nulls_last=True,
    )
)


runs_by_year_df = (
    runs_df.with_columns(pl.col("activity_date").dt.year().alias("year"))
    .group_by("year")
    .agg(
        pl.len().alias("run_count"),
        pl.col("activity_date").n_unique().alias("active_day_count"),
        pl.col("distance_miles").sum().alias("total_distance_miles"),
        pl.col("distance_miles").mean().alias("average_distance_miles"),
        pl.col("distance_miles").max().alias("longest_distance_miles"),
        (pl.col("moving_time_seconds").sum() / 3600).alias("total_moving_hours"),
        (pl.col("elapsed_time_seconds").sum() / 3600).alias("total_elapsed_hours"),
        aggregate_moving_pace_seconds_per_mile.alias(
            "aggregate_moving_pace_seconds_per_mile"
        ),
        pl.col("elevation_gain_feet").sum().alias("total_elevation_gain_feet"),
    )
    .sort("year")
)

runs_by_month_df = (
    runs_df.with_columns(pl.col("activity_date").dt.truncate("1mo").alias("month"))
    .group_by("month")
    .agg(
        pl.len().alias("run_count"),
        pl.col("activity_date").n_unique().alias("active_day_count"),
        pl.col("distance_miles").sum().alias("total_distance_miles"),
        pl.col("distance_miles").mean().alias("average_distance_miles"),
        pl.col("distance_miles").max().alias("longest_distance_miles"),
        (pl.col("moving_time_seconds").sum() / 3600).alias("total_moving_hours"),
        (pl.col("elapsed_time_seconds").sum() / 3600).alias("total_elapsed_hours"),
        aggregate_moving_pace_seconds_per_mile.alias(
            "aggregate_moving_pace_seconds_per_mile"
        ),
        pl.col("elevation_gain_feet").sum().alias("total_elevation_gain_feet"),
    )
    .sort("month")
)

runs_by_week = (
    runs_df.with_columns(pl.col("activity_date").dt.truncate("1w").alias("week"))
    .group_by("week")
    .agg(
        pl.len().alias("run_count"),
        pl.col("activity_date").n_unique().alias("active_day_count"),
        pl.col("distance_miles").sum().alias("total_distance_miles"),
        pl.col("distance_miles").mean().alias("average_distance_miles"),
        pl.col("distance_miles").max().alias("longest_distance_miles"),
        (pl.col("moving_time_seconds").sum() / 3600).alias("total_moving_hours"),
        (pl.col("elapsed_time_seconds").sum() / 3600).alias("total_elapsed_hours"),
        aggregate_moving_pace_seconds_per_mile.alias(
            "aggregate_moving_pace_seconds_per_mile"
        ),
        pl.col("elevation_gain_feet").sum().alias("total_elevation_gain_feet"),
    )
    .sort("week")
)


def write_outputs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {
        "run_overview.csv": overview_df,
        "runs_by_locality.csv": runs_by_locality_df,
        "runs_by_city.csv": runs_by_city_df,
        "runs_by_region.csv": runs_by_region_df,
        "runs_by_country.csv": runs_by_country_df,
        "runs_by_year.csv": runs_by_year_df,
        "runs_by_month.csv": runs_by_month_df,
        "runs_by_week.csv": runs_by_week,
    }
    for filename, frame in outputs.items():
        frame.with_columns(pl.col(pl.Float64).round(2)).write_csv(OUTPUT_DIR / filename)


if __name__ == "__main__":
    write_outputs()
