"""
routes.py
---------
FastAPI router for the OR-Tools optimization endpoints.

Endpoints:
    POST /api/v1/full-route    — Mapbox Matrix → ML → OR-Tools → Mapbox Directions
    GET  /api/v1/model-info    — ML model health-check / metadata
    POST /api/v1/explain       — Ollama natural language explanation
    GET  /api/v1/explain/health — Ollama health-check
"""

from __future__ import annotations

import asyncio
import os
import re
from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from database import get_db

from api.schemas import (
    ExplainRequest,
    ExplanationResult,
    FullRouteResponse,
    OptimizeRequest,
    OptimizeResponse,
    SegmentConditionOverride,
    ScenarioImpactFactor,
    ScenarioReoptimizationResponse,
    ScenarioRouteRequest,
    VehicleRoute,
)
from optimization.route_optimizer import RouteOptimizer
from optimization.scoring import score_leg
from optimization.mapbox_adapter import mapbox_to_pipeline
from mapbox.matrix_api import get_weight_matrices
from mapbox.directions_api import get_final_route, get_route_alternatives
from mapbox.coordinate import Coordinate
from ml.inference import get_model_info, predict_route as predict_route_ml, predict_stop as predict_stop_ml
from ai.explainer import check_ollama_health, generate_explanation
from api.data_pipeline import pipeline
from api.fleet_store import fleet_store
from cache.redis_client import set_matrix_cache, set_matrix_index_map, set_route_state, set_stops_cache
from db.models import Courier, Route, RouteStatus, Stop, StopPool, StopStatus, User, UserRole
from db.scenario_models import Scenario
from api.schemas import ScenarioCreateRequest, ScenarioResponse

router = APIRouter(prefix="/api/v1")

# Singleton — RouteOptimizer is stateless; model is loaded once at import time.
_optimizer = RouteOptimizer()


async def _build_vehicle_routes(
    optimized_route: list[dict],
    depot_lat: float,
    depot_lon: float,
    token: str,
) -> list[VehicleRoute]:
    """
    Her vehicle için Mapbox Directions çağrısını paralel yapar ve VehicleRoute listesi döndürür.
    asyncio.to_thread ile sync requests.get event loop'u bloklamaz.
    """
    from collections import defaultdict
    groups: dict[int, list[dict]] = defaultdict(list)
    for stop in optimized_route:
        groups[stop["vehicle_id"]].append(stop)

    async def _fetch_vehicle(vid: int, stops: list[dict]) -> VehicleRoute:
        p90_values = [s["delay_p90_min"] for s in stops if s["delay_p90_min"] is not None]
        coords = [Coordinate(lat=depot_lat, lon=depot_lon, name="depot")]
        for s in stops:
            if s["latitude"] is not None and s["longitude"] is not None:
                coords.append(Coordinate(
                    lat=s["latitude"],
                    lon=s["longitude"],
                    name=s.get("stop_name") or f"stop_{s['optimized_position']}",
                ))

        route_data = await asyncio.to_thread(get_final_route, coords, token)
        if route_data is None:
            raise HTTPException(
                status_code=502,
                detail=f"Mapbox Directions API failed for vehicle {vid}",
            )

        return VehicleRoute(
            vehicle_id=vid,
            geometry=route_data["geometry"],
            distance_m=route_data["distance"],
            duration_s=route_data["duration"],
            stop_count=len(stops),
            stop_names=[s.get("stop_name") for s in stops],
            total_planned_travel_min=round(sum(s["planned_travel_min"] or 0.0 for s in stops), 2),
            total_expected_delay_min=round(sum(s["expected_delay_min"] for s in stops), 2),
            total_worst_case_delay_min=round(sum(p90_values), 2) if p90_values else None,
            high_risk_stop_count=sum(1 for s in stops if s["risk_level"] == "high"),
            severe_stop_count=sum(1 for s in stops if s["severity"] == "severe"),
        )

    tasks = [
        _fetch_vehicle(vid, sorted(groups[vid], key=lambda s: s["optimized_position"]))
        for vid in sorted(groups)
    ]
    return list(await asyncio.gather(*tasks))


def _traffic_level_from_density(density: int) -> str:
    if density >= 85:
        return "congested"
    if density >= 65:
        return "high"
    if density >= 35:
        return "moderate"
    return "low"


def _congestion_ratio_from_density(density: int) -> float:
    # Model feature means current_speed/free_flow_speed; lower means worse traffic.
    return round(max(0.15, min(0.95, 0.95 - (density / 100.0) * 0.80)), 4)


def _scenario_weather_features(condition: str, severity: int) -> dict:
    intensity = severity / 100.0
    if condition == "rain":
        return {
            "precipitation_mm": round(3.0 + intensity * 22.0, 2),
            "wind_speed_kmh": round(8.0 + intensity * 28.0, 1),
            "visibility_km": round(max(1.0, 14.0 - intensity * 10.0), 1),
            "road_surface_condition": "wet",
        }
    if condition == "snow":
        return {
            "precipitation_mm": round(2.0 + intensity * 18.0, 2),
            "wind_speed_kmh": round(10.0 + intensity * 34.0, 1),
            "visibility_km": round(max(0.6, 10.0 - intensity * 8.5), 1),
            "road_surface_condition": "snow_covered",
        }
    if condition == "fog":
        return {
            "precipitation_mm": round(intensity * 2.0, 2),
            "wind_speed_kmh": round(4.0 + intensity * 10.0, 1),
            "visibility_km": round(max(0.4, 8.0 - intensity * 7.0), 1),
            "road_surface_condition": "wet",
        }
    if condition == "wind":
        return {
            "precipitation_mm": 0.0,
            "wind_speed_kmh": round(15.0 + intensity * 55.0, 1),
            "visibility_km": round(max(4.0, 15.0 - intensity * 4.0), 1),
            "road_surface_condition": "dry",
        }
    return {
        "precipitation_mm": 0.0,
        "wind_speed_kmh": round(4.0 + intensity * 8.0, 1),
        "visibility_km": round(16.0 - intensity * 2.0, 1),
        "road_surface_condition": "dry",
    }


def _effective_weather_ratio(condition: str, severity: int) -> float:
    """Convert weather type + severity into actual disruption pressure.

    A weather severity slider at 70 with condition="clear" should not behave
    like a storm. Severity only becomes costly when the selected weather type
    actually creates road friction.
    """
    type_weight = {
        "clear": 0.0,
        "cloudy": 0.12,
        "wind": 0.45,
        "fog": 0.70,
        "rain": 0.65,
        "snow": 1.00,
    }.get(str(condition or "clear").lower(), 0.0)
    return (severity / 100.0) * type_weight


def _build_scenario_factors(controls, congestion_ratio: float, delay_factor: float) -> list[dict]:
    traffic_level = _traffic_level_from_density(controls.traffic_density)
    return [
        {
            "label": "Traffic density",
            "value": f"{controls.traffic_density}/100 ({traffic_level.title()})",
            "impact": f"Speed ratio set to {congestion_ratio:.2f}; lower ratio increases ML delay risk",
            "severity": "danger" if controls.traffic_density >= 80 else ("warning" if controls.traffic_density >= 55 else "info"),
        },
        {
            "label": "Accident severity",
            "value": f"{controls.accident_severity}/100",
            "impact": "Converted to road_incident and incident_severity features",
            "severity": "danger" if controls.accident_severity >= 65 else ("warning" if controls.accident_severity > 0 else "info"),
        },
        {
            "label": "Weather pressure",
            "value": f"{controls.weather_condition.title()} {controls.weather_severity}/100",
            "impact": "Updates precipitation, wind, visibility, surface, and weather risk",
            "severity": "danger" if controls.weather_condition in {"snow", "fog"} and controls.weather_severity >= 60 else ("warning" if controls.weather_severity >= 45 else "info"),
        },
        {
            "label": "Road disruption",
            "value": f"{controls.road_disruption}/100",
            "impact": "Adds disruption pressure to travel matrix and delay factor",
            "severity": "danger" if controls.road_disruption >= 70 else ("warning" if controls.road_disruption >= 35 else "info"),
        },
        {
            "label": "ML delay factor",
            "value": f"{delay_factor:.2f}x",
            "impact": "Combined factor sent to the trained delay model",
            "severity": "danger" if delay_factor >= 2.0 else ("warning" if delay_factor >= 1.45 else "info"),
        },
    ]


def _apply_scenario_to_stops(stops: list[dict], controls) -> tuple[list[dict], list[ScenarioImpactFactor]]:
    traffic_level = _traffic_level_from_density(controls.traffic_density)
    congestion_ratio = _congestion_ratio_from_density(controls.traffic_density)
    weather = _scenario_weather_features(controls.weather_condition, controls.weather_severity)
    accident_ratio = controls.accident_severity / 100.0
    weather_ratio = _effective_weather_ratio(controls.weather_condition, controls.weather_severity)
    disruption_ratio = controls.road_disruption / 100.0
    load_ratio = controls.package_load / 100.0
    load_multiplier = 0.65 + (controls.package_load / 100.0) * 1.25
    # This factor is an ML feature, not the full route cost. Keep it moderate so
    # road conditions do not get counted once here and again in the travel matrix.
    overall_delay_factor = (
        1.0
        + (controls.traffic_density / 100.0) * 0.22
        + accident_ratio * 0.24
        + weather_ratio * 0.14
        + disruption_ratio * 0.20
        + load_ratio * 0.16
    )

    scenario_factors = _build_scenario_factors(
        controls,
        congestion_ratio=congestion_ratio,
        delay_factor=overall_delay_factor,
    )

    modified: list[dict] = []
    for stop in stops:
        current = deepcopy(stop)
        base_hist = _float_or(current.get("hist_delay_probability"), 0.25)
        base_slack = _float_or(current.get("time_window_slack_min"), 75.0)
        package_weight = _float_or(current.get("package_weight_kg"), 25.0)
        package_count = _int_or(current.get("package_count"), 1)
        time_pressure = max(0.0, (90.0 - min(base_slack, 90.0)) / 90.0)
        stop_risk_amplifier = 1.0 + ((base_hist + time_pressure) / 2.0) * 0.45

        current.update({
            "traffic_level": traffic_level,
            "congestion_ratio_mean": congestion_ratio,
            "weather_condition": controls.weather_condition,
            "hour_of_day": controls.dispatch_hour,
            "is_rush_hour": int(7 <= controls.dispatch_hour <= 9 or 16 <= controls.dispatch_hour <= 19),
            "road_incident": int(controls.accident_severity > 0 or controls.road_disruption >= 55),
            "incident_severity": round(max(accident_ratio, disruption_ratio * 0.85), 3),
            "incident_rate": round(max(_float_or(current.get("incident_rate"), 0.05), accident_ratio * 0.75, disruption_ratio * 0.55), 4),
            "overall_delay_factor": round(overall_delay_factor * stop_risk_amplifier, 3),
            "hist_delay_probability": round(min(0.95, base_hist + accident_ratio * 0.22 + disruption_ratio * 0.18 + weather_ratio * 0.12 + load_ratio * 0.10), 4),
            "delay_risk_score_mean": round(min(0.98, _float_or(current.get("delay_risk_score_mean"), base_hist) + weather_ratio * 0.25 + accident_ratio * 0.15 + load_ratio * 0.08), 4),
            "time_window_slack_min": round(max(5.0, base_slack - controls.traffic_density * 0.10 - controls.road_disruption * 0.12), 2),
            "package_weight_kg": round(package_weight * load_multiplier, 2),
            "package_count": max(1, round(package_count * (0.75 + controls.package_load / 100.0))),
            "feature_source": "scenario_lab_ml_features",
            "delay_factors": scenario_factors,
            **weather,
        })
        modified.append(current)

    impacts = [
        ScenarioImpactFactor(
            label="Traffic density",
            before="CSV/Mapbox baseline",
            after=f"{controls.traffic_density}/100",
            impact="Changes traffic_level and congestion_ratio_mean before ML prediction.",
            severity="danger" if controls.traffic_density >= 80 else ("warning" if controls.traffic_density >= 55 else "info"),
        ),
        ScenarioImpactFactor(
            label="Accident severity",
            before="Route incident profile",
            after=f"{controls.accident_severity}/100",
            impact="Changes road_incident, incident_rate, and incident_severity.",
            severity="danger" if controls.accident_severity >= 65 else ("warning" if controls.accident_severity > 0 else "info"),
        ),
        ScenarioImpactFactor(
            label="Weather",
            before="Route weather profile",
            after=f"{controls.weather_condition} / {controls.weather_severity}/100",
            impact="Changes precipitation, wind, visibility, road surface, and weather risk.",
            severity="danger" if controls.weather_condition in {"snow", "fog"} and controls.weather_severity >= 60 else ("warning" if controls.weather_severity >= 45 else "info"),
        ),
        ScenarioImpactFactor(
            label="Package load",
            before="Stop package profile",
            after=f"{controls.package_load}/100",
            impact="Scales package weight and package count before ML scoring.",
            severity="warning" if controls.package_load >= 75 else "info",
        ),
    ]
    return modified, impacts


