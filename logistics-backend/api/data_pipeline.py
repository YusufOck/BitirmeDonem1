"""
api/data_pipeline.py
--------------------
Inference-time feature pipeline for route optimization.

The app has historical CSV files for traffic, weather, route, and stop
behavior. This module keeps those files in memory and turns them into the
feature dictionary consumed by ml/inference.py.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import pandas as pd

from api.location_store import courier_store

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw_data")
_SYNTH_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "synthetic")

_ROAD_ENC = {"highway": 0, "mountain": 1, "rural": 2, "urban": 3}
_TRAFFIC_ENC = {"congested": 0, "high": 1, "low": 2, "moderate": 3}
_WEATHER_ENC = {"clear": 0, "cloudy": 1, "fog": 2, "rain": 3, "snow": 4, "wind": 5}
_SURF_ENC = {"dry": 0, "icy": 1, "snow_covered": 2, "wet": 3}
_SURF_LABEL = {value: key for key, value in _SURF_ENC.items()}
_CONGESTION_BY_TRAFFIC = {
    "congested": 0.30,
    "high": 0.50,
    "moderate": 0.65,
    "low": 0.90,
}
_TRAFFIC_SEVERITY = {"low": 0, "moderate": 1, "high": 2, "congested": 3}
_VALID_VEHICLES = {"car", "motorcycle", "truck", "van"}


def _bucket(hour: int) -> str:
    if 7 <= hour <= 9:
        return "morning_rush"
    if 16 <= hour <= 19:
        return "evening_rush"
    if hour >= 22 or hour <= 5:
        return "night"
    return "midday"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    return int(round(_safe_float(value, float(default))))


def _safe_str(value: Any, default: str = "") -> str:
    if value is None or pd.isna(value):
        return default
    text = str(value).strip()
    return text if text else default


def _minutes_between(end_value: Any, start_value: Any, default: float) -> float:
    try:
        end = pd.to_datetime(end_value)
        start = pd.to_datetime(start_value)
        minutes = (end - start).total_seconds() / 60.0
        if np.isfinite(minutes):
            return max(minutes, 0.0)
    except Exception:
        pass
    return default


def _traffic_from_congestion(ratio: float) -> str:
    if ratio <= 0.40:
        return "congested"
    if ratio <= 0.58:
        return "high"
    if ratio <= 0.78:
        return "moderate"
    return "low"


def _factor_severity(score: float, warn: float, danger: float) -> str:
    if score >= danger:
        return "danger"
    if score >= warn:
        return "warning"
    return "info"


def _normalize_vehicle_type(vehicle_type: str | None) -> str:
    value = (vehicle_type or "van").lower()
    if value == "bike":
        return "motorcycle"
    if value not in _VALID_VEHICLES:
        return "van"
    return value


def _choose_worst_traffic(*levels: str) -> str:
    valid_levels = [level for level in levels if level in _TRAFFIC_SEVERITY]
    if not valid_levels:
        return "moderate"
    return max(valid_levels, key=lambda level: _TRAFFIC_SEVERITY[level])


def _surface_for_weather(weather_condition: str, fallback_enc: int) -> int:
    if weather_condition == "rain":
        return _SURF_ENC["wet"]
    if weather_condition == "snow":
        return _SURF_ENC["snow_covered"]
    if weather_condition == "fog":
        return _SURF_ENC["wet"]
    if weather_condition in {"clear", "cloudy", "wind"}:
        return _SURF_ENC["dry"]
    return fallback_enc if fallback_enc in _SURF_LABEL else _SURF_ENC["dry"]


class DataPipeline:
    def __init__(self):
        self._traffic: pd.DataFrame | None = None
        self._weather: pd.DataFrame | None = None
        self._hist: pd.DataFrame | None = None
        self._stop_profiles: pd.DataFrame | None = None
        self._stop_profiles_by_id: dict[str, dict[str, Any]] = {}
        self._loaded = False

    def load(self):
        """Load CSV-backed feature sources once during FastAPI startup."""
        self._load_aggregate_features()
        self._load_stop_profiles()

    def _load_aggregate_features(self):
        try:
            traffic = pd.read_csv(f"{_DATA_DIR}/traffic_segments.csv")
            weather = pd.read_csv(f"{_DATA_DIR}/weather_observations.csv")
            hist = pd.read_csv(f"{_DATA_DIR}/historical_delay_stats.csv")

            traffic["road_type_enc"] = traffic["road_type"].str.lower().map(_ROAD_ENC)
            self._traffic = (
                traffic.groupby(["road_type_enc", "hour_of_day"])
                .agg(
                    congestion_ratio_mean=("congestion_ratio", "mean"),
                    incident_rate=("incident_reported", "mean"),
                )
                .reset_index()
            )

            weather["_wc_enc"] = weather["weather_condition"].str.lower().map(_WEATHER_ENC)
            weather_agg = (
                weather.groupby("_wc_enc")
                .agg(
                    _surf_mode=("road_surface_condition", lambda x: x.mode().iloc[0]),
                    delay_risk_score_mean=("delay_risk_score", "mean"),
                )
                .reset_index()
            )
            weather_agg["road_surface_condition_enc"] = (
                weather_agg["_surf_mode"].str.lower().map(_SURF_ENC).fillna(0).astype(int)
            )
            self._weather = weather_agg[
                ["_wc_enc", "road_surface_condition_enc", "delay_risk_score_mean"]
            ]

            hist["_rt"] = hist["road_type"].str.lower().map(_ROAD_ENC)
            hist["_tl"] = hist["traffic_level"].str.lower().map(_TRAFFIC_ENC)
            hist["_wc"] = hist["weather_condition"].str.lower().map(_WEATHER_ENC)
            self._hist = (
                hist.groupby(["_rt", "_tl", "_wc", "time_bucket"])
                .agg(
                    hist_delay_probability=("delay_probability", "mean"),
                    hist_median_delay_min=("median_delay_min", "mean"),
                )
                .reset_index()
            )

            self._loaded = True
        except FileNotFoundError as exc:
            self._loaded = False
            print(f"[DataPipeline] Aggregate CSV files unavailable: {exc}")

    def _load_stop_profiles(self):
        for base_dir in (_SYNTH_DIR, _DATA_DIR):
            stop_path = os.path.join(base_dir, "route_stops.csv")
            route_path = os.path.join(base_dir, "routes.csv")
            if not os.path.exists(stop_path) or not os.path.exists(route_path):
                continue

            stops = pd.read_csv(stop_path)
            routes = pd.read_csv(route_path)
            profiles = stops.merge(routes, on="route_id", how="left", suffixes=("", "_route"))
            profiles = profiles.sort_values(["route_id", "stop_sequence"]).reset_index(drop=True)

            self._stop_profiles = profiles
            self._stop_profiles_by_id = {
                str(row["stop_id"]): row.to_dict()
                for _, row in profiles.iterrows()
                if not pd.isna(row.get("stop_id"))
            }
            return

        self._stop_profiles = None
        self._stop_profiles_by_id = {}
        print("[DataPipeline] Stop profile CSV files unavailable.")

    def _profile_for_stop(self, stop_code: str | None, stop_sequence: int) -> dict[str, Any]:
        if stop_code and stop_code in self._stop_profiles_by_id:
            return self._stop_profiles_by_id[stop_code]

        if self._stop_profiles is not None and not self._stop_profiles.empty:
            index = (max(stop_sequence, 1) - 1) % len(self._stop_profiles)
            return self._stop_profiles.iloc[index].to_dict()

        return {}

    def get_stop_context(
        self,
        stop_code: str | None,
        stop_sequence: int,
        hour_of_day: int,
        day_of_week: int,
        vehicle_type: str | None = None,
        courier_id: str = "",
    ) -> dict:
        """Build ML-ready features and UI-ready delay factors for one stop."""
        profile = self._profile_for_stop(stop_code, stop_sequence)

        road_type = _safe_str(profile.get("road_type"), "urban").lower()
        if road_type not in _ROAD_ENC:
            road_type = "urban"

        weather_condition = _safe_str(profile.get("weather_condition"), "clear").lower()
        if weather_condition not in _WEATHER_ENC:
            weather_condition = "clear"

        realtime = self.get_realtime_features(
            courier_id=courier_id,
            road_type=road_type,
            hour_of_day=hour_of_day,
            day_of_week=day_of_week,
            weather_condition=weather_condition,
        )

        congestion_ratio_mean = _safe_float(
            realtime.get("congestion_ratio_mean"),
            _CONGESTION_BY_TRAFFIC["moderate"],
        )
        profile_traffic_level = _safe_str(
            profile.get("traffic_level"),
            _traffic_from_congestion(congestion_ratio_mean),
        ).lower()
        if profile_traffic_level not in _TRAFFIC_ENC:
            profile_traffic_level = "moderate"
        traffic_level = _choose_worst_traffic(
            profile_traffic_level,
            _traffic_from_congestion(congestion_ratio_mean),
        )

        road_incident = _safe_int(profile.get("road_incident"), 0)
        incident_severity = _safe_float(profile.get("incident_severity"), 0.0)
        incident_rate = max(
            _safe_float(realtime.get("incident_rate"), 0.05),
            0.35 if road_incident else 0.05,
        )

        time_window_slack_min = _minutes_between(
            profile.get("time_window_close"),
            profile.get("planned_arrival"),
            _safe_float(realtime.get("hist_slack_min"), 75.0),
        )
        time_window_duration_min = _minutes_between(
            profile.get("time_window_close"),
            profile.get("time_window_open"),
            60.0,
        )
        hist_delay_probability = _safe_float(
            profile.get("delay_probability"),
            _safe_float(realtime.get("hist_delay_probability"), 0.25),
        )

        road_surface_condition_enc = _surface_for_weather(
            weather_condition,
            _safe_int(realtime.get("road_surface_condition_enc"), 0),
        )

        context = {
            **realtime,
            "feature_source": "csv_profile",
            "road_type": road_type,
            "traffic_level": traffic_level,
            "weather_condition": weather_condition,
            "hour_of_day": hour_of_day,
            "day_of_week": day_of_week,
            "time_window_slack_min": round(time_window_slack_min, 2),
            "time_window_duration_min": round(time_window_duration_min, 2),
            "hist_slack_min": round(_safe_float(realtime.get("hist_slack_min"), time_window_slack_min), 2),
            "hist_delay_probability": round(hist_delay_probability, 4),
            "distance_from_prev_km": _safe_float(profile.get("distance_from_prev_km"), None),
            "planned_travel_min": _safe_float(profile.get("planned_travel_min"), None),
            "package_count": _safe_int(profile.get("package_count"), 1),
            "package_weight_kg": round(_safe_float(profile.get("package_weight_kg"), 1.0), 2),
            "vehicle_type": _normalize_vehicle_type(vehicle_type),
            "road_incident": road_incident,
            "incident_severity": round(incident_severity, 3),
            "incident_rate": round(incident_rate, 4),
            "temperature_c": round(_safe_float(profile.get("temperature_c"), 17.6), 1),
            "precipitation_mm": round(_safe_float(profile.get("precipitation_mm"), 0.0), 2),
            "wind_speed_kmh": round(_safe_float(profile.get("wind_speed_kmh"), 10.3), 1),
            "visibility_km": round(_safe_float(profile.get("visibility_km"), 12.8), 1),
            "congestion_ratio_mean": round(congestion_ratio_mean, 4),
            "road_surface_condition_enc": road_surface_condition_enc,
            "delay_risk_score_mean": round(
                _safe_float(realtime.get("delay_risk_score_mean"), hist_delay_probability),
                4,
            ),
            "overall_delay_factor": round(
                _safe_float(profile.get("overall_delay_factor"), 1.0),
                3,
            ),
        }
        context["delay_factors"] = self._build_delay_factors(context)
        return context

    def _build_delay_factors(self, context: dict[str, Any]) -> list[dict[str, str]]:
        traffic = context["traffic_level"]
        congestion = _safe_float(context.get("congestion_ratio_mean"), 0.65)
        traffic_pressure = 1.0 - congestion
        traffic_severity = _factor_severity(traffic_pressure, warn=0.30, danger=0.55)

        weather = context["weather_condition"]
        weather_risk = {
            "clear": 0.05,
            "cloudy": 0.18,
            "wind": 0.28,
            "fog": 0.45,
            "rain": 0.50,
            "snow": 0.72,
        }.get(weather, 0.10)

        incident = _safe_int(context.get("road_incident"), 0)
        incident_severity = _safe_float(context.get("incident_severity"), 0.0)
        historical_probability = _safe_float(context.get("hist_delay_probability"), 0.25)
        slack = _safe_float(context.get("time_window_slack_min"), 75.0)
        package_count = _safe_int(context.get("package_count"), 1)
        package_weight = _safe_float(context.get("package_weight_kg"), 1.0)
        surface = _SURF_LABEL.get(_safe_int(context.get("road_surface_condition_enc"), 0), "dry")

        return [
            {
                "label": "Traffic",
                "value": traffic.title(),
                "impact": f"Congestion ratio {congestion:.2f}",
                "severity": traffic_severity,
            },
            {
                "label": "Weather",
                "value": weather.title(),
                "impact": f"Surface {surface.replace('_', ' ')}, visibility {context['visibility_km']} km",
                "severity": _factor_severity(weather_risk, warn=0.30, danger=0.60),
            },
            {
                "label": "Road incident",
                "value": "Reported" if incident else "None",
                "impact": f"Incident severity {incident_severity:.2f}",
                "severity": "danger" if incident and incident_severity >= 0.55 else ("warning" if incident else "info"),
            },
            {
                "label": "Historical delay",
                "value": f"{round(historical_probability * 100)}%",
                "impact": "Probability from similar historical stops",
                "severity": _factor_severity(historical_probability, warn=0.28, danger=0.55),
            },
            {
                "label": "Time window",
                "value": f"{round(slack)} min slack",
                "impact": "Lower slack increases miss-window risk",
                "severity": "danger" if slack <= 30 else ("warning" if slack <= 75 else "info"),
            },
            {
                "label": "Load",
                "value": f"{package_count} packages / {round(package_weight, 1)} kg",
                "impact": "Heavier stops increase service pressure",
                "severity": "warning" if package_weight >= 120 else "info",
            },
        ]

    def get_realtime_features(
        self,
        courier_id: str,
        road_type: str,
        hour_of_day: int,
        day_of_week: int,
        weather_condition: str,
    ) -> dict:
        """
        Produce the aggregate feature dict used by ml/inference.py.
        """
        if not self._loaded or self._traffic is None or self._weather is None or self._hist is None:
            return {}

        rt_enc = _ROAD_ENC.get(road_type.lower(), 3)
        wc_enc = _WEATHER_ENC.get(weather_condition.lower(), 0)
        bucket = _bucket(hour_of_day)

        t_row = self._traffic[
            (self._traffic["road_type_enc"] == rt_enc)
            & (self._traffic["hour_of_day"] == hour_of_day)
        ]
        if t_row.empty:
            congestion_ratio_mean = float(self._traffic["congestion_ratio_mean"].mean())
            incident_rate = float(self._traffic["incident_rate"].mean())
        else:
            congestion_ratio_mean = float(t_row["congestion_ratio_mean"].iloc[0])
            incident_rate = float(t_row["incident_rate"].iloc[0])

        w_row = self._weather[self._weather["_wc_enc"] == wc_enc]
        if w_row.empty:
            road_surface_condition_enc = 0
            delay_risk_score_mean = 0.25
        else:
            road_surface_condition_enc = int(w_row["road_surface_condition_enc"].iloc[0])
            delay_risk_score_mean = float(w_row["delay_risk_score_mean"].iloc[0])

        tl_enc = _TRAFFIC_ENC.get(
            "high" if congestion_ratio_mean < 0.55 else "moderate",
            3,
        )
        h_row = self._hist[
            (self._hist["_rt"] == rt_enc)
            & (self._hist["_tl"] == tl_enc)
            & (self._hist["_wc"] == wc_enc)
            & (self._hist["time_bucket"] == bucket)
        ]
        if h_row.empty:
            hist_delay_probability = 0.25
            hist_median_delay_min = 10.0
        else:
            hist_delay_probability = float(h_row["hist_delay_probability"].iloc[0])
            hist_median_delay_min = float(h_row["hist_median_delay_min"].iloc[0])

        all_couriers = {c.get("courier_id", ""): c for c in courier_store.get_all()}
        courier = all_couriers.get(courier_id) or all_couriers.get(f"courier-{courier_id}")
        courier_lat = float(courier["latitude"]) if courier else None
        courier_lon = float(courier["longitude"]) if courier else None

        return {
            "congestion_ratio_mean": round(congestion_ratio_mean, 4),
            "incident_rate": round(incident_rate, 4),
            "road_surface_condition_enc": road_surface_condition_enc,
            "delay_risk_score_mean": round(delay_risk_score_mean, 4),
            "hist_delay_probability": round(hist_delay_probability, 4),
            "hist_slack_min": round(max(0.0, 60.0 - hist_median_delay_min), 2),
            "road_type": road_type,
            "hour_of_day": hour_of_day,
            "day_of_week": day_of_week,
            "weather_condition": weather_condition,
            "is_rush_hour": int(7 <= hour_of_day <= 9 or 16 <= hour_of_day <= 19),
            "courier_lat": courier_lat,
            "courier_lon": courier_lon,
        }


pipeline = DataPipeline()
