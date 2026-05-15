"""
verify_live_reoptimization.py
-----------------------------
Simulates live reoptimization by removing a completed stop before calling the
scenario endpoint. The recommendation must contain exactly the remaining stops.

Requires backend running at http://localhost:8000.
"""

from __future__ import annotations

import sys

import requests

from verify_path_alternatives import API, build_dispatch, scenario_stop, check, results


def main() -> None:
    print("=" * 72)
    print("  Live Reoptimization Verification")
    print("=" * 72)
    try:
        requests.get(f"{API}/health", timeout=5).raise_for_status()
        dispatch = build_dispatch()
    except Exception as exc:
        print(f"  [FAIL] Backend unavailable or dispatch failed: {exc}")
        sys.exit(1)

    all_stops = [stop for stop in dispatch["optimized_route"] if stop["vehicle_id"] == 0]
    completed = all_stops[0]
    remaining = all_stops[1:]
    body = {
        "depot_latitude": float(completed["latitude"]),
        "depot_longitude": float(completed["longitude"]),
        "stops": [scenario_stop(stop, index) for index, stop in enumerate(remaining)],
        "controls": {
            "traffic_density": 70,
            "accident_severity": 30,
            "weather_condition": "rain",
            "weather_severity": 55,
            "road_disruption": 15,
            "package_load": 60,
            "dispatch_hour": 17,
            "conservative_mode": False,
        },
        "segment_overrides": [],
        "time_limit_seconds": 15,
    }
    response = requests.post(f"{API}/scenario/reoptimize", json=body, timeout=90)
    check("HTTP 200", response.status_code == 200, f"status={response.status_code}")
    if response.status_code != 200:
        print(response.text[:500])
        sys.exit(1)

    data = response.json()
    completed_id = str(completed["stop_id"])
    remaining_ids = {str(stop["stop_id"]) for stop in remaining}
    recommended_ids = {str(stop.get("stop_id")) for stop in data.get("scenario_route", [])}

    check("completed stop excluded", completed_id not in recommended_ids, f"completed={completed_id}, recommended={recommended_ids}")
    check("recommended route equals remaining stops", recommended_ids == remaining_ids, f"{recommended_ids} vs {remaining_ids}")
    check("segment count would be remaining-1", max(len(remaining) - 1, 0) == max(len(data.get("scenario_route", [])) - 1, 0))
    check("active route not mutated before apply", data.get("debug_breakdown", {}).get("active_route_mutated_before_apply") is False)
    check("candidate validation valid", data.get("validation", {}).get("valid") is True, str(data.get("validation")))

    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
