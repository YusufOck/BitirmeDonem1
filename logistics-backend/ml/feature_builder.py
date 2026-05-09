"""
Shared feature builder for training and academic evaluation.

The previous v7 training script had its own merge pipeline and a different
categorical encoding from runtime inference. This module keeps the offline
feature matrix aligned with ml/inference.py so evaluation measures the same
signals used by the running API.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "data" / "raw_data"

ROAD_ENC = {"highway": 0, "mountain": 1, "rural": 2, "urban": 3}
TRAFFIC_ENC = {"congested": 0, "high": 1, "low": 2, "moderate": 3}
WEATHER_ENC = {"clear": 0, "cloudy": 1, "fog": 2, "rain": 3, "snow": 4, "wind": 5}
VEHICLE_ENC = {"car": 0, "motorcycle": 1, "truck": 2, "van": 3}
SURFACE_ENC = {"dry": 0, "icy": 1, "snow_covered": 2, "wet": 3}

VEHICLE_CAPACITY_KG = {0: 200, 1: 30, 2: 3000, 3: 800}
TRAFFIC_RISK = {0: 4, 1: 3, 2: 1, 3: 2}
WEATHER_RISK = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 2}


def time_bucket(hour: int) -> str:
    if 0 <= hour <= 6:
        return "early_morning"
    if 7 <= hour <= 9:
        return "morning_rush"
    if 10 <= hour <= 15:
        return "midday"
    if 16 <= hour <= 19:
        return "evening_rush"
    return "night"


def _read_csv(data_dir: Path, filename: str, **kwargs) -> pd.DataFrame:
    path = data_dir / filename
    if not path.exists():
        raise FileNotFoundError(f"Missing data file: {path}")
    return pd.read_csv(path, **kwargs)


def build_feature_matrix(
    data_dir: str | Path = DEFAULT_DATA_DIR,
    include_runtime_safe_ratio: bool = True,
) -> pd.DataFrame:
    """Build the stop-level ML feature matrix from the five raw CSV sources."""
    data_dir = Path(data_dir)
    stops = _read_csv(
        data_dir,
        "route_stops.csv",
        parse_dates=[
            "planned_arrival",
            "actual_arrival",
            "time_window_open",
            "time_window_close",
        ],
    )
    routes = _read_csv(data_dir, "routes.csv")
    traffic = _read_csv(data_dir, "traffic_segments.csv")
    weather = _read_csv(data_dir, "weather_observations.csv")
    hist = _read_csv(data_dir, "historical_delay_stats.csv")

    df = stops.copy()
    df["hour_of_day"] = df["planned_arrival"].dt.hour
    df["day_of_week"] = df["planned_arrival"].dt.dayofweek
    df["time_bucket"] = df["hour_of_day"].apply(time_bucket)

    df["time_window_slack_min"] = (
        (df["time_window_close"] - df["planned_arrival"]).dt.total_seconds() / 60
    ).clip(lower=0)
    df["time_window_duration_min"] = (
        (df["time_window_close"] - df["time_window_open"]).dt.total_seconds() / 60
    ).clip(lower=0)

    df = df.sort_values(["route_id", "stop_sequence"]).reset_index(drop=True)
    df["cumulative_delay_min"] = (
        df.groupby("route_id")["delay_at_stop_min"].cumsum().shift(1).fillna(0)
    )
    df["prev_stop_delay_min"] = (
        df.groupby("route_id")["delay_at_stop_min"].shift(1).fillna(0)
    )
    total_stops = df.groupby("route_id")["stop_sequence"].transform("max")
    df["stop_progress_ratio"] = df["stop_sequence"] / total_stops.clip(lower=1)
    df["planned_speed_kmh"] = (
        df["distance_from_prev_km"] / (df["planned_travel_min"] / 60.0).clip(lower=0.01)
    )

    df["road_type"] = df["road_type"].str.lower().map(ROAD_ENC)

    route_cols = [
        "route_id",
        "vehicle_type",
        "road_incident",
        "incident_severity",
        "temperature_c",
        "wind_speed_kmh",
        "visibility_km",
        "overall_delay_factor",
        "traffic_level",
        "weather_condition",
        "precipitation_mm",
    ]
    route_df = routes[route_cols].copy()
    route_df["vehicle_type"] = route_df["vehicle_type"].str.lower()
    route_df["traffic_level"] = route_df["traffic_level"].str.lower()
    route_df["weather_condition"] = route_df["weather_condition"].str.lower()
    df = df.merge(route_df, on="route_id", how="left")

    df["vehicle_type_enc"] = df["vehicle_type"].map(VEHICLE_ENC)
    df["traffic_level"] = df["traffic_level"].map(TRAFFIC_ENC)
    df["weather_condition"] = df["weather_condition"].map(WEATHER_ENC)

    df["is_rush_hour"] = (
        df["hour_of_day"].between(7, 9) | df["hour_of_day"].between(16, 19)
    ).astype(int)
    df["is_high_risk_weather"] = df["weather_condition"].isin([3, 4]).astype(int)
    df["is_urban_road"] = (df["road_type"] == ROAD_ENC["urban"]).astype(int)
    df["traffic_weather_risk"] = (
        df["traffic_level"].map(TRAFFIC_RISK).fillna(2)
        * df["weather_condition"].map(WEATHER_RISK).fillna(1)
    )

    traffic["road_type_enc"] = traffic["road_type"].str.lower().map(ROAD_ENC)
    traffic_agg = (
        traffic.groupby(["road_type_enc", "hour_of_day"])
        .agg(
            congestion_ratio_mean=("congestion_ratio", "mean"),
            incident_rate=("incident_reported", "mean"),
        )
        .reset_index()
        .rename(columns={"road_type_enc": "_rt", "hour_of_day": "_hr"})
    )
    df = df.merge(
        traffic_agg,
        left_on=["road_type", "hour_of_day"],
        right_on=["_rt", "_hr"],
        how="left",
    ).drop(columns=["_rt", "_hr"])
    df["congestion_ratio_mean"] = df["congestion_ratio_mean"].fillna(
        traffic["congestion_ratio"].mean()
    )
    df["incident_rate"] = df["incident_rate"].fillna(
        traffic["incident_reported"].mean()
    )

    weather["_wc_enc"] = weather["weather_condition"].str.lower().map(WEATHER_ENC)
    weather_agg = (
        weather.groupby("_wc_enc")
        .agg(
            _surface_mode=("road_surface_condition", lambda x: x.mode().iloc[0]),
            delay_risk_score_mean=("delay_risk_score", "mean"),
        )
        .reset_index()
    )
    weather_agg["road_surface_condition_enc"] = (
        weather_agg["_surface_mode"].str.lower().map(SURFACE_ENC).fillna(0).astype(int)
    )
    df = df.merge(
        weather_agg[["_wc_enc", "road_surface_condition_enc", "delay_risk_score_mean"]],
        left_on="weather_condition",
        right_on="_wc_enc",
        how="left",
    ).drop(columns=["_wc_enc"])
    df["road_surface_condition_enc"] = df["road_surface_condition_enc"].fillna(0).astype(int)
    df["delay_risk_score_mean"] = df["delay_risk_score_mean"].fillna(
        weather["delay_risk_score"].mean()
    )

    hist_enc = hist.copy()
    hist_enc["_rt"] = hist_enc["road_type"].str.lower().map(ROAD_ENC)
    hist_enc["_tl"] = hist_enc["traffic_level"].str.lower().map(TRAFFIC_ENC)
    hist_enc["_wc"] = hist_enc["weather_condition"].str.lower().map(WEATHER_ENC)
    hist_enc = (
        hist_enc.groupby(["_rt", "_tl", "_wc", "time_bucket"])
        .agg(
            hist_delay_probability=("delay_probability", "mean"),
            hist_median_delay_min=("median_delay_min", "mean"),
        )
        .reset_index()
    )
    df = df.merge(
        hist_enc[[
            "_rt",
            "_tl",
            "_wc",
            "time_bucket",
            "hist_delay_probability",
            "hist_median_delay_min",
        ]],
        left_on=["road_type", "traffic_level", "weather_condition", "time_bucket"],
        right_on=["_rt", "_tl", "_wc", "time_bucket"],
        how="left",
    ).drop(columns=["_rt", "_tl", "_wc"])
    df["hist_delay_probability"] = df["hist_delay_probability"].fillna(0.25)
    df["hist_median_delay_min"] = df["hist_median_delay_min"].fillna(
        df["time_window_slack_min"].median()
    )
    df["hist_slack_min"] = (
        df["time_window_slack_min"] - df["hist_median_delay_min"]
    ).clip(lower=0)

    if include_runtime_safe_ratio:
        df["travel_delay_ratio"] = (
            df["cumulative_delay_min"] / df["planned_travel_min"].clip(lower=1.0)
            + (1.0 - df["congestion_ratio_mean"]).clip(lower=0.0)
        ).clip(lower=0.0, upper=5.0)
    else:
        df["travel_delay_ratio"] = (
            (df["actual_travel_min"] - df["planned_travel_min"])
            / df["planned_travel_min"].clip(lower=1.0)
        ).clip(lower=-1.0, upper=5.0)

    df["weight_per_package"] = df["package_weight_kg"] / df["package_count"].clip(lower=1)
    capacity = df["vehicle_type_enc"].map(VEHICLE_CAPACITY_KG).fillna(800)
    df["vehicle_load_ratio"] = (df["package_weight_kg"] / capacity).clip(upper=5.0)
    df["speed_loss_ratio"] = df["congestion_ratio_mean"]
    df["incident_traffic_risk"] = df["road_incident"] * (df["congestion_ratio_mean"] + 1)

    drop_cols = [
        "actual_arrival",
        "actual_service_min",
        "planned_arrival",
        "time_window_open",
        "time_window_close",
        "planned_service_min",
        "vehicle_type",
        "time_bucket",
        "hist_median_delay_min",
        "latitude",
        "longitude",
        "delay_probability",
    ]
    df = df.drop(columns=[column for column in drop_cols if column in df.columns])
    df = df.dropna(subset=["delay_at_stop_min", "missed_time_window"]).reset_index(drop=True)
    return df
