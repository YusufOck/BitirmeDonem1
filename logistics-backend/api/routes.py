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
from ml.inference import get_model_info
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
    weather_ratio = controls.weather_severity / 100.0
    disruption_ratio = controls.road_disruption / 100.0
    load_multiplier = 0.65 + (controls.package_load / 100.0) * 1.25
    overall_delay_factor = (
        1.0
        + (controls.traffic_density / 100.0) * 0.55
        + accident_ratio * 0.55
        + weather_ratio * 0.35
        + disruption_ratio * 0.45
    )

    scenario_factors = _build_scenario_factors(
        controls,
        congestion_ratio=congestion_ratio,
        delay_factor=overall_delay_factor,
    )

    modified: list[dict] = []
    for stop in stops:
        current = deepcopy(stop)
        base_hist = float(current.get("hist_delay_probability") or 0.25)
        base_slack = float(current.get("time_window_slack_min") or 75.0)
        package_weight = float(current.get("package_weight_kg") or 25.0)
        package_count = int(current.get("package_count") or 1)
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
            "incident_rate": round(max(float(current.get("incident_rate") or 0.05), accident_ratio * 0.75, disruption_ratio * 0.55), 4),
            "overall_delay_factor": round(overall_delay_factor * stop_risk_amplifier, 3),
            "hist_delay_probability": round(min(0.95, base_hist + accident_ratio * 0.22 + disruption_ratio * 0.18 + weather_ratio * 0.12), 4),
            "delay_risk_score_mean": round(min(0.98, float(current.get("delay_risk_score_mean") or base_hist) + weather_ratio * 0.25 + accident_ratio * 0.15), 4),
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
    factor = (
        1.0
        + (controls.traffic_density / 100.0) * 0.45
        + (controls.accident_severity / 100.0) * 0.25
        + (controls.road_disruption / 100.0) * 0.35
        + (controls.weather_severity / 100.0) * 0.18
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


def _route_from_node_order(
    stops_data: list[dict],
    stop_predictions: list[dict],
    node_order: list[int],
    vehicle_id: int = 0,
) -> list[dict]:
    """Build an OptimizedStop-compatible route for an explicit stop-node order."""
    route: list[dict] = []
    for delivery_position, node_index in enumerate(node_order):
        stop_idx = int(node_index) - 1
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

    The old UI compared route-level OR-Tools cost with raw per-stop ML delay,
    which could say "saved" while most stop rows looked worse.  This helper
    creates one comparable metric for both totals and rows: road leg cost +
    ML delay/risk penalties + ETA/window lateness under the evaluated order.
    """
    ordered = sorted(route, key=lambda stop: (stop.get("vehicle_id", 0), stop.get("optimized_position", 0)))
    annotated: list[dict] = []
    previous_node = 0
    elapsed_min = 0.0
    total_operational = 0.0
    total_ml_delay = 0.0
    total_travel = 0.0
    late_stop_count = 0

    for position, stop in enumerate(ordered):
        node = int(stop.get("original_stop_index", position)) + 1
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
        slack = float(stop.get("time_window_slack_min") or destination_stop.get("time_window_slack_min") or 480.0)
        schedule_delay = max(0.0, (elapsed_min + ml_delay) - slack)
        operational_delay = float(leg_cost["total_cost_min"]) + schedule_delay

        row = deepcopy(stop)
        row["optimized_position"] = position
        row["planned_travel_min"] = round(float(leg_cost["base_travel_min"]), 1)
        row["leg_travel_min"] = round(float(leg_cost["base_travel_min"]), 1)
        row["arrival_eta_min"] = round(elapsed_min, 1)
        row["ml_delay_min"] = round(ml_delay, 1)
        row["schedule_delay_min"] = round(schedule_delay, 1)
        row["operational_delay_min"] = round(operational_delay, 1)
        row["cost_breakdown"] = leg_cost
        annotated.append(row)

        total_operational += operational_delay
        total_ml_delay += ml_delay
        total_travel += float(leg_cost["base_travel_min"])
        if schedule_delay > 0.1:
            late_stop_count += 1
        elapsed_min += ml_delay
        previous_node = node

    return_travel = _matrix_leg_minutes(previous_node, 0, stops_data, travel_time_matrix)
    summary = {
        "total_operational_delay_min": round(total_operational, 1),
        "total_ml_delay_min": round(total_ml_delay, 1),
        "total_travel_min": round(total_travel, 1),
        "return_to_depot_min": round(return_travel, 1),
        "route_cost_with_return_min": round(total_operational + return_travel, 1),
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
            f"Under the same updated road conditions, keeping the current order costs {before_cost:.1f} min "
            f"of operational delay-risk and the optimized order costs {after_cost:.1f} min, saving {saved_cost:.1f} min."
            if before_cost is not None and after_cost is not None
            else f"The optimized order saves {saved_cost:.1f} min of operational delay-risk."
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
        f"Raw ML delay changed by {delay_delta:+.1f} min and road duration changed by {time_delta:+.1f} min compared with the previous conditions."
    )



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

    stops_as_dicts = [stop.model_dump() for stop in body.stops]
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
        scenario_result = await asyncio.to_thread(
            _optimizer.optimize,
            stops_input=scenario_stops,
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

    scenario_predictions = scenario_result.get("ml_predictions", {}).get("stop_predictions", [])
    current_order_route = _route_from_node_order(
        scenario_stops,
        scenario_predictions,
        list(range(1, len(scenario_stops) + 1)),
    )
    current_order_route, current_schedule_summary = _annotate_route_operational_cost(
        current_order_route,
        scenario_stops,
        scenario_predictions,
        scenario_matrix,
        use_p90=body.controls.conservative_mode,
    )
    optimized_order_route, optimized_schedule_summary = _annotate_route_operational_cost(
        scenario_result["optimized_route"],
        scenario_stops,
        scenario_predictions,
        scenario_matrix,
        use_p90=body.controls.conservative_mode,
    )
    scenario_result["optimized_route"] = optimized_order_route

    baseline_coords = _coords_from_route(
        current_order_route,
        body.depot_latitude,
        body.depot_longitude,
    )
    scenario_coords = _coords_from_route(
        scenario_result["optimized_route"],
        body.depot_latitude,
        body.depot_longitude,
    )

    baseline_route_data = await asyncio.to_thread(get_final_route, baseline_coords, token)
    scenario_route_data = await asyncio.to_thread(get_final_route, scenario_coords, token)
    if baseline_route_data is None or scenario_route_data is None:
        raise HTTPException(status_code=502, detail="Mapbox Directions API request failed")

    raw_alternatives = await asyncio.to_thread(get_route_alternatives, scenario_coords, token, 3)
    mapbox_alternatives = []
    for alt in raw_alternatives:
        if (
            abs(float(alt["distance"]) - float(scenario_route_data["distance"])) < 1.0
            and abs(float(alt["duration"]) - float(scenario_route_data["duration"])) < 1.0
        ):
            continue
        mapbox_alternatives.append({
            "rank": alt["rank"],
            "geometry": alt["geometry"],
            "distance_m": alt["distance"],
            "duration_s": alt["duration"],
            "duration_min": round(float(alt["duration"]) / 60.0, 1),
            "distance_km": round(float(alt["distance"]) / 1000.0, 2),
            "source": "mapbox_directions_alternative",
        })

    baseline_metrics = _route_metrics(baseline_route_data, scenario_result)
    scenario_metrics = _route_metrics(scenario_route_data, scenario_result)
    baseline_metrics.update({
        "expected_delay_min": current_schedule_summary["total_operational_delay_min"],
        "ml_delay_min": current_schedule_summary["total_ml_delay_min"],
        "travel_time_min": current_schedule_summary["total_travel_min"],
        "late_stop_count": current_schedule_summary["late_stop_count"],
        "comparison_basis": "current_order_under_updated_conditions",
    })
    scenario_metrics.update({
        "expected_delay_min": optimized_schedule_summary["total_operational_delay_min"],
        "ml_delay_min": optimized_schedule_summary["total_ml_delay_min"],
        "travel_time_min": optimized_schedule_summary["total_travel_min"],
        "late_stop_count": optimized_schedule_summary["late_stop_count"],
        "comparison_basis": "optimized_order_under_updated_conditions",
    })
    scenario_comparison = scenario_result.get("optimization_comparison") or {}
    raw_cost_improvement_min = round(float(scenario_comparison.get("improvement_min") or 0.0), 1)
    operational_improvement_min = round(
        current_schedule_summary["total_operational_delay_min"]
        - optimized_schedule_summary["total_operational_delay_min"],
        1,
    )

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

    raw_original_cost = round(float(scenario_comparison.get("original_cost_min") or 0.0), 1)
    raw_optimized_cost = round(float(scenario_comparison.get("optimized_cost_min") or 0.0), 1)
    optimization_delta = {
        "original_cost_min": raw_original_cost,
        "optimized_cost_min": raw_optimized_cost,
        "improvement_min": raw_cost_improvement_min,
        "improvement_pct": round(float(scenario_comparison.get("improvement_pct") or 0.0), 2),
        "current_operational_delay_min": current_schedule_summary["total_operational_delay_min"],
        "optimized_operational_delay_min": optimized_schedule_summary["total_operational_delay_min"],
        "operational_delay_saved_min": operational_improvement_min,
        "improved_stop_count": improved_stop_count,
        "worsened_stop_count": worsened_stop_count,
        "unchanged_stop_count": unchanged_stop_count,
        "comparison_basis": "same_updated_conditions",
    }
    no_dropped_stops = not scenario_result.get("vrp_result", {}).get("dropped_nodes")
    enough_stop_level_benefit = improved_stop_count >= max(1, worsened_stop_count)
    recommendation_allowed = (
        scenario_comparison.get("status") == "improved"
        and raw_cost_improvement_min > 0.1
        and operational_improvement_min > 0.1
        and enough_stop_level_benefit
        and no_dropped_stops
    )
    recommendation_status = "recommended" if recommendation_allowed else "keep_current"
    if recommendation_allowed:
        recommendation_reason = (
            f"Recommended because the optimized order saves {operational_improvement_min:.1f} min of operational delay-risk "
            f"under the same updated conditions and improves {improved_stop_count}/{len(optimized_order_route)} stops."
        )
    elif not no_dropped_stops:
        recommendation_reason = "No recommendation: the solver dropped at least one required remaining stop."
    elif operational_improvement_min <= 0.1:
        recommendation_reason = (
            "No recommendation: the recalculated route does not reduce operational delay-risk under the same updated conditions."
        )
    elif not enough_stop_level_benefit:
        recommendation_reason = (
            "No recommendation: the route-level score improved, but too many individual stops would become worse."
        )
    else:
        recommendation_reason = "No recommendation: the recalculated route is not materially better than keeping the current order."
    sequence_before = [stop.get("stop_name") for stop in current_order_route]
    sequence_after = [stop.get("stop_name") for stop in scenario_result["optimized_route"]]
    order_changed = sequence_before != sequence_after

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
        "saved_min": operational_improvement_min,
        "improved_stop_count": improved_stop_count,
        "worsened_stop_count": worsened_stop_count,
        "unchanged_stop_count": unchanged_stop_count,
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
