"""
simulation_routes.py
--------------------
Demo simulation WebSocket — couriers move along planned route geometries.

  WS /ws/simulation

Protocol (frontend → backend):
  1. Connect
  2. Send config:
     {
       "vehicles": [
         {
           "courier_id": "courier-0",
           "name": "Kurye 1",
           "vehicle_id": 0,
           "coordinates": [[lon, lat], ...],        // full-route geometry
           "stops": [
             { "stop_id": "s1", "lat": 41.01, "lon": 28.97, "dwell_seconds": 30 }
           ],
           "color": "#60a5fa"
         }
       ],
       "speed_kmh": 40,
       "loop": false
     }
  3. Send reroute when re-opt returns new geometry:
     { "type": "reroute", "courier_id": "courier-0", "coordinates": [[lon, lat], ...] }
  4. Disconnect → simulation stops automatically

Protocol (backend → frontend):
  { "type": "started",        "vehicle_count": N }
  { "type": "position_update","couriers": [...] }
  { "type": "at_stop",        "courier_id": "...", "stop_id": "..." }
  { "type": "completed" }
  { "type": "error",          "message": "..." }
"""

from __future__ import annotations

import asyncio
import math

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from api.location_store import courier_store

router = APIRouter(tags=["simulation"])

UPDATE_INTERVAL_S = 2.0
STOP_RADIUS_KM = 0.03   # 30 metres
DEFAULT_DWELL_S = 30.0

# Shared queue: REST endpoint → active WS session (one simulation at a time for demo)
_skip_queue: asyncio.Queue = asyncio.Queue()


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


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
    """Return cumulative km along route at the vertex closest to (stop_lat, stop_lon)."""
    min_d = float('inf')
    best_cum = 0.0
    for i, (lon, lat) in enumerate(coords):
        d = _haversine_km(stop_lat, stop_lon, lat, lon)
        if d < min_d:
            min_d = d
            best_cum = cum[i]
    return best_cum


def _interpolate(coords: list, cum: list[float], target_km: float) -> tuple[float, float, float]:
    """Returns (lon, lat, heading) at target_km along the route."""
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


# ── Message listener ──────────────────────────────────────────────────────────

async def _message_listener(ws: WebSocket, queue: asyncio.Queue) -> None:
    """Receives reroute messages from frontend and puts them into the queue."""
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "reroute":
                await queue.put(msg)
    except (WebSocketDisconnect, Exception):
        pass


# ── REST: time-skip endpoint ──────────────────────────────────────────────────

@router.post("/api/v1/simulation/next-stop", tags=["simulation"],
             summary="Jump simulation to the next stop arrival")
async def skip_to_next_stop():
    """
    Signals the active simulation WebSocket to jump all vehicles forward to
    whichever courier's next unvisited stop is closest (in route-distance terms).
    Returns 202 immediately; the WS processes the jump within the next tick (~2 s).
    """
    await _skip_queue.put({"type": "skip"})
    return JSONResponse(status_code=202, content={"status": "queued"})


