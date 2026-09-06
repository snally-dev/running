from __future__ import annotations

from pathlib import Path

import polars as pl

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNS_PATH = PROJECT_ROOT / "data/public/runs.csv"
METERS_PER_MILE = 1609.344
FEET_PER_METER = 3.280839895013123
DISTANCE_TOLERANCE = 0.02
DISTANCE_TARGETS_M = (
    ("5K", 5_000.0),
    ("10K", 10_000.0),
    ("Half Marathon", 21_097.5),
    ("Marathon", 42_195.0),
)


def format_duration(seconds: int | None) -> str | None:
    """Format seconds as M:SS or H:MM:SS for presentation."""
    if seconds is None:
        return None
    if seconds < 0:
        raise ValueError("seconds must be non-negative")
    hours, remainder = divmod(seconds, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{remaining_seconds:02d}"
    return f"{minutes}:{remaining_seconds:02d}"


def format_pace(minutes_per_mile: float | None) -> str | None:
    """Format decimal minutes per mile as M:SS/mi for presentation."""
    if minutes_per_mile is None:
        return None
    if minutes_per_mile < 0:
        raise ValueError("pace must be non-negative")
    total_seconds = round(minutes_per_mile * 60)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}:{seconds:02d}/mi"


runs_df = (
    pl.read_csv(RUNS_PATH)
    .with_columns(
        pl.col("local_date").str.to_date(),
        pl.col("start_datetime").str.slice(0, 10).str.to_date().alias("utc_date"),
        (pl.col("distance_m") / METERS_PER_MILE).alias("distance_miles"),
        (pl.col("elevation_gain_m") * FEET_PER_METER).alias("elevation_gain_feet"),
    )
    .with_columns(
        (pl.col("moving_time_s") / 60 / pl.col("distance_miles")).alias(
            "moving_pace_min_per_mile"
        ),
    )
    .with_columns(pl.coalesce("local_date", "utc_date").alias("activity_date"))
)


weighted_pace = pl.col("moving_time_s").sum() / 60 / pl.col("distance_miles").sum()

overview_df = runs_df.select(
    pl.len().alias("run_count"),
    pl.col("activity_date").n_unique().alias("active_day_count"),
    pl.col("activity_date").min().alias("first_run_date"),
    pl.col("activity_date").max().alias("latest_run_date"),
    pl.col("distance_miles").sum().alias("total_distance_miles"),
    pl.col("distance_miles").mean().alias("average_distance_miles"),
    pl.col("distance_miles").median().alias("median_distance_miles"),
    pl.col("distance_miles").max().alias("longest_distance_miles"),
    (pl.col("moving_time_s").sum() / 3600).alias("total_moving_hours"),
    (pl.col("elapsed_time_s").sum() / 3600).alias("total_elapsed_hours"),
    pl.col("elevation_gain_feet").sum().alias("total_elevation_gain_feet"),
    weighted_pace.alias("weighted_pace_min_per_mile"),
    pl.col("start_locality").drop_nulls().n_unique().alias("locality_count"),
    pl.col("start_city").drop_nulls().n_unique().alias("city_count"),
    pl.col("start_state_code").drop_nulls().n_unique().alias("state_count"),
    pl.col("start_country_code").drop_nulls().n_unique().alias("country_count"),
    (pl.col("timezone").is_not_null().mean() * 100).alias("location_coverage_pct"),
    (pl.col("average_heart_rate_bpm").is_not_null().mean() * 100).alias(
        "heart_rate_coverage_pct"
    ),
)


