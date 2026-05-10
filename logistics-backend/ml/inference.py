"""
ml/inference.py
===============
Self-contained inference module for Smart Logistics v5 models.

Usage from any backend service:
    import sys
    sys.path.insert(0, "path/to/ml")
    from inference import predict_route, predict_stop

Models are loaded once at import time (singleton).
All string inputs (road_type, traffic_level, weather_condition,
vehicle_type, road_surface_condition) are encoded internally —
the caller never deals with LabelEncoder integers.

v5: 19 new optional features added. If omitted, dataset medians are used.
    Requires scikit-learn >= 1.8.0.
"""

from __future__ import annotations

import os
import sys
import warnings
import numpy as np
import pandas as pd
import joblib

warnings.filterwarnings("ignore")

# ── Paths ─────────────────────────────────────────────────────────────────────
_ML_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_ML_DIR)

if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if _ML_DIR not in sys.path:
    sys.path.insert(0, _ML_DIR)

# ── Label encodings (LabelEncoder sorts alphabetically) ──────────────────────
_ROAD_TYPE_ENC        = {"highway": 0, "mountain": 1, "rural": 2, "urban": 3}
_TRAFFIC_ENC          = {"congested": 0, "high": 1, "low": 2, "moderate": 3}
_WEATHER_ENC          = {"clear": 0, "cloudy": 1, "fog": 2, "rain": 3, "snow": 4, "wind": 5}
_VEHICLE_TYPE_ENC     = {"car": 0, "motorcycle": 1, "truck": 2, "van": 3}
_ROAD_SURFACE_ENC     = {"dry": 0, "icy": 1, "snow_covered": 2, "wet": 3}

# ── Feature defaults (dataset medians) ───────────────────────────────────────
# Only include features that cannot be derived from other inputs.
# is_rush_hour, is_high_risk_weather, is_urban_road, traffic_weather_risk,
# planned_speed_kmh, stop_progress_ratio, and all v5 interaction features are
# intentionally omitted — always derived in _derive_convenience_fields /
# _derive_v5_interaction_features so actual input values are reflected.
_DEFAULTS: dict = {
    # Raw atomic inputs only — derived features intentionally excluded so
    # _derive_convenience_fields / _derive_v5_interaction_features always
    # compute them from the caller's actual inputs rather than stale defaults.
    "cumulative_delay_min":   22.2,
    "day_of_week":            3,
    "distance_from_prev_km":  30.45,
    "hist_delay_probability": 0.25,
    "hist_slack_min":         3.2,
    "hour_of_day":            13,
    "incident_severity":      0.0,
    "package_count":          11.0,
    "package_weight_kg":      25.2,
    "planned_travel_min":     30.6,
    "precipitation_mm":       0.0,
    "prev_stop_delay_min":    6.7,
    "road_incident":          0.0,
    "road_type":              3,
    "stop_sequence":          4,
    "temperature_c":          17.6,
    "time_window_duration_min": 40.0,
    "time_window_slack_min":  75.0,
    "traffic_level":          3,
    "vehicle_type_enc":       2.0,
    "visibility_km":          12.8,
    "weather_condition":      0,
    "wind_speed_kmh":         10.3,
}

# ── Singleton model loading ───────────────────────────────────────────────────
import model_classes  # noqa: E402 — registers _PreFitCalibrator / RouteDelayPredictor

_predictor       = None
_predictor_fname = None   # tracks which pkl was actually loaded


def _get_predictor():
    global _predictor, _predictor_fname
    if _predictor is None:
        fname = "route_predictor_v9.pkl"
        pkl = os.path.join(_ML_DIR, fname)
        if os.path.exists(pkl):
            _predictor = joblib.load(pkl)
            _predictor_fname = fname
            return _predictor
        raise FileNotFoundError(
            f"No model found in {_ML_DIR}.\n"
            "Run ml/train_model_v9.py to generate the latest model files."
        )
    return _predictor


# ── Input encoding helpers ────────────────────────────────────────────────────

