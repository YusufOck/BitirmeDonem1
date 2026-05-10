"""
verify_frontend_flow_api.py
-----------------------------
Simulates the exact API call sequence the frontend makes:

  1. GET /couriers              -- load courier list
  2. GET /stop-pool             -- load available stops
  3. POST /auto-dispatch        -- initial route build (VRP)
  4. POST /scenario/reoptimize  -- scenario with stops from auto-dispatch

This catches the real-world bug where auto-dispatch stops carry
tight time_window_slack_min values that caused hard-infeasibility drops
in the scenario reoptimize path.

Checks:
  - Auto-dispatch returns correct stop count per vehicle
  - Scenario reoptimize with traffic=80 preserves all stops
  - Scenario reoptimize with traffic=100 preserves all stops
  - No missing stop_names between input and output
  - No duplicate stop_ids
  - order_changed is allowed, missing stops are not

Usage:
    python scripts/verify_frontend_flow_api.py

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


def build_scenario_stop(stop, index):
    """
    Convert an auto-dispatch optimized_route stop into the payload shape
    the frontend sends to /scenario/reoptimize.
    """
    return {
        "stop_sequence": index + 1,
        "cumulative_delay_min": float(stop.get("cumulative_delay_min") or 0),
        "prev_stop_delay_min": float(stop.get("prev_stop_delay_min") or 0),
        "time_window_slack_min": float(stop.get("time_window_slack_min") or 480),
        "hist_slack_min": float(stop.get("hist_slack_min") or 55),
        "hist_delay_probability": float(stop.get("hist_delay_probability") or 0.25),
        "latitude": stop["latitude"],
        "longitude": stop["longitude"],
        "stop_name": stop.get("stop_name", ""),
        "stop_id": stop.get("stop_id", ""),
        # pass through ML/feature fields the frontend also sends
        "planned_travel_min": stop.get("planned_travel_min"),
        "road_type": stop.get("road_type"),
        "traffic_level": stop.get("traffic_level"),
        "weather_condition": stop.get("weather_condition"),
        "congestion_ratio_mean": stop.get("congestion_ratio_mean"),
        "road_incident": stop.get("road_incident"),
        "incident_severity": stop.get("incident_severity"),
        "precipitation_mm": stop.get("precipitation_mm"),
        "wind_speed_kmh": stop.get("wind_speed_kmh"),
        "visibility_km": stop.get("visibility_km"),
        "package_count": stop.get("package_count"),
        "package_weight_kg": stop.get("package_weight_kg"),
    }


def run_scenario_check(label, vehicle_stops, traffic_density):
    """
    Run a single scenario reoptimize and verify stop preservation.
    """
    input_names = sorted(s.get("stop_name", "?") for s in vehicle_stops)
    input_count = len(vehicle_stops)

    scenario_stops = [build_scenario_stop(s, i) for i, s in enumerate(vehicle_stops)]
    body = {
        "depot_latitude": 39.75,
        "depot_longitude": 37.015,
        "stops": scenario_stops,
        "controls": {
            "traffic_density": traffic_density,
            "accident_severity": 30,
            "weather_condition": "rain",
        },
        "segment_overrides": [],
        "time_limit_seconds": 15,
    }

    print(f"\n  {label} (traffic={traffic_density}):")
    resp = requests.post(f"{API}/scenario/reoptimize", json=body, timeout=30)
    if not check("HTTP 200", resp.status_code == 200, f"status={resp.status_code}"):
        return

    data = resp.json()
    bl = data.get("baseline_route", [])
    sc = data.get("scenario_route", [])
    bl_names = sorted(s.get("stop_name", "?") for s in bl)
    sc_names = sorted(s.get("stop_name", "?") for s in sc)
    sc_ids = [s.get("stop_id", "?") for s in sc]

    check(f"baseline count == {input_count}", len(bl) == input_count, f"got {len(bl)}")
    check(f"scenario count == {input_count}", len(sc) == input_count, f"got {len(sc)}")
    check(
        "no missing stops",
        set(bl_names) == set(sc_names),
        f"missing={set(bl_names) - set(sc_names)}" if set(bl_names) != set(sc_names) else "all present",
    )
    check(
        "no duplicate stop_ids",
        len(set(sc_ids)) == len(sc_ids),
        f"{len(sc_ids)} unique",
    )

    # order_changed is allowed
    order_changed = data.get("order_changed", False)
    print(f"    order_changed={order_changed} (informational, not a failure)")


def run_segment_override_check(vehicle_stops):
    """
    Force a high-risk closure on the first active leg and verify the backend
    produces a different valid recommendation. This proves segment overrides
    are not just UI labels.
    """
    if len(vehicle_stops) < 3:
        check("segment override skipped", False, "need at least 3 stops")
        return

    scenario_stops = [build_scenario_stop(s, i) for i, s in enumerate(vehicle_stops)]
    first = scenario_stops[0]
    second = scenario_stops[1]
    body = {
        "depot_latitude": 39.75,
        "depot_longitude": 37.015,
        "stops": scenario_stops,
        "controls": {
            "traffic_density": 20,
            "accident_severity": 0,
            "weather_condition": "clear",
            "weather_severity": 0,
            "road_disruption": 0,
            "package_load": 0,
            "dispatch_hour": 12,
        },
        "segment_overrides": [{
            "from_stop_id": str(first.get("stop_id")),
            "to_stop_id": str(second.get("stop_id")),
            "traffic_density": 100,
            "accident_severity": 100,
            "road_closure": True,
            "weather_severity": 100,
            "speed_reduction": 100,
            "extra_delay_min": 60,
            "risk_level": "high",
            "priority": 0,
            "road_type": "urban",
        }],
        "time_limit_seconds": 15,
    }

    print("\n  Vehicle 0, forced segment closure:")
    resp = requests.post(f"{API}/scenario/reoptimize", json=body, timeout=30)
    if not check("HTTP 200", resp.status_code == 200, f"status={resp.status_code}"):
        return
    data = resp.json()
    sc = data.get("scenario_route", [])
    sc_ids = [s.get("stop_id", "?") for s in sc]
    check("scenario count preserved", len(sc) == len(vehicle_stops), f"got {len(sc)}")
    check("no duplicate stop_ids", len(set(sc_ids)) == len(sc_ids), f"{len(sc_ids)} unique")
    check("order changed under closed segment", bool(data.get("order_changed")), f"after={data.get('sequence_after')}")
    check(
        "segment impact explained",
        any("Segment" in item.get("label", "") for item in data.get("factor_impacts", [])),
        "factor_impacts contains segment override",
    )


def main():
    print("=" * 60)
    print("  Frontend-Equivalent Flow -- API Verification")
    print("=" * 60)

    # ── Step 1: Load couriers and stop pool ───────────────────────
    print("\n  Step 1: Loading couriers and stop pool ...")
    try:
        couriers = requests.get(f"{API}/couriers", timeout=10).json()
        stop_pool = requests.get(f"{API}/stop-pool", timeout=10).json()
    except Exception as exc:
        print(f"  [FAIL] Cannot reach backend: {exc}")
        sys.exit(1)

    check("Couriers loaded", len(couriers) >= 2, f"count={len(couriers)}")
    check("Stop pool loaded", len(stop_pool) >= 10, f"count={len(stop_pool)}")

    # ── Step 2: Auto-dispatch ─────────────────────────────────────
    print("\n  Step 2: Running auto-dispatch ...")
    active = couriers[:2]
    selected = stop_pool[: len(active) * 5]
    spc = len(selected) // len(active)

    dispatch_body = {
        "depot_latitude": 39.75,
        "depot_longitude": 37.015,
        "date": "2026-05-09",
        "time_limit_seconds": 15,
        "use_p90": False,
        "generate_explanation": False,
        "assignments": [
            {
                "courier_id": c["id"],
                "stop_pool_ids": [s["id"] for s in selected[i * spc : (i + 1) * spc]],
            }
            for i, c in enumerate(active)
        ],
    }

    resp = requests.post(f"{API}/auto-dispatch", json=dispatch_body, timeout=30)
    check("auto-dispatch HTTP 200", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code != 200:
        print(f"  Response: {resp.text[:300]}")
        sys.exit(1)

    data = resp.json()
    optimized = data.get("optimized_route", [])

    v0_stops = [s for s in optimized if s["vehicle_id"] == 0]
    v1_stops = [s for s in optimized if s["vehicle_id"] == 1]

    check(f"vehicle 0: {spc} stops", len(v0_stops) == spc, f"got {len(v0_stops)}")
    check(f"vehicle 1: {spc} stops", len(v1_stops) == spc, f"got {len(v1_stops)}")

    # Show stop details (slack values are the key to the bug)
    print("\n  Vehicle 0 stop details (from auto-dispatch):")
    for s in v0_stops:
        slack = s.get("time_window_slack_min", "?")
        wm = s.get("will_miss_window", False)
        print(f"    {s.get('stop_name','?')} slack={slack} will_miss={wm}")

    # ── Step 3: Scenario reoptimize with auto-dispatch stops ──────
    print("\n  Step 3: Scenario reoptimize with real auto-dispatch stops ...")

    run_scenario_check("Vehicle 0, moderate traffic", v0_stops, 80)
    run_scenario_check("Vehicle 0, extreme traffic", v0_stops, 100)
    run_scenario_check("Vehicle 1, extreme traffic", v1_stops, 100)
    run_segment_override_check(v0_stops)

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