place_aggregations = (
    pl.len().alias("run_count"),
    pl.col("activity_date").n_unique().alias("active_day_count"),
    pl.col("distance_miles").sum().alias("total_distance_miles"),
    pl.col("distance_miles").mean().alias("average_distance_miles"),
    pl.col("distance_miles").median().alias("median_distance_miles"),
    (pl.col("moving_time_s").sum() / 3600).alias("total_moving_hours"),
    (pl.col("elapsed_time_s").sum() / 3600).alias("total_elapsed_hours"),
    weighted_pace.alias("weighted_pace_min_per_mile"),
    pl.col("elevation_gain_feet").sum().alias("total_elevation_gain_feet"),
    pl.col("average_heart_rate_bpm").mean().alias("average_heart_rate_bpm"),
)


runs_by_locality_df = (
    runs_df.filter(pl.col("start_locality").is_not_null())
    .group_by(
        "start_country_code",
        "start_country",
        "start_state_code",
        "start_state",
        "start_city",
        "start_locality",
    )
    .agg(*place_aggregations)
    .sort("total_distance_miles", descending=True)
)

runs_by_city_df = (
    runs_df.filter(pl.col("start_city").is_not_null())
    .group_by(
        "start_country_code",
        "start_country",
        "start_state_code",
        "start_state",
        "start_city",
    )
    .agg(*place_aggregations)
    .sort("total_distance_miles", descending=True)
)

runs_by_state_df = (
    runs_df.filter(pl.col("start_state").is_not_null())
    .group_by(
        "start_country_code",
        "start_country",
        "start_state_code",
        "start_state",
    )
    .agg(*place_aggregations)
    .sort("total_distance_miles", descending=True)
)

runs_by_country_df = (
    runs_df.filter(pl.col("start_country").is_not_null())
    .group_by("start_country_code", "start_country")
    .agg(*place_aggregations)
    .sort("total_distance_miles", descending=True)
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
        (pl.col("moving_time_s").sum() / 3600).alias("total_moving_hours"),
        (pl.col("elapsed_time_s").sum() / 3600).alias("total_elapsed_hours"),
        weighted_pace.alias("weighted_pace_min_per_mile"),
        pl.col("elevation_gain_feet").sum().alias("total_elevation_gain_feet"),
    )
    .sort("year")
)


# Pick one complete activity record per target so its time, pace, and location
# always refer to the same effort. These are not verified race classifications.
distance_effort_rows: list[dict[str, object]] = []
for distance_label, target_distance_m in DISTANCE_TARGETS_M:
    candidates_df = runs_df.filter(
        (pl.col("distance_m") - target_distance_m).abs()
        <= target_distance_m * DISTANCE_TOLERANCE
    ).sort("elapsed_time_s")
    if candidates_df.is_empty():
        continue

    activity = candidates_df.row(0, named=True)
    distance_miles = float(activity["distance_miles"])
    elapsed_time_s = int(activity["elapsed_time_s"])
    elapsed_pace_min_per_mile = elapsed_time_s / 60 / distance_miles
    distance_effort_rows.append(
        {
            "distance_label": distance_label,
            "target_distance_m": target_distance_m,
            "activity_id": activity["activity_id"],
            "activity_date": activity["activity_date"],
            "name": activity["name"],
            "distance_miles": distance_miles,
            "distance_delta_pct": (
                (float(activity["distance_m"]) - target_distance_m)
                / target_distance_m
                * 100
            ),
            "elapsed_time_s": elapsed_time_s,
            "elapsed_time": format_duration(elapsed_time_s),
            "moving_time_s": activity["moving_time_s"],
            "elapsed_pace_min_per_mile": elapsed_pace_min_per_mile,
            "elapsed_pace": format_pace(elapsed_pace_min_per_mile),
            "moving_pace_min_per_mile": activity["moving_pace_min_per_mile"],
            "moving_pace": format_pace(activity["moving_pace_min_per_mile"]),
            "start_locality": activity["start_locality"],
            "start_city": activity["start_city"],
            "start_state": activity["start_state"],
            "start_country": activity["start_country"],
            "start_country_code": activity["start_country_code"],
        }
    )

best_distance_efforts_df = pl.DataFrame(distance_effort_rows)