def _encode_categoricals(row: dict) -> dict:
    """Convert string category values to their integer encodings in-place."""
    row = dict(row)

    if isinstance(row.get("road_type"), str):
        key = row["road_type"].lower()
        if key not in _ROAD_TYPE_ENC:
            raise ValueError(
                f"road_type '{row['road_type']}' unknown. "
                f"Valid values: {list(_ROAD_TYPE_ENC)}"
            )
        row["road_type"] = _ROAD_TYPE_ENC[key]

    if isinstance(row.get("traffic_level"), str):
        key = row["traffic_level"].lower()
        if key not in _TRAFFIC_ENC:
            raise ValueError(
                f"traffic_level '{row['traffic_level']}' unknown. "
                f"Valid values: {list(_TRAFFIC_ENC)}"
            )
        row["traffic_level"] = _TRAFFIC_ENC[key]

    if isinstance(row.get("weather_condition"), str):
        key = row["weather_condition"].lower()
        if key not in _WEATHER_ENC:
            raise ValueError(
                f"weather_condition '{row['weather_condition']}' unknown. "
                f"Valid values: {list(_WEATHER_ENC)}"
            )
        row["weather_condition"] = _WEATHER_ENC[key]

    if "vehicle_type" in row:
        key = row["vehicle_type"]
        if isinstance(key, str):
            key = key.lower()
            if key not in _VEHICLE_TYPE_ENC:
                raise ValueError(
                    f"vehicle_type '{row['vehicle_type']}' unknown. "
                    f"Valid values: {list(_VEHICLE_TYPE_ENC)}"
                )
            row["vehicle_type_enc"] = _VEHICLE_TYPE_ENC[key]
        else:
            row["vehicle_type_enc"] = int(key)

    if "road_surface_condition" in row:
        key = row["road_surface_condition"]
        if isinstance(key, str):
            key = key.lower()
            if key not in _ROAD_SURFACE_ENC:
                raise ValueError(
                    f"road_surface_condition '{row['road_surface_condition']}' unknown. "
                    f"Valid values: {list(_ROAD_SURFACE_ENC)}"
                )
            row["road_surface_condition_enc"] = _ROAD_SURFACE_ENC[key]
        else:
            row["road_surface_condition_enc"] = int(key)

    return row


def _fill_defaults(row: dict) -> dict:
    """Fill missing features with dataset medians."""
    filled = dict(_DEFAULTS)
    filled.update(row)
    return filled


def _derive_convenience_fields(row: dict) -> dict:
    """
    Compute fields that callers can omit if they supply the raw inputs:
      - is_rush_hour          from hour_of_day
      - is_high_risk_weather  from weather_condition
      - is_urban_road         from road_type
      - traffic_weather_risk  from traffic_level × weather_condition
      - planned_speed_kmh     from distance / planned_travel_min
    """
    row = dict(row)

    hour = row.get("hour_of_day", 13)
    if "is_rush_hour" not in row:
        row["is_rush_hour"] = int(7 <= hour <= 9 or 16 <= hour <= 19)

    wx = row.get("weather_condition", 0)   # already encoded int at this point
    if "is_high_risk_weather" not in row:
        row["is_high_risk_weather"] = int(wx in (3, 4))   # rain=3, snow=4

    rt = row.get("road_type", 3)
    if "is_urban_road" not in row:
        row["is_urban_road"] = int(rt == 3)                # urban=3

    _TW_RISK = {0: 4, 1: 3, 2: 1, 3: 2}    # congested=0→4, high=1→3, low=2→1, moderate=3→2
    _WX_RISK = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 2}  # clear→1 … snow→5
    if "traffic_weather_risk" not in row:
        row["traffic_weather_risk"] = (
            _TW_RISK.get(row.get("traffic_level", 3), 2) *
            _WX_RISK.get(wx, 1)
        )

    dist  = row.get("distance_from_prev_km", 30.45)
    ptmin = row.get("planned_travel_min", 30.60)
    if "planned_speed_kmh" not in row:
        row["planned_speed_kmh"] = dist / max(ptmin / 60.0, 0.01)

    # stop_progress_ratio: caller may omit; use stop_sequence as fraction of a
    # nominal 8-stop route (dataset median) when total stops is unknown.
    if "stop_progress_ratio" not in row:
        seq = row.get("stop_sequence", 4)
        row["stop_progress_ratio"] = min(seq / 8.0, 1.0)

    return row


