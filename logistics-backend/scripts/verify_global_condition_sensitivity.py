"""
verify_global_condition_sensitivity.py
--------------------------------------
Exercises /api/v1/scenario/reoptimize with one global condition changed at a
time. The key product invariant is that the displayed delay metric is stable:

    current route delay-risk under scenario
    vs
    recommended route delay-risk under the same scenario

The script also verifies that 0 is preserved as a valid value instead of being
replaced by backend defaults.

Usage:
    python scripts/verify_global_condition_sensitivity.py

Requires backend running at http://localhost:8000.
"""

from __future__ import annotations

import sys
from copy import deepcopy
from typing import Any

import requests


API = "http://localhost:8000/api/v1"
PASS = "PASS"
FAIL = "FAIL"

BASE_CONTROLS = {
    "traffic_density": 45,
    "accident_severity": 0,
    "weather_condition": "clear",
    "weather_severity": 10,
    "road_disruption": 0,
    "package_load": 50,
    "dispatch_hour": 9,
    "conservative_mode": False,
}


def check(label: str, condition: bool, detail: str = "") -> bool:
    tag = PASS if condition else FAIL
    line = f"  [{tag}] {label}"
    if detail:
        line += f" -- {detail}"
    print(line)
    return condition


def build_auto_dispatch() -> dict[str, Any]:
    couriers = requests.get(f"{API}/couriers", timeout=10).json()
    stop_pool = requests.get(f"{API}/stop-pool", timeout=10).json()
    active = couriers[:2]
    selected = stop_pool[: len(active) * 5]
    stops_per_courier = len(selected) // len(active)
    body = {
        "depot_latitude": 39.75,
        "depot_longitude": 37.015,
        "date": "2026-05-12",
        "time_limit_seconds": 15,
        "use_p90": False,
        "generate_explanation": False,
        "assignments": [
            {
                "courier_id": courier["id"],
                "stop_pool_ids": [
                    stop["id"]
                    for stop in selected[index * stops_per_courier : (index + 1) * stops_per_courier]
                ],
            }
            for index, courier in enumerate(active)
        ],
    }
    response = requests.post(f"{API}/auto-dispatch", json=body, timeout=45)
    response.raise_for_status()
    return response.json()


def scenario_stop(stop: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "stop_sequence": index + 1,
        "cumulative_delay_min": stop.get("cumulative_delay_min", 0),
        "prev_stop_delay_min": stop.get("prev_stop_delay_min", 0),
        "time_window_slack_min": stop.get("time_window_slack_min", 480),
        "hist_slack_min": stop.get("hist_slack_min", 55),
        "hist_delay_probability": stop.get("hist_delay_probability", 0.25),
        "latitude": stop["latitude"],
        "longitude": stop["longitude"],
        "stop_name": stop.get("stop_name", ""),
        "stop_id": stop.get("stop_id", ""),
        "planned_travel_min": stop.get("planned_travel_min"),
        "road_type": stop.get("road_type"),
        "traffic_level": stop.get("traffic_level"),
        "weather_condition": stop.get("weather_condition"),
        "congestion_ratio_mean": stop.get("congestion_ratio_mean"),
        "incident_rate": stop.get("incident_rate"),
        "road_surface_condition": stop.get("road_surface_condition"),
        "road_incident": stop.get("road_incident"),
        "incident_severity": stop.get("incident_severity"),
        "precipitation_mm": stop.get("precipitation_mm"),
        "wind_speed_kmh": stop.get("wind_speed_kmh"),
        "visibility_km": stop.get("visibility_km"),
        "package_count": stop.get("package_count"),
        "package_weight_kg": stop.get("package_weight_kg"),
    }


def run_scenario(stops: list[dict[str, Any]], controls: dict[str, Any]) -> dict[str, Any]:
    body = {
        "depot_latitude": 39.75,
        "depot_longitude": 37.015,
        "stops": [scenario_stop(stop, index) for index, stop in enumerate(stops)],
        "controls": controls,
        "segment_overrides": [],
        "time_limit_seconds": 15,
    }
    response = requests.post(f"{API}/scenario/reoptimize", json=body, timeout=45)
    response.raise_for_status()
    return response.json()


def current_delay(data: dict[str, Any]) -> float:
    return float(data["baseline_metrics"]["expected_delay_min"])


def recommended_delay(data: dict[str, Any]) -> float:
    return float(data["scenario_metrics"]["expected_delay_min"])


