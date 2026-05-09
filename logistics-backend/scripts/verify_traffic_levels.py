"""
verify_traffic_levels.py
-------------------------
Verifies that the scenario/reoptimize endpoint preserves ALL stops across
every tested traffic density level (0, 50, 70, 80, 90, 100).

Checks per traffic level:
  - baseline_route count == input stop count
  - scenario_route count == input stop count
  - No missing stop_names between baseline and scenario
  - No duplicate stop_ids

Usage:
    python scripts/verify_traffic_levels.py

Requires backend running at http://localhost:8000
"""

import sys
import requests

API = "http://localhost:8000/api/v1"
PASS = "PASS"
FAIL = "FAIL"

TRAFFIC_LEVELS = [0, 50, 70, 80, 90, 100]

# Fixed 5-stop input with real Sivas-area coordinates
STOPS = [
    {
        "stop_sequence": i + 1,
        "cumulative_delay_min": 0,
        "prev_stop_delay_min": 0,
        "time_window_slack_min": 480,
        "hist_slack_min": 55,
        "hist_delay_probability": 0.25,
        "latitude": lat,
        "longitude": lon,
        "stop_name": name,
        "stop_id": sid,
    }
    for i, (lat, lon, name, sid) in enumerate(
        [
            (39.759, 37.002, "S1", "STP-001"),
            (39.761, 37.005, "S2", "STP-002"),
            (39.755, 37.010, "S3", "STP-003"),
            (39.763, 36.998, "S4", "STP-004"),
            (39.753, 37.015, "S5", "STP-005"),
        ]
    )
]

INPUT_COUNT = len(STOPS)
INPUT_NAMES = sorted(s["stop_name"] for s in STOPS)

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
    print("  Scenario Reoptimize -- Traffic Level Sweep")
    print(f"  Input: {INPUT_COUNT} stops, levels: {TRAFFIC_LEVELS}")
    print("=" * 60)

    # Verify backend is reachable
    try:
        requests.get(f"{API}/health", timeout=5)
    except Exception as exc:
        print(f"\n  [FAIL] Cannot reach backend: {exc}")
        sys.exit(1)

    for traffic in TRAFFIC_LEVELS:
        body = {
            "depot_latitude": 39.75,
            "depot_longitude": 37.015,
            "stops": STOPS,
            "controls": {
                "traffic_density": traffic,
                "accident_severity": 0,
                "weather_condition": "clear",
            },
            "segment_overrides": [],
            "time_limit_seconds": 15,
        }

        print(f"\n  Traffic={traffic:3d}/100:")
        resp = requests.post(f"{API}/scenario/reoptimize", json=body, timeout=30)
        if resp.status_code != 200:
            check(f"HTTP 200", False, f"status={resp.status_code}")
            continue

        data = resp.json()
        bl = data.get("baseline_route", [])
        sc = data.get("scenario_route", [])
        bl_names = sorted(s.get("stop_name", "?") for s in bl)
        sc_names = sorted(s.get("stop_name", "?") for s in sc)
        sc_ids = [s.get("stop_id", "?") for s in sc]

        check(
            f"baseline count == {INPUT_COUNT}",
            len(bl) == INPUT_COUNT,
            f"got {len(bl)}",
        )
        check(
            f"scenario count == {INPUT_COUNT}",
            len(sc) == INPUT_COUNT,
            f"got {len(sc)}",
        )
        check(
            "no missing stops in scenario",
            bl_names == sc_names,
            f"baseline={bl_names}, scenario={sc_names}" if bl_names != sc_names else "all present",
        )
        check(
            "no duplicate stop_ids",
            len(set(sc_ids)) == len(sc_ids),
            f"ids={sc_ids}" if len(set(sc_ids)) != len(sc_ids) else f"{len(sc_ids)} unique",
        )

    # ── Summary ───────────────────────────────────────────────────
    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"  {passed}/{total} checks passed across {len(TRAFFIC_LEVELS)} traffic levels")
    if passed == total:
        print("  ALL PASSED")
    else:
        print(f"  {total - passed} check(s) FAILED")
    print("=" * 60)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