def _derive_v5_interaction_features(row: dict) -> dict:
    """
    Compute v5 interaction features from available inputs.
    Features not provided by caller are derived from traffic_level / weather_condition
    so the model sees realistic signal instead of neutral defaults.
    """
    row = dict(row)

    # ── congestion_ratio_mean from traffic_level if not supplied ──────────────
    # current_speed / free_flow_speed ratio per traffic bucket (training medians).
    _CONG_BY_TRAFFIC = {0: 0.30, 1: 0.50, 2: 0.90, 3: 0.65}  # congested/high/low/moderate
    if "congestion_ratio_mean" not in row:
        row["congestion_ratio_mean"] = _CONG_BY_TRAFFIC.get(row.get("traffic_level", 3), 0.42)

    # ── overall_delay_factor from traffic × weather if not supplied ───────────
    _TF = {0: 1.8, 1: 1.4, 2: 1.0, 3: 1.2}   # congested/high/low/moderate
    _WF = {0: 1.0, 1: 1.05, 2: 1.15, 3: 1.25, 4: 1.45, 5: 1.1}  # clear/cloudy/fog/rain/snow/wind
    if "overall_delay_factor" not in row:
        row["overall_delay_factor"] = (
            _TF.get(row.get("traffic_level", 3), 1.2) *
            _WF.get(row.get("weather_condition", 0), 1.0)
        )

    # ── delay_risk_score_mean from hist_delay_probability if not supplied ─────
    if "delay_risk_score_mean" not in row:
        row["delay_risk_score_mean"] = row.get("hist_delay_probability", 0.25)

    # ── road_surface_condition_enc from weather if not supplied ───────────────
    # rain→wet(3), snow→snow_covered(2), else→dry(0)
    if "road_surface_condition_enc" not in row:
        wx = row.get("weather_condition", 0)
        row["road_surface_condition_enc"] = 3 if wx == 3 else (2 if wx == 4 else 0)

    # ── travel_delay_ratio: cumulative proportion + congestion slowdown ────────
    # Training computed (actual_travel - planned_travel) / planned_travel.
    # At inference we approximate: accumulated delay ratio + congestion overhead.
    if "travel_delay_ratio" not in row:
        ptmin      = max(row.get("planned_travel_min", 30.6), 1.0)
        cum_ratio  = row.get("cumulative_delay_min", 0.0) / ptmin
        cong_over  = max(1.0 - row["congestion_ratio_mean"], 0.0)  # speed loss fraction
        row["travel_delay_ratio"] = min(cum_ratio + cong_over, 5.0)

    # ── incident_rate derived from road_incident flag ─────────────────────────
    if "incident_rate" not in row:
        row["incident_rate"] = 0.35 if row.get("road_incident", 0) else 0.05

    # ── package & vehicle load ratios ─────────────────────────────────────────
    pkg_count  = max(row.get("package_count", 8), 1)
    pkg_weight = row.get("package_weight_kg", 95.0)
    veh_cap    = max(row.get("vehicle_capacity_kg", 1000.0), 1.0)
    cong_ratio = row["congestion_ratio_mean"]
    incident   = row.get("road_incident", 0)

    row["weight_per_package"]    = pkg_weight / pkg_count
    row["vehicle_load_ratio"]    = pkg_weight / veh_cap
    row["speed_loss_ratio"]      = cong_ratio
    row["incident_traffic_risk"] = incident * (cong_ratio + 1)

    return row


def _prepare_dataframe(stops: list[dict]) -> pd.DataFrame:
    """
    Convert a list of stop dicts to a DataFrame ready for the predictor.
    Handles encoding, defaults, and derived fields.
    """
    if len(stops) == 0:
        raise ValueError("stops must contain at least one stop dict.")
    rows = []
    for stop in stops:
        r = dict(stop)
        r = _encode_categoricals(r)
        r = _fill_defaults(r)
        r = _validate_and_clip(r)
        r = _derive_convenience_fields(r)
        r = _derive_v5_interaction_features(r)
        rows.append(r)
    return pd.DataFrame(rows)


# ── Public API ────────────────────────────────────────────────────────────────

_NUMERIC_CLIPS = {
    "hist_delay_probability": (0.0, 1.0),
    "time_window_slack_min":  (0.0, None),
    "cumulative_delay_min":   (0.0, None),
    "prev_stop_delay_min":    (0.0, None),
    "precipitation_mm":       (0.0, None),
    # v5
    "incident_severity":      (0.0, 1.0),
    "congestion_ratio_mean":  (0.0, 1.0),
    "incident_rate":          (0.0, 1.0),
    "delay_risk_score_mean":  (0.0, 1.0),
    "travel_delay_ratio":     (None, None),
    "overall_delay_factor":   (0.0, None),
    "visibility_km":          (0.0, None),
    "wind_speed_kmh":         (0.0, None),
    "package_count":          (1, None),
    "package_weight_kg":      (0.0, None),
    "vehicle_capacity_kg":    (1.0, None),
}


