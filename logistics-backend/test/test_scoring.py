import pytest
from optimization.scoring import score_leg, COST_SCALE


def test_score_leg_no_penalty():
    stop = {
        "traffic_level": "low",
        "weather_condition": "clear",
    }
    pred = {
        "expected_delay_min": 0.0,
        "risk_level": "low",
    }
    cost = score_leg(
        from_node=0,
        to_node=1,
        base_travel_min=10.0,
        destination_stop=stop,
        destination_prediction=pred,
        use_p90=False,
    )
    # 10.0 min travel × COST_SCALE=100 → 1000 units
    assert cost.total_cost_units == 1000
    assert cost.total_cost_min == pytest.approx(10.0)
    assert cost.base_travel_min == 10.0


def test_score_leg_with_delay_and_traffic():
    stop = {
        "traffic_level": "high",
        "weather_condition": "clear",
    }
    pred = {
        "expected_delay_min": 3.5,
        "risk_level": "low",
    }
    cost = score_leg(0, 1, 10.0, stop, pred)
    assert cost.base_travel_min == 10.0
    assert cost.predicted_delay_min == 3.5
    assert cost.traffic_penalty_min > 0.0
    assert cost.total_cost_min == pytest.approx(10.0 + 3.5 + cost.traffic_penalty_min)


def test_score_leg_bare_minimum_no_stop_no_pred():
    cost = score_leg(
        from_node=0,
        to_node=1,
        base_travel_min=10.0,
        destination_stop=None,
        destination_prediction=None,
    )
    assert cost.total_cost_units == 1000
    assert cost.total_cost_min == pytest.approx(10.0)
    assert cost.traffic_penalty_min == 0.0
    assert cost.accident_penalty_min == 0.0