def _adjust_travel_matrix_for_scenario(matrix: list[list[float]], controls) -> list[list[float]]:
    weather_ratio = _effective_weather_ratio(controls.weather_condition, controls.weather_severity)
    factor = (
        1.0
        + (controls.traffic_density / 100.0) * 0.25
        + (controls.accident_severity / 100.0) * 0.16
        + (controls.road_disruption / 100.0) * 0.22
        + weather_ratio * 0.10
    )
    return [
        [
            0.0 if i == j else round(float(value) * factor, 3)
            for j, value in enumerate(row)
        ]
        for i, row in enumerate(matrix)
    ]


def _segment_override_factor(override: SegmentConditionOverride) -> float:
    risk_weight = {
        "low": 0.0,
        "medium": 0.12,
        "high": 0.28,
        "critical": 0.55,
    }.get(override.risk_level, 0.0)
    road_weight = {
        "highway": -0.05,
        "urban": 0.0,
        "rural": 0.08,
        "mountain": 0.18,
    }.get(override.road_type, 0.0)
    return max(
        0.25,
        1.0
        + (override.traffic_density / 100.0) * 0.70
        + (override.accident_severity / 100.0) * 0.85
        + (override.weather_severity / 100.0) * 0.35
        + (override.speed_reduction / 100.0) * 0.80
        + risk_weight
        + road_weight
        - ((override.priority - 50) / 100.0) * 0.12,
    )


def _apply_segment_overrides_to_matrix(
    matrix: list[list[float]],
    stops: list[dict],
    overrides: list[SegmentConditionOverride],
) -> tuple[list[list[float]], list[ScenarioImpactFactor]]:
    if not overrides:
        return matrix, []

    stop_index_by_id = {
        str(stop.get("stop_id")): index
        for index, stop in enumerate(stops)
        if stop.get("stop_id") is not None
    }
    next_matrix = [list(row) for row in matrix]
    impacts: list[ScenarioImpactFactor] = []

    for override in overrides:
        from_index = stop_index_by_id.get(str(override.from_stop_id))
        to_index = stop_index_by_id.get(str(override.to_stop_id))
        if from_index is None or to_index is None or from_index == to_index:
            continue

        if override.road_closure:
            adjusted = 9999.0
            severity = "danger"
            impact_text = "Road closure makes this segment almost impossible unless no alternative exists."
        else:
            adjusted = round(
                next_matrix[from_index][to_index] * _segment_override_factor(override)
                + override.extra_delay_min,
                3,
            )
            severity = "danger" if override.accident_severity >= 65 or override.speed_reduction >= 70 else (
                "warning" if override.traffic_density >= 55 or override.extra_delay_min >= 5 else "info"
            )
            impact_text = "Manual segment conditions modify the Mapbox travel-time matrix before OR-Tools solves."

        next_matrix[from_index][to_index] = adjusted
        next_matrix[to_index][from_index] = adjusted
        impacts.append(ScenarioImpactFactor(
            label=f"Segment {override.from_stop_id} -> {override.to_stop_id}",
            before="Mapbox road duration",
            after="closed" if override.road_closure else f"{adjusted:.1f} min cost",
            impact=impact_text,
            severity=severity,
        ))

    return next_matrix, impacts


def _coords_from_route(
    route: list[dict],
    depot_lat: float,
    depot_lon: float,
) -> list[Coordinate]:
    ordered = sorted(route, key=lambda stop: (stop["vehicle_id"], stop["optimized_position"]))
    coords = [Coordinate(lat=depot_lat, lon=depot_lon, name="depot")]
    coords.extend(
        Coordinate(
            lat=stop["latitude"],
            lon=stop["longitude"],
            name=stop.get("stop_name") or stop.get("stop_id") or "stop",
        )
        for stop in ordered
        if stop.get("latitude") is not None and stop.get("longitude") is not None
    )
    return coords


def _route_metrics(route_data: dict, result: dict) -> dict:
    return {
        "distance_km": round(float(route_data["distance"]) / 1000.0, 2),
        "duration_min": round(float(route_data["duration"]) / 60.0, 1),
        "expected_delay_min": round(float(result["route_summary"].get("expected_total_delay_min", 0.0)), 1),
        "risk_score": round(float(result["route_summary"].get("overall_risk_score", 0.0)), 3),
        "high_risk_stops": int(result["route_summary"].get("high_risk_stop_count", 0)),
        "severe_stops": int(result["route_summary"].get("severe_stop_count", 0)),
    }


def _matrix_leg_minutes(
    from_node: int,
    to_node: int,
    stops_data: list[dict],
    travel_time_matrix: list[list[float]],
) -> float:
    """Return road travel minutes between depot/node ids used by the optimizer."""
    if from_node == to_node:
        return 0.0
    if to_node == 0:
        if from_node <= 0 or from_node > len(stops_data):
            return 0.0
        return float(stops_data[from_node - 1].get("stop_to_depot_travel_min") or 0.0)
    if from_node == 0:
        if to_node <= 0 or to_node > len(stops_data):
            return 0.0
        stop = stops_data[to_node - 1]
        return float(stop.get("depot_to_stop_travel_min") or stop.get("planned_travel_min") or 0.0)
    return float(travel_time_matrix[from_node - 1][to_node - 1] or 0.0)


def _prediction_delay_value(prediction: dict, use_p90: bool = False) -> float:
    if use_p90 and prediction.get("delay_p90_min") is not None:
        return _float_or(prediction.get("delay_p90_min"), 0.0)
    return _float_or(prediction.get("expected_delay_min"), 0.0)


def _float_or(value, default: float) -> float:
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int_or(value, default: int) -> int:
    try:
        if value is None or value == "":
            return int(default)
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _strip_none_fields(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if value is not None}


def _evaluate_route_order(
    stops_data: list[dict],
    node_order: list[int],
    travel_time_matrix: list[list[float]],
    use_p90: bool = False,
    vehicle_id: int = 0,
) -> dict:
    """Rebuild order-sensitive ML features and rescore one explicit stop order.

    The ML model uses sequence-aware fields such as ``stop_sequence``,
    ``cumulative_delay_min`` and ``prev_stop_delay_min``. When OR-Tools changes
    the stop order, those fields must also be recalculated or the backend ends
    up attaching stale delay predictions from the previous order to the new
    route.
    """
    ordered_features: list[dict] = []
    previous_node = 0
    cumulative_delay = 0.0
    prev_stop_delay = 0.0
    total_stops = max(len(node_order), 1)

    # First pass: build a sequence-aware feature table for this exact order.
    for position, node_index in enumerate(node_order):
        stop_idx = _int_or(node_index, 0) - 1
        if stop_idx < 0 or stop_idx >= len(stops_data):
            continue
        stop = deepcopy(stops_data[stop_idx])
        base_travel = _matrix_leg_minutes(previous_node, node_index, stops_data, travel_time_matrix)
        stop["original_stop_index"] = stop_idx
        stop["stop_sequence"] = position + 1
        stop["stop_progress_ratio"] = round((position + 1) / total_stops, 4)
        stop["planned_travel_min"] = round(base_travel, 3)
        stop["cumulative_delay_min"] = round(cumulative_delay, 3)
        stop["prev_stop_delay_min"] = round(prev_stop_delay, 3)
        ordered_features.append(stop)

        quick_prediction = predict_stop_ml(stop)
        prev_stop_delay = _prediction_delay_value(quick_prediction, use_p90=use_p90)
        cumulative_delay += prev_stop_delay
        previous_node = node_index

    ml_result = predict_route_ml(ordered_features)
    ordered_predictions = ml_result.get("stop_predictions", [])

    annotated: list[dict] = []
    previous_node = 0
    elapsed_min = 0.0
    total_display_delay = 0.0
    total_ml_delay = 0.0
    total_travel = 0.0
    total_optimizer_cost = 0.0
    total_schedule_delay = 0.0
    late_stop_count = 0

    for position, (stop, prediction) in enumerate(zip(ordered_features, ordered_predictions)):
        node = _int_or(stop.get("original_stop_index", position), position) + 1
        base_travel = _matrix_leg_minutes(previous_node, node, stops_data, travel_time_matrix)
        leg_cost = score_leg(
            from_node=previous_node,
            to_node=node,
            base_travel_min=base_travel,
            destination_stop=stop,
            destination_prediction=prediction,
            use_p90=use_p90,
        ).to_dict()

        arrival_eta = elapsed_min + float(leg_cost["base_travel_min"])
        ml_delay = float(leg_cost["predicted_delay_min"])
        slack = _float_or(stop.get("time_window_slack_min"), 480.0)
        schedule_delay = max(0.0, (arrival_eta + ml_delay) - slack)
        display_delay = ml_delay
        optimizer_cost = float(leg_cost["total_cost_min"]) + schedule_delay

        row = {
            "optimized_position": position,
            "vehicle_id": vehicle_id,
            "original_stop_index": _int_or(stop.get("original_stop_index", position), position),
            "stop_id": stop.get("stop_id"),
            "stop_name": stop.get("stop_name"),
            "latitude": stop.get("latitude"),
            "longitude": stop.get("longitude"),
            "stop_sequence": position + 1,
            "planned_travel_min": round(float(leg_cost["base_travel_min"]), 1),
            "time_window_slack_min": float(stop.get("time_window_slack_min") or 60.0),
            "feature_source": stop.get("feature_source"),
            "road_type": stop.get("road_type"),
            "traffic_level": stop.get("traffic_level"),
            "weather_condition": stop.get("weather_condition"),
            "congestion_ratio_mean": stop.get("congestion_ratio_mean"),
            "road_incident": stop.get("road_incident"),
            "incident_severity": stop.get("incident_severity"),
            "precipitation_mm": stop.get("precipitation_mm"),
            "wind_speed_kmh": stop.get("wind_speed_kmh"),
            "visibility_km": stop.get("visibility_km"),
            "package_count": stop.get("package_count"),
            "package_weight_kg": stop.get("package_weight_kg"),
            "delay_factors": deepcopy(stop.get("delay_factors", [])),
            "delay_probability": float(prediction.get("delay_probability") or 0.0),
            "expected_delay_min": round(display_delay, 1),
            "delay_p90_min": prediction.get("delay_p90_min"),
            "calibration_applied": bool(prediction.get("calibration_applied", False)),
            "calibration_reasons": list(prediction.get("calibration_reasons", [])),
            "will_miss_window": bool(prediction.get("will_miss_window", False)),
            "risk_level": prediction.get("risk_level") or "low",
            "severity": prediction.get("severity") or "on-time",
            "cumulative_delay_min": round(_float_or(stop.get("cumulative_delay_min"), 0.0), 1),
            "prev_stop_delay_min": round(_float_or(stop.get("prev_stop_delay_min"), 0.0), 1),
            "arrival_eta_min": round(arrival_eta, 1),
            "ml_delay_min": round(ml_delay, 1),
            "schedule_delay_min": round(schedule_delay, 1),
            "lateness_penalty_min": round(schedule_delay, 1),
            "operational_delay_min": round(display_delay, 1),
            "optimizer_cost_min": round(optimizer_cost, 1),
            "cost_breakdown": leg_cost,
        }
        annotated.append(row)

        total_display_delay += display_delay
        total_ml_delay += ml_delay
        total_travel += float(leg_cost["base_travel_min"])
        total_optimizer_cost += optimizer_cost
        total_schedule_delay += schedule_delay
        if schedule_delay > 0.1:
            late_stop_count += 1
        elapsed_min = arrival_eta + ml_delay
        previous_node = node

    return_travel = _matrix_leg_minutes(previous_node, 0, stops_data, travel_time_matrix)
    summary = {
        "total_operational_delay_min": round(total_display_delay, 1),
        "total_delay_min": round(total_display_delay, 1),
        "total_ml_delay_min": round(total_ml_delay, 1),
        "total_schedule_delay_min": round(total_schedule_delay, 1),
        "total_travel_min": round(total_travel, 1),
        "total_optimizer_cost_min": round(total_optimizer_cost, 1),
        "return_to_depot_min": round(return_travel, 1),
        "route_cost_with_return_min": round(total_optimizer_cost + return_travel, 1),
        "late_stop_count": late_stop_count,
    }
    route_summary = {
        **deepcopy(ml_result.get("route_summary", {})),
        "expected_total_delay_min": summary["total_operational_delay_min"],
        "ml_expected_total_delay_min": summary["total_ml_delay_min"],
        "schedule_lateness_total_min": summary["total_schedule_delay_min"],
        "high_risk_stop_count": sum(1 for stop in annotated if stop.get("risk_level") == "high"),
        "severe_stop_count": sum(1 for stop in annotated if stop.get("severity") == "severe"),
    }
    return {
        "ordered_features": ordered_features,
        "ml_result": ml_result,
        "route": annotated,
        "summary": summary,
        "route_summary": route_summary,
    }


