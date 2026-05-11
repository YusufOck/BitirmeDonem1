from api.routes import ApplyRecommendationRequest, _resolve_route_stop_ids, _validate_recommendation_payload
from api.simulation_routes import _build_cumulative, _order_stops_by_route_progress
from ml.inference import predict_route
from optimization.scoring import score_leg
from optimization.vrp_solver import solve_vrp


def _stop(**overrides):
    base = {
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
    base.update(overrides)
    return base


def test_ml_delay_increases_under_extreme_scenario():
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

    assert extreme_summary["expected_total_delay_min"] > normal_summary["expected_total_delay_min"]
    assert extreme_summary["route_delay_probability"] > normal_summary["route_delay_probability"]
    assert any(stop["calibration_applied"] for stop in extreme_result["stop_predictions"])


def test_cost_scoring_uses_delay_and_road_condition_penalties():
    low = score_leg(
        0,
        1,
        12.0,
        {"traffic_level": "low", "weather_condition": "clear", "road_incident": 0},
        {"expected_delay_min": 1.0, "risk_level": "low", "will_miss_window": False},
    )
    high = score_leg(
        0,
        1,
        12.0,
        {
            "traffic_level": "congested",
            "weather_condition": "snow",
            "road_incident": 1,
            "incident_severity": 0.8,
            "priority": 20,
        },
        {"expected_delay_min": 8.0, "risk_level": "high", "will_miss_window": True},
    )

    assert high.total_cost_min > low.total_cost_min
    assert high.predicted_delay_min > low.predicted_delay_min
    assert high.accident_penalty_min > 0
    assert high.traffic_penalty_min > 0
    assert high.risk_penalty_min > 0


def test_vrp_preserves_all_required_stops():
    cost_matrix = [
        [0, 100, 120, 140],
        [100, 0, 80, 90],
        [120, 80, 0, 70],
        [140, 90, 70, 0],
    ]
    time_windows = [(0, 10_000_000)] * 4
    result = solve_vrp(cost_matrix, time_windows, time_limit_seconds=2)
    visited = [node for node in result["routes"][0] if node != 0]

    assert result["status"] == "success"
    assert result["dropped_nodes"] == []
    assert set(visited) == {1, 2, 3}


def test_recommendation_validation_rejects_completed_or_unexpected_stops():
    valid = _validate_recommendation_payload(
        ApplyRecommendationRequest(
            active_stop_ids=["A", "B", "C"],
            completed_stop_ids=["A"],
            recommended_stop_ids=["B", "C"],
        )
    )
    assert valid["valid"] is True

    completed_reused = _validate_recommendation_payload(
        ApplyRecommendationRequest(
            active_stop_ids=["A", "B", "C"],
            completed_stop_ids=["A"],
            recommended_stop_ids=["A", "B", "C"],
        )
    )
    assert completed_reused["valid"] is False
    assert "A" in completed_reused["unexpected_stop_ids"] or completed_reused["errors"]

    missing_and_unexpected = _validate_recommendation_payload(
        ApplyRecommendationRequest(
            active_stop_ids=["A", "B", "C"],
            completed_stop_ids=["A"],
            recommended_stop_ids=["B", "X"],
        )
    )
    assert missing_and_unexpected["valid"] is False
    assert missing_and_unexpected["missing_stop_ids"] == ["C"]
    assert missing_and_unexpected["unexpected_stop_ids"] == ["X"]


def test_recommendation_stop_aliases_resolve_to_route_stop_ids():
    class DummyStop:
        def __init__(self, stop_id, name):
            self.id = stop_id
            self.name = name

    db_stops = [
        DummyStop(1581, "STP-00001"),
        DummyStop(1582, "STP-00002"),
        DummyStop(1583, "STP-00003"),
    ]

    resolved, unknown = _resolve_route_stop_ids(db_stops, ["3", "STP-00001", "1582"])

    assert unknown == []
    assert resolved == ["1583", "1581", "1582"]


def test_simulation_orders_next_stops_by_route_geometry_progress():
    coords = [
        [37.000, 39.000],
        [37.010, 39.000],
        [37.020, 39.000],
        [37.030, 39.000],
    ]
    cumulative = _build_cumulative(coords)
    shuffled_stops = [
        {"stop_id": "far", "lat": 39.000, "lon": 37.030},
        {"stop_id": "near", "lat": 39.000, "lon": 37.010},
        {"stop_id": "middle", "lat": 39.000, "lon": 37.020},
    ]

    ordered = _order_stops_by_route_progress(coords, cumulative, shuffled_stops)

    assert [stop["stop_id"] for stop in ordered] == ["near", "middle", "far"]
