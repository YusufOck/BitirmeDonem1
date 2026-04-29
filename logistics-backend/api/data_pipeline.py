"""
api/data_pipeline.py
--------------------
V7 merge pipeline'ının inference-time karşılığı.

Startup'ta 5 CSV'yi RAM'e yükler.
Re-opt tetiklenince courier'ın anlık durumuna göre
ML feature dict'i döndürür — DB veya external API çağrısı yok.

Kullanım:
    from api.data_pipeline import pipeline
    features = pipeline.get_realtime_features(
        courier_id="courier-0",
        road_type="urban",
        hour_of_day=14,
        day_of_week=2,
        weather_condition="rain",
    )
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

from api.location_store import courier_store

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw_data")

_ROAD_ENC    = {"highway": 0, "mountain": 1, "rural": 2, "urban": 3}
_TRAFFIC_ENC = {"congested": 0, "high": 1, "low": 2, "moderate": 3}
_WEATHER_ENC = {"clear": 0, "cloudy": 1, "fog": 2, "rain": 3, "snow": 4, "wind": 5}
_SURF_ENC    = {"dry": 0, "icy": 1, "snow_covered": 2, "wet": 3}

def _bucket(hour: int) -> str:
    if 7 <= hour <= 9:           return "morning_rush"
    if 16 <= hour <= 19:         return "evening_rush"
    if hour >= 22 or hour <= 5:  return "night"
    return "midday"


class DataPipeline:
    def __init__(self):
        self._traffic: pd.DataFrame | None = None
        self._weather: pd.DataFrame | None = None
        self._hist:    pd.DataFrame | None = None
        self._loaded = False

    def load(self):
        """Startup'ta bir kere çağrılır."""
        try:
            traffic = pd.read_csv(f"{_DATA_DIR}/traffic_segments.csv")
            weather = pd.read_csv(f"{_DATA_DIR}/weather_observations.csv")
            hist    = pd.read_csv(f"{_DATA_DIR}/historical_delay_stats.csv")

            # Traffic aggregate: road_type × hour_of_day
            traffic["road_type_enc"] = traffic["road_type"].str.lower().map(_ROAD_ENC)
            self._traffic = (
                traffic.groupby(["road_type_enc", "hour_of_day"])
                .agg(
                    congestion_ratio_mean=("congestion_ratio", "mean"),
                    incident_rate=("incident_reported", "mean"),
                )
                .reset_index()
            )

            # Weather aggregate: weather_condition
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

            # Historical delay stats aggregate
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
        except FileNotFoundError as e:
            print(f"[DataPipeline] CSV bulunamadı, pipeline devre dışı: {e}")

    def get_realtime_features(
        self,
        courier_id: str,
        road_type: str,
        hour_of_day: int,
        day_of_week: int,
        weather_condition: str,
    ) -> dict:
        """
        V7 merge pipeline'ıyla anlık feature dict üretir.

        Parameters
        ----------
        courier_id       : "courier-0" veya "0"
        road_type        : "urban" | "highway" | "rural" | "mountain"
        hour_of_day      : 0-23
        day_of_week      : 0=Pazartesi … 6=Pazar
        weather_condition: "clear" | "cloudy" | "rain" | "snow" | "fog" | "wind"

        Returns
        -------
        dict — inference.py'ın anlayacağı feature key'leri
        """
        if not self._loaded:
            return {}

        rt_enc = _ROAD_ENC.get(road_type.lower(), 3)
        wc_enc = _WEATHER_ENC.get(weather_condition.lower(), 0)
        bucket = _bucket(hour_of_day)

        # ── Traffic lookup ────────────────────────────────────────────────────
        t_row = self._traffic[
            (self._traffic["road_type_enc"] == rt_enc) &
            (self._traffic["hour_of_day"]   == hour_of_day)
        ]
        if t_row.empty:
            congestion_ratio_mean = float(self._traffic["congestion_ratio_mean"].mean())
            incident_rate         = float(self._traffic["incident_rate"].mean())
        else:
            congestion_ratio_mean = float(t_row["congestion_ratio_mean"].iloc[0])
            incident_rate         = float(t_row["incident_rate"].iloc[0])

        # ── Weather lookup ────────────────────────────────────────────────────
        w_row = self._weather[self._weather["_wc_enc"] == wc_enc]
        if w_row.empty:
            road_surface_condition_enc = 0
            delay_risk_score_mean      = 0.25
        else:
            road_surface_condition_enc = int(w_row["road_surface_condition_enc"].iloc[0])
            delay_risk_score_mean      = float(w_row["delay_risk_score_mean"].iloc[0])

        # ── Historical delay lookup ───────────────────────────────────────────
        tl_enc = _TRAFFIC_ENC.get(
            "high" if congestion_ratio_mean > 0.4 else "moderate", 3
        )
        h_row = self._hist[
            (self._hist["_rt"]         == rt_enc) &
            (self._hist["_tl"]         == tl_enc) &
            (self._hist["_wc"]         == wc_enc) &
            (self._hist["time_bucket"] == bucket)
        ]
        if h_row.empty:
            hist_delay_probability = 0.25
            hist_median_delay_min  = 10.0
        else:
            hist_delay_probability = float(h_row["hist_delay_probability"].iloc[0])
            hist_median_delay_min  = float(h_row["hist_median_delay_min"].iloc[0])

        # ── Courier GPS ───────────────────────────────────────────────────────
        all_c    = {c.get("courier_id", ""): c for c in courier_store.get_all()}
        courier  = all_c.get(courier_id) or all_c.get(f"courier-{courier_id}")
        courier_lat = float(courier["latitude"])  if courier else None
        courier_lon = float(courier["longitude"]) if courier else None

        return {
            # Traffic features
            "congestion_ratio_mean":     round(congestion_ratio_mean, 4),
            "incident_rate":             round(incident_rate, 4),
            # Weather features
            "road_surface_condition_enc": road_surface_condition_enc,
            "delay_risk_score_mean":     round(delay_risk_score_mean, 4),
            # Historical features
            "hist_delay_probability":    round(hist_delay_probability, 4),
            "hist_slack_min":            round(max(0.0, 60.0 - hist_median_delay_min), 2),
            # Context
            "road_type":                 road_type,
            "hour_of_day":              hour_of_day,
            "day_of_week":              day_of_week,
            "weather_condition":        weather_condition,
            "is_rush_hour":             int(7 <= hour_of_day <= 9 or 16 <= hour_of_day <= 19),
            # Courier location (re-opt için yeni depot)
            "courier_lat":              courier_lat,
            "courier_lon":              courier_lon,
        }


pipeline = DataPipeline()
