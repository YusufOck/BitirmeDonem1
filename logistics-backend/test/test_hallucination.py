import pytest
from ai.explainer import _check_grounding

def test_check_grounding_valid():
    parsed = {
        "stop_alerts": [{"stop_name": "Stop A", "message": "Delay"}],
        "overall_assessment": "Good"
    }
    route = [{"stop_name": "Stop A"}, {"stop_name": "Stop B"}]
    res = _check_grounding(parsed, route)
    assert res["hallucination_risk"] == "low"
    assert res["should_answer"] is True

def test_check_grounding_invalid():
    parsed = {
        "stop_alerts": [{"stop_name": "Stop Z", "message": "Delay"}],
        "overall_assessment": "Good"
    }
    route = [{"stop_name": "Stop A"}, {"stop_name": "Stop B"}]
    res = _check_grounding(parsed, route)
    assert res["hallucination_risk"] == "high"
    assert res["should_answer"] is False
    assert "HALLUCINATION BLOCKED" in res["overall_assessment"]
