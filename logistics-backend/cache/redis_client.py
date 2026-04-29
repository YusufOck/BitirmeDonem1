"""
redis_client.py
---------------
Redis client + key schema for SBTU Logistics active route state.

Key schema:
    route:{id}:state      → hash  — current_sequence, cumulative_delay, active_stop_index
    route:{id}:matrix     → hash  — duration_matrix, distance_matrix (JSON serialized)
    route:{id}:stops      → hash  — stop details keyed by position (JSON serialized)

TTL: 24 hours — active routes expire automatically after a day.
"""

from __future__ import annotations

import json
import os
from typing import Any

import redis

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_TTL_SECONDS = 60 * 60 * 24  # 24 hours

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(_REDIS_URL, decode_responses=True)
    return _client


# ── Key helpers ───────────────────────────────────────────────────────────────

def _state_key(route_id: int) -> str:
    return f"route:{route_id}:state"

def _matrix_key(route_id: int) -> str:
    return f"route:{route_id}:matrix"

def _stops_key(route_id: int) -> str:
    return f"route:{route_id}:stops"

def _matrix_idx_key(route_id: int) -> str:
    return f"route:{route_id}:matrix_idx"

def _suggestion_key(route_id: int, suggestion_id: str) -> str:
    return f"route:{route_id}:suggestion:{suggestion_id}"


# ── Route state ───────────────────────────────────────────────────────────────

def set_route_state(route_id: int, state: dict[str, Any]) -> None:
    """Save active route state. Resets TTL."""
    r = get_redis()
    key = _state_key(route_id)
    r.hset(key, mapping={k: json.dumps(v) for k, v in state.items()})
    r.expire(key, _TTL_SECONDS)


def get_route_state(route_id: int) -> dict[str, Any] | None:
    """Load active route state. Returns None if not found."""
    r = get_redis()
    raw = r.hgetall(_state_key(route_id))
    if not raw:
        return None
    return {k: json.loads(v) for k, v in raw.items()}


def update_route_state(route_id: int, updates: dict[str, Any]) -> None:
    """Partial update — only overwrites provided keys."""
    r = get_redis()
    key = _state_key(route_id)
    r.hset(key, mapping={k: json.dumps(v) for k, v in updates.items()})
    r.expire(key, _TTL_SECONDS)


# ── Mapbox matrix cache ───────────────────────────────────────────────────────

def set_matrix_cache(
    route_id: int,
    duration_matrix: list[list[float]],
    distance_matrix: list[list[float]],
) -> None:
    """Cache the Mapbox weight matrices for the route."""
    r = get_redis()
    key = _matrix_key(route_id)
    r.hset(key, mapping={
        "duration_matrix": json.dumps(duration_matrix),
        "distance_matrix": json.dumps(distance_matrix),
    })
    r.expire(key, _TTL_SECONDS)


def get_matrix_cache(route_id: int) -> tuple[list[list[float]], list[list[float]]] | None:
    """Returns (duration_matrix, distance_matrix) or None if not cached."""
    r = get_redis()
    raw = r.hgetall(_matrix_key(route_id))
    if not raw:
        return None
    return json.loads(raw["duration_matrix"]), json.loads(raw["distance_matrix"])


def set_matrix_index_map(route_id: int, stop_id_to_idx: dict[str, int]) -> None:
    """Cache stop_id → Mapbox matrix column/row index mapping.

    Index 0 is depot; stop indices start at 1. This mapping lets re-opt
    look up the correct matrix position regardless of the request body order.
    """
    r = get_redis()
    key = _matrix_idx_key(route_id)
    r.hset(key, mapping={k: str(v) for k, v in stop_id_to_idx.items()})
    r.expire(key, _TTL_SECONDS)


def get_matrix_index_map(route_id: int) -> dict[str, int] | None:
    """Returns {stop_id: matrix_index} or None if not cached."""
    r = get_redis()
    raw = r.hgetall(_matrix_idx_key(route_id))
    if not raw:
        return None
    return {k: int(v) for k, v in raw.items()}


# ── Stop details cache ────────────────────────────────────────────────────────

def set_stops_cache(route_id: int, stops: list[dict[str, Any]]) -> None:
    """Cache stop details indexed by position."""
    r = get_redis()
    key = _stops_key(route_id)
    r.hset(key, mapping={str(i): json.dumps(stop) for i, stop in enumerate(stops)})
    r.expire(key, _TTL_SECONDS)


def get_stops_cache(route_id: int) -> list[dict[str, Any]] | None:
    """Returns ordered stop list or None if not cached."""
    r = get_redis()
    raw = r.hgetall(_stops_key(route_id))
    if not raw:
        return None
    return [json.loads(raw[str(i)]) for i in range(len(raw))]


# ── Cleanup ───────────────────────────────────────────────────────────────────

def delete_route_cache(route_id: int) -> None:
    """Remove all Redis keys for a completed/cancelled route."""
    r = get_redis()
    r.delete(_state_key(route_id), _matrix_key(route_id), _stops_key(route_id), _matrix_idx_key(route_id))

# ── Route Suggestions ─────────────────────────────────────────────────────────

def set_route_suggestion(route_id: int, suggestion_id: str, suggestion_data: dict[str, Any]) -> None:
    """Cache a proposed route sequence for dispatcher review."""
    r = get_redis()
    key = _suggestion_key(route_id, suggestion_id)
    # Suggestions expire after 1 hour (or we could use _TTL_SECONDS)
    r.setex(key, 3600, json.dumps(suggestion_data))

def get_route_suggestion(route_id: int, suggestion_id: str) -> dict[str, Any] | None:
    """Load a proposed route sequence."""
    r = get_redis()
    key = _suggestion_key(route_id, suggestion_id)
    raw = r.get(key)
    if not raw:
        return None
    return json.loads(raw)

def delete_route_suggestion(route_id: int, suggestion_id: str) -> None:
    """Remove a proposed route sequence after decision."""
    r = get_redis()
    key = _suggestion_key(route_id, suggestion_id)
    r.delete(key)
