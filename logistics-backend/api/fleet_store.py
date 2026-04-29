"""
fleet_store.py
--------------
In-memory cache for active route optimization results + DB sync layer.

In-memory store provides sub-millisecond reads for the dispatcher dashboard.
DB writes happen on every mutation so state survives restarts.

    save_route(...)     → upsert in-memory + update Route fields in DB
    update_status(...)  → update in-memory + update Route.status in DB
    delete_route(...)   → remove from in-memory (Route stays in DB)
    get(...)            → in-memory lookup
    get_all_active()    → in-memory filtered list (seeded from DB on startup)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _derive_delay_status(summary: dict) -> str:
    """green / amber / red — mirrors SDD §7.2 colour-coding convention."""
    severe = summary.get("severe_stop_count", 0)
    high = summary.get("high_risk_stop_count", 0)
    total_delay = summary.get("expected_total_delay_min", 0.0)
    if severe >= 2 or total_delay >= 25:
        return "red"
    if high >= 1 or total_delay >= 10:
        return "amber"
    return "green"


class FleetStore:
    def __init__(self) -> None:
        self._routes: dict[int, dict[str, Any]] = {}

    # ── Write operations ──────────────────────────────────────────────────────

    def save_route(
        self,
        route_id: int,
        route_summary: dict,
        optimized_route: list[dict],
        num_vehicles: int,
        use_p90: bool,
        db=None,
    ) -> dict:
        """Upsert in-memory + sync optimization result to DB Route if db given."""
        now = datetime.now(timezone.utc).isoformat()
        existing = self._routes.get(route_id)
        record: dict[str, Any] = {
            "route_id": route_id,
            "status": "active",
            "delay_status": _derive_delay_status(route_summary),
            "num_vehicles": num_vehicles,
            "use_p90": use_p90,
            "route_summary": route_summary,
            "stop_count": len(optimized_route),
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
        }
        self._routes[route_id] = record

        if db is not None:
            try:
                from db.models import Route, RouteStatus
                route = db.get(Route, route_id)
                if route:
                    raw_delay = route_summary.get("expected_total_delay_min")
                    route.total_delay_min = float(raw_delay) if raw_delay is not None else None
                    route.total_distance_m = None   # filled by full-route via vehicle_routes
                    route.total_duration_s = None
                    if route.status == RouteStatus.planned:
                        route.status = RouteStatus.active
                    db.commit()
            except Exception:
                pass  # DB sync is best-effort — in-memory record always written

        return record

    def update_status(self, route_id: int, status: str, db=None) -> bool:
        """Update status in-memory and in DB."""
        if route_id not in self._routes:
            return False
        self._routes[route_id]["status"] = status
        self._routes[route_id]["updated_at"] = datetime.now(timezone.utc).isoformat()

        if db is not None:
            try:
                from db.models import Route, RouteStatus
                route = db.get(Route, route_id)
                if route:
                    route.status = RouteStatus(status) if status in ("active", "planned") else (
                        RouteStatus.completed if status == "completed" else RouteStatus.cancelled
                    )
                    if status == "completed":
                        route.completed_at = datetime.utcnow()
                    db.commit()
            except Exception:
                pass

        return True

    def delete_route(self, route_id: int) -> bool:
        return bool(self._routes.pop(route_id, None))

    # ── Read operations ───────────────────────────────────────────────────────

    def get(self, route_id: int) -> dict | None:
        return self._routes.get(route_id)

    def get_all_active(self) -> list[dict]:
        return [r for r in self._routes.values() if r["status"] == "active"]

    def get_all(self) -> list[dict]:
        return list(self._routes.values())

    def count_active(self) -> int:
        return sum(1 for r in self._routes.values() if r["status"] == "active")

    # ── Aggregate helpers ─────────────────────────────────────────────────────

    def risk_breakdown(self) -> dict[str, int]:
        counts: dict[str, int] = {"green": 0, "amber": 0, "red": 0}
        for r in self.get_all_active():
            ds = r.get("delay_status", "green")
            counts[ds] = counts.get(ds, 0) + 1
        return counts


fleet_store = FleetStore()
