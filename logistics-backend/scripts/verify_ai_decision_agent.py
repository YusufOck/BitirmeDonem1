"""
verify_ai_decision_agent.py
---------------------------
Checks that the decision agent selects only backend-generated candidates and
that the validator blocks invalid or non-improving recommendations.

Requires backend running at http://localhost:8000.
"""

from __future__ import annotations

import sys

import requests

from verify_path_alternatives import API, build_dispatch, scenario_stop, check, results


def main() -> None:
    print("=" * 72)
    print("  Guarded AI Decision Agent Verification")
    print("=" * 72)
    try:
        requests.get(f"{API}/health", timeout=5).raise_for_status()
        dispatch = build_dispatch()
    except Exception as exc:
        print(f"  [FAIL] Backend unavailable or dispatch failed: {exc}")
        sys.exit(1)

    stops = [stop for stop in dispatch["optimized_route"] if stop["vehicle_id"] == 0]
    body = {
        "depot_latitude": 39.75,
        "depot_longitude": 37.015,
        "stops": [scenario_stop(stop, index) for index, stop in enumerate(stops)],
        "controls": {
            "traffic_density": 80,
            "accident_severity": 20,
            "weather_condition": "rain",
            "weather_severity": 55,
            "road_disruption": 20,
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
    candidate_ids = {candidate.get("candidate_id") for candidate in data.get("candidate_routes", [])}
    decision = data.get("ai_decision", {})
    validation = data.get("validated_decision", {})
    selected = data.get("selected_candidate", {})
    current = next(
        (candidate for candidate in data.get("candidate_routes", []) if candidate.get("candidate_id") == "current_order_current_path"),
        {},
    )

    check("AI selected a generated candidate", decision.get("selected_route_id") in candidate_ids, str(decision))
    check("backend validation passed", validation.get("valid") is True, str(validation))
    check("selected candidate matches response", selected.get("candidate_id") == decision.get("selected_route_id"))
    if decision.get("decision") == "apply_recommendation":
        check(
            "apply decision is actually lower cost",
            float(selected.get("total_cost_min", 0)) < float(current.get("total_cost_min", 0)),
            f"selected={selected.get('total_cost_min')} current={current.get('total_cost_min')}",
        )
    else:
        check("keep-current decision reports no better route", data.get("no_better_route") is True or decision.get("decision") == "keep_current")
    check("facts_used includes alternatives count", decision.get("facts_used", {}).get("alternatives_evaluated", 0) >= 1)

    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