def assert_non_decreasing(label: str, rows: list[tuple[Any, float]], tolerance: float = 0.75) -> bool:
    ok = True
    for (prev_value, prev_delay), (next_value, next_delay) in zip(rows, rows[1:]):
        if next_delay + tolerance < prev_delay:
            ok = False
            print(
                f"    violation: {label} {prev_value}->{next_value} "
                f"delay {prev_delay:.2f}->{next_delay:.2f}"
            )
    values = ", ".join(f"{value}={delay:.1f}" for value, delay in rows)
    return check(f"{label} current-route delay is non-decreasing", ok, values)


def main() -> None:
    print("=" * 72)
    print("  Global Condition Sensitivity Verification")
    print("=" * 72)
    try:
        requests.get(f"{API}/health", timeout=5)
    except Exception as exc:
        print(f"  [FAIL] Cannot reach backend: {exc}")
        sys.exit(1)

    dispatch = build_auto_dispatch()
    stops = [stop for stop in dispatch["optimized_route"] if stop["vehicle_id"] == 0]
    print(f"  Loaded {len(stops)} vehicle-0 stops from auto-dispatch")

    checks: list[bool] = []

    zero_controls = deepcopy(BASE_CONTROLS)
    zero_controls.update({
        "traffic_density": 0,
        "accident_severity": 0,
        "weather_severity": 0,
        "road_disruption": 0,
        "package_load": 0,
        "dispatch_hour": 0,
    })
    zero_data = run_scenario(stops, zero_controls)
    echoed = zero_data.get("controls_applied", {})
    checks.append(check("traffic_density=0 preserved", echoed.get("traffic_density") == 0, str(echoed)))
    checks.append(check("accident_severity=0 preserved", echoed.get("accident_severity") == 0, str(echoed)))
    checks.append(check("weather_severity=0 preserved", echoed.get("weather_severity") == 0, str(echoed)))
    checks.append(check("road_disruption=0 preserved", echoed.get("road_disruption") == 0, str(echoed)))
    checks.append(check("package_load=0 preserved", echoed.get("package_load") == 0, str(echoed)))
    checks.append(check("dispatch_hour=0 preserved", echoed.get("dispatch_hour") == 0, str(echoed)))

    sweeps = {
        "traffic_density": [0, 30, 70, 100],
        "accident_severity": [0, 30, 70, 100],
        "weather_severity": [0, 30, 70, 100],
        "road_disruption": [0, 30, 70, 100],
        "package_load": [0, 30, 70, 100],
    }
    for key, values in sweeps.items():
        rows = []
        for value in values:
            controls = deepcopy(BASE_CONTROLS)
            controls[key] = value
            if key == "weather_severity":
                controls["weather_condition"] = "rain"
            data = run_scenario(stops, controls)
            rows.append((value, current_delay(data)))
            checks.append(check(
                f"{key}={value} uses same delay metric",
                data.get("debug_breakdown", {}).get("metric_definition", "").startswith("Predicted delay"),
                data.get("debug_breakdown", {}).get("metric_definition", ""),
            ))
        checks.append(assert_non_decreasing(key, rows))

    weather_rows = []
    for weather in ["clear", "rain", "snow", "fog", "wind"]:
        controls = deepcopy(BASE_CONTROLS)
        controls["weather_condition"] = weather
        controls["weather_severity"] = 70
        data = run_scenario(stops, controls)
        weather_rows.append((weather, current_delay(data)))
    clear_delay = dict(weather_rows)["clear"]
    severe_min = min(delay for weather, delay in weather_rows if weather != "clear")
    checks.append(check(
        "severe weather is not easier than clear weather",
        severe_min + 0.75 >= clear_delay,
        ", ".join(f"{weather}={delay:.1f}" for weather, delay in weather_rows),
    ))

    conservative_false = run_scenario(stops, BASE_CONTROLS)
    conservative_controls = deepcopy(BASE_CONTROLS)
    conservative_controls["conservative_mode"] = True
    conservative_true = run_scenario(stops, conservative_controls)
    checks.append(check(
        "conservative mode does not lower delay-risk",
        current_delay(conservative_true) + 0.75 >= current_delay(conservative_false),
        f"false={current_delay(conservative_false):.1f}, true={current_delay(conservative_true):.1f}",
    ))

    checks.append(check(
        "recommended delay is compared under same conditions",
        conservative_false.get("optimization_delta", {}).get("comparison_basis") == "same_updated_conditions",
        str(conservative_false.get("optimization_delta", {})),
    ))
    checks.append(check(
        "recalculate does not mutate active route before apply",
        conservative_false.get("debug_breakdown", {}).get("active_route_mutated_before_apply") is False,
        str(conservative_false.get("debug_breakdown", {})),
    ))

    passed = sum(checks)
    total = len(checks)
    print(f"\n{'=' * 72}")
    print(f"  {passed}/{total} checks passed")
    print("=" * 72)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
