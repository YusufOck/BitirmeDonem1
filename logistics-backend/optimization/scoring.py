"""
Explicit route-cost scoring helpers.

The optimizer should not be a black box that only says "optimized".  This
module keeps the cost formula in one place so API responses, tests, and
documentation can point to the same math.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


COST_SCALE = 100
ROAD_CLOSURE_COST_MIN = 100_000.0

TRAFFIC_LEVEL_PENALTY = {
    "low": 0.00,
    "moderate": 0.06,
    "high": 0.16,
    "congested": 0.34,
}

WEATHER_PENALTY_MIN = {
    "clear": 0.0,
    "cloudy": 0.4,
    "wind": 0.8,
    "fog": 1.4,
    "rain": 1.7,
    "snow": 2.6,
}

RISK_LEVEL_PENALTY_MIN = {
    "low": 0.0,
    "medium": 1.2,
    "high": 3.0,
}


@dataclass(frozen=True)
class LegCostBreakdown:
    from_node: int
    to_node: int
    base_travel_min: float
    predicted_delay_min: float
    traffic_penalty_min: float
    accident_penalty_min: float
    road_closure_penalty_min: float
    weather_penalty_min: float
    risk_penalty_min: float
    priority_penalty_min: float
    constraint_penalty_min: float
    total_cost_min: float
    total_cost_units: int
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _traffic_penalty(stop: dict[str, Any], base_travel_min: float) -> tuple[float, str | None]:
    level = str(stop.get("traffic_level") or "moderate").lower()
    ratio = stop.get("congestion_ratio_mean")
    if ratio is not None:
        try:
            congestion_pressure = max(0.0, 0.72 - float(ratio))
            penalty = base_travel_min * congestion_pressure * 0.55
            if penalty > 0.05:
                return penalty, f"traffic speed ratio {float(ratio):.2f}"
            return 0.0, None
        except (TypeError, ValueError):
            pass

    multiplier = TRAFFIC_LEVEL_PENALTY.get(level, TRAFFIC_LEVEL_PENALTY["moderate"])
    penalty = base_travel_min * multiplier
    return penalty, f"traffic level {level}" if penalty > 0.05 else None


def _weather_penalty(stop: dict[str, Any]) -> tuple[float, str | None]:
    condition = str(stop.get("weather_condition") or "clear").lower()
    penalty = WEATHER_PENALTY_MIN.get(condition, 0.4)
    visibility = stop.get("visibility_km")
    wind = stop.get("wind_speed_kmh")
    precipitation = stop.get("precipitation_mm")
    try:
        if visibility is not None and float(visibility) < 5:
            penalty += 1.2
        if wind is not None and float(wind) >= 45:
            penalty += 0.8
        if precipitation is not None and float(precipitation) >= 10:
            penalty += 0.8
    except (TypeError, ValueError):
        pass
    return penalty, f"weather {condition}" if penalty > 0.05 else None


def _accident_penalty(stop: dict[str, Any]) -> tuple[float, str | None]:
    incident = int(_num_or_default(stop.get("road_incident"), 0))
    severity = _num_or_default(stop.get("incident_severity"), 0.0)
    rate = _num_or_default(stop.get("incident_rate"), 0.0)
    penalty = incident * 4.0 + severity * 14.0 + rate * 3.0
    return penalty, f"incident severity {severity:.2f}" if penalty > 0.05 else None


def _priority_penalty(stop: dict[str, Any]) -> tuple[float, str | None]:
    priority = stop.get("priority")
    if priority is None:
        return 0.0, None
    try:
        priority_value = max(0.0, min(100.0, float(priority)))
    except (TypeError, ValueError):
        return 0.0, None
    penalty = (100.0 - priority_value) / 100.0 * 2.5
    return penalty, f"priority {priority_value:.0f}/100" if penalty > 0.05 else None


def _constraint_penalty(stop: dict[str, Any], prediction: dict[str, Any]) -> tuple[float, str | None]:
    slack = _num_or_default(stop.get("time_window_slack_min"), 480.0)
    expected_delay = _num_or_default(prediction.get("expected_delay_min"), 0.0)
    will_miss = bool(prediction.get("will_miss_window"))
    penalty = 0.0
    if will_miss:
        penalty += 5.0
    if expected_delay > slack:
        penalty += min(expected_delay - slack, 60.0) * 0.25
    return penalty, "time-window pressure" if penalty > 0.05 else None


def _num_or_default(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def score_leg(
    from_node: int,
    to_node: int,
    base_travel_min: float,
    destination_stop: dict[str, Any] | None,
    destination_prediction: dict[str, Any] | None,
    use_p90: bool = False,
) -> LegCostBreakdown:
    """
    Score one route leg.

    Formula:

    total_cost =
        base_travel_time
        + predicted_delay
        + traffic_penalty
        + accident_penalty
        + road_closure_penalty
        + weather_penalty
        + risk_penalty
        + priority_penalty
        + constraint_penalty
    """
    stop = destination_stop or {}
    pred = destination_prediction or {}

    base = max(_num_or_default(base_travel_min, 0.0), 0.0)
    road_closed = bool(stop.get("road_closure")) or base >= 9_999.0
    reasons: list[str] = []

    if road_closed:
        closure_penalty = ROAD_CLOSURE_COST_MIN
        reasons.append("road closure or blocked matrix edge")
    else:
        closure_penalty = 0.0

    if destination_stop is None:
        predicted_delay = 0.0
        traffic_penalty = 0.0
        accident_penalty = 0.0
        weather_penalty = 0.0
        risk_penalty = 0.0
        priority_penalty = 0.0
        constraint_penalty = 0.0
    else:
        delay_value = pred.get("delay_p90_min") if use_p90 else pred.get("expected_delay_min")
        if delay_value is None:
            delay_value = pred.get("expected_delay_min", 0.0)
        predicted_delay = max(_num_or_default(delay_value, 0.0), 0.0)

        traffic_penalty, reason = _traffic_penalty(stop, base)
        if reason:
            reasons.append(reason)
        accident_penalty, reason = _accident_penalty(stop)
        if reason:
            reasons.append(reason)
        weather_penalty, reason = _weather_penalty(stop)
        if reason:
            reasons.append(reason)
        risk = str(pred.get("risk_level") or "low").lower()
        risk_penalty = RISK_LEVEL_PENALTY_MIN.get(risk, 0.0)
        if risk_penalty:
            reasons.append(f"ML risk {risk}")
        priority_penalty, reason = _priority_penalty(stop)
        if reason:
            reasons.append(reason)
        constraint_penalty, reason = _constraint_penalty(stop, pred)
        if reason:
            reasons.append(reason)

    total = (
        base
        + predicted_delay
        + traffic_penalty
        + accident_penalty
        + closure_penalty
        + weather_penalty
        + risk_penalty
        + priority_penalty
        + constraint_penalty
    )
    units = int(round(total * COST_SCALE))

    return LegCostBreakdown(
        from_node=from_node,
        to_node=to_node,
        base_travel_min=round(base, 4),
        predicted_delay_min=round(predicted_delay, 4),
        traffic_penalty_min=round(traffic_penalty, 4),
        accident_penalty_min=round(accident_penalty, 4),
        road_closure_penalty_min=round(closure_penalty, 4),
        weather_penalty_min=round(weather_penalty, 4),
        risk_penalty_min=round(risk_penalty, 4),
        priority_penalty_min=round(priority_penalty, 4),
        constraint_penalty_min=round(constraint_penalty, 4),
        total_cost_min=round(total, 4),
        total_cost_units=units,
        reasons=reasons,
    )


def route_cost_from_sequence(
    cost_matrix: list[list[int]],
    sequence: list[int],
) -> float:
    """Return total arc cost in minutes for a node sequence including depot."""
    if len(sequence) < 2:
        return 0.0
    total_units = 0
    for from_node, to_node in zip(sequence, sequence[1:]):
        total_units += int(cost_matrix[from_node][to_node])
    return round(total_units / COST_SCALE, 4)


def sequence_edges(sequence: list[int]) -> list[tuple[int, int]]:
    return list(zip(sequence, sequence[1:]))
