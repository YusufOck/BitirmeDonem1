"""
test_e2e.py
-----------
End-to-end akış testi:
  1. POST /api/v1/full-route  → Mapbox Matrix → ML → OR-Tools → Directions
  2. PATCH /api/v1/stops/{id}/complete  → stop tamamla + re-opt tetikle
  3. GET  /api/v1/routes/{id}  → son durumu göster

Çalıştır:
    python test/test_e2e.py [--base-url http://localhost:8001] [--route-id 1]
"""

import argparse
import json
import sys

import requests

BASE_URL = "http://localhost:8001"

DEPOT = {"lat": 41.0082, "lon": 28.9784}  # fallback — route'tan çekilemezse kullanılır


def pp(label: str, data) -> None:
    print(f"\n{'─'*60}")
    print(f"  {label}")
    print(f"{'─'*60}")
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def post(path: str, body: dict) -> dict:
    r = requests.post(f"{BASE_URL}{path}", json=body, timeout=60)
    if not r.ok:
        print(f"\n[ERROR] POST {path} → {r.status_code}")
        print(r.text)
        sys.exit(1)
    return r.json()


def patch(path: str, body: dict) -> dict:
    r = requests.patch(f"{BASE_URL}{path}", json=body, timeout=30)
    if not r.ok:
        print(f"\n[ERROR] PATCH {path} → {r.status_code}")
        print(r.text)
        sys.exit(1)
    return r.json()


def get(path: str) -> dict:
    r = requests.get(f"{BASE_URL}{path}", timeout=10)
    if not r.ok:
        print(f"\n[ERROR] GET {path} → {r.status_code}")
        print(r.text)
        sys.exit(1)
    return r.json()


