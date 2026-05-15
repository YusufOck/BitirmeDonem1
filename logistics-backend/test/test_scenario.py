import pytest
# We mock out the internal matrix adjustment since it's an internal function in routes.py
from api.routes import _apply_scenario_to_stops, _evaluate_route_order
from api.schemas import ScenarioControls, SegmentConditionOverride
from ml.inference import predict_stop

def test_segment_override_model():
    override = SegmentConditionOverride(
        from_stop_id="A",
        to_stop_id="B",
        extra_delay_min=5.0
    )
    assert override.extra_delay_min == 5.0


def test_predict_stop_tolerates_optional_null_categorical_fields():
    result = predict_stop({
        "stop_sequence": 1,
        "planned_travel_min": 18.0,
        "distance_from_prev_km": 6.0,
        "time_window_slack_min": 55.0,
        "hist_delay_probability": 0.22,
        "traffic_level": "moderate",
        "weather_condition": "clear",
        "road_incident": 0,
        "incident_severity": 0.0,
        "package_weight_kg": 50.0,
        "vehicle_type": None,
        "road_surface_condition": None,
    })

    assert "expected_delay_min" in result
    assert result["expected_delay_min"] >= 0


def _sample_stop(stop_id="STP-001", sequence=1, slack=45.0):
    return {
        "stop_id": stop_id,
        "stop_name": stop_id,
        "latitude": 39.75 + sequence * 0.001,
        "longitude": 37.01 + sequence * 0.001,
        "stop_sequence": sequence,
        "planned_travel_min": 8.0,
        "distance_from_prev_km": 2.2,
        "time_window_slack_min": slack,
        "hist_slack_min": slack - 5.0,
        "hist_delay_probability": 0.0,
        "delay_risk_score_mean": 0.0,
        "incident_rate": 0.0,
        "traffic_level": "low",
        "weather_condition": "clear",
        "road_incident": 0,
        "incident_severity": 0.0,
        "package_count": 1,
        "package_weight_kg": 0.0,
        "vehicle_capacity_kg": 1000.0,
        "depot_to_stop_travel_min": 8.0,
        "stop_to_depot_travel_min": 8.0,
    }


def test_scenario_zero_values_are_preserved_as_real_inputs():
    controls = ScenarioControls(
        traffic_density=0,
        accident_severity=0,
        weather_condition="clear",
        weather_severity=0,
        road_disruption=0,
        package_load=0,
        dispatch_hour=0,
    )

    modified, _ = _apply_scenario_to_stops([_sample_stop()], controls)
    stop = modified[0]

    assert stop["traffic_level"] == "low"
    assert stop["congestion_ratio_mean"] == pytest.approx(0.95)
    assert stop["hour_of_day"] == 0
    assert stop["road_incident"] == 0
    assert stop["incident_severity"] == 0
    assert stop["incident_rate"] == 0
    assert stop["hist_delay_probability"] == 0
    assert stop["delay_risk_score_mean"] == 0


def test_route_order_delay_metric_excludes_lateness_penalty():
    controls = ScenarioControls(
        traffic_density=100,
        accident_severity=70,
        weather_condition="rain",
        weather_severity=70,
        road_disruption=40,
        package_load=80,
        dispatch_hour=17,
    )
    stops, _ = _apply_scenario_to_stops(
        [_sample_stop("STP-001", 1, slack=5.0), _sample_stop("STP-002", 2, slack=5.0)],
        controls,
    )
    matrix = [
        [0.0, 6.0],
        [6.0, 0.0],
    ]

    result = _evaluate_route_order(stops, [1, 2], matrix)
    summary = result["summary"]

    assert summary["total_schedule_delay_min"] > 0
    assert summary["total_operational_delay_min"] == summary["total_ml_delay_min"]
    for stop in result["route"]:
        assert stop["expected_delay_min"] == stop["ml_delay_min"]
        assert stop["operational_delay_min"] == stop["ml_delay_min"]