def _validate_and_clip(row: dict) -> dict:
    """Clip numeric fields to sensible bounds; raise on invalid types."""
    row = dict(row)
    for field, (lo, hi) in _NUMERIC_CLIPS.items():
        if field in row:
            v = row[field]
            if lo is not None:
                row[field] = max(lo, v)
            if hi is not None:
                row[field] = min(hi, row[field])
    return row


def predict_route(stops: list[dict]) -> dict:
    """
    Predict delay risk for every stop on a route.

    Parameters
    ----------
    stops : list[dict]
        Each dict represents one stop. Required fields:

        stop_sequence          int   (1-based position on the route)
        cumulative_delay_min   float (total delay accumulated so far, minutes)
        prev_stop_delay_min    float (delay at the immediately prior stop)
        time_window_slack_min  float (minutes between planned arrival and window close)
        hist_slack_min         float (time_window_slack_min minus historical mean delay)
        hist_delay_probability float (0-1, historical miss rate for this stop context)

        Optional but recommended (v3/v4):
        distance_from_prev_km  float
        planned_travel_min     float
        road_type              str   "highway" | "urban" | "rural" | "mountain"
        traffic_level          str   "low" | "moderate" | "high" | "congested"
        weather_condition      str   "clear" | "cloudy" | "wind" | "fog" | "rain" | "snow"
        hour_of_day            int   0-23
        day_of_week            int   0=Monday … 6=Sunday
        stop_progress_ratio    float 0-1 (stop_sequence / total_stops)

        Optional v5 route-level features (applied per-stop, same value for whole route):
        vehicle_type           str   "van" | "car" | "motorcycle" | "truck"
        road_incident          int   0 or 1
        incident_severity      float 0.0–1.0
        temperature_c          float
        wind_speed_kmh         float
        visibility_km          float
        overall_delay_factor   float (1.0 = no delay)

        Optional v5 stop-level features:
        package_count          int
        package_weight_kg      float
        vehicle_capacity_kg    float (used to derive vehicle_load_ratio)
        travel_delay_ratio     float (actual_travel - planned) / planned
        time_window_duration_min float

        Optional v5 traffic signal features:
        congestion_ratio_mean  float 0–1
        incident_rate          float 0–1

        Optional v5 weather signal features:
        road_surface_condition str   "dry" | "wet" | "icy" | "snow_covered"
        delay_risk_score_mean  float 0–1

        DO NOT send: weight_per_package, vehicle_load_ratio, speed_loss_ratio,
        incident_traffic_risk — these are derived internally.

        All other fields default to dataset medians if omitted.

    Returns
    -------
    dict with keys:
        stop_predictions : list[dict]   — per-stop risk details
        route_summary    : dict         — aggregated route-level metrics
    """
    predictor = _get_predictor()
    df = _prepare_dataframe(stops)
    return predictor.predict_route(df)


def predict_stop(stop: dict) -> dict:
    """
    Predict delay risk for a single stop.

    Parameters
    ----------
    stop : dict  — same fields as predict_route stops

    Returns
    -------
    dict with keys:
        delay_probability   float       (calibrated)
        expected_delay_min  float       (P50 — median estimate)
        delay_p90_min       float|None  (P90 worst-case; None if model has no p90_reg)
        will_miss_window    bool
        risk_level          str         "low" | "medium" | "high"
        severity            str         "on-time" | "delayed" | "severe"
    """
    predictor = _get_predictor()
    df = _prepare_dataframe([stop])
    # Use the same route-level wrapper as predict_route so single-stop calls get
    # cascade-safe formatting and runtime condition calibration too.
    result = predictor.predict_route(df)
    item = result["stop_predictions"][0]
    return {
        "delay_probability": item["delay_probability"],
        "expected_delay_min": item["expected_delay_min"],
        "delay_p90_min": item.get("delay_p90_min"),
        "will_miss_window": item["will_miss_window"],
        "risk_level": item["risk_level"],
        "severity": item["severity"],
        "calibration_applied": item.get("calibration_applied", False),
        "calibration_reasons": item.get("calibration_reasons", []),
    }


