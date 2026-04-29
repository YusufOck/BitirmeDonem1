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
    VehicleRoute,
)
from optimization.route_optimizer import RouteOptimizer
from optimization.mapbox_adapter import mapbox_to_pipeline
from mapbox.matrix_api import get_weight_matrices
from mapbox.directions_api import get_final_route
from mapbox.coordinate import Coordinate
from ml.inference import get_model_info
from ai.explainer import check_ollama_health, generate_explanation
from api.fleet_store import fleet_store
from cache.redis_client import set_matrix_cache, set_matrix_index_map, set_route_state, set_stops_cache
from db.models import Courier, Route, RouteStatus, Stop, StopPool, StopStatus, User, UserRole

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

        courier_stops: list[StopInput] = []
        for i, sid in enumerate(assignment.stop_pool_ids):
            sp = stops_db[sid]
            courier_stops.append(StopInput(
                stop_sequence=i + 1,
                stop_id=str(sp.id),
                stop_name=sp.name,
                latitude=sp.latitude,
                longitude=sp.longitude,
                cumulative_delay_min=0.0,
                prev_stop_delay_min=0.0,
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
        use_p90=body.use_p90,
        num_vehicles=len(body.assignments),
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
