"""
verify_path_alternatives.py
---------------------------
Checks that scenario reoptimization now evaluates backend route candidates that
include road-path alternatives, not only stop-order changes.

Requires backend running at http://localhost:8000.
"""

from __future__ import annotations

import sys
from typing import Any

import requests


API = "http://localhost:8000/api/v1"
results: list[bool] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    tag = "PASS" if condition else "FAIL"
    print(f"  [{tag}] {label}{' -- ' + detail if detail else ''}")
    results.append(condition)
    return condition


def build_dispatch() -> dict[str, Any]:
    couriers = requests.get(f"{API}/couriers", timeout=10).json()
    stop_pool = requests.get(f"{API}/stop-pool", timeout=10).json()
    active = couriers[:2]
    selected = stop_pool[: len(active) * 5]
    per_courier = len(selected) // len(active)
    body = {
        "depot_latitude": 39.75,
        "depot_longitude": 37.015,
        "date": "2026-05-15",
        "time_limit_seconds": 15,
        "use_p90": False,
        "generate_explanation": False,
        "assignments": [
            {
                "courier_id": courier["id"],
                "stop_pool_ids": [
                    stop["id"]
                    for stop in selected[index * per_courier : (index + 1) * per_courier]
                ],
            }
            for index, courier in enumerate(active)
        ],
    }
    response = requests.post(f"{API}/auto-dispatch", json=body, timeout=60)
    response.raise_for_status()
    return response.json()


def scenario_stop(stop: dict[str, Any], index: int) -> dict[str, Any]:
    fields = [
        "planned_travel_min",
        "road_type",
        "traffic_level",
        "weather_condition",
        "congestion_ratio_mean",
        "incident_rate",
        "road_surface_condition",
        "road_incident",
        "incident_severity",
        "precipitation_mm",
        "wind_speed_kmh",
        "visibility_km",
        "package_count",
        "package_weight_kg",
    ]
    payload = {
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
    }
    payload.update({field: stop.get(field) for field in fields if stop.get(field) is not None})
    return payload


def main() -> None:
    print("=" * 72)
    print("  Path Alternative Candidate Verification")
    print("=" * 72)
    try:
        requests.get(f"{API}/health", timeout=5).raise_for_status()
        dispatch = build_dispatch()
    except Exception as exc:
        print(f"  [FAIL] Backend unavailable or dispatch failed: {exc}")
        sys.exit(1)

    stops = [stop for stop in dispatch["optimized_route"] if stop["vehicle_id"] == 0]
    scenario_stops = [scenario_stop(stop, index) for index, stop in enumerate(stops)]
    first, second = scenario_stops[0], scenario_stops[1]
    body = {
        "depot_latitude": 39.75,
        "depot_longitude": 37.015,
        "stops": scenario_stops,
        "controls": {
            "traffic_density": 20,
            "accident_severity": 0,
            "weather_condition": "clear",
            "weather_severity": 0,
            "road_disruption": 0,
            "package_load": 50,
            "dispatch_hour": 9,
            "conservative_mode": False,
        },
        "segment_overrides": [
            {
                "from_stop_id": str(first["stop_id"]),
                "to_stop_id": str(second["stop_id"]),
                "traffic_density": 100,
                "accident_severity": 90,
                "road_closure": True,
                "weather_severity": 0,
                "speed_reduction": 80,
                "extra_delay_min": 25,
                "risk_level": "critical",
                "priority": 50,
                "road_type": "urban",
            }
        ],
        "time_limit_seconds": 15,
    }
    response = requests.post(f"{API}/scenario/reoptimize", json=body, timeout=90)
    check("HTTP 200", response.status_code == 200, f"status={response.status_code}")
    if response.status_code != 200:
        print(response.text[:500])
        sys.exit(1)

    data = response.json()
    candidates = data.get("candidate_routes", [])
    selected_id = data.get("selected_candidate_id")
    selected = next((candidate for candidate in candidates if candidate.get("candidate_id") == selected_id), {})
    remaining_ids = {str(stop["stop_id"]) for stop in scenario_stops}
    selected_ids = {str(stop.get("stop_id")) for stop in data.get("scenario_route", [])}

    check("at least three backend candidates evaluated", data.get("candidates_evaluated", 0) >= 3)
    check("path alternatives counted", data.get("path_alternatives_evaluated", 0) >= max(1, len(stops) - 1))
    check("selected candidate exists", bool(selected_id and selected))
    check("selected candidate validates stop set", selected.get("validation", {}).get("valid") is True, str(selected.get("validation")))
    check("recommended route preserves remaining stops", selected_ids == remaining_ids, f"{selected_ids} vs {remaining_ids}")
    check("change type is explicit", data.get("change_type") in {"path_changed", "order_changed", "both_changed", "cost_changed", "no_better_route"})

    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