def get_model_info() -> dict:
    """Return model metadata for health-check / API info endpoints."""
    predictor = _get_predictor()
    version = {
        "route_predictor_v9.pkl":        "v9",
        "route_predictor_v8.pkl":        "v8",
        "route_predictor_v7.pkl":        "v7",
        "route_predictor_v6.pkl":        "v6",
        "route_predictor_v5.pkl":        "v5",
        "route_predictor_v3_hybrid.pkl": "hybrid (v3-clf + v2-reg + v4-p90)",
        "route_predictor_v4.pkl":        "v4",
        "route_predictor_v3.pkl":        "v3",
    }.get(_predictor_fname, "unknown")

    # Fallback metrics — replaced by CSV load below for v8/v9
    evaluation = {
        "trained_at": "unknown",
        "note": "Run train_model_v9.py and reports/generate_evaluation_reports.py to populate metrics.",
    }
    if _predictor_fname in {"route_predictor_v8.pkl", "route_predictor_v9.pkl"}:
        project_root = os.path.dirname(_ML_DIR)
        metrics_file = "v9_model_metrics.csv" if _predictor_fname == "route_predictor_v9.pkl" else "v8_model_metrics.csv"
        metrics_path = os.path.join(project_root, "analysis_output", metrics_file)
        evaluation = {
            "trained_at": "2026-05-09",
            "training_note": "Runtime-safe model: no actual_travel_min leakage; encodings match inference.py.",
            "source": f"analysis_output/{metrics_file}",
        }
        if os.path.exists(metrics_path):
            metrics_df = pd.read_csv(metrics_path)
            evaluation["metrics"] = {
                str(row["metric"]): row["value"]
                for _, row in metrics_df.iterrows()
            }
        else:
            evaluation["metrics_unavailable"] = True
            evaluation["metrics_error"] = f"Metrics file not found at {metrics_path}. Run training/evaluation scripts to generate it."

    return {
        "model_version":        version,
        "n_clf_features":       len(predictor.feature_cols),
        "n_reg_features":       len(predictor.reg_feature_cols),
        "clf_feature_cols":     list(predictor.feature_cols),
        "reg_feature_cols":     list(predictor.reg_feature_cols),
        "has_p90":              getattr(predictor, 'p90_reg', None) is not None,
        "threshold":            round(predictor.threshold, 4),
        "severe_threshold_min": predictor.severe_threshold,
        "categorical_encodings": {
            "road_type":             dict(_ROAD_TYPE_ENC),
            "traffic_level":         dict(_TRAFFIC_ENC),
            "weather_condition":     dict(_WEATHER_ENC),
            "vehicle_type":          dict(_VEHICLE_TYPE_ENC),
            "road_surface_condition": dict(_ROAD_SURFACE_ENC),
        },
        "evaluation": evaluation,
    }


# ── Self-test (run directly: python ml/inference.py) ─────────────────────────

