"""
verify_segment_conditions.py
----------------------------
Verifies that localized traffic/accident/closure inputs affect only the edited
route segment candidate scoring instead of being treated as decorative UI data.

Requires backend running at http://localhost:8000.
"""

from __future__ import annotations

import sys

import requests

from verify_path_alternatives import API, build_dispatch, scenario_stop, check, results


def main() -> None:
    print("=" * 72)
    print("  Segment-Specific Condition Verification")
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
            "traffic_density": 0,
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
                "traffic_density": 90,
                "accident_severity": 70,
                "road_closure": False,
                "weather_severity": 0,
                "speed_reduction": 70,
                "extra_delay_min": 12,
                "risk_level": "high",
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
    current_candidate = next(
        (candidate for candidate in data.get("candidate_routes", []) if candidate.get("candidate_id") == "current_order_current_path"),
        {},
    )
    edited_leg = next(
        (
            leg for leg in current_candidate.get("selected_leg_paths", [])
            if str(leg.get("from_stop_id")) == str(first["stop_id"]) and str(leg.get("to_stop_id")) == str(second["stop_id"])
        ),
        {},
    )

    check("segment impact listed in factor impacts", any("Segment" in item.get("label", "") for item in data.get("factor_impacts", [])))
    check("edited segment has positive local penalty", float(edited_leg.get("segment_penalty_min", 0)) > 0, str(edited_leg))
    check("global traffic=0 preserved", data.get("controls_applied", {}).get("traffic_density") == 0, str(data.get("controls_applied")))
    check("active route not mutated before apply", data.get("debug_breakdown", {}).get("active_route_mutated_before_apply") is False)
    check("recommendation stop count equals input", len(data.get("scenario_route", [])) == len(scenario_stops))

    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
