"""Product-level backend verification for Smart Logistics.

This script checks the behaviors that matter for the demo claim:
ML scenario sensitivity, explicit route-cost scoring, stop preservation, and
recommendation validation. It is intentionally small and deterministic so it
can be run during presentations without depending on the frontend.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.routes import ApplyRecommendationRequest, _validate_recommendation_payload
from ml.inference import predict_route
from optimization.scoring import score_leg
from optimization.vrp_solver import solve_vrp


def _stop(**overrides):
    row = {
        "stop_sequence": 1,
        "planned_travel_min": 20.0,
        "distance_from_prev_km": 8.0,
        "time_window_slack_min": 60.0,
        "hist_delay_probability": 0.15,
        "traffic_level": "low",
        "weather_condition": "clear",
        "road_incident": 0,
        "incident_severity": 0.0,
        "package_weight_kg": 80.0,
        "vehicle_capacity_kg": 1000.0,
        "vehicle_type": "van",
        "road_type": "urban",
    }
    row.update(overrides)
    return row


def _check(name: str, ok: bool, detail: str) -> bool:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}: {detail}")
    return ok


def main() -> int:
    checks: list[bool] = []

    normal = [_stop(stop_sequence=1), _stop(stop_sequence=2)]
    extreme = [
        _stop(
            stop_sequence=1,
            traffic_level="congested",
            weather_condition="snow",
            road_incident=1,
            incident_severity=0.95,
            congestion_ratio_mean=0.2,
            overall_delay_factor=2.4,
            time_window_slack_min=10.0,
        ),
        _stop(
            stop_sequence=2,
            traffic_level="congested",
            weather_condition="snow",
            road_incident=1,
            incident_severity=0.95,
            congestion_ratio_mean=0.2,
            overall_delay_factor=2.4,
            time_window_slack_min=10.0,
        ),
    ]
    normal_summary = predict_route(normal)["route_summary"]
    extreme_result = predict_route(extreme)
    extreme_summary = extreme_result["route_summary"]
    checks.append(_check(
        "ML scenario sensitivity",
        extreme_summary["expected_total_delay_min"] > normal_summary["expected_total_delay_min"],
        f"normal={normal_summary['expected_total_delay_min']} min, "
        f"extreme={extreme_summary['expected_total_delay_min']} min",
    ))
    checks.append(_check(
        "ML safety calibration",
        any(stop["calibration_applied"] for stop in extreme_result["stop_predictions"]),
        "extreme scenario raises delay/risk floor",
    ))

    low_cost = score_leg(
        0, 1, 12.0,
        {"traffic_level": "low", "weather_condition": "clear", "road_incident": 0},
        {"expected_delay_min": 1.0, "risk_level": "low", "will_miss_window": False},
    )
    high_cost = score_leg(
        0, 1, 12.0,
        {"traffic_level": "congested", "weather_condition": "snow", "road_incident": 1, "incident_severity": 0.8},
        {"expected_delay_min": 8.0, "risk_level": "high", "will_miss_window": True},
    )
    checks.append(_check(
        "Route scoring sensitivity",
        high_cost.total_cost_min > low_cost.total_cost_min,
        f"low={low_cost.total_cost_min:.2f}, high={high_cost.total_cost_min:.2f}",
    ))

    vrp = solve_vrp(
        [
            [0, 100, 120, 140],
            [100, 0, 80, 90],
            [120, 80, 0, 70],
            [140, 90, 70, 0],
        ],
        [(0, 10_000_000)] * 4,
        time_limit_seconds=2,
    )
    visited = {node for node in vrp["routes"][0] if node != 0}
    checks.append(_check(
        "OR-Tools stop preservation",
        vrp["status"] == "success" and visited == {1, 2, 3} and not vrp["dropped_nodes"],
        f"visited={sorted(visited)}, dropped={vrp['dropped_nodes']}",
    ))

    invalid = _validate_recommendation_payload(
        ApplyRecommendationRequest(
            active_stop_ids=["A", "B", "C"],
            completed_stop_ids=["A"],
            recommended_stop_ids=["A", "B", "C"],
        )
    )
    checks.append(_check(
        "Completed-stop exclusion validation",
        invalid["valid"] is False,
        "; ".join(invalid["errors"]),
    ))

    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