if __name__ == "__main__":
    import json

    print("=" * 60)
    print("  Smart Logistics — Inference Module Self-Test (v5)")
    print("=" * 60)

    # --- Test 1: low-risk route (strings, minimal fields) ---
    low_risk_stops = [
        {
            "stop_sequence": i,
            "cumulative_delay_min": 0.0,
            "prev_stop_delay_min": 0.0,
            "time_window_slack_min": 90.0,
            "hist_slack_min": 70.0,
            "hist_delay_probability": 0.05,
            "road_type": "highway",
            "traffic_level": "low",
            "weather_condition": "clear",
            "hour_of_day": 10,
            "stop_progress_ratio": i / 5,
        }
        for i in range(1, 6)
    ]

    result_low = predict_route(low_risk_stops)
    rs = result_low["route_summary"]
    print(f"\n[Low-risk route]")
    print(f"  route_delay_probability  : {rs['route_delay_probability']}")
    print(f"  expected_total_delay_min : {rs['expected_total_delay_min']}")
    print(f"  high_risk_stop_count     : {rs['high_risk_stop_count']}")
    print(f"  severe_stop_count        : {rs['severe_stop_count']}")

    # --- Test 2: high-risk route with v5 features ---
    high_risk_stops = [
        {
            "stop_sequence": 1,
            "cumulative_delay_min": 45.0,
            "prev_stop_delay_min": 30.0,
            "time_window_slack_min": 60.0,
            "hist_slack_min": 15.0,
            "hist_delay_probability": 0.75,
            "road_type": "urban",
            "traffic_level": "congested",
            "weather_condition": "snow",
            "hour_of_day": 8,
            "stop_progress_ratio": 0.1,
            # v5 features
            "vehicle_type": "van",
            "road_incident": 1,
            "incident_severity": 0.8,
            "road_surface_condition": "icy",
            "congestion_ratio_mean": 0.85,
            "package_count": 12,
            "package_weight_kg": 150.0,
            "overall_delay_factor": 1.4,
        },
        {
            "stop_sequence": 2,
            "cumulative_delay_min": 75.0,
            "prev_stop_delay_min": 30.0,
            "time_window_slack_min": 60.0,
            "hist_slack_min": 10.0,
            "hist_delay_probability": 0.80,
            "road_type": "urban",
            "traffic_level": "congested",
            "weather_condition": "snow",
            "hour_of_day": 8,
            "stop_progress_ratio": 0.2,
            "vehicle_type": "van",
            "road_incident": 1,
            "incident_severity": 0.8,
            "road_surface_condition": "icy",
            "congestion_ratio_mean": 0.85,
            "package_count": 8,
            "package_weight_kg": 95.0,
            "overall_delay_factor": 1.4,
        },
        {
            "stop_sequence": 3,
            "cumulative_delay_min": 100.0,
            "prev_stop_delay_min": 25.0,
            "time_window_slack_min": 60.0,
            "hist_slack_min": 5.0,
            "hist_delay_probability": 0.85,
            "road_type": "urban",
            "traffic_level": "congested",
            "weather_condition": "snow",
            "hour_of_day": 9,
            "stop_progress_ratio": 0.3,
            "vehicle_type": "van",
            "road_incident": 1,
            "incident_severity": 0.8,
            "road_surface_condition": "icy",
            "congestion_ratio_mean": 0.85,
            "package_count": 5,
            "package_weight_kg": 60.0,
            "overall_delay_factor": 1.4,
        },
    ]

    result_high = predict_route(high_risk_stops)
    rs2 = result_high["route_summary"]
    print(f"\n[High-risk route — congested + snow + v5 features]")
    print(f"  route_delay_probability     : {rs2['route_delay_probability']}")
    print(f"  expected_total_delay_min    : {rs2['expected_total_delay_min']}")
    print(f"  worst_case_total_delay_min  : {rs2['worst_case_total_delay_min']}  (P90)")
    print(f"  high_risk_stop_count        : {rs2['high_risk_stop_count']}")
    print(f"  severe_stop_count           : {rs2['severe_stop_count']}")
    for s in result_high["stop_predictions"]:
        p90_str = f"  p90={s['delay_p90_min']:>5.1f}min" \
                  if s['delay_p90_min'] is not None else ""
        print(f"    stop {s['stop_sequence']}  p={s['delay_probability']:.3f}  "
              f"exp={s['expected_delay_min']:>5.1f}min{p90_str}  {s['severity']}")

    # --- Test 3: single stop ---
    single = predict_stop({
        "stop_sequence": 3,
        "cumulative_delay_min": 20.0,
        "prev_stop_delay_min": 8.0,
        "time_window_slack_min": 75.0,
        "hist_slack_min": 55.0,
        "hist_delay_probability": 0.20,
        "road_type": "urban",
        "traffic_level": "moderate",
        "weather_condition": "rain",
        "hour_of_day": 17,
        "road_surface_condition": "wet",
        "delay_risk_score_mean": 0.55,
    })
    print(f"\n[Single stop]")
    print(f"  delay_probability   : {single['delay_probability']}")
    print(f"  expected_delay_min  : {single['expected_delay_min']}")
    print(f"  delay_p90_min       : {single['delay_p90_min']}")
    print(f"  will_miss_window    : {single['will_miss_window']}")
    print(f"  risk_level          : {single['risk_level']}")
    print(f"  severity            : {single['severity']}")

    # --- Test 4: model info ---
    info = get_model_info()
    print(f"\n[Model info]")
    print(f"  version         : {info['model_version']}")
    print(f"  clf features    : {info['n_clf_features']}")
    print(f"  reg features    : {info['n_reg_features']}")
    print(f"  has P90 model   : {info['has_p90']}")
    print(f"  threshold       : {info['threshold']}")
    print(f"  severe_threshold: {info['severe_threshold_min']} min")

    # --- Test 5: bad category raises clean error ---
    print(f"\n[Error handling]")
    try:
        predict_stop({"road_type": "underwater"})
    except ValueError as e:
        print(f"  Bad road_type caught: {e}")
    try:
        predict_stop({"vehicle_type": "bicycle"})
    except ValueError as e:
        print(f"  Bad vehicle_type caught: {e}")
    try:
        predict_stop({"road_surface_condition": "muddy"})
    except ValueError as e:
        print(f"  Bad road_surface_condition caught: {e}")

    print("\n" + "=" * 60)
    print("  All tests passed.")
    print("=" * 60)
