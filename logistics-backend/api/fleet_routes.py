"""
fleet_routes.py
---------------
Fleet-level monitoring and system health endpoints.

  GET   /api/v1/fleet/risk-summary          — Aggregated risk view across all tracked routes
  PATCH /api/v1/fleet/routes/{id}/status    — Mark route completed / cancelled
  GET   /api/v1/health                      — ML + Mapbox + Ollama system health check
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db

from ai.explainer import check_ollama_health
from api.fleet_store import fleet_store
from api.schemas import FleetRiskSummaryResponse, FleetRouteSummary
from ml.inference import get_model_info

router = APIRouter(prefix="/api/v1", tags=["fleet"])


@router.get("/fleet/risk-summary", response_model=FleetRiskSummaryResponse, summary="Aggregated risk view across all active routes")
def fleet_risk_summary() -> FleetRiskSummaryResponse:
    """
    Aggregated risk view across all active routes in the fleet store.

    Routes are registered automatically when /optimize or /full-route is called
    with a non-null route_id field. Use PATCH .../status to retire routes.
    """
    active = fleet_store.get_all_active()
    breakdown = fleet_store.risk_breakdown()

    route_summaries = [
        FleetRouteSummary(
            route_id=r["route_id"],
            status=r["status"],
            delay_status=r["delay_status"],
            num_vehicles=r["num_vehicles"],
            stop_count=r["stop_count"],
            high_risk_stop_count=r["route_summary"].get("high_risk_stop_count", 0),
            severe_stop_count=r["route_summary"].get("severe_stop_count", 0),
            expected_total_delay_min=r["route_summary"].get("expected_total_delay_min", 0.0),
            worst_case_total_delay_min=r["route_summary"].get("worst_case_total_delay_min"),
            dropped_stop_count=len(r["route_summary"].get("dropped_stop_indices", [])),
            updated_at=r["updated_at"],
        )
        for r in active
    ]

    return FleetRiskSummaryResponse(
        timestamp=datetime.now(timezone.utc).isoformat(),
        total_active_routes=len(active),
        at_risk_routes=breakdown["amber"] + breakdown["red"],
        green_routes=breakdown["green"],
        amber_routes=breakdown["amber"],
        red_routes=breakdown["red"],
        routes=route_summaries,
    )


@router.patch("/fleet/routes/{route_id}/status", summary="Update route lifecycle status (active / completed / cancelled)")
def update_route_status(
    route_id: int,
    status: str,
    db: Session = Depends(get_db),
) -> dict:
    """
    Update a tracked route's lifecycle status in both fleet store and DB.

    Valid values: active | completed | cancelled
    """
    valid = {"active", "completed", "cancelled"}
    if status not in valid:
        raise HTTPException(status_code=422, detail=f"status must be one of {valid}")

    # Update DB first — source of truth
    from db.models import Route, RouteStatus
    route = db.get(Route, route_id)
    if not route:
        raise HTTPException(status_code=404, detail=f"Route {route_id} not found")

    route.status = RouteStatus(status) if status in ("active", "planned") else (
        RouteStatus.completed if status == "completed" else RouteStatus.cancelled
    )
    if status == "completed":
        from datetime import datetime
        route.completed_at = datetime.utcnow()
    db.commit()

    # Keep in-memory store in sync
    fleet_store.update_status(route_id, status)

    return {"route_id": route_id, "status": status, "ok": True}


@router.get("/health", tags=["system"], summary="System health check (ML, Mapbox, Ollama, fleet store)")
def health_check() -> dict:
    """
    System health check across all backend services.

    Returns overall status: "ok" | "degraded" | "down"

    Checks:
    - ML model: can the model be loaded and queried?
    - Mapbox: is MAPBOX_TOKEN configured?
    - Ollama: is the local LLM service reachable?
    - Fleet store: in-memory state summary (DB type shown for migration awareness)
    """
    services: dict = {}
    overall = "ok"

    # ── ML model ──────────────────────────────────────────────────────────────
    try:
        info = get_model_info()
        services["ml_model"] = {
            "status": "ok",
            "version": info["model_version"],
            "has_p90": info["has_p90"],
            "clf_features": info["n_clf_features"],
        }
    except Exception as exc:
        services["ml_model"] = {"status": "error", "error": str(exc)}
        overall = "degraded"

    # ── Mapbox ────────────────────────────────────────────────────────────────
    token = os.getenv("MAPBOX_TOKEN", "")
    services["mapbox"] = {
        "status": "ok" if token else "not_configured",
        "token_configured": bool(token),
    }
    if not token and overall == "ok":
        overall = "degraded"

    # ── Ollama ────────────────────────────────────────────────────────────────
    try:
        ollama = check_ollama_health()
        services["ollama"] = ollama
        if ollama.get("status") != "ok" and overall == "ok":
            overall = "degraded"
    except Exception as exc:
        services["ollama"] = {"status": "error", "error": str(exc)}
        if overall == "ok":
            overall = "degraded"

    # ── Fleet store ───────────────────────────────────────────────────────────
    services["fleet_store"] = {
        "status": "ok",
        "type": "in_memory",
        "active_routes": fleet_store.count_active(),
    }

    return {
        "status": overall,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "services": services,
    }
