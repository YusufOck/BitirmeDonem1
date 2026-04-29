"""
courier_routes.py
-----------------
Courier-facing endpoints.

    GET    /api/v1/couriers/{courier_id}/routes   → today's active/planned routes
    PATCH  /api/v1/stops/{stop_id}/complete       → mark stop completed + trigger re-opt
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from database import get_db
from db.models import (
    Package, PackageStatus, Route, RouteStatus,
    Stop, StopStatus,
)
from api.dispatch_routes import RouteOut, StopOut
from cache.redis_client import (
    get_matrix_cache,
    get_matrix_index_map,
    get_route_state,
    get_stops_cache,
    update_route_state,
    set_matrix_cache,
    set_matrix_index_map,
    set_route_suggestion,
    get_route_suggestion,
    delete_route_suggestion,
)

router = APIRouter(prefix="/api/v1", tags=["courier"])


# ── Request schemas ───────────────────────────────────────────────────────────

class CompleteStopRequest(BaseModel):
    actual_delay_min: float | None = None


# ── Response schemas ──────────────────────────────────────────────────────────

class ReoptResult(BaseModel):
    triggered: bool
    previous_sequence: list[int] | None = None  # reopt öncesi sıra (stop DB id'leri)
    new_sequence: list[int] | None = None        # reopt sonrası sıra (stop DB id'leri)
    estimated_time_savings_min: float | None = None
    reason: str | None = None               # re-opt yapılamadıysa neden
    geometry: dict | None = None            # Mapbox Directions GeoJSON — YENİ rota
    previous_geometry: dict | None = None   # Mapbox Directions GeoJSON — ESKİ rota (karşılaştırma için)
    suggestion_id: str | None = None        # UUID for accepting/rejecting this suggestion

class CompleteStopResponse(BaseModel):
    stop: StopOut
    reopt: ReoptResult


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/couriers/{courier_id}/routes", response_model=list[RouteOut])
def get_courier_routes(courier_id: int, db: Session = Depends(get_db)):
    routes = (
        db.query(Route)
        .options(selectinload(Route.stops).selectinload(Stop.packages))
        .filter(
            Route.courier_id == courier_id,
            Route.status.in_([RouteStatus.planned, RouteStatus.active]),
        )
        .all()
    )
    return routes


@router.patch("/stops/{stop_id}/complete", response_model=CompleteStopResponse, tags=["optimization"], summary="Mark stop completed and trigger live re-optimization")
def complete_stop(stop_id: int, body: CompleteStopRequest, db: Session = Depends(get_db)):
    stop = (
        db.query(Stop)
        .options(selectinload(Stop.packages))
        .filter(Stop.id == stop_id)
        .first()
    )
    if not stop:
        raise HTTPException(status_code=404, detail="Stop not found")
    if stop.status == StopStatus.completed:
        raise HTTPException(status_code=400, detail="Stop already completed")

    # ── 1. DB: stop tamamlandı ────────────────────────────────────────────────
    stop.status = StopStatus.completed
    stop.arrived_at = datetime.utcnow()
    stop.actual_delay_min = body.actual_delay_min

    for package in stop.packages:
        package.status = PackageStatus.delivered
        package.delivered_at = datetime.utcnow()

    route = db.get(Route, stop.route_id)
    if route and route.status == RouteStatus.planned:
        route.status = RouteStatus.active
        route.started_at = datetime.utcnow()

    db.commit()
    db.refresh(stop)

    # ── 2. Re-optimization ────────────────────────────────────────────────────
    reopt = _trigger_reopt(stop, route, body.actual_delay_min or 0.0, db)

    return CompleteStopResponse(stop=StopOut.model_validate(stop), reopt=reopt)


# ── Re-optimization helper ────────────────────────────────────────────────────

def _trigger_reopt(
    completed_stop: Stop,
    route: Route,
    actual_delay_min: float,
    db: Session,
) -> ReoptResult:
    """
    Kalan durakları re-optimize et.
    Redis'ten matrix cache ve route state oku.
    Yeni sırayı DB'ye yaz ve Redis state'i güncelle.
    """
    route_id = route.id

    # Kalan pending stop'ları al
    remaining_stops = [
        s for s in route.stops
        if s.status == StopStatus.pending
    ]

    if len(remaining_stops) < 2:
        return ReoptResult(
            triggered=False,
            reason="Less than 2 remaining stops — no resequencing needed",
        )

    # Redis'ten matrix cache kontrol et; yoksa Mapbox'tan yeniden çek ve cache'le
    try:
        cached = get_matrix_cache(route_id)
    except Exception:
        return ReoptResult(
            triggered=False,
            reason="Redis unavailable — resequence skipped",
        )
    if cached is None:
        token = os.getenv("MAPBOX_TOKEN")
        if not token:
            return ReoptResult(
                triggered=False,
                reason="Mapbox matrix not cached and MAPBOX_TOKEN missing — resequence skipped",
            )
        all_stops_sorted = sorted(route.stops, key=lambda s: s.sequence)
        depot_lat = route.depot_latitude
        depot_lon = route.depot_longitude
        if depot_lat is None or depot_lon is None:
            return ReoptResult(
                triggered=False,
                reason="Mapbox matrix not cached and depot coordinates missing — resequence skipped",
            )
        coords = [Coordinate(lat=depot_lat, lon=depot_lon, name="depot")] + [
            Coordinate(lat=s.latitude, lon=s.longitude, name=s.name or str(s.id))
            for s in all_stops_sorted
        ]
        duration_matrix, distance_matrix = get_weight_matrices(coords, token)
        if duration_matrix is None or distance_matrix is None:
            return ReoptResult(
                triggered=False,
                reason="Mapbox matrix fetch failed — resequence skipped",
            )
        set_matrix_cache(route_id, duration_matrix, distance_matrix)
        matrix_idx_map = {str(s.id): i + 1 for i, s in enumerate(all_stops_sorted)}
        set_matrix_index_map(route_id, matrix_idx_map)
    else:
        duration_matrix, distance_matrix = cached

    # Redis'ten mevcut route state'i al
    try:
        state = get_route_state(route_id) or {}
    except Exception:
        state = {}
    cumulative_delay = state.get("cumulative_delay_min", 0.0) + actual_delay_min
    completed_count = state.get("completed_stop_count", 0) + 1

    # Matrix index mapping: stop_id (str) → Mapbox matrix row/col index.
    # Depot is at index 0; stop indices start at 1 and reflect the original
    # request body order (NOT the sequence field), so we must use this map
    # instead of deriving indices from sequence-sorted position.
    idx_map = get_matrix_index_map(route_id) or {}

    def _midx(stop: Stop) -> int:
        """Mapbox matrix index for a stop. Falls back to sequence order if map missing."""
        mapped = idx_map.get(str(stop.id))
        if mapped is not None:
            return mapped
        all_ids = [s.id for s in sorted(route.stops, key=lambda s: s.sequence)]
        return all_ids.index(stop.id) + 1

    completed_midx = _midx(completed_stop)
    remaining_midxs = [_midx(s) for s in remaining_stops]

    # Submatrix: completed_stop as new depot → remaining stops
    sub_size = len(remaining_stops) + 1
    sub_duration = [[0.0] * sub_size for _ in range(sub_size)]

    for i, ri in enumerate(remaining_midxs):
        sub_duration[0][i + 1] = duration_matrix[completed_midx][ri]
        sub_duration[i + 1][0] = duration_matrix[ri][completed_midx]
        for j, rj in enumerate(remaining_midxs):
            sub_duration[i + 1][j + 1] = duration_matrix[ri][rj]

    # Travel time matrix (dakika) — submatrix'ten depot satırı/sütunu çıkar
    travel_time_matrix = [
        [sub_duration[i + 1][j + 1] / 60.0 for j in range(len(remaining_stops))]
        for i in range(len(remaining_stops))
    ]

    # Stop feature dict'lerini oluştur
    stops_input = []
    for i, s in enumerate(remaining_stops):
        stops_input.append({
            "stop_id": str(s.id),
            "stop_name": s.name,
            "latitude": s.latitude,
            "longitude": s.longitude,
            "stop_sequence": completed_count + i + 1,
            "cumulative_delay_min": cumulative_delay,
            "prev_stop_delay_min": actual_delay_min if i == 0 else 0.0,
            "time_window_slack_min": s.time_window_slack_min,
        })

    # Optimizer çalıştır
    try:
        from optimization.route_optimizer import RouteOptimizer
        optimizer = RouteOptimizer()

        # Optimized run
        result = optimizer.optimize(
            stops_input=stops_input,
            travel_time_matrix=travel_time_matrix,
        )

        # Baseline run — mevcut sırayla (time_limit=2s, ilk çözümü al)
        baseline = optimizer.optimize(
            stops_input=stops_input,
            travel_time_matrix=travel_time_matrix,
            time_limit_seconds=2,
        )
    except Exception as exc:
        return ReoptResult(triggered=False, reason=f"Optimizer error: {exc}")

    # Reopt öncesi sırayı sakla
    previous_sequence = [s.id for s in sorted(remaining_stops, key=lambda s: s.sequence)]

    # Yeni sırayı hemen DB'ye YAZMIYORUZ (Suggestion olarak tutacağız)
    optimized = sorted(result["optimized_route"], key=lambda s: s["optimized_position"])
    
    new_sequence = [int(s["stop_id"]) for s in optimized]

    # ── Mapbox Directions — eski ve yeni rota geometrisi ─────────────────────
    geometry = None
    previous_geometry = None
    token = os.getenv("MAPBOX_TOKEN")
    stop_lookup = {s.id: s for s in remaining_stops}
    origin = Coordinate(
        lat=completed_stop.latitude,
        lon=completed_stop.longitude,
        name="current_position",
    )
    if token:
        try:
            # Yeni rota: optimized sırayla
            new_coords = [origin] + [
                Coordinate(lat=stop_lookup[sid].latitude, lon=stop_lookup[sid].longitude,
                           name=stop_lookup[sid].name or str(sid))
                for sid in new_sequence
            ]
            route_data = get_final_route(new_coords, token)
            if route_data:
                geometry = route_data.get("geometry")
        except Exception:
            pass

        try:
            # Eski rota: reopt öncesi sırayla (frontend'de karşılaştırma için)
            prev_coords = [origin] + [
                Coordinate(lat=stop_lookup[sid].latitude, lon=stop_lookup[sid].longitude,
                           name=stop_lookup[sid].name or str(sid))
                for sid in previous_sequence
            ]
            prev_data = get_final_route(prev_coords, token)
            if prev_data:
                previous_geometry = prev_data.get("geometry")
        except Exception:
            pass  # Directions hatası re-opt'u engellemesin

    # Zaman tasarrufu: baseline - optimized (gerçek fark)
    baseline_delay   = baseline["route_summary"].get("expected_total_delay_min", 0.0)
    optimized_delay  = result["route_summary"].get("expected_total_delay_min", 0.0)
    time_savings     = max(round(baseline_delay - optimized_delay, 2), 0.0)

    # Suggestion oluştur ve kaydet
    suggestion_id = str(uuid.uuid4())
    suggestion_data = {
        "new_sequence": new_sequence,
        "completed_count": completed_count,
    }
    set_route_suggestion(route_id, suggestion_id, suggestion_data)

    # Redis state'de sadece tamamlanan durakları güncelle, sırayı elleme
    update_route_state(route_id, {
        "cumulative_delay_min": cumulative_delay,
        "completed_stop_count": completed_count,
        "last_reopt_at": datetime.utcnow().isoformat(),
    })

    return ReoptResult(
        triggered=True,
        previous_sequence=previous_sequence,
        new_sequence=new_sequence,
        estimated_time_savings_min=time_savings,
        geometry=geometry,
        previous_geometry=previous_geometry,
        suggestion_id=suggestion_id,
    )

# ── Decision Endpoint ─────────────────────────────────────────────────────────

class SuggestionDecisionRequest(BaseModel):
    action: str  # "accept" | "reject"

@router.post("/routes/{route_id}/suggestion/{suggestion_id}/decision", summary="Accept or reject a re-optimized route suggestion")
def make_suggestion_decision(route_id: int, suggestion_id: str, body: SuggestionDecisionRequest, db: Session = Depends(get_db)):
    if body.action not in ("accept", "reject"):
        raise HTTPException(status_code=400, detail="Action must be 'accept' or 'reject'")

    suggestion = get_route_suggestion(route_id, suggestion_id)
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found or expired")

    if body.action == "accept":
        new_sequence = suggestion["new_sequence"]
        completed_count = suggestion["completed_count"]

        # Apply sequence changes to DB
        for position, stop_id in enumerate(new_sequence):
            db.query(Stop).filter(Stop.id == stop_id).update({"sequence": completed_count + position})
        db.commit()

        # Update Redis route state to reflect the accepted sequence
        update_route_state(route_id, {
            "current_sequence": new_sequence,
        })

    delete_route_suggestion(route_id, suggestion_id)
    return {"ok": True, "action": body.action}