# ── WebSocket ─────────────────────────────────────────────────────────────────

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
            await ws.send_json({"type": "error", "message": "vehicles listesi boş"})
            return

        # ── Per-vehicle state ─────────────────────────────────────────────────
        states: dict[str, dict] = {}
        for v in vehicles:
            coords = v["coordinates"]
            if len(coords) < 2:
                continue
            cum = _build_cumulative(coords)
            states[v["courier_id"]] = {
                "courier_id": v["courier_id"],
                "name": v.get("name", v["courier_id"]),
                "vehicle_id": v.get("vehicle_id", 0),
                "color": v.get("color"),
                "coords": coords,
                "cum": cum,
                "total_km": cum[-1],
                "stops": v.get("stops", []),   # [{stop_id, lat, lon, dwell_seconds}]
                "dist_km": 0.0,
                "visited_stops": set(),
                "dwell_remaining_s": 0.0,
                "done": False,
            }

        await ws.send_json({"type": "started", "vehicle_count": len(states)})

        # Drain stale skip signals left over from a previous session
        while not _skip_queue.empty():
            _skip_queue.get_nowait()

        listener = asyncio.create_task(_message_listener(ws, reroute_queue))
        step_km = (speed_kmh / 3600.0) * UPDATE_INTERVAL_S

        try:
            while True:
                # ── Apply pending reroutes ────────────────────────────────────
                while not reroute_queue.empty():
                    msg = reroute_queue.get_nowait()
                    cid = msg.get("courier_id")
                    new_coords = msg.get("coordinates")
                    if cid in states and new_coords and len(new_coords) >= 2:
                        new_cum = _build_cumulative(new_coords)
                        r = states[cid]
                        r["coords"] = new_coords
                        r["cum"] = new_cum
                        r["total_km"] = new_cum[-1]
                        r["dist_km"] = 0.0
                        r["dwell_remaining_s"] = 0.0
                        r["done"] = False

                # ── Apply pending skip signals ────────────────────────────────
                if not _skip_queue.empty():
                    _skip_queue.get_nowait()
                    while not _skip_queue.empty():  # drain rapid duplicates
                        _skip_queue.get_nowait()

                    best_cid = None
                    best_stop = None
                    best_target_dist = float('inf')
                    best_gap = float('inf')

                    for cid, r in states.items():
                        if r["done"] or r["dwell_remaining_s"] > 0:
                            continue
                        for stop in r["stops"]:
                            if stop["stop_id"] in r["visited_stops"]:
                                continue
                            target_dist = _find_stop_dist_on_route(
                                r["coords"], r["cum"], stop["lat"], stop["lon"]
                            )
                            if target_dist <= r["dist_km"]:
                                continue
                            gap = target_dist - r["dist_km"]
                            if gap < best_gap:
                                best_gap = gap
                                best_cid = cid
                                best_stop = stop
                                best_target_dist = target_dist

                    if best_cid is not None:
                        r = states[best_cid]
                        r["dist_km"] = min(best_target_dist, r["total_km"])
                        sid = best_stop["stop_id"]
                        r["visited_stops"].add(sid)
                        r["dwell_remaining_s"] = float(best_stop.get("dwell_seconds", DEFAULT_DWELL_S))
                        await ws.send_json({
                            "type": "at_stop",
                            "courier_id": best_cid,
                            "stop_id": sid,
                        })

                # ── Tick ──────────────────────────────────────────────────────
                positions = []
                all_done = True

                for cid, r in states.items():
                    if r["done"]:
                        lon, lat = r["coords"][-1]
                        positions.append({
                            "courier_id": cid,
                            "name": r["name"],
                            "latitude": round(lat, 6),
                            "longitude": round(lon, 6),
                            "heading": 0.0,
                            "speed_kmh": 0.0,
                            "vehicle_id": r["vehicle_id"],
                            "status": "completed",
                        })
                        continue

                    all_done = False

                    # Dwell at stop — don't advance
                    if r["dwell_remaining_s"] > 0:
                        r["dwell_remaining_s"] -= UPDATE_INTERVAL_S
                        lon, lat, hdg = _interpolate(r["coords"], r["cum"], r["dist_km"])
                        positions.append({
                            "courier_id": cid,
                            "name": r["name"],
                            "latitude": round(lat, 6),
                            "longitude": round(lon, 6),
                            "heading": round(hdg, 1),
                            "speed_kmh": 0.0,
                            "vehicle_id": r["vehicle_id"],
                            "status": "at_stop",
                        })
                        continue

                    # Advance along route
                    r["dist_km"] = min(r["dist_km"] + step_km, r["total_km"])
                    lon, lat, hdg = _interpolate(r["coords"], r["cum"], r["dist_km"])

                    # Check stop proximity
                    for stop in r["stops"]:
                        sid = stop.get("stop_id") or stop.get("id")
                        slat = stop.get("lat") or stop.get("latitude")
                        slon = stop.get("lon") or stop.get("longitude")
                        if sid is None or slat is None or slon is None:
                            print(f"[SIM DEBUG] stop missing fields: {list(stop.keys())}")
                            continue
                        sid = str(sid)
                        if sid not in r["visited_stops"]:
                            dist = _haversine_km(lat, lon, float(slat), float(slon))
                            print(f"[SIM DEBUG] courier={cid} stop={sid} dist={dist:.4f}km radius={STOP_RADIUS_KM}")
                            if dist < STOP_RADIUS_KM:
                                r["visited_stops"].add(sid)
                                r["dwell_remaining_s"] = float(stop.get("dwell_seconds", DEFAULT_DWELL_S))
                                print(f"[SIM DEBUG] at_stop FIRED courier={cid} stop={sid}")
                                await ws.send_json({
                                    "type": "at_stop",
                                    "courier_id": cid,
                                    "stop_id": sid,
                                })
                                break

                    if r["dist_km"] >= r["total_km"] and r["dwell_remaining_s"] <= 0:
                        if loop:
                            r["dist_km"] = 0.0
                        else:
                            r["done"] = True

                    status = "completed" if r["done"] else "active"
                    pos = {
                        "courier_id": cid,
                        "name": r["name"],
                        "latitude": round(lat, 6),
                        "longitude": round(lon, 6),
                        "heading": round(hdg, 1),
                        "speed_kmh": speed_kmh if not r["done"] else 0.0,
                        "vehicle_id": r["vehicle_id"],
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
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
