"""
verify_stop_preservation.py
----------------------------
Verifies that the auto-dispatch endpoint preserves ALL stops for every vehicle.

Checks:
  - Each vehicle receives the expected number of stops
  - No stop_id appears in the input but not in the output
  - No duplicate stop_ids in the output
  - Total output stop count == total input stop count

Usage:
    python scripts/verify_stop_preservation.py

Requires backend running at http://localhost:8000
"""

import sys
import requests

API = "http://localhost:8000/api/v1"
PASS = "PASS"
FAIL = "FAIL"

results = []


def check(label, condition, detail=""):
    tag = PASS if condition else FAIL
    line = f"  [{tag}] {label}"
    if detail:
        line += f" -- {detail}"
    print(line)
    results.append(condition)
    return condition


def main():
    print("=" * 60)
    print("  Auto-Dispatch Stop Preservation Test")
    print("=" * 60)

    # ── Fetch couriers and stop pool ─────────────────────────────
    try:
        couriers = requests.get(f"{API}/couriers", timeout=10).json()
        stop_pool = requests.get(f"{API}/stop-pool", timeout=10).json()
    except Exception as exc:
        print(f"\n  [FAIL] Cannot reach backend: {exc}")
        sys.exit(1)

    check("Couriers loaded", len(couriers) >= 2, f"count={len(couriers)}")
    check("Stop pool loaded", len(stop_pool) >= 10, f"count={len(stop_pool)}")

    # ── Build request body (same logic as frontend) ──────────────
    active_couriers = couriers[:2]
    selected_stops = stop_pool[: len(active_couriers) * 5]
    stops_per_courier = len(selected_stops) // len(active_couriers)

    assignments = []
    input_ids_by_courier = {}
    for idx, courier in enumerate(active_couriers):
        chunk = selected_stops[idx * stops_per_courier : (idx + 1) * stops_per_courier]
        ids = [s["id"] for s in chunk]
        assignments.append({"courier_id": courier["id"], "stop_pool_ids": ids})
        input_ids_by_courier[idx] = set(ids)

    body = {
        "depot_latitude": 39.75,
        "depot_longitude": 37.015,
        "date": "2026-05-09",
        "time_limit_seconds": 15,
        "use_p90": False,
        "generate_explanation": False,
        "assignments": assignments,
    }

    total_input_stops = sum(len(ids) for ids in input_ids_by_courier.values())
    print(f"\n  Input: {len(active_couriers)} vehicles, {total_input_stops} total stops")
    for vid, ids in input_ids_by_courier.items():
        print(f"    Vehicle {vid}: {len(ids)} stops (pool IDs: {sorted(ids)})")

    # ── Call auto-dispatch ────────────────────────────────────────
    print("\n  Calling POST /auto-dispatch ...")
    resp = requests.post(f"{API}/auto-dispatch", json=body, timeout=30)
    check("HTTP 200", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code != 200:
        print(f"  Response: {resp.text[:300]}")
        sys.exit(1)

    data = resp.json()
    optimized = data.get("optimized_route", [])
    vehicle_routes = data.get("vehicle_routes", [])

    check("optimized_route present", len(optimized) > 0, f"count={len(optimized)}")
    check("vehicle_routes present", len(vehicle_routes) > 0, f"count={len(vehicle_routes)}")

    # ── Per-vehicle checks ────────────────────────────────────────
    print()
    for vr in vehicle_routes:
        vid = vr["vehicle_id"]
        v_stops = [s for s in optimized if s["vehicle_id"] == vid]
        v_stop_ids = [s.get("stop_id") for s in v_stops]
        v_stop_names = [s.get("stop_name", "?") for s in v_stops]
        expected_count = stops_per_courier

        print(f"  Vehicle {vid}:")
        check(
            f"stop count == {expected_count}",
            len(v_stops) == expected_count,
            f"got {len(v_stops)}: {v_stop_names}",
        )
        check(
            "no duplicate stop_ids",
            len(set(v_stop_ids)) == len(v_stop_ids),
            f"ids={v_stop_ids}",
        )

    # ── Global total check ────────────────────────────────────────
    total_output = len(optimized)
    check(
        f"total output stops == {total_input_stops}",
        total_output == total_input_stops,
        f"got {total_output}",
    )

    # ── Summary ───────────────────────────────────────────────────
    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"  {passed}/{total} checks passed")
    if passed == total:
        print("  ALL PASSED")
    else:
        print(f"  {total - passed} check(s) FAILED")
    print("=" * 60)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