def summarize_full_route(resp: dict) -> None:
    """full-route response'unun önemli kısımlarını özetle."""
    summary = resp.get("route_summary", {})
    print(f"\n  [SUMMARY]")
    print(f"    Araç sayısı       : {resp.get('num_vehicles')}")
    print(f"    Beklenen gecikme  : {summary.get('expected_total_delay_min')} dk")
    print(f"    Worst-case gecikme: {summary.get('worst_case_total_delay_min')} dk")
    print(f"    Yüksek risk stop  : {summary.get('high_risk_stop_count')}")
    print(f"    Genel risk skoru  : {summary.get('overall_risk_score')}")
    print(f"    VRP durumu        : {summary.get('vrp_status')}  ({summary.get('vrp_solve_time_ms')} ms)")

    print(f"\n  [OPTİMİZE SIRALAMA]")
    for s in sorted(resp.get("optimized_route", []), key=lambda x: x["optimized_position"]):
        risk = s.get("risk_level", "?")
        delay = s.get("expected_delay_min", 0)
        print(f"    {s['optimized_position']}. {s.get('stop_name'):25s}  risk={risk}  beklenen_gecikme={delay:.1f}dk")

    print(f"\n  [VEHICLE ROUTES]")
    for vr in resp.get("vehicle_routes", []):
        print(f"    Araç {vr['vehicle_id']}: {vr['stop_count']} stop  "
              f"{vr['distance_m']/1000:.1f}km  "
              f"{vr['duration_s']/60:.0f}dk  "
              f"gecikme={vr['total_expected_delay_min']:.1f}dk")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8001")
    parser.add_argument("--route-id", type=int, default=None)
    args = parser.parse_args()
    global BASE_URL
    BASE_URL = args.base_url.rstrip("/")

    route_id = args.route_id
    if route_id is None:
        import pathlib, json as _json
        state_file = pathlib.Path(__file__).parent / ".seed_state.json"
        if state_file.exists():
            state = _json.loads(state_file.read_text())
            route_id = state["route1_id"]
            print(f"  (seed state'ten route_id={route_id} okundu)")
        else:
            print("[ERROR] --route-id verilmedi ve test/.seed_state.json bulunamadı.")
            print("        Önce: python test/seed.py")
            sys.exit(1)

    print("=" * 60)
    print("  SBTU Logistics E2E Test")
    print(f"  route_id = {route_id}")
    print("=" * 60)

    # ── 0. DB'den gerçek stop ID'lerini çek ──────────────────────────────────
    route_detail = get(f"/api/v1/routes/{route_id}")
    depot_lat = route_detail.get("depot_latitude") or DEPOT["lat"]
    depot_lon = route_detail.get("depot_longitude") or DEPOT["lon"]
    db_stops = sorted(route_detail["stops"], key=lambda s: s["sequence"])

    stops_for_request = [
        {
            "stop_id": str(s["id"]),
            "stop_name": s["name"],
            "latitude": s["latitude"],
            "longitude": s["longitude"],
            "stop_sequence": i + 1,
        }
        for i, s in enumerate(db_stops)
    ]
    print(f"\n  DB'den {len(db_stops)} stop çekildi: {[s['id'] for s in db_stops]}")

    # ── 1. full-route ─────────────────────────────────────────────────────────
    print("\n[1/3] POST /api/v1/full-route  (Mapbox → ML → OR-Tools → Directions)")
    print("      Bekleniyor...")

    full_route_body = {
        "route_id": route_id,
        "depot_latitude": depot_lat,
        "depot_longitude": depot_lon,
        "stops": stops_for_request,
        "num_vehicles": 1,
        "time_limit_seconds": 15,
    }

    resp = post("/api/v1/full-route", full_route_body)
    summarize_full_route(resp)

    # Optimized sıradaki ilk stop_id'yi al (bunlar artık gerçek DB ID'leri)
    optimized = sorted(resp.get("optimized_route", []), key=lambda x: x["optimized_position"])
    first_stop_id = int(optimized[0]["stop_id"])
    second_stop_id = int(optimized[1]["stop_id"]) if len(optimized) > 1 else None

    print(f"\n  → İlk teslim edilecek stop: stop_id={first_stop_id} ({optimized[0].get('stop_name')})")

    # ── 2. complete_stop (1. stop) ────────────────────────────────────────────
    print(f"\n[2/3] PATCH /api/v1/stops/{first_stop_id}/complete  (5dk gecikme ile)")

    complete_resp = patch(f"/api/v1/stops/{first_stop_id}/complete", {"actual_delay_min": 5.0})

    stop_out = complete_resp["stop"]
    reopt = complete_resp["reopt"]

    print(f"\n  [STOP]")
    print(f"    stop_id={stop_out['id']}  status={stop_out['status']}  gecikme={stop_out['actual_delay_min']}dk")

    print(f"\n  [RE-OPT]")
    print(f"    triggered        : {reopt['triggered']}")
    if reopt["triggered"]:
        print(f"    yeni sıra        : {reopt['new_sequence']}")
        print(f"    tahmini kazanım  : {reopt['estimated_time_savings_min']} dk")
    else:
        print(f"    neden tetiklenmedi: {reopt.get('reason')}")

    # ── 3. complete_stop (2. stop, opsiyonel) ─────────────────────────────────
    if second_stop_id:
        print(f"\n[3/3] PATCH /api/v1/stops/{second_stop_id}/complete  (2dk gecikme ile)")
        complete_resp2 = patch(f"/api/v1/stops/{second_stop_id}/complete", {"actual_delay_min": 2.0})
        reopt2 = complete_resp2["reopt"]
        print(f"  triggered: {reopt2['triggered']}  |  {reopt2.get('reason') or 'yeni sıra: ' + str(reopt2.get('new_sequence'))}")

    # ── Son durum ─────────────────────────────────────────────────────────────
    print(f"\n[SON] GET /api/v1/routes/{route_id}")
    final = get(f"/api/v1/routes/{route_id}")
    print(f"\n  route status : {final['status']}")
    print(f"  Stoplar:")
    for s in final["stops"]:
        pkg_count = len(s.get("packages", []))
        print(f"    seq={s['sequence']}  stop_id={s['id']:3d}  {s['name']:25s}  "
              f"status={s['status']}  paket={pkg_count}")

    print("\n" + "=" * 60)
    print("  E2E test tamamlandı.")
    print("=" * 60)


if __name__ == "__main__":
    main()