def _route_from_node_order(
    stops_data: list[dict],
    stop_predictions: list[dict],
    node_order: list[int],
    vehicle_id: int = 0,
) -> list[dict]:
    """Build an OptimizedStop-compatible route for an explicit stop-node order."""
    route: list[dict] = []
    for delivery_position, node_index in enumerate(node_order):
        stop_idx = _int_or(node_index, 0) - 1
        if stop_idx < 0 or stop_idx >= len(stops_data):
            continue
        original_stop = stops_data[stop_idx]
        ml_pred = stop_predictions[stop_idx] if stop_idx < len(stop_predictions) else {}
        route.append({
            "optimized_position": delivery_position,
            "vehicle_id": vehicle_id,
            "original_stop_index": stop_idx,
            "stop_id": original_stop.get("stop_id"),
            "stop_name": original_stop.get("stop_name"),
            "latitude": original_stop.get("latitude"),
            "longitude": original_stop.get("longitude"),
            "stop_sequence": original_stop.get("stop_sequence"),
            "planned_travel_min": original_stop.get("planned_travel_min"),
            "time_window_slack_min": original_stop.get("time_window_slack_min", 60.0),
            "feature_source": original_stop.get("feature_source"),
            "road_type": original_stop.get("road_type"),
            "traffic_level": original_stop.get("traffic_level"),
            "weather_condition": original_stop.get("weather_condition"),
            "congestion_ratio_mean": original_stop.get("congestion_ratio_mean"),
            "road_incident": original_stop.get("road_incident"),
            "incident_severity": original_stop.get("incident_severity"),
            "precipitation_mm": original_stop.get("precipitation_mm"),
            "wind_speed_kmh": original_stop.get("wind_speed_kmh"),
            "visibility_km": original_stop.get("visibility_km"),
            "package_count": original_stop.get("package_count"),
            "package_weight_kg": original_stop.get("package_weight_kg"),
            "delay_factors": deepcopy(original_stop.get("delay_factors", [])),
            "delay_probability": float(ml_pred.get("delay_probability") or 0.0),
            "expected_delay_min": float(ml_pred.get("expected_delay_min") or 0.0),
            "delay_p90_min": ml_pred.get("delay_p90_min"),
            "calibration_applied": bool(ml_pred.get("calibration_applied", False)),
            "calibration_reasons": list(ml_pred.get("calibration_reasons", [])),
            "will_miss_window": bool(ml_pred.get("will_miss_window", False)),
            "risk_level": ml_pred.get("risk_level") or "low",
            "severity": ml_pred.get("severity") or "on-time",
        })
    return route


def _annotate_route_operational_cost(
    route: list[dict],
    stops_data: list[dict],
    stop_predictions: list[dict],
    travel_time_matrix: list[list[float]],
    use_p90: bool = False,
) -> tuple[list[dict], dict]:
    """
    Score a route order under one exact set of road conditions.

    The user-facing delay metric is the sum of per-stop ML expected delay.
    Time-window lateness and route cost are still calculated, but they stay as
    separate optimizer/debug metrics so UI totals do not mix minutes of delay
    with cost penalties.
    """
    ordered = sorted(route, key=lambda stop: (stop.get("vehicle_id", 0), stop.get("optimized_position", 0)))
    annotated: list[dict] = []
    previous_node = 0
    elapsed_min = 0.0
    total_operational = 0.0
    total_ml_delay = 0.0
    total_travel = 0.0
    total_schedule_delay = 0.0
    total_optimizer_cost = 0.0
    late_stop_count = 0

    for position, stop in enumerate(ordered):
        node = _int_or(stop.get("original_stop_index", position), position) + 1
        stop_idx = node - 1
        base_travel = _matrix_leg_minutes(previous_node, node, stops_data, travel_time_matrix)
        prediction = stop_predictions[stop_idx] if 0 <= stop_idx < len(stop_predictions) else {}
        destination_stop = stops_data[stop_idx] if 0 <= stop_idx < len(stops_data) else stop
        leg_cost = score_leg(
            from_node=previous_node,
            to_node=node,
            base_travel_min=base_travel,
            destination_stop=destination_stop,
            destination_prediction=prediction,
            use_p90=use_p90,
        ).to_dict()

        elapsed_min += float(leg_cost["base_travel_min"])
        ml_delay = float(leg_cost["predicted_delay_min"])
        slack = _float_or(
            stop.get("time_window_slack_min"),
            _float_or(destination_stop.get("time_window_slack_min"), 480.0),
        )
        schedule_delay = max(0.0, (elapsed_min + ml_delay) - slack)
        optimizer_cost = float(leg_cost["total_cost_min"]) + schedule_delay

        row = deepcopy(stop)
        row["optimized_position"] = position
        row["planned_travel_min"] = round(float(leg_cost["base_travel_min"]), 1)
        row["leg_travel_min"] = round(float(leg_cost["base_travel_min"]), 1)
        row["arrival_eta_min"] = round(elapsed_min, 1)
        row["ml_delay_min"] = round(ml_delay, 1)
        row["schedule_delay_min"] = round(schedule_delay, 1)
        row["lateness_penalty_min"] = round(schedule_delay, 1)
        row["operational_delay_min"] = round(ml_delay, 1)
        row["expected_delay_min"] = round(ml_delay, 1)
        row["optimizer_cost_min"] = round(optimizer_cost, 1)
        row["cost_breakdown"] = leg_cost
        annotated.append(row)

        total_operational += ml_delay
        total_ml_delay += ml_delay
        total_travel += float(leg_cost["base_travel_min"])
        total_schedule_delay += schedule_delay
        total_optimizer_cost += optimizer_cost
        if schedule_delay > 0.1:
            late_stop_count += 1
        elapsed_min += ml_delay
        previous_node = node

    return_travel = _matrix_leg_minutes(previous_node, 0, stops_data, travel_time_matrix)
    summary = {
        "total_operational_delay_min": round(total_operational, 1),
        "total_ml_delay_min": round(total_ml_delay, 1),
        "total_schedule_delay_min": round(total_schedule_delay, 1),
        "total_travel_min": round(total_travel, 1),
        "total_optimizer_cost_min": round(total_optimizer_cost, 1),
        "return_to_depot_min": round(return_travel, 1),
        "route_cost_with_return_min": round(total_optimizer_cost + return_travel, 1),
        "late_stop_count": late_stop_count,
    }
    return annotated, summary


def _scenario_explanation(
    controls,
    baseline_metrics: dict,
    scenario_metrics: dict,
    order_changed: bool,
    segment_override_count: int = 0,
    optimization_delta: dict | None = None,
    recommendation_allowed: bool = True,
) -> str:
    delay_delta = scenario_metrics["expected_delay_min"] - baseline_metrics["expected_delay_min"]
    time_delta = scenario_metrics["duration_min"] - baseline_metrics["duration_min"]
    order_text = "changed the stop order" if order_changed else "kept the same stop order"
    optimization_delta = optimization_delta or {}
    saved_cost = float(
        optimization_delta.get("operational_delay_saved_min")
        if optimization_delta.get("operational_delay_saved_min") is not None
        else optimization_delta.get("improvement_min")
        or 0.0
    )
    before_cost = (
        optimization_delta.get("current_operational_delay_min")
        if optimization_delta.get("current_operational_delay_min") is not None
        else optimization_delta.get("original_cost_min")
    )
    after_cost = (
        optimization_delta.get("optimized_operational_delay_min")
        if optimization_delta.get("optimized_operational_delay_min") is not None
        else optimization_delta.get("optimized_cost_min")
    )
    segment_text = (
        f" {segment_override_count} manually edited segment penalties were also applied to the route matrix."
        if segment_override_count
        else ""
    )
    if recommendation_allowed:
        decision_text = (
            f"Under the same updated road conditions, keeping the current order produces {before_cost:.1f} min "
            f"of predicted delay and the optimized order produces {after_cost:.1f} min, saving {saved_cost:.1f} min."
            if before_cost is not None and after_cost is not None
            else f"The optimized order saves {saved_cost:.1f} min of predicted delay."
        )
    else:
        decision_text = (
            "The recalculation did not find a route that is materially better than the current order, "
            "so this result should be kept as analysis only and not applied."
        )
    return (
        f"The scenario was evaluated with the trained ML delay model, not by a visual shortcut. "
        f"Traffic density {controls.traffic_density}/100, accident severity {controls.accident_severity}/100, "
        f"{controls.weather_condition} weather at {controls.weather_severity}/100 intensity, and road disruption "
        f"{controls.road_disruption}/100 were converted into model features. OR-Tools then {order_text} using "
        f"the updated ML delay costs and Mapbox road-time matrix.{segment_text} {decision_text} "
        f"Compared with keeping the current stop order under the same updated conditions, raw ML delay changed by {delay_delta:+.1f} min "
        f"and road duration changed by {time_delta:+.1f} min."
    )


def _coordinate_for_node(
    node: int,
    stops_data: list[dict],
    depot_lat: float,
    depot_lon: float,
) -> Coordinate:
    if node == 0:
        return Coordinate(lat=depot_lat, lon=depot_lon, name="depot")
    stop = stops_data[node - 1]
    return Coordinate(
        lat=float(stop["latitude"]),
        lon=float(stop["longitude"]),
        name=stop.get("stop_name") or stop.get("stop_id") or f"stop_{node}",
    )


def _stop_id_for_node(node: int, stops_data: list[dict]) -> str:
    if node == 0:
        return "depot"
    stop = stops_data[node - 1]
    return str(stop.get("stop_id") or stop.get("stop_name") or f"node-{node}")


def _route_stop_ids(node_order: list[int], stops_data: list[dict]) -> list[str]:
    return [_stop_id_for_node(node, stops_data) for node in node_order if node != 0]


def _override_for_leg(
    from_id: str,
    to_id: str,
    overrides: list[SegmentConditionOverride],
) -> SegmentConditionOverride | None:
    for override in overrides:
        pair = (str(override.from_stop_id), str(override.to_stop_id))
        if pair == (from_id, to_id) or pair == (to_id, from_id):
            return override
    return None


def _localized_segment_penalty_min(
    override: SegmentConditionOverride | None,
    base_duration_min: float,
    path_rank: int,
) -> tuple[float, list[str], bool]:
    if override is None:
        return 0.0, [], False

    reasons: list[str] = []
    pressure = (
        base_duration_min * (override.traffic_density / 100.0) * 0.65
        + base_duration_min * (override.accident_severity / 100.0) * 0.95
        + base_duration_min * (override.weather_severity / 100.0) * 0.25
        + base_duration_min * (override.speed_reduction / 100.0) * 0.75
        + override.extra_delay_min
    )
    risk_extra = {
        "low": 0.0,
        "medium": 2.0,
        "high": 6.0,
        "critical": 14.0,
    }.get(override.risk_level, 0.0)
    pressure += risk_extra

    if override.traffic_density:
        reasons.append(f"segment traffic {override.traffic_density}/100")
    if override.accident_severity:
        reasons.append(f"segment accident {override.accident_severity}/100")
    if override.weather_severity:
        reasons.append(f"segment weather {override.weather_severity}/100")
    if override.speed_reduction:
        reasons.append(f"speed reduction {override.speed_reduction}/100")
    if override.extra_delay_min:
        reasons.append(f"manual delay +{override.extra_delay_min:.1f} min")
    if risk_extra:
        reasons.append(f"segment risk {override.risk_level}")

    # MVP path assumption: the first Mapbox path is the currently selected road.
    # Alternative paths are treated as avoiding most of the localized incident,
    # while still carrying a small residual area penalty.
    if override.road_closure:
        reasons.append("road closure on selected segment")
        if path_rank == 1:
            return 10_000.0, reasons, True
        return round(max(0.0, pressure * 0.10), 3), reasons, True

    if path_rank > 1:
        pressure *= 0.18
    return round(max(0.0, pressure), 3), reasons, False


def _merge_route_geometries(leg_geometries: list[dict]) -> dict:
    merged: list[list[float]] = []
    fallback = False
    for geometry in leg_geometries:
        coords = geometry.get("coordinates") or []
        if geometry.get("isFallback"):
            fallback = True
        if not coords:
            continue
        if merged and merged[-1] == coords[0]:
            merged.extend(coords[1:])
        else:
            merged.extend(coords)
    result = {"type": "LineString", "coordinates": merged}
    if fallback:
        result["isFallback"] = True
    return result


