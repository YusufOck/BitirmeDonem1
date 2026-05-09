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


def _scenario_explanation(
    controls,
    baseline_metrics: dict,
    scenario_metrics: dict,
    order_changed: bool,
    segment_override_count: int = 0,
) -> str:
    delay_delta = scenario_metrics["expected_delay_min"] - baseline_metrics["expected_delay_min"]
    time_delta = scenario_metrics["duration_min"] - baseline_metrics["duration_min"]
    order_text = "changed the stop order" if order_changed else "kept the same stop order"
    segment_text = (
        f" {segment_override_count} manually edited segment penalties were also applied to the route matrix."
        if segment_override_count
        else ""
    )
    return (
        f"The scenario was evaluated with the trained ML delay model, not by a visual shortcut. "
        f"Traffic density {controls.traffic_density}/100, accident severity {controls.accident_severity}/100, "
        f"{controls.weather_condition} weather at {controls.weather_severity}/100 intensity, and road disruption "
        f"{controls.road_disruption}/100 were converted into model features. OR-Tools then {order_text} using "
        f"the updated ML delay costs and Mapbox road-time matrix.{segment_text} Expected delay changed by {delay_delta:+.1f} min "
        f"and road duration changed by {time_delta:+.1f} min."
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

    baseline_coords = _coords_from_route(
        baseline_result["optimized_route"],
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

    baseline_metrics = _route_metrics(baseline_route_data, baseline_result)
    scenario_metrics = _route_metrics(scenario_route_data, scenario_result)
    sequence_before = [stop.get("stop_name") for stop in baseline_result["optimized_route"]]
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

    return ScenarioReoptimizationResponse(
        baseline_summary=baseline_result["route_summary"],
        scenario_summary=scenario_result["route_summary"],
        baseline_route=baseline_result["optimized_route"],
        scenario_route=scenario_result["optimized_route"],
        baseline_geometry=baseline_route_data["geometry"],
        scenario_geometry=scenario_route_data["geometry"],
        baseline_metrics=baseline_metrics,
        scenario_metrics=scenario_metrics,
        delta=delta,
        baseline_comparison=baseline_result.get("optimization_comparison"),
        scenario_comparison=scenario_result.get("optimization_comparison"),
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
        ),
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
def apply_recommendation(id: int):
    state = _get_lifecycle_state(id)
    state["status"] = "in_progress"
    state["applied_recommendation"] = True
    return state

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
def explain_agent_recommendation(request: AgentExplainRequest):
    from ai.agent import process_recommendation
    return process_recommendation(
        route_id=request.route_id,
        metrics={"before": request.before_metrics, "after": request.after_metrics},
        conditions=request.scenario_conditions,
        stop_order_before=request.stop_order_before,
        stop_order_after=request.stop_order_after,
        user_question=request.user_question
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
