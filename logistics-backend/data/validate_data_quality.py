"""
Validate logistics CSV quality and compatibility.

This script does not pretend synthetic data is real. It reports mismatches,
impossible values, and schema gaps so model reports can be defended honestly.
Outputs are written to evaluation_results/.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw_data"
OUT_DIR = ROOT / "evaluation_results"


REQUIRED_COLUMNS = {
    "route_stops.csv": {
        "route_id", "stop_sequence", "stop_id", "latitude", "longitude",
        "distance_from_prev_km", "road_type", "planned_arrival", "actual_arrival",
        "time_window_open", "time_window_close", "planned_travel_min",
        "actual_travel_min", "delay_at_stop_min", "missed_time_window",
        "package_count", "package_weight_kg",
    },
    "routes.csv": {
        "route_id", "num_stops", "planned_duration_min", "actual_duration_min",
        "total_delay_min", "weather_condition", "traffic_level",
        "road_incident", "incident_severity", "overall_delay_factor",
    },
    "traffic_segments.csv": {
        "segment_id", "road_type", "hour_of_day", "free_flow_speed_kmh",
        "current_speed_kmh", "congestion_ratio", "traffic_level",
        "incident_reported", "timestamp",
    },
    "weather_observations.csv": {
        "obs_id", "timestamp", "weather_condition", "precipitation_mm",
        "wind_speed_kmh", "visibility_km", "road_surface_condition",
        "delay_risk_score",
    },
    "historical_delay_stats.csv": {
        "road_type", "traffic_level", "weather_condition", "time_bucket",
        "sample_count", "median_delay_min", "p90_delay_min",
        "delay_probability",
    },
}


def _issue(file: str, check: str, severity: str, count: int, detail: str) -> dict:
    return {
        "file": file,
        "check": check,
        "severity": severity,
        "count": int(count),
        "detail": detail,
    }


def _read_csv(name: str, **kwargs) -> pd.DataFrame:
    path = RAW_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Missing required CSV: {path}")
    return pd.read_csv(path, **kwargs)


def validate() -> tuple[pd.DataFrame, dict]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    issues: list[dict] = []
    frames = {}

    for filename, required in REQUIRED_COLUMNS.items():
        frame = _read_csv(filename)
        frames[filename] = frame
        missing = sorted(required - set(frame.columns))
        if missing:
            issues.append(_issue(filename, "required_columns", "error", len(missing), f"Missing columns: {missing}"))
        duplicate_count = int(frame.duplicated().sum())
        if duplicate_count:
            issues.append(_issue(filename, "duplicate_rows", "warning", duplicate_count, "Exact duplicate rows exist."))
        null_counts = frame.isna().sum()
        null_fields = {column: int(value) for column, value in null_counts.items() if value > 0}
        if null_fields:
            issues.append(_issue(filename, "missing_values", "warning", sum(null_fields.values()), str(null_fields)))

    stops = _read_csv(
        "route_stops.csv",
        parse_dates=["planned_arrival", "actual_arrival", "time_window_open", "time_window_close"],
    )
    routes = _read_csv("routes.csv")
    traffic = _read_csv("traffic_segments.csv")
    hist = _read_csv("historical_delay_stats.csv")

    negative_delay = int((stops["delay_at_stop_min"] < 0).sum())
    if negative_delay:
        issues.append(_issue(
            "route_stops.csv",
            "negative_delay",
            "warning",
            negative_delay,
            "Negative delay means early arrival; training clamps this to 0 but raw data should label it explicitly.",
        ))

    impossible_travel = int(((stops["planned_travel_min"] <= 0) | (stops["actual_travel_min"] <= 0)).sum())
    if impossible_travel:
        issues.append(_issue("route_stops.csv", "non_positive_travel_time", "error", impossible_travel, "Travel times must be positive."))

    planned_speed = stops["distance_from_prev_km"] / (stops["planned_travel_min"] / 60.0).clip(lower=0.01)
    speed_outliers = int(((planned_speed < 5) | (planned_speed > 130)).sum())
    if speed_outliers:
        issues.append(_issue(
            "route_stops.csv",
            "planned_speed_outliers",
            "warning",
            speed_outliers,
            "Planned speed outside 5-130 km/h suggests unit or synthetic-data issues.",
        ))

    agg = (
        stops.groupby("route_id")
        .agg(
            stop_delay_sum=("delay_at_stop_min", "sum"),
            planned_sum=("planned_travel_min", "sum"),
            actual_sum=("actual_travel_min", "sum"),
            stop_count=("stop_id", "count"),
        )
        .reset_index()
    )
    merged = agg.merge(
        routes[["route_id", "total_delay_min", "planned_duration_min", "actual_duration_min", "num_stops"]],
        on="route_id",
        how="inner",
    )
    consistency_checks = [
        ("route_total_delay_mismatch", "stop_delay_sum", "total_delay_min"),
        ("route_planned_duration_mismatch", "planned_sum", "planned_duration_min"),
        ("route_actual_duration_mismatch", "actual_sum", "actual_duration_min"),
        ("route_stop_count_mismatch", "stop_count", "num_stops"),
    ]
    for check_name, left, right in consistency_checks:
        diff = (merged[left] - merged[right]).abs()
        bad = int((diff > 1.0).sum())
        if bad:
            issues.append(_issue(
                "routes.csv",
                check_name,
                "error" if "duration" in check_name else "warning",
                bad,
                f"{bad}/{len(merged)} routes differ by more than 1.0 between route-level and stop-level data.",
            ))

    missed_rate = float(stops["missed_time_window"].mean())
    if missed_rate > 0.60:
        issues.append(_issue(
            "route_stops.csv",
            "missed_window_rate_high",
            "warning",
            round(missed_rate * len(stops)),
            f"Missed-window rate is {missed_rate:.1%}; this is unusually high and indicates stress-test/synthetic data.",
        ))

    congestion_invalid = int(((traffic["congestion_ratio"] < 0) | (traffic["congestion_ratio"] > 1)).sum())
    if congestion_invalid:
        issues.append(_issue("traffic_segments.csv", "invalid_congestion_ratio", "error", congestion_invalid, "congestion_ratio must be 0..1."))

    hist_invalid = int(((hist["delay_probability"] < 0) | (hist["delay_probability"] > 1)).sum())
    if hist_invalid:
        issues.append(_issue("historical_delay_stats.csv", "invalid_delay_probability", "error", hist_invalid, "delay_probability must be 0..1."))

    summary = {
        "route_count": int(stops["route_id"].nunique()),
        "stop_count": int(len(stops)),
        "delay_mean_min": round(float(stops["delay_at_stop_min"].clip(lower=0).mean()), 4),
        "delay_p95_min": round(float(stops["delay_at_stop_min"].clip(lower=0).quantile(0.95)), 4),
        "high_delay_count_ge_60": int((stops["delay_at_stop_min"] >= 60).sum()),
        "missed_window_rate": round(missed_rate, 4),
        "distance_planned_corr": round(float(stops["distance_from_prev_km"].corr(stops["planned_travel_min"])), 4),
        "issue_count": len(issues),
    }
    return pd.DataFrame(issues), summary


def write_report(issues: pd.DataFrame, summary: dict) -> None:
    issues.to_csv(OUT_DIR / "data_quality_issues.csv", index=False)
    lines = [
        "# Data Quality Report",
        "",
        "Generated by `data/validate_data_quality.py`.",
        "",
        "## Summary",
        "",
    ]
    for key, value in summary.items():
        lines.append(f"- `{key}`: {value}")

    lines.extend(["", "## Issues", ""])
    if issues.empty:
        lines.append("No blocking issues detected.")
    else:
        for _, row in issues.iterrows():
            lines.append(
                f"- **{row['severity'].upper()}** `{row['file']}` / `{row['check']}` "
                f"({row['count']}): {row['detail']}"
            )

    lines.extend([
        "",
        "## Canonical Segment Schema",
        "",
        "Use `data/canonical_route_segments_schema.csv` for future real road-segment data.",
        "The current CSVs are usable for controlled academic testing, but route-level totals are not a trusted source of truth.",
    ])
    (OUT_DIR / "data_quality_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    issues, summary = validate()
    write_report(issues, summary)
    print(f"Data quality report written to: {OUT_DIR}")
    print(pd.DataFrame([summary]).to_string(index=False))
    if not issues.empty:
        print(issues.to_string(index=False))


if __name__ == "__main__":
    main()