def _fallback_leg_geometry(from_coord: Coordinate, to_coord: Coordinate) -> dict:
    return {
        "type": "LineString",
        "coordinates": [
            [from_coord.lon, from_coord.lat],
            [to_coord.lon, to_coord.lat],
        ],
        "isFallback": True,
    }


async def _fetch_leg_path_options(
    from_coord: Coordinate,
    to_coord: Coordinate,
    token: str,
    fallback_duration_min: float,
    max_routes: int = 3,
) -> list[dict]:
    base_route = await asyncio.to_thread(get_final_route, [from_coord, to_coord], token)
    raw_alternatives = await asyncio.to_thread(get_route_alternatives, [from_coord, to_coord], token, max_routes)

    options: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for index, route in enumerate(raw_alternatives or [], start=1):
        key = (round(float(route.get("duration", 0.0))), round(float(route.get("distance", 0.0))))
        if key in seen:
            continue
        seen.add(key)
        options.append({
            "rank": index,
            "path_id": f"path-{index}",
            "geometry": route["geometry"],
            "duration_min": round(float(route["duration"]) / 60.0, 3),
            "distance_km": round(float(route["distance"]) / 1000.0, 3),
            "source": "mapbox_directions_alternative" if index > 1 else "mapbox_directions_primary",
            "weight_name": route.get("weight_name"),
            "fallback": False,
        })

    if base_route and not options:
        options.append({
            "rank": 1,
            "path_id": "path-1",
            "geometry": base_route["geometry"],
            "duration_min": round(float(base_route["duration"]) / 60.0, 3),
            "distance_km": round(float(base_route["distance"]) / 1000.0, 3),
            "source": "mapbox_directions_primary",
            "weight_name": base_route.get("weight_name"),
            "fallback": False,
        })

    if not options:
        options.append({
            "rank": 1,
            "path_id": "path-1",
            "geometry": _fallback_leg_geometry(from_coord, to_coord),
            "duration_min": round(max(float(fallback_duration_min), 0.0), 3),
            "distance_km": 0.0,
            "source": "fallback_matrix_duration_straight_line_geometry",
            "weight_name": "fallback",
            "fallback": True,
        })

    return options[:max_routes]


async def _build_route_candidate(
    *,
    candidate_id: str,
    label: str,
    change_family: str,
    node_order: list[int],
    evaluation: dict,
    stops_data: list[dict],
    travel_time_matrix: list[list[float]],
    depot_lat: float,
    depot_lon: float,
    token: str,
    segment_overrides: list[SegmentConditionOverride],
    choose_best_paths: bool,
    current_stop_ids: list[str],
    path_cache: dict[tuple[int, int], list[dict]] | None = None,
) -> dict:
    sequence = [0, *node_order]
    selected_legs: list[dict] = []
    leg_debug: list[dict] = []
    total_path_options = 0
    total_path_duration_min = 0.0
    total_path_distance_km = 0.0
    total_segment_penalty_min = 0.0
    path_changed = False
    fallback_used = False

    for leg_index, (from_node, to_node) in enumerate(zip(sequence, sequence[1:]), start=1):
        from_coord = _coordinate_for_node(from_node, stops_data, depot_lat, depot_lon)
        to_coord = _coordinate_for_node(to_node, stops_data, depot_lat, depot_lon)
        from_id = _stop_id_for_node(from_node, stops_data)
        to_id = _stop_id_for_node(to_node, stops_data)
        base_duration = _matrix_leg_minutes(from_node, to_node, stops_data, travel_time_matrix)
        override = _override_for_leg(from_id, to_id, segment_overrides)
        cache_key = (from_node, to_node)
        if path_cache is not None and cache_key in path_cache:
            options = deepcopy(path_cache[cache_key])
        else:
            options = await _fetch_leg_path_options(
                from_coord,
                to_coord,
                token,
                fallback_duration_min=base_duration,
                max_routes=3,
            )
            if path_cache is not None:
                path_cache[cache_key] = deepcopy(options)
        total_path_options += len(options)

        scored_options = []
        for option in options:
            penalty, reasons, closed = _localized_segment_penalty_min(
                override,
                base_duration_min=float(option["duration_min"]),
                path_rank=int(option["rank"]),
            )
            scored_options.append({
                **option,
                "from_stop_id": from_id,
                "to_stop_id": to_id,
                "segment_penalty_min": penalty,
                "closed_current_segment": closed,
                "score_min": round(float(option["duration_min"]) + penalty, 3),
                "reasons": reasons,
            })

        selected = min(scored_options, key=lambda item: item["score_min"]) if choose_best_paths else scored_options[0]
        if selected["rank"] > 1:
            path_changed = True
        if selected.get("fallback"):
            fallback_used = True
        selected_legs.append(selected)
        leg_debug.append({
            "leg_index": leg_index,
            "from_stop_id": from_id,
            "to_stop_id": to_id,
            "options_evaluated": len(scored_options),
            "selected_path_rank": selected["rank"],
            "selected_path_score_min": selected["score_min"],
            "alternatives": [
                {
                    "rank": option["rank"],
                    "duration_min": option["duration_min"],
                    "distance_km": option["distance_km"],
                    "segment_penalty_min": option["segment_penalty_min"],
                    "score_min": option["score_min"],
                    "source": option["source"],
                }
                for option in scored_options
            ],
        })
        total_path_duration_min += float(selected["duration_min"])
        total_path_distance_km += float(selected["distance_km"])
        total_segment_penalty_min += float(selected["segment_penalty_min"])

    route_ids = _route_stop_ids(node_order, stops_data)
    duplicate_ids = sorted({stop_id for stop_id in route_ids if route_ids.count(stop_id) > 1})
    missing_ids = [stop_id for stop_id in current_stop_ids if stop_id not in route_ids]
    unexpected_ids = [stop_id for stop_id in route_ids if stop_id not in current_stop_ids]
    valid = not duplicate_ids and not missing_ids and not unexpected_ids and len(route_ids) == len(current_stop_ids)

    summary = evaluation["summary"]
    route_stability_penalty = 0.0
    if route_ids != current_stop_ids:
        route_stability_penalty += 1.0
    if path_changed:
        route_stability_penalty += 0.35 * sum(1 for leg in selected_legs if int(leg["rank"]) > 1)

    total_cost = (
        total_path_duration_min
        + float(summary["total_operational_delay_min"])
        + float(summary["total_schedule_delay_min"])
        + total_segment_penalty_min
        + route_stability_penalty
    )

    return {
        "candidate_id": candidate_id,
        "label": label,
        "change_family": change_family,
        "stop_order": route_ids,
        "node_order": node_order,
        "stop_count": len(route_ids),
        "geometry": _merge_route_geometries([leg["geometry"] for leg in selected_legs]),
        "distance_km": round(total_path_distance_km, 2),
        "road_duration_min": round(total_path_duration_min, 1),
        "predicted_delay_min": round(float(summary["total_operational_delay_min"]), 1),
        "schedule_lateness_min": round(float(summary["total_schedule_delay_min"]), 1),
        "segment_penalty_min": round(total_segment_penalty_min, 1),
        "route_stability_penalty_min": round(route_stability_penalty, 1),
        "total_cost_min": round(total_cost, 1),
        "validation": {
            "valid": valid,
            "missing_stop_ids": missing_ids,
            "unexpected_stop_ids": unexpected_ids,
            "duplicate_stop_ids": duplicate_ids,
            "stop_count_matches_remaining": len(route_ids) == len(current_stop_ids),
        },
        "path_changed": path_changed,
        "order_changed": route_ids != current_stop_ids,
        "fallback_geometry_used": fallback_used,
        "path_alternatives_evaluated": total_path_options,
        "selected_leg_paths": [
            {
                "from_stop_id": leg["from_stop_id"],
                "to_stop_id": leg["to_stop_id"],
                "path_rank": leg["rank"],
                "duration_min": leg["duration_min"],
                "distance_km": leg["distance_km"],
                "segment_penalty_min": leg["segment_penalty_min"],
                "score_min": leg["score_min"],
                "source": leg["source"],
                "reasons": leg["reasons"],
            }
            for leg in selected_legs
        ],
        "leg_debug": leg_debug,
        "reason_codes": [
            code for code, enabled in {
                "order_changed": route_ids != current_stop_ids,
                "path_changed": path_changed,
                "avoids_congested_segment": any(leg["rank"] > 1 and leg["segment_penalty_min"] > 0 for leg in selected_legs),
                "lower_delay": False,
                "lower_total_cost": False,
            }.items() if enabled
        ],
    }


def _candidate_change_type(selected: dict, current: dict, recommendation_allowed: bool) -> str:
    if not recommendation_allowed:
        return "no_better_route"
    if selected.get("order_changed") and selected.get("path_changed"):
        return "both_changed"
    if selected.get("order_changed"):
        return "order_changed"
    if selected.get("path_changed"):
        return "path_changed"
    if float(selected.get("total_cost_min", 0.0)) < float(current.get("total_cost_min", 0.0)) - 0.1:
        return "cost_changed"
    return "no_better_route"


def _validate_ai_decision(
    ai_decision: dict,
    candidates: list[dict],
    current_candidate: dict,
) -> dict:
    by_id = {candidate["candidate_id"]: candidate for candidate in candidates}
    selected_id = ai_decision.get("selected_route_id")
    selected = by_id.get(selected_id)
    errors: list[str] = []
    if selected is None:
        errors.append("selected_route_id does not exist in backend candidate set")
        selected = current_candidate
    if not selected.get("validation", {}).get("valid", False):
        errors.append("selected route failed stop-set validation")
    current_cost = float(current_candidate.get("total_cost_min", 0.0))
    selected_cost = float(selected.get("total_cost_min", 0.0))
    decision = ai_decision.get("decision")
    if decision == "apply_recommendation" and selected_cost >= current_cost - 0.1:
        errors.append("selected route is not better than current route")
    if selected.get("candidate_id") == current_candidate.get("candidate_id") and decision == "apply_recommendation":
        errors.append("cannot apply current route as a new recommendation")

    return {
        "valid": not errors,
        "errors": errors,
        "selected_route_id": selected.get("candidate_id"),
        "selected_total_cost_min": round(selected_cost, 1),
        "current_total_cost_min": round(current_cost, 1),
    }


def _run_controlled_ai_decision_agent(candidates: list[dict], current_candidate: dict) -> tuple[dict, dict, dict]:
    valid_candidates = [candidate for candidate in candidates if candidate.get("validation", {}).get("valid")]
    deterministic_best = min(valid_candidates or [current_candidate], key=lambda item: float(item["total_cost_min"]))
    current_cost = float(current_candidate["total_cost_min"])
    best_cost = float(deterministic_best["total_cost_min"])
    saving = round(current_cost - best_cost, 1)
    apply_allowed = deterministic_best["candidate_id"] != current_candidate["candidate_id"] and saving > 0.1

    reason_codes: list[str] = []
    if apply_allowed:
        reason_codes.append("lower_total_cost")
        if deterministic_best.get("predicted_delay_min", 0.0) < current_candidate.get("predicted_delay_min", 0.0):
            reason_codes.append("lower_delay")
        if deterministic_best.get("path_changed"):
            reason_codes.append("path_changed")
        if deterministic_best.get("order_changed"):
            reason_codes.append("order_changed")
        if any("segment" in " ".join(leg.get("reasons", [])).lower() for leg in deterministic_best.get("selected_leg_paths", [])):
            reason_codes.append("avoids_congested_segment")
    else:
        reason_codes.append("no_better_route")

    decision = {
        "selected_route_id": deterministic_best["candidate_id"],
        "decision": "apply_recommendation" if apply_allowed else "keep_current",
        "confidence": 0.88 if apply_allowed else 0.78,
        "reason_codes": reason_codes,
        "human_explanation": (
            f"Selected {deterministic_best['label']} because it lowers total route cost by {saving:.1f} min "
            f"under the same backend-evaluated conditions."
            if apply_allowed
            else "No candidate lowered total route cost enough to justify changing the active route."
        ),
        "facts_used": {
            "current_delay_min": current_candidate["predicted_delay_min"],
            "selected_delay_min": deterministic_best["predicted_delay_min"],
            "saving_min": saving,
            "current_total_cost_min": current_candidate["total_cost_min"],
            "selected_total_cost_min": deterministic_best["total_cost_min"],
            "alternatives_evaluated": len(candidates),
            "path_alternatives_evaluated": sum(int(candidate.get("path_alternatives_evaluated", 0)) for candidate in candidates),
        },
        "source": "deterministic_guarded_agent",
    }
    validation = _validate_ai_decision(decision, candidates, current_candidate)
    if not validation["valid"]:
        fallback = {
            **decision,
            "selected_route_id": current_candidate["candidate_id"],
            "decision": "keep_current",
            "confidence": 0.55,
            "reason_codes": ["validator_fallback", "keep_current"],
            "human_explanation": "The AI decision failed backend validation, so the current route is kept.",
        }
        return fallback, _validate_ai_decision(fallback, candidates, current_candidate), current_candidate
    return decision, validation, deterministic_best



