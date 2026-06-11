"""
Demo simulation WebSocket.

Couriers move along the active road geometry sent by the frontend. Road-condition
recalculation only produces a pending recommendation; the frontend sends a
reroute message after the user applies that recommendation.

Stop-completion detection uses proximity radius. The default 0.20 km (200 m)
is appropriate for the Sivas dataset where Mapbox road geometry follows road
centerlines while stop coordinates are at building/POI addresses (typically
50-200 m offset). Configure via SIM_STOP_RADIUS_KM environment variable.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from api.location_store import courier_store

logger = logging.getLogger(__name__)

router = APIRouter(tags=["simulation"])

UPDATE_INTERVAL_S = float(os.getenv("SIM_UPDATE_INTERVAL_S", "1.0"))
DEFAULT_DWELL_S = float(os.getenv("SIM_DWELL_SECONDS", "1.0"))

# Configurable stop detection radius.
# Default 0.20 km (200 m) — appropriate for Sivas demo data where road
# geometry is 50-200 m from stop coordinates. Override with env var.
STOP_RADIUS_KM = float(os.getenv("SIM_STOP_RADIUS_KM", "0.20"))

# REST skip endpoint -> active WebSocket session. The demo runs one simulation.
_skip_queue: asyncio.Queue = asyncio.Queue()


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return radius_km * 2 * math.asin(math.sqrt(a))


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _build_cumulative(coords: list) -> list[float]:
    cum = [0.0]
    for i in range(1, len(coords)):
        d = _haversine_km(coords[i - 1][1], coords[i - 1][0], coords[i][1], coords[i][0])
        cum.append(cum[-1] + d)
    return cum


def _find_stop_dist_on_route(coords: list, cum: list[float], stop_lat: float, stop_lon: float) -> float:
    """Return cumulative km along route at the vertex closest to the stop."""
    min_d = float("inf")
    best_cum = 0.0
    for i, (lon, lat) in enumerate(coords):
        d = _haversine_km(stop_lat, stop_lon, lat, lon)
        if d < min_d:
            min_d = d
            best_cum = cum[i]
    return best_cum


def _stop_lat_lon(stop: dict) -> tuple[float | None, float | None]:
    stop_lat = stop.get("lat", stop.get("latitude"))
    stop_lon = stop.get("lon", stop.get("longitude"))
    try:
        return float(stop_lat), float(stop_lon)
    except (TypeError, ValueError):
        return None, None


def _order_stops_by_route_progress(coords: list, cum: list[float], stops: list) -> list:
    """Sort future stops by where they appear on the route geometry."""
    ordered = []
    for index, stop in enumerate(stops or []):
        stop_lat, stop_lon = _stop_lat_lon(stop)
        if stop_lat is None or stop_lon is None:
            progress_km = float("inf")
        else:
            progress_km = _find_stop_dist_on_route(coords, cum, stop_lat, stop_lon)
        next_stop = dict(stop)
        next_stop["route_progress_km"] = progress_km if math.isfinite(progress_km) else None
        ordered.append((progress_km, index, next_stop))
    return [stop for _, _, stop in sorted(ordered, key=lambda item: (item[0], item[1]))]


def _route_progress_for_stop(route: dict, stop: dict) -> float | None:
    progress = stop.get("route_progress_km")
    try:
        progress = float(progress)
    except (TypeError, ValueError):
        progress = None
    if progress is not None and math.isfinite(progress):
        return progress

    stop_lat, stop_lon = _stop_lat_lon(stop)
    if stop_lat is None or stop_lon is None:
        return None
    return _find_stop_dist_on_route(route["coords"], route["cum"], stop_lat, stop_lon)


def _interpolate(coords: list, cum: list[float], target_km: float) -> tuple[float, float, float]:
    """Return lon, lat, heading at target_km along the route."""
    target_km = min(target_km, cum[-1])
    for i in range(len(cum) - 1):
        if cum[i] <= target_km <= cum[i + 1]:
            seg = cum[i + 1] - cum[i]
            t = (target_km - cum[i]) / seg if seg > 0 else 0.0
            lon = coords[i][0] + (coords[i + 1][0] - coords[i][0]) * t
            lat = coords[i][1] + (coords[i + 1][1] - coords[i][1]) * t
            hdg = _bearing(coords[i][1], coords[i][0], coords[i + 1][1], coords[i + 1][0])
            return lon, lat, hdg
    lon, lat = coords[-1]
    hdg = _bearing(coords[-2][1], coords[-2][0], lat, lon) if len(coords) >= 2 else 0.0
    return lon, lat, hdg


def _project_dist_on_route(coords: list, cum: list[float], lat: float, lon: float) -> float:
    """Project a current lon/lat position onto a route and return cumulative km."""
    if len(coords) < 2:
        return 0.0

    ref_lat_rad = math.radians(lat)

    def to_local_km(point_lon: float, point_lat: float) -> tuple[float, float]:
        x = (point_lon - lon) * 111.320 * math.cos(ref_lat_rad)
        y = (point_lat - lat) * 110.574
        return x, y

    best_sq = float("inf")
    best_km = 0.0
    for i in range(len(coords) - 1):
        ax, ay = to_local_km(coords[i][0], coords[i][1])
        bx, by = to_local_km(coords[i + 1][0], coords[i + 1][1])
        vx, vy = bx - ax, by - ay
        denom = vx * vx + vy * vy
        t = 0.0 if denom == 0 else max(0.0, min(1.0, -(ax * vx + ay * vy) / denom))
        px, py = ax + t * vx, ay + t * vy
        sq = px * px + py * py
        if sq < best_sq:
            best_sq = sq
            best_km = cum[i] + (cum[i + 1] - cum[i]) * t
    return best_km


async def _message_listener(ws: WebSocket, queue: asyncio.Queue) -> None:
    """Receive reroute messages from the frontend and put them in the queue."""
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "reroute":
                await queue.put(msg)
    except (WebSocketDisconnect, Exception):
        pass


@router.post(
    "/api/v1/simulation/next-stop",
    tags=["simulation"],
    summary="Jump simulation to the next stop arrival",
)
async def skip_to_next_stop():
    """
    Signal the active simulation WebSocket to jump to whichever courier's next
    unvisited stop is closest in route-distance terms.
    """
    await _skip_queue.put({"type": "skip"})
    return JSONResponse(status_code=202, content={"status": "queued"})


@router.websocket("/ws/simulation")
async def simulation_ws(ws: WebSocket):
    await ws.accept()
    reroute_queue: asyncio.Queue = asyncio.Queue()

    try:
        config = await ws.receive_json()

        vehicles = config.get("vehicles", [])
        speed_kmh = float(config.get("speed_kmh", 50.0))
        loop = bool(config.get("loop", False))

        if not vehicles:
            await ws.send_json({"type": "error", "message": "vehicles list is empty"})
            return

        states: dict[str, dict] = {}
        for vehicle in vehicles:
            coords = vehicle["coordinates"]
            if len(coords) < 2:
                continue
            cum = _build_cumulative(coords)
            states[vehicle["courier_id"]] = {
                "courier_id": vehicle["courier_id"],
                "name": vehicle.get("name", vehicle["courier_id"]),
                "vehicle_id": vehicle.get("vehicle_id", 0),
                "color": vehicle.get("color"),
                "coords": coords,
                "cum": cum,
                "total_km": cum[-1],
                "stops": _order_stops_by_route_progress(coords, cum, vehicle.get("stops", [])),
                "dist_km": 0.0,
                "visited_stops": set(),
                "next_stop_index": 0,
                "dwell_remaining_s": 0.0,
                "done": False,
            }

        await ws.send_json({"type": "started", "vehicle_count": len(states)})

        while not _skip_queue.empty():
            _skip_queue.get_nowait()

        listener = asyncio.create_task(_message_listener(ws, reroute_queue))
        step_km = (speed_kmh / 3600.0) * UPDATE_INTERVAL_S

        try:
            while True:
                while not reroute_queue.empty():
                    msg = reroute_queue.get_nowait()
                    cid = msg.get("courier_id")
                    new_coords = msg.get("coordinates")
                    if cid in states and new_coords and len(new_coords) >= 2:
                        route = states[cid]
                        current_lon, current_lat, _ = _interpolate(
                            route["coords"],
                            route["cum"],
                            route["dist_km"],
                        )
                        new_cum = _build_cumulative(new_coords)
                        route["coords"] = new_coords
                        route["cum"] = new_cum
                        route["total_km"] = new_cum[-1]
                        route["dist_km"] = min(
                            _project_dist_on_route(new_coords, new_cum, current_lat, current_lon),
                            route["total_km"],
                        )
                        if isinstance(msg.get("stops"), list):
                            route["stops"] = _order_stops_by_route_progress(new_coords, new_cum, msg["stops"])
                            # Reset ordered index — the visited_stops set
                            # is preserved so already-completed stops stay done.
                            route["next_stop_index"] = 0
                            # Advance next_stop_index past already-visited stops
                            while (route["next_stop_index"] < len(route["stops"])
                                   and str(route["stops"][route["next_stop_index"]].get("stop_id", ""))
                                   in route["visited_stops"]):
                                route["next_stop_index"] += 1
                        route["dwell_remaining_s"] = 0.0
                        route["done"] = False

                if not _skip_queue.empty():
                    _skip_queue.get_nowait()
                    while not _skip_queue.empty():
                        _skip_queue.get_nowait()

                    best_cid = None
                    best_stop = None
                    best_target_dist = float("inf")
                    best_gap = float("inf")

                    for cid, route in states.items():
                        if route["done"] or route["dwell_remaining_s"] > 0:
                            continue
                        nsi = route["next_stop_index"]
                        if nsi >= len(route["stops"]):
                            continue
                        stop = route["stops"][nsi]
                        stop_id = str(stop.get("stop_id") or stop.get("id") or "")
                        if not stop_id or stop_id in route["visited_stops"]:
                            continue
                        target_dist = _route_progress_for_stop(route, stop)
                        if target_dist is None or target_dist <= route["dist_km"]:
                            continue
                        gap = target_dist - route["dist_km"]
                        if gap < best_gap:
                            best_gap = gap
                            best_cid = cid
                            best_stop = stop
                            best_target_dist = target_dist

                    if best_cid is not None:
                        route = states[best_cid]
                        route["dist_km"] = min(best_target_dist, route["total_km"])
                        stop_id = str(best_stop["stop_id"])
                        route["visited_stops"].add(stop_id)
                        # Advance next_stop_index past visited stops
                        while (route["next_stop_index"] < len(route["stops"])
                               and str(route["stops"][route["next_stop_index"]].get("stop_id", ""))
                               in route["visited_stops"]):
                            route["next_stop_index"] += 1
                        route["dwell_remaining_s"] = float(best_stop.get("dwell_seconds", DEFAULT_DWELL_S))
                        logger.info(
                            "[SIM-SKIP] AT_STOP %s stop=%s completed=%d/%d",
                            best_cid, stop_id,
                            len(route["visited_stops"]),
                            len(route["stops"]),
                        )
                        await ws.send_json({
                            "type": "at_stop",
                            "courier_id": best_cid,
                            "stop_id": stop_id,
                            "completed_count": len(route["visited_stops"]),
                            "total_count": len(route["stops"]),
                        })

                positions = []
                all_done = True

                for cid, route in states.items():
                    if route["done"]:
                        lon, lat = route["coords"][-1]
                        positions.append({
                            "courier_id": cid,
                            "name": route["name"],
                            "latitude": round(lat, 6),
                            "longitude": round(lon, 6),
                            "heading": 0.0,
                            "speed_kmh": 0.0,
                            "vehicle_id": route["vehicle_id"],
                            "status": "completed",
                        })
                        continue

                    all_done = False

                    if route["dwell_remaining_s"] > 0:
                        route["dwell_remaining_s"] -= UPDATE_INTERVAL_S
                        lon, lat, heading = _interpolate(route["coords"], route["cum"], route["dist_km"])
                        positions.append({
                            "courier_id": cid,
                            "name": route["name"],
                            "latitude": round(lat, 6),
                            "longitude": round(lon, 6),
                            "heading": round(heading, 1),
                            "speed_kmh": 0.0,
                            "vehicle_id": route["vehicle_id"],
                            "status": "at_stop",
                        })
                        continue

                    previous_dist_km = route["dist_km"]
                    route["dist_km"] = min(previous_dist_km + step_km, route["total_km"])
                    lon, lat, heading = _interpolate(route["coords"], route["cum"], route["dist_km"])

                    # --- Ordered stop completion: only check the NEXT expected stop ---
                    nsi = route["next_stop_index"]
                    if nsi < len(route["stops"]):
                        stop = route["stops"][nsi]
                        stop_id = stop.get("stop_id") or stop.get("id")
                        stop_lat = stop.get("lat") or stop.get("latitude")
                        stop_lon = stop.get("lon") or stop.get("longitude")
                        if stop_id is not None and stop_lat is not None and stop_lon is not None:
                            stop_id = str(stop_id)
                            dist = _haversine_km(lat, lon, float(stop_lat), float(stop_lon))
                            target_dist = _route_progress_for_stop(route, stop)
                            reached_by_progress = (
                                target_dist is not None
                                and target_dist <= route["dist_km"] + 1e-9
                            )
                            logger.debug(
                                "[SIM] %s next_stop=%s dist=%.4f km radius=%.3f km progress=%s",
                                cid, stop_id, dist, STOP_RADIUS_KM, target_dist,
                            )
                            if reached_by_progress or dist < STOP_RADIUS_KM:
                                if (
                                    reached_by_progress
                                    and target_dist is not None
                                    and previous_dist_km <= target_dist <= route["total_km"]
                                ):
                                    route["dist_km"] = target_dist
                                    lon, lat, heading = _interpolate(
                                        route["coords"],
                                        route["cum"],
                                        route["dist_km"],
                                    )
                                route["visited_stops"].add(stop_id)
                                route["next_stop_index"] = nsi + 1
                                route["dwell_remaining_s"] = float(
                                    stop.get("dwell_seconds", DEFAULT_DWELL_S)
                                )
                                logger.info(
                                    "[SIM] AT_STOP %s stop=%s completed=%d/%d remaining=%d",
                                    cid, stop_id,
                                    len(route["visited_stops"]),
                                    len(route["stops"]),
                                    len(route["stops"]) - len(route["visited_stops"]),
                                )
                                await ws.send_json({
                                    "type": "at_stop",
                                    "courier_id": cid,
                                    "stop_id": stop_id,
                                    "completed_count": len(route["visited_stops"]),
                                    "total_count": len(route["stops"]),
                                })

                    if route["dist_km"] >= route["total_km"] and route["dwell_remaining_s"] <= 0:
                        if loop:
                            route["dist_km"] = 0.0
                        else:
                            route["done"] = True

                    status = "completed" if route["done"] else "active"
                    pos = {
                        "courier_id": cid,
                        "name": route["name"],
                        "latitude": round(lat, 6),
                        "longitude": round(lon, 6),
                        "heading": round(heading, 1),
                        "speed_kmh": speed_kmh if not route["done"] else 0.0,
                        "vehicle_id": route["vehicle_id"],
                        "status": status,
                    }
                    positions.append(pos)
                    courier_store.upsert(cid, dict(pos))

                await ws.send_json({"type": "position_update", "couriers": positions})

                if all_done and not loop:
                    await ws.send_json({"type": "completed"})
                    break

                await asyncio.sleep(UPDATE_INTERVAL_S)

        finally:
            listener.cancel()

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