@router.post("/full-route", response_model=FullRouteResponse, tags=["optimization"],
             summary="Plan full route end-to-end",
             description=(
                 "✅ Aktif. Mock data veya özel stop listesiyle doğrudan optimize eder. "
                 "DB entegrasyonlu otomatik akış için POST /api/v1/auto-dispatch kullan."
             ))
async def full_route(request: OptimizeRequest, db: Session = Depends(get_db)) -> FullRouteResponse:
    """
    End-to-end pipeline in a single request:
      Mapbox Matrix → ML inference → OR-Tools VRP → Mapbox Directions

    The frontend only calls this endpoint; ordered GeoJSON + delivery
    intelligence are returned together, no need for a second request.

    Requirements:
      - All stops must have latitude / longitude
      - depot_latitude / depot_longitude must be provided
      - MAPBOX_TOKEN environment variable must be set
    """
    token = os.getenv("MAPBOX_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="MAPBOX_TOKEN not configured")

    if request.depot_latitude is None or request.depot_longitude is None:
        raise HTTPException(status_code=422, detail="depot_latitude and depot_longitude are required for full-route")

    if request.depot_latitude == 0.0 and request.depot_longitude == 0.0:
        raise HTTPException(status_code=422, detail="depot coordinates are (0, 0) — missing or unset")

    if not all(s.latitude is not None and s.longitude is not None for s in request.stops):
        raise HTTPException(status_code=422, detail="All stops must have latitude and longitude for full-route")

    bad_stops = [i for i, s in enumerate(request.stops) if s.latitude == 0.0 and s.longitude == 0.0]
    if bad_stops:
        raise HTTPException(status_code=422, detail=f"Stops at indices {bad_stops} have coordinates (0, 0) — missing or unset")

    try:
        # ── Step 1: Mapbox Matrix API (thread'de çalıştır — sync HTTP) ──────────
        coords = [Coordinate(lat=request.depot_latitude, lon=request.depot_longitude, name="depot")]
        coords += [
            Coordinate(lat=s.latitude, lon=s.longitude, name=s.stop_name or str(i))
            for i, s in enumerate(request.stops)
        ]

        duration_matrix, distance_matrix = await asyncio.to_thread(
            get_weight_matrices, coords, token
        )
        if duration_matrix is None or distance_matrix is None:
            raise HTTPException(status_code=502, detail="Mapbox Matrix API request failed")

        stops_as_dicts = [stop.model_dump() for stop in request.stops]
        stops_enriched, travel_time_matrix = mapbox_to_pipeline(
            stops_as_dicts, duration_matrix, distance_matrix
        )

        # ── Steps 2-4: ML → OR-Tools (thread'de çalıştır — CPU-bound) ────────
        result = await asyncio.to_thread(
            _optimizer.optimize,
            stops_input=stops_enriched,
            use_p90=request.use_p90,
            num_vehicles=request.num_vehicles,
            time_limit_seconds=request.time_limit_seconds,
            travel_time_matrix=travel_time_matrix,
        )
        result["route_summary"]["recommended_departure_shift_min"] = round(
            result["route_summary"].get("expected_total_delay_min", 0.0), 1
        )

        # ── Step 5: Mapbox Directions API — her vehicle için ayrı çağrı ─────────
        vehicle_routes = await _build_vehicle_routes(
            optimized_route=result["optimized_route"],
            depot_lat=request.depot_latitude,
            depot_lon=request.depot_longitude,
            token=token,
        )

        optimize_response = OptimizeResponse(**result)

        explanation = None
        if request.generate_explanation:
            raw = generate_explanation(
                route_summary=result["route_summary"],
                optimized_route=result["optimized_route"],
                use_p90=request.use_p90,
            )
            explanation = ExplanationResult(**raw) if "error" not in raw else ExplanationResult(
                overall_assessment="", risk_factors=[], recommendations=[],
                stop_alerts=[], model_used="", generation_time_ms=0, error=raw["error"],
            )

        if request.route_id:
            # ── Persist stops to DB if Route exists ───────────────────────────
            route_db = db.get(Route, request.route_id)
            if route_db is not None:
                # Idempotent: wipe previous stops for this route before re-writing
                db.query(Stop).filter(Stop.route_id == request.route_id).delete()
                db.flush()

                stop_id_map: dict[int, int] = {}  # original_stop_index → new DB Stop.id
                for opt_stop in result["optimized_route"]:
                    orig_idx = opt_stop["original_stop_index"]
                    src = request.stops[orig_idx]
                    db_stop = Stop(
                        route_id=request.route_id,
                        sequence=opt_stop["optimized_position"],
                        name=src.stop_name,
                        latitude=src.latitude,
                        longitude=src.longitude,
                        time_window_slack_min=src.time_window_slack_min,
                        status=StopStatus.pending,
                    )
                    db.add(db_stop)
                    db.flush()  # populate db_stop.id
                    stop_id_map[orig_idx] = db_stop.id

                db.commit()

                # Patch stop_id in result dicts (used by fleet_store + route_state below)
                for opt_stop in result["optimized_route"]:
                    orig_idx = opt_stop["original_stop_index"]
                    if orig_idx in stop_id_map:
                        opt_stop["stop_id"] = str(stop_id_map[orig_idx])

                # Patch already-created Pydantic response models
                for opt_stop_model in optimize_response.optimized_route:
                    orig_idx = opt_stop_model.original_stop_index
                    if orig_idx in stop_id_map:
                        opt_stop_model.stop_id = str(stop_id_map[orig_idx])

                # matrix col index: depot=0, request.stops[i]=i+1
                matrix_idx_map = {
                    str(db_id): orig_idx + 1
                    for orig_idx, db_id in stop_id_map.items()
                }
            else:
                # Route not in DB — fall back to request-provided stop_ids (may be null)
                matrix_idx_map = {
                    s.stop_id: i + 1
                    for i, s in enumerate(request.stops)
                    if s.stop_id is not None
                }

            fleet_store.save_route(
                route_id=request.route_id,
                route_summary=result["route_summary"],
                optimized_route=result["optimized_route"],
                num_vehicles=request.num_vehicles,
                use_p90=request.use_p90,
                db=db,
            )
            # Cache Mapbox matrix + initial route state in Redis for re-opt
            set_matrix_cache(request.route_id, duration_matrix, distance_matrix)
            set_matrix_index_map(request.route_id, matrix_idx_map)
            set_stops_cache(request.route_id, stops_enriched)
            set_route_state(request.route_id, {
                "cumulative_delay_min": 0.0,
                "completed_stop_count": 0,
                "current_sequence": [
                    s["stop_id"] for s in sorted(
                        result["optimized_route"], key=lambda x: x["optimized_position"]
                    )
                ],
            })

        return FullRouteResponse(
            vehicle_routes=vehicle_routes,
            optimized_route=optimize_response.optimized_route,
            route_summary=optimize_response.route_summary,
            optimization_comparison=result.get("optimization_comparison"),
            use_p90=result["use_p90"],
            num_vehicles=result["num_vehicles"],
            explanation=explanation,
        )

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"ML model not found: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Full route pipeline failed: {exc}") from exc


class CourierAssignment(BaseModel):
    courier_id: int = Field(..., description="Courier DB ID")
    stop_pool_ids: list[int] = Field(..., min_length=1, description="Bu courier'a atanan stop_pool ID'leri")


class AutoDispatchRequest(BaseModel):
    depot_latitude: float
    depot_longitude: float
    date: str = Field(..., description="YYYY-MM-DD")
    assignments: list[CourierAssignment] = Field(..., min_length=1, description="Her courier için atanan stop listesi")
    time_limit_seconds: int = Field(default=15, ge=5, le=120)
    use_p90: bool = False
    generate_explanation: bool = False


@router.post(
    "/auto-dispatch",
    response_model=FullRouteResponse,
    tags=["optimization"],
    summary="Dispatcher'ın manuel atadığı courier→stop listesini optimize et ve DB'ye kaydet",
)
async def auto_dispatch(body: AutoDispatchRequest, db: Session = Depends(get_db)) -> FullRouteResponse:
    from datetime import datetime as dt
    from api.schemas import StopInput

    try:
        target_date = dt.strptime(body.date, "%Y-%m-%d")
        dispatch_hour = 9
        dispatch_day = target_date.weekday()
    except ValueError:
        raise HTTPException(status_code=400, detail="date formatı YYYY-MM-DD olmalı")

    # Courier'ları doğrula
    all_courier_ids = [a.courier_id for a in body.assignments]
    couriers_db = {c.id: c for c in db.query(Courier).filter(Courier.id.in_(all_courier_ids)).all()}
    missing = [cid for cid in all_courier_ids if cid not in couriers_db]
    if missing:
        raise HTTPException(status_code=404, detail=f"Courier ID'leri bulunamadı: {missing}")

    # Stop'ları doğrula
    all_stop_ids = [sid for a in body.assignments for sid in a.stop_pool_ids]
    stops_db = {s.id: s for s in db.query(StopPool).filter(StopPool.id.in_(all_stop_ids)).all()}
    missing_stops = [sid for sid in all_stop_ids if sid not in stops_db]
    if missing_stops:
        raise HTTPException(status_code=404, detail=f"Stop ID'leri bulunamadı: {missing_stops}")

    dispatcher = db.query(User).filter(User.role == UserRole.dispatcher).first()
    if not dispatcher:
        dispatcher = User(name="Default Dispatcher", email="dispatcher@sbtu.local", role=UserRole.dispatcher)
        db.add(dispatcher)
        db.flush()

    # Her courier için Route oluştur (aynı courier+date varsa eski route+stop'ları sil)
    couriers: list[Courier] = []
    vehicle_route_map: dict[int, int] = {}  # vehicle_id (0-based) → route DB id
    for v_idx, assignment in enumerate(body.assignments):
        courier = couriers_db[assignment.courier_id]
        couriers.append(courier)

        existing_routes = db.query(Route).filter(
            Route.courier_id == courier.id,
            Route.date == target_date,
            Route.status.in_([RouteStatus.planned, RouteStatus.active]),
        ).all()
        for old_route in existing_routes:
            db.query(Stop).filter(Stop.route_id == old_route.id).delete()
            db.delete(old_route)
        db.flush()

        route = Route(
            courier_id=courier.id,
            dispatcher_id=dispatcher.id,
            date=target_date,
            depot_latitude=body.depot_latitude,
            depot_longitude=body.depot_longitude,
            status=RouteStatus.planned,
        )
        db.add(route)
        db.flush()
        vehicle_route_map[v_idx] = route.id
    db.commit()

    # Her courier için ayrı full_route çağrısı
    all_vehicle_routes = []
    all_optimized_stops = []
    all_summaries = []

    for v_idx, assignment in enumerate(body.assignments):
        route_id = vehicle_route_map[v_idx]
        courier = couriers_db[assignment.courier_id]

        courier_stops: list[StopInput] = []
        for i, sid in enumerate(assignment.stop_pool_ids):
            sp = stops_db[sid]
            stop_context = pipeline.get_stop_context(
                stop_code=sp.name,
                stop_sequence=i + 1,
                hour_of_day=dispatch_hour,
                day_of_week=dispatch_day,
                vehicle_type=courier.vehicle_type,
                courier_id=f"courier-{v_idx}",
            )
            courier_stops.append(StopInput(
                stop_sequence=i + 1,
                stop_id=str(sp.id),
                stop_name=sp.name,
                latitude=sp.latitude,
                longitude=sp.longitude,
                cumulative_delay_min=0.0,
                prev_stop_delay_min=0.0,
                **stop_context,
            ))

        opt_request = OptimizeRequest(
            stops=courier_stops,
            depot_latitude=body.depot_latitude,
            depot_longitude=body.depot_longitude,
            num_vehicles=1,
            time_limit_seconds=body.time_limit_seconds,
            use_p90=body.use_p90,
            generate_explanation=False,
            route_id=route_id,
        )

        result = await full_route(opt_request, db)

        # vehicle_id'yi global sıraya göre güncelle
        for vr in result.vehicle_routes:
            all_vehicle_routes.append(vr.model_copy(update={"vehicle_id": v_idx}))
        for opt_stop in result.optimized_route:
            all_optimized_stops.append(opt_stop.model_copy(update={"vehicle_id": v_idx}))

        all_summaries.append(result.route_summary)

    from api.schemas import RouteSummary
    n = len(all_summaries)
    combined_summary = RouteSummary(
        route_delay_probability=sum(s.route_delay_probability for s in all_summaries) / n,
        expected_total_delay_min=sum(s.expected_total_delay_min for s in all_summaries),
        worst_case_total_delay_min=(
            sum(s.worst_case_total_delay_min for s in all_summaries if s.worst_case_total_delay_min is not None)
            or None
        ),
        high_risk_stop_count=sum(s.high_risk_stop_count for s in all_summaries),
        severe_stop_count=sum(s.severe_stop_count for s in all_summaries),
        overall_risk_score=sum(s.overall_risk_score for s in all_summaries) / n,
        recommended_departure_shift_min=sum(s.recommended_departure_shift_min for s in all_summaries),
        vrp_status=all_summaries[-1].vrp_status,
        vrp_objective_value=sum(s.vrp_objective_value for s in all_summaries),
        vrp_solve_time_ms=sum(s.vrp_solve_time_ms for s in all_summaries),
        dropped_stop_indices=[i for s in all_summaries for i in s.dropped_stop_indices],
        cost_mode=all_summaries[-1].cost_mode,
    )

    return FullRouteResponse(
        vehicle_routes=all_vehicle_routes,
        optimized_route=all_optimized_stops,
        route_summary=combined_summary,
        optimization_comparison=None,
        use_p90=body.use_p90,
        num_vehicles=len(body.assignments),
    )


@router.post(
    "/scenario/reoptimize",
    response_model=ScenarioReoptimizationResponse,
    tags=["scenario"],
    summary="Run ML-backed scenario re-optimization",
    description=(
        "Recomputes stop delay with user-controlled traffic, weather, accident, "
        "load, and disruption factors; then solves a new OR-Tools route and "
        "draws the resulting road geometry through Mapbox."
    ),
)
async def scenario_reoptimize(body: ScenarioRouteRequest) -> ScenarioReoptimizationResponse:
    token = os.getenv("MAPBOX_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="MAPBOX_TOKEN not configured")

    if body.depot_latitude == 0.0 and body.depot_longitude == 0.0:
        raise HTTPException(status_code=422, detail="depot coordinates are missing")

    if not all(stop.latitude is not None and stop.longitude is not None for stop in body.stops):
        raise HTTPException(status_code=422, detail="All stops must have latitude and longitude")

    coords = [Coordinate(lat=body.depot_latitude, lon=body.depot_longitude, name="depot")]
    coords.extend(
        Coordinate(lat=stop.latitude, lon=stop.longitude, name=stop.stop_name or str(index))
        for index, stop in enumerate(body.stops)
    )

    duration_matrix, distance_matrix = await asyncio.to_thread(get_weight_matrices, coords, token)
    if duration_matrix is None or distance_matrix is None:
        raise HTTPException(status_code=502, detail="Mapbox Matrix API request failed")

    stops_as_dicts = [_strip_none_fields(stop.model_dump()) for stop in body.stops]
    stops_enriched, travel_time_matrix = mapbox_to_pipeline(
        stops_as_dicts,
        duration_matrix,
        distance_matrix,
    )

    try:
        baseline_result = await asyncio.to_thread(
            _optimizer.optimize,
            stops_input=stops_enriched,
            use_p90=False,
            num_vehicles=1,
            time_limit_seconds=body.time_limit_seconds,
            travel_time_matrix=travel_time_matrix,
        )

        scenario_stops, factor_impacts = _apply_scenario_to_stops(
            stops_enriched,
            body.controls,
        )
        scenario_matrix = _adjust_travel_matrix_for_scenario(
            travel_time_matrix,
            body.controls,
        )
        scenario_matrix, segment_impacts = _apply_segment_overrides_to_matrix(
            scenario_matrix,
            scenario_stops,
            body.segment_overrides,
        )
        factor_impacts.extend(segment_impacts)
        current_order_node_order = list(range(1, len(scenario_stops) + 1))
        current_order_eval = _evaluate_route_order(
            scenario_stops,
            current_order_node_order,
            scenario_matrix,
            use_p90=body.controls.conservative_mode,
        )
        scenario_result = await asyncio.to_thread(
            _optimizer.optimize,
            stops_input=current_order_eval["ordered_features"],
            use_p90=body.controls.conservative_mode,
            num_vehicles=1,
            time_limit_seconds=body.time_limit_seconds,
            travel_time_matrix=scenario_matrix,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Scenario optimization failed: {exc}") from exc

    baseline_result["route_summary"]["recommended_departure_shift_min"] = round(
        baseline_result["route_summary"].get("expected_total_delay_min", 0.0),
        1,
    )
    scenario_result["route_summary"]["recommended_departure_shift_min"] = round(
        scenario_result["route_summary"].get("expected_total_delay_min", 0.0),
        1,
    )

    current_order_route = current_order_eval["route"]
    current_schedule_summary = current_order_eval["summary"]

    optimized_node_order = []
    for route_nodes in scenario_result.get("vrp_result", {}).get("routes", []):
        for node in route_nodes:
            node_int = _int_or(node, 0)
            if node_int != 0:
                optimized_node_order.append(node_int)
    if not optimized_node_order:
        optimized_node_order = current_order_node_order

    optimized_order_eval = _evaluate_route_order(
        scenario_stops,
        optimized_node_order,
        scenario_matrix,
        use_p90=body.controls.conservative_mode,
    )

    current_stop_ids = _route_stop_ids(current_order_node_order, scenario_stops)
    path_cache: dict[tuple[int, int], list[dict]] = {}
    current_candidate = await _build_route_candidate(
        candidate_id="current_order_current_path",
        label="Keep current order and current road paths",
        change_family="current_order",
        node_order=current_order_node_order,
        evaluation=current_order_eval,
        stops_data=scenario_stops,
        travel_time_matrix=scenario_matrix,
        depot_lat=body.depot_latitude,
        depot_lon=body.depot_longitude,
        token=token,
        segment_overrides=body.segment_overrides,
        choose_best_paths=False,
        current_stop_ids=current_stop_ids,
        path_cache=path_cache,
    )
    current_path_candidate = await _build_route_candidate(
        candidate_id="current_order_best_path",
        label="Keep stop order, switch to best road paths",
        change_family="current_order",
        node_order=current_order_node_order,
        evaluation=current_order_eval,
        stops_data=scenario_stops,
        travel_time_matrix=scenario_matrix,
        depot_lat=body.depot_latitude,
        depot_lon=body.depot_longitude,
        token=token,
        segment_overrides=body.segment_overrides,
        choose_best_paths=True,
        current_stop_ids=current_stop_ids,
        path_cache=path_cache,
    )
    optimized_candidate = await _build_route_candidate(
        candidate_id="optimized_order_best_path",
        label="Change stop order and use best road paths",
        change_family="optimized_order",
        node_order=optimized_node_order,
        evaluation=optimized_order_eval,
        stops_data=scenario_stops,
        travel_time_matrix=scenario_matrix,
        depot_lat=body.depot_latitude,
        depot_lon=body.depot_longitude,
        token=token,
        segment_overrides=body.segment_overrides,
        choose_best_paths=True,
        current_stop_ids=current_stop_ids,
        path_cache=path_cache,
    )
    candidates = [current_candidate, current_path_candidate, optimized_candidate]
    ai_decision, validated_decision, selected_candidate = _run_controlled_ai_decision_agent(
        candidates,
        current_candidate,
    )

    selected_eval = optimized_order_eval if selected_candidate["change_family"] == "optimized_order" else current_order_eval
    optimized_order_route = selected_eval["route"]
    optimized_schedule_summary = selected_eval["summary"]
    scenario_result["optimized_route"] = optimized_order_route
    scenario_result["route_summary"] = {
        **scenario_result.get("route_summary", {}),
        **selected_eval["route_summary"],
        "recommended_departure_shift_min": round(
            selected_eval["route_summary"].get("expected_total_delay_min", 0.0),
            1,
        ),
    }

    baseline_route_data = {
        "geometry": current_candidate["geometry"],
        "distance": current_candidate["distance_km"] * 1000.0,
        "duration": current_candidate["road_duration_min"] * 60.0,
        "weight_name": "candidate_current_path",
    }
    scenario_route_data = {
        "geometry": selected_candidate["geometry"],
        "distance": selected_candidate["distance_km"] * 1000.0,
        "duration": selected_candidate["road_duration_min"] * 60.0,
        "weight_name": "candidate_selected_path",
    }
    mapbox_alternatives = []

    baseline_metrics = _route_metrics(baseline_route_data, scenario_result)
    scenario_metrics = _route_metrics(scenario_route_data, scenario_result)
    baseline_metrics.update({
        "expected_delay_min": current_schedule_summary["total_operational_delay_min"],
        "ml_delay_min": current_schedule_summary["total_ml_delay_min"],
        "schedule_lateness_min": current_schedule_summary["total_schedule_delay_min"],
        "optimizer_cost_min": current_candidate["total_cost_min"],
        "route_cost_with_return_min": current_candidate["total_cost_min"],
        "travel_time_min": current_candidate["road_duration_min"],
        "segment_penalty_min": current_candidate["segment_penalty_min"],
        "late_stop_count": current_schedule_summary["late_stop_count"],
        "risk_score": round(float(current_order_eval["route_summary"].get("overall_risk_score", 0.0)), 3),
        "high_risk_stops": int(current_order_eval["route_summary"].get("high_risk_stop_count", 0)),
        "severe_stops": int(current_order_eval["route_summary"].get("severe_stop_count", 0)),
        "comparison_basis": "current_order_under_updated_conditions",
        "metric_definition": "sum_of_per_stop_ml_expected_delay_min",
    })
    scenario_metrics.update({
        "expected_delay_min": optimized_schedule_summary["total_operational_delay_min"],
        "ml_delay_min": optimized_schedule_summary["total_ml_delay_min"],
        "schedule_lateness_min": optimized_schedule_summary["total_schedule_delay_min"],
        "optimizer_cost_min": selected_candidate["total_cost_min"],
        "route_cost_with_return_min": selected_candidate["total_cost_min"],
        "travel_time_min": selected_candidate["road_duration_min"],
        "segment_penalty_min": selected_candidate["segment_penalty_min"],
        "late_stop_count": optimized_schedule_summary["late_stop_count"],
        "risk_score": round(float(selected_eval["route_summary"].get("overall_risk_score", 0.0)), 3),
        "high_risk_stops": int(selected_eval["route_summary"].get("high_risk_stop_count", 0)),
        "severe_stops": int(selected_eval["route_summary"].get("severe_stop_count", 0)),
        "comparison_basis": "selected_candidate_under_updated_conditions",
        "metric_definition": "sum_of_per_stop_ml_expected_delay_min",
    })
    raw_optimizer_comparison = scenario_result.get("optimization_comparison") or {}
    delay_improvement_min = round(
        current_schedule_summary["total_operational_delay_min"]
        - optimized_schedule_summary["total_operational_delay_min"],
        1,
    )
    current_route_cost_min = round(current_candidate["total_cost_min"], 1)
    optimized_route_cost_min = round(selected_candidate["total_cost_min"], 1)
    route_cost_saved_min = round(current_route_cost_min - optimized_route_cost_min, 1)
    route_cost_improvement_pct = round(
        (route_cost_saved_min / current_route_cost_min) * 100.0,
        2,
    ) if current_route_cost_min > 0 else 0.0

    current_by_id = {
        str(stop.get("stop_id") or stop.get("stop_name") or stop.get("original_stop_index")): stop
        for stop in current_order_route
    }
    improved_stop_count = 0
    worsened_stop_count = 0
    unchanged_stop_count = 0
    for stop in optimized_order_route:
        key = str(stop.get("stop_id") or stop.get("stop_name") or stop.get("original_stop_index"))
        before = current_by_id.get(key)
        if not before:
            continue
        before_delay = float(before.get("operational_delay_min") or 0.0)
        after_delay = float(stop.get("operational_delay_min") or 0.0)
        if after_delay < before_delay - 0.1:
            improved_stop_count += 1
        elif after_delay > before_delay + 0.1:
            worsened_stop_count += 1
        else:
            unchanged_stop_count += 1

    no_dropped_stops = not scenario_result.get("vrp_result", {}).get("dropped_nodes")
    sequence_before = current_candidate["stop_order"]
    sequence_after = selected_candidate["stop_order"]
    order_changed = sequence_before != sequence_after
    path_changed = bool(selected_candidate.get("path_changed"))
    recommendation_allowed = (
        validated_decision.get("valid", False)
        and ai_decision.get("decision") == "apply_recommendation"
        and route_cost_saved_min > 0.1
        and no_dropped_stops
        and selected_candidate.get("validation", {}).get("valid", False)
    )
    scenario_status = "improved" if recommendation_allowed else "not_improved"
    if not scenario_result.get("vrp_result", {}).get("routes"):
        scenario_status = "infeasible"
    scenario_comparison = {
        **raw_optimizer_comparison,
        "status": scenario_status,
        "confidence": "high" if route_cost_saved_min > 0.1 else "medium",
        "original_cost_min": current_route_cost_min,
        "optimized_cost_min": optimized_route_cost_min,
        "improvement_min": route_cost_saved_min,
        "improvement_pct": route_cost_improvement_pct,
        "current_delay_min": current_schedule_summary["total_operational_delay_min"],
        "optimized_delay_min": optimized_schedule_summary["total_operational_delay_min"],
        "delay_saved_min": delay_improvement_min,
        "path_changed": path_changed,
        "change_type": _candidate_change_type(selected_candidate, current_candidate, recommendation_allowed),
        "comparison_basis": "same_updated_conditions",
    }
    optimization_delta = {
        "original_cost_min": current_route_cost_min,
        "optimized_cost_min": optimized_route_cost_min,
        "improvement_min": route_cost_saved_min,
        "improvement_pct": route_cost_improvement_pct,
        "current_operational_delay_min": current_schedule_summary["total_operational_delay_min"],
        "optimized_operational_delay_min": optimized_schedule_summary["total_operational_delay_min"],
        "operational_delay_saved_min": delay_improvement_min,
        "current_schedule_lateness_min": current_schedule_summary["total_schedule_delay_min"],
        "optimized_schedule_lateness_min": optimized_schedule_summary["total_schedule_delay_min"],
        "current_route_cost_min": current_route_cost_min,
        "optimized_route_cost_min": optimized_route_cost_min,
        "route_cost_saved_min": route_cost_saved_min,
        "current_road_duration_min": current_candidate["road_duration_min"],
        "optimized_road_duration_min": selected_candidate["road_duration_min"],
        "road_duration_saved_min": round(current_candidate["road_duration_min"] - selected_candidate["road_duration_min"], 1),
        "current_segment_penalty_min": current_candidate["segment_penalty_min"],
        "optimized_segment_penalty_min": selected_candidate["segment_penalty_min"],
        "path_changed": path_changed,
        "change_type": _candidate_change_type(selected_candidate, current_candidate, recommendation_allowed),
        "improved_stop_count": improved_stop_count,
        "worsened_stop_count": worsened_stop_count,
        "unchanged_stop_count": unchanged_stop_count,
        "comparison_basis": "same_updated_conditions",
    }
    recommendation_status = "recommended" if recommendation_allowed else "keep_current"
    if recommendation_allowed:
        recommendation_reason = (
            f"Recommended candidate '{selected_candidate['candidate_id']}' saves {route_cost_saved_min:.1f} min "
            f"of total route cost under the same updated conditions. "
            f"Predicted delay changes by {-delay_improvement_min:+.1f} min and road duration changes by "
            f"{selected_candidate['road_duration_min'] - current_candidate['road_duration_min']:+.1f} min."
        )
    elif not no_dropped_stops:
        recommendation_reason = "No recommendation: the solver dropped at least one required remaining stop."
    elif not validated_decision.get("valid", False):
        recommendation_reason = "No recommendation: the AI-selected candidate failed backend validation."
    elif route_cost_saved_min <= 0.1:
        recommendation_reason = (
            "No recommendation: candidate route generation did not find a lower-cost route under the same updated conditions."
        )
    else:
        recommendation_reason = "No recommendation: the selected route is not materially better than keeping the current route."

    delta = {
        "distance_km": round(scenario_metrics["distance_km"] - baseline_metrics["distance_km"], 2),
        "duration_min": round(scenario_metrics["duration_min"] - baseline_metrics["duration_min"], 1),
        "expected_delay_min": round(scenario_metrics["expected_delay_min"] - baseline_metrics["expected_delay_min"], 1),
        "risk_score": round(scenario_metrics["risk_score"] - baseline_metrics["risk_score"], 3),
        "high_risk_stops": scenario_metrics["high_risk_stops"] - baseline_metrics["high_risk_stops"],
        "severe_stops": scenario_metrics["severe_stops"] - baseline_metrics["severe_stops"],
    }
    schedule_comparison = {
        "current_order": current_schedule_summary,
        "optimized_order": optimized_schedule_summary,
        "saved_min": delay_improvement_min,
        "improved_stop_count": improved_stop_count,
        "worsened_stop_count": worsened_stop_count,
        "unchanged_stop_count": unchanged_stop_count,
    }
    debug_breakdown = {
        "endpoint": "/api/v1/scenario/reoptimize",
        "metric_definition": "Predicted delay is the sum of per-stop ML expected delay for the remaining stops under the same updated conditions.",
        "controls_applied": body.controls.model_dump(),
        "matrix_condition_factor": round(
            1.0
            + (body.controls.traffic_density / 100.0) * 0.25
            + (body.controls.accident_severity / 100.0) * 0.16
            + (body.controls.road_disruption / 100.0) * 0.22
            + _effective_weather_ratio(body.controls.weather_condition, body.controls.weather_severity) * 0.10,
            4,
        ),
        "current_route_delay_under_scenario_min": current_schedule_summary["total_operational_delay_min"],
        "recommended_route_delay_under_scenario_min": optimized_schedule_summary["total_operational_delay_min"],
        "current_route_lateness_penalty_min": current_schedule_summary["total_schedule_delay_min"],
        "recommended_route_lateness_penalty_min": optimized_schedule_summary["total_schedule_delay_min"],
        "current_route_optimizer_cost_min": current_schedule_summary["route_cost_with_return_min"],
        "recommended_route_optimizer_cost_min": selected_candidate["total_cost_min"],
        "active_route_mutated_before_apply": False,
        "stop_order_before": sequence_before,
        "stop_order_after": sequence_after,
        "candidate_route_scoring_formula": (
            "total_cost = road_travel_time + ML_predicted_delay + time_window_lateness "
            "+ localized_segment_penalty + route_stability_penalty"
        ),
        "candidates_evaluated": len(candidates),
        "stop_orders_evaluated": len({tuple(candidate["stop_order"]) for candidate in candidates}),
        "path_alternatives_evaluated": sum(candidate["path_alternatives_evaluated"] for candidate in candidates),
        "selected_candidate_id": selected_candidate["candidate_id"],
        "ai_decision": ai_decision,
        "validated_decision": validated_decision,
    }

    return ScenarioReoptimizationResponse(
        baseline_summary=baseline_result["route_summary"],
        scenario_summary=scenario_result["route_summary"],
        baseline_route=current_order_route,
        current_order_route=current_order_route,
        scenario_route=scenario_result["optimized_route"],
        baseline_geometry=baseline_route_data["geometry"],
        scenario_geometry=scenario_route_data["geometry"],
        baseline_metrics=baseline_metrics,
        scenario_metrics=scenario_metrics,
        delta=delta,
        baseline_comparison=baseline_result.get("optimization_comparison"),
        scenario_comparison=scenario_comparison,
        factor_impacts=factor_impacts,
        controls_applied=body.controls,
        order_changed=order_changed,
        sequence_before=sequence_before,
        sequence_after=sequence_after,
        explanation=_scenario_explanation(
            body.controls,
            baseline_metrics,
            scenario_metrics,
            order_changed,
            len(body.segment_overrides),
            optimization_delta,
            recommendation_allowed,
        ),
        recommendation_allowed=recommendation_allowed,
        recommendation_status=recommendation_status,
        recommendation_reason=recommendation_reason,
        optimization_delta=optimization_delta,
        schedule_comparison=schedule_comparison,
        mapbox_alternatives=mapbox_alternatives,
        debug_breakdown=debug_breakdown,
        candidates_evaluated=len(candidates),
        stop_orders_evaluated=len({tuple(candidate["stop_order"]) for candidate in candidates}),
        path_alternatives_evaluated=sum(candidate["path_alternatives_evaluated"] for candidate in candidates),
        candidate_routes=candidates,
        selected_candidate_id=selected_candidate["candidate_id"],
        selected_candidate=selected_candidate,
        change_type=_candidate_change_type(selected_candidate, current_candidate, recommendation_allowed),
        no_better_route=not recommendation_allowed,
        validation=selected_candidate.get("validation", {}),
        ai_decision=ai_decision,
        validated_decision=validated_decision,
        decision_facts=ai_decision.get("facts_used", {}),
    )


@router.get("/model-info", tags=["ml"], summary="ML model metadata and health")
async def model_info() -> dict:
    """
    ML model health-check.

    Returns model version, feature lists, thresholds, and categorical encodings.
    Also acts as a warm-up endpoint — triggers model load on first call.
    """
    try:
        return get_model_info()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/explain", response_model=ExplanationResult, tags=["ai"], summary="Generate natural language route explanation")
async def explain_route(request: ExplainRequest) -> ExplanationResult:
    """
    Generate a dispatcher-friendly natural language explanation for a previously
    optimized route — without re-running the optimization pipeline.

    Pass the ``route_summary`` and ``optimized_route`` fields from a
    ``/optimize`` or ``/full-route`` response.

    The explanation is produced by a local Ollama LLM and describes:
    - Overall route risk in plain language
    - Top risk factors (e.g. cascade delays, tight windows)
    - Concrete actions the dispatcher should take
    - Per-stop alerts for high-risk or window-missing stops

    Ollama must be running locally (``ollama serve``) or reachable at
    ``OLLAMA_BASE_URL`` for Docker/VM deployments.
    """
    route_summary_dict  = request.route_summary.model_dump()
    optimized_route_list = [s.model_dump() for s in request.optimized_route]

    raw = generate_explanation(
        route_summary=route_summary_dict,
        optimized_route=optimized_route_list,
        use_p90=request.use_p90,
    )

    if "error" in raw:
        return ExplanationResult(
            overall_assessment="",
            risk_factors=[],
            recommendations=[],
            stop_alerts=[],
            model_used="",
            generation_time_ms=0,
            error=raw["error"],
        )

    return ExplanationResult(**raw)


@router.get("/explain/health", tags=["ai"], summary="Ollama LLM service health check")
async def explain_health() -> dict:
    """
    Check whether Ollama is reachable and the configured model is available.

    Useful for the dispatcher UI to show a warning when AI explanations are
    unavailable, and for ops to verify the Ollama service is up.

    Returns:
        status       : "ok" | "unreachable" | "error"
        ollama_url   : the URL being used (from OLLAMA_BASE_URL env var)
        configured_model : model name (from OLLAMA_MODEL env var)
        available_models : list of models pulled on the Ollama server
        model_ready  : bool — True if configured_model is available
    """
    return check_ollama_health()


# ── Scenario Persistence Endpoints ─────────────────────────────────────────────

@router.post("/scenario/create", response_model=ScenarioResponse, tags=["scenario"])
def create_scenario(req: ScenarioCreateRequest, db: Session = Depends(get_db)):
    db_scenario = Scenario(
        name=req.name,
        controls=req.controls.model_dump(),
        segment_overrides=[s.model_dump() for s in req.segment_overrides],
        stops=[s.model_dump() for s in req.stops]
    )
    db.add(db_scenario)
    db.commit()
    db.refresh(db_scenario)
    return db_scenario

@router.get("/scenario/list", response_model=list[ScenarioResponse], tags=["scenario"])
def list_scenarios(db: Session = Depends(get_db)):
    scenarios = db.query(Scenario).order_by(Scenario.id.desc()).all()
    result = []
    for s in scenarios:
        data = s.__dict__.copy()
        data["created_at"] = s.created_at.isoformat()
        data["updated_at"] = s.updated_at.isoformat()
        result.append(data)
    return result

@router.get("/scenario/{id}", response_model=ScenarioResponse, tags=["scenario"])
def get_scenario(id: int, db: Session = Depends(get_db)):
    scenario = db.query(Scenario).filter(Scenario.id == id).first()
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")
    data = scenario.__dict__.copy()
    data["created_at"] = scenario.created_at.isoformat()
    data["updated_at"] = scenario.updated_at.isoformat()
    return data

@router.post("/scenario/{id}/update-segment", response_model=ScenarioResponse, tags=["scenario"])
def update_scenario_segment(id: int, req: SegmentConditionOverride, db: Session = Depends(get_db)):
    scenario = db.query(Scenario).filter(Scenario.id == id).first()
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")
    
    overrides = scenario.segment_overrides.copy() if scenario.segment_overrides else []
    updated = False
    for i, s in enumerate(overrides):
        if s.get("from_stop_id") == req.from_stop_id and s.get("to_stop_id") == req.to_stop_id:
            overrides[i] = req.model_dump()
            updated = True
            break
    if not updated:
        overrides.append(req.model_dump())
        
    scenario.segment_overrides = overrides
    db.commit()
    db.refresh(scenario)
    data = scenario.__dict__.copy()
    data["created_at"] = scenario.created_at.isoformat()
    data["updated_at"] = scenario.updated_at.isoformat()
    return data

@router.post("/scenario/{id}/optimize", response_model=ScenarioReoptimizationResponse, tags=["scenario"])
async def optimize_scenario(id: int, db: Session = Depends(get_db)):
    raise HTTPException(status_code=501, detail="Optimize via ID requires depot coordinates in the model. This workflow is under construction.")


# ── Route Lifecycle Endpoints (MVP) ──────────────────────────────────────────

_route_lifecycle_state: dict[int, dict] = {}


class ApplyRecommendationRequest(BaseModel):
    """Payload used to make recommendation application auditable."""

    active_stop_ids: list[str] = Field(default_factory=list)
    completed_stop_ids: list[str] = Field(default_factory=list)
    recommended_stop_ids: list[str] = Field(default_factory=list)
    scenario_geometry: dict | None = None
    scenario_summary: dict | None = None


def _validate_recommendation_payload(body: ApplyRecommendationRequest | None) -> dict:
    """Validate that a recommendation contains exactly the future stops."""
    if body is None:
        return {"valid": True, "warnings": ["legacy apply without recommendation payload"]}

    active_ids = [str(item) for item in body.active_stop_ids if str(item)]
    completed_ids = [str(item) for item in body.completed_stop_ids if str(item)]
    recommended_ids = [str(item) for item in body.recommended_stop_ids if str(item)]

    active_set = set(active_ids)
    completed_set = set(completed_ids)
    recommended_set = set(recommended_ids)
    remaining_set = active_set - completed_set

    errors: list[str] = []
    if len(recommended_ids) != len(recommended_set):
        errors.append("recommended route contains duplicate stops")
    if recommended_set & completed_set:
        errors.append(
            "recommended route includes completed stops: "
            + ", ".join(sorted(recommended_set & completed_set))
        )
    missing = sorted(remaining_set - recommended_set)
    unexpected = sorted(recommended_set - remaining_set)
    if missing:
        errors.append("recommended route is missing remaining stops: " + ", ".join(missing))
    if unexpected:
        errors.append("recommended route includes unexpected stops: " + ", ".join(unexpected))
    if active_ids and len(recommended_ids) != len(remaining_set):
        errors.append(
            f"recommended count {len(recommended_ids)} does not match remaining count {len(remaining_set)}"
        )

    return {
        "valid": not errors,
        "errors": errors,
        "active_count": len(active_set),
        "completed_count": len(completed_set),
        "remaining_count": len(remaining_set),
        "recommended_count": len(recommended_set),
        "missing_stop_ids": missing,
        "unexpected_stop_ids": unexpected,
    }


def _stop_aliases(stop: Stop) -> set[str]:
    """Return every stable identifier that may refer to the same route stop."""
    aliases = {str(stop.id)}
    if stop.name:
        name = str(stop.name)
        aliases.add(name)
        match = re.search(r"(\d+)$", name)
        if match:
            aliases.add(match.group(1))
            aliases.add(str(int(match.group(1))))
    return aliases


def _resolve_route_stop_ids(db_stops: list[Stop], raw_ids: list[str]) -> tuple[list[str], list[str]]:
    """Resolve scenario/StopPool aliases to the concrete Stop.id values for this route."""
    alias_to_stop: dict[str, Stop] = {}
    for stop in db_stops:
        for alias in _stop_aliases(stop):
            alias_to_stop[alias] = stop

    resolved: list[str] = []
    unknown: list[str] = []
    for raw_id in raw_ids:
        stop = alias_to_stop.get(str(raw_id))
        if stop is None:
            unknown.append(str(raw_id))
        else:
            resolved.append(str(stop.id))
    return resolved, unknown

def _get_lifecycle_state(route_id: int) -> dict:
    if route_id not in _route_lifecycle_state:
        _route_lifecycle_state[route_id] = {
            "status": "planned",
            "applied_recommendation": None,
            "scenario_conditions": None
        }
    return _route_lifecycle_state[route_id]

@router.get("/routes/{id}/state", tags=["lifecycle"])
def get_route_state(id: int):
    return _get_lifecycle_state(id)

@router.post("/routes/{id}/dispatch/start", tags=["lifecycle"])
def start_dispatch(id: int):
    state = _get_lifecycle_state(id)
    state["status"] = "dispatched"
    fleet_store.update_status(id, "active")
    return state

@router.post("/routes/{id}/conditions/update", tags=["lifecycle"])
def update_conditions(id: int, conditions: dict):
    state = _get_lifecycle_state(id)
    state["scenario_conditions"] = conditions
    # This might trigger recommendation_available in a real system
    # For MVP, we allow frontend to trigger recalculate
    return state

@router.post("/routes/{id}/recommendation/recalculate", tags=["lifecycle"])
def recalculate_recommendation(id: int):
    state = _get_lifecycle_state(id)
    state["status"] = "recommendation_available"
    # Logic to populate new recommendation is normally done here
    return state

@router.post("/routes/{id}/recommendation/apply", tags=["lifecycle"])
def apply_recommendation(
    id: int,
    body: ApplyRecommendationRequest | None = None,
    db: Session = Depends(get_db),
):
    state = _get_lifecycle_state(id)
    route = db.get(Route, id) if body is not None else None
    db_stops: list[Stop] = []
    body_for_apply = body

    if body is not None and route is not None:
        db_stops = (
            db.query(Stop)
            .filter(Stop.route_id == id)
            .all()
        )
        resolved_active, unknown_active = _resolve_route_stop_ids(db_stops, body.active_stop_ids)
        resolved_completed, unknown_completed = _resolve_route_stop_ids(db_stops, body.completed_stop_ids)
        resolved_recommended, unknown_recommended = _resolve_route_stop_ids(db_stops, body.recommended_stop_ids)
        unknown = sorted(set(unknown_active + unknown_completed + unknown_recommended))
        if unknown:
            raise HTTPException(status_code=422, detail={
                "message": "Recommendation includes stops that do not belong to this route",
                "unknown_stop_ids": unknown,
            })
        body_for_apply = ApplyRecommendationRequest(
            active_stop_ids=resolved_active,
            completed_stop_ids=resolved_completed,
            recommended_stop_ids=resolved_recommended,
            scenario_geometry=body.scenario_geometry,
            scenario_summary=body.scenario_summary,
        )

    validation = _validate_recommendation_payload(body_for_apply)
    if not validation["valid"]:
        raise HTTPException(status_code=422, detail={
            "message": "Recommendation validation failed",
            "validation": validation,
        })

    if body_for_apply is not None:
        if body_for_apply.scenario_summary and body_for_apply.scenario_summary.get("recommendation_allowed") is False:
            raise HTTPException(status_code=422, detail={
                "message": "Recommendation was not applied because the optimizer did not find a better route",
                "reason": body_for_apply.scenario_summary.get("recommendation_status", "keep_current"),
            })
        if route is not None and body_for_apply.recommended_stop_ids:
            completed = set(str(item) for item in body_for_apply.completed_stop_ids)
            stop_by_id = {str(stop.id): stop for stop in db_stops}
            unknown = [sid for sid in body_for_apply.recommended_stop_ids if str(sid) not in stop_by_id]
            if unknown:
                raise HTTPException(status_code=422, detail={
                    "message": "Recommendation includes stops that do not belong to this route",
                    "unknown_stop_ids": unknown,
                })

            completed_count = 0
            for stop in db_stops:
                if str(stop.id) in completed:
                    stop.status = StopStatus.completed
                    completed_count += 1

            for position, stop_id in enumerate(body_for_apply.recommended_stop_ids, start=completed_count + 1):
                stop = stop_by_id[str(stop_id)]
                if str(stop.id) in completed:
                    raise HTTPException(status_code=422, detail={
                        "message": "Completed stop cannot be placed in future route",
                        "stop_id": str(stop.id),
                    })
                stop.sequence = position
                if stop.status == StopStatus.completed:
                    stop.status = StopStatus.pending

            route.status = RouteStatus.active
            db.commit()

    state["status"] = "in_progress"
    state["applied_recommendation"] = True
    if body_for_apply is not None:
        state["active_stop_ids"] = [str(item) for item in body_for_apply.active_stop_ids]
        state["completed_stop_ids"] = [str(item) for item in body_for_apply.completed_stop_ids]
        state["current_sequence"] = [str(item) for item in body_for_apply.recommended_stop_ids]
        state["scenario_geometry"] = body_for_apply.scenario_geometry
        state["scenario_summary"] = body_for_apply.scenario_summary
        state["validation"] = validation
    state["last_transition"] = "recommendation_applied"
    return state

@router.post("/routes/{id}/lifecycle/reset", tags=["lifecycle"])
def reset_lifecycle(id: int):
    """Reset route lifecycle to planned state (demo/testing only)."""
    _route_lifecycle_state[id] = {
        "status": "planned",
        "applied_recommendation": None,
        "scenario_conditions": None
    }
    return _route_lifecycle_state[id]

# ── Health and status endpoints ─────────────────────────────────────────────

@router.get("/health", tags=["system"])
def health_check():
    return {"status": "ok"}

@router.get("/data/status", tags=["data"])
def data_status():
    return {"status": "ok", "pipeline_active": True}

@router.post("/model/evaluate", tags=["ml"])
def evaluate_model():
    import subprocess
    try:
        subprocess.run(["python", "reports/generate_evaluation_reports.py"], check=True)
        return {"status": "success", "message": "Evaluation reports generated."}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {e}")

@router.post("/reports/generate-model-graphs", tags=["ml"])
def generate_graphs():
    import subprocess
    try:
        subprocess.run(["python", "reports/generate_evaluation_reports.py"], check=True)
        return {"status": "success", "message": "Graphs generated."}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Graph generation failed: {e}")

# ── Agent Endpoint ────────────────────────────────────────────────────────────

class AgentExplainRequest(BaseModel):
    route_id: int
    route_state: str
    scenario_conditions: dict
    before_metrics: dict
    after_metrics: dict
    changed_segments: list = []
    stop_order_before: list = []
    stop_order_after: list = []
    user_question: str = ""

@router.post("/agent/recommendation/explain", tags=["agent"])
async def explain_agent_recommendation(request: AgentExplainRequest):
    from ai.agent import process_recommendation
    return await asyncio.to_thread(
        process_recommendation,
        route_id=request.route_id,
        metrics={"before": request.before_metrics, "after": request.after_metrics},
        conditions=request.scenario_conditions,
        stop_order_before=request.stop_order_before,
        stop_order_after=request.stop_order_after,
        user_question=request.user_question,
    )

@router.get("/agent/evaluation/summary", tags=["agent"])
def get_agent_evaluation_summary():
    import os
    import pandas as pd
    
    project_root = os.path.dirname(os.path.dirname(__file__))
    summary_path = os.path.join(project_root, "evaluation_results", "rag", "rag_summary_statistics.csv")
    results_path = os.path.join(project_root, "evaluation_results", "rag", "rag_evaluation_results.csv")
    
    if not os.path.exists(summary_path) or not os.path.exists(results_path):
        return {"status": "unavailable", "reason": "Evaluation has not been run yet."}
        
    try:
        summary_df = pd.read_csv(summary_path)
        metrics = {str(row.iloc[0]): float(row.iloc[1]) for _, row in summary_df.iterrows()}
        
        results_df = pd.read_csv(results_path)
        hallucination_blocked_count = int((results_df["Hallucination_Pass"] == 0.0).sum())
        fallback_used_count = int((results_df["Fallback_Used"] == 1).sum())
        total_questions = len(results_df)
        
        return {
            "status": "available",
            "metrics": metrics,
            "hallucination_blocked_count": hallucination_blocked_count,
            "fallback_used_count": fallback_used_count,
            "total_questions": total_questions,
            "file_paths": {
                "summary_csv": "evaluation_results/rag/rag_summary_statistics.csv",
                "results_csv": "evaluation_results/rag/rag_evaluation_results.csv",
                "heatmap_image": "evaluation_results/rag/rag_metric_heatmap.png",
                "average_scores_image": "evaluation_results/rag/rag_average_scores.png"
            }
        }
    except Exception as e:
        return {"status": "error", "reason": str(e)}
