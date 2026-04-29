"""
seed.py
-------
Test verisi oluşturur ve dispatcher/courier endpoint'lerini doğrular.

Çalıştır:
    python test/seed.py [--base-url http://localhost:8000]

Ne yapar:
    1. DB'ye direkt 1 dispatcher + 2 courier user yazar (user create endpoint yok)
    2. API üzerinden route / stop / package oluşturur
    3. GET /api/v1/routes/{id} ile sonucu gösterir
    4. GET /api/v1/couriers/{id}/routes ile courier görünümünü gösterir
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone

import requests
from sqlalchemy.orm import Session

# Proje root'unu path'e ekle
sys.path.insert(0, ".")

from database import SessionLocal, create_tables
from db.models import Courier, User, UserRole


BASE_URL = "http://localhost:8000"

# ── İstanbul koordinatları ────────────────────────────────────────────────────
DEPOT = {"lat": 41.0082, "lon": 28.9784, "name": "Depot - Eminönü"}

STOPS_COURIER_1 = [
    {"name": "Kapalıçarşı",       "lat": 41.0107, "lon": 28.9679, "address": "Kapalıçarşı, Fatih"},
    {"name": "Sultanahmet Meydanı","lat": 41.0054, "lon": 28.9768, "address": "Sultanahmet, Fatih"},
    {"name": "Arasta Bazaar",      "lat": 41.0046, "lon": 28.9784, "address": "Torun Sk., Fatih"},
    {"name": "Sirkeci Garı",       "lat": 41.0136, "lon": 28.9799, "address": "Sirkeci, Fatih"},
]

STOPS_COURIER_2 = [
    {"name": "Galata Kulesi",      "lat": 41.0256, "lon": 28.9742, "address": "Galata, Beyoğlu"},
    {"name": "Taksim Meydanı",     "lat": 41.0369, "lon": 28.9850, "address": "Taksim, Beyoğlu"},
    {"name": "İstiklal Caddesi",   "lat": 41.0335, "lon": 28.9775, "address": "İstiklal Cd., Beyoğlu"},
]


def pp(label: str, data: dict) -> None:
    print(f"\n{'─'*60}")
    print(f"  {label}")
    print(f"{'─'*60}")
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def post(path: str, body: dict) -> dict:
    r = requests.post(f"{BASE_URL}{path}", json=body, timeout=10)
    if not r.ok:
        print(f"\n[ERROR] POST {path} → {r.status_code}: {r.text}")
        sys.exit(1)
    return r.json()


def get(path: str) -> dict | list:
    r = requests.get(f"{BASE_URL}{path}", timeout=10)
    if not r.ok:
        print(f"\n[ERROR] GET {path} → {r.status_code}: {r.text}")
        sys.exit(1)
    return r.json()


# ── 1. DB: Users + Couriers ───────────────────────────────────────────────────

def seed_users(db: Session) -> tuple[int, int, int]:
    """dispatcher_id, courier1_id, courier2_id döner."""

    # Mevcut seed verisini temizle (tekrar çalıştırılabilir olsun)
    existing_emails = {u.email for u in db.query(User).all()}

    def get_or_create_user(name, email, role) -> User:
        if email in existing_emails:
            return db.query(User).filter(User.email == email).first()
        u = User(name=name, email=email, role=role)
        db.add(u)
        db.flush()
        return u

    dispatcher = get_or_create_user(
        "Ali Dispatcher", "ali@sbtu.test", UserRole.dispatcher
    )
    c1_user = get_or_create_user(
        "Mehmet Kurye", "mehmet@sbtu.test", UserRole.courier
    )
    c2_user = get_or_create_user(
        "Ayşe Kurye", "ayse@sbtu.test", UserRole.courier
    )
    db.flush()

    def get_or_create_courier(user: User, vehicle, plate, capacity) -> Courier:
        existing = db.query(Courier).filter(Courier.user_id == user.id).first()
        if existing:
            return existing
        c = Courier(
            user_id=user.id,
            vehicle_type=vehicle,
            plate=plate,
            capacity_kg=capacity,
        )
        db.add(c)
        db.flush()
        return c

    courier1 = get_or_create_courier(c1_user, "van",  "34 ABC 001", 500.0)
    courier2 = get_or_create_courier(c2_user, "bike", "34 XYZ 002", 50.0)
    db.commit()

    print(f"\n[DB] Dispatcher  → user_id={dispatcher.id}")
    print(f"[DB] Courier 1   → user_id={c1_user.id}, courier_id={courier1.id}")
    print(f"[DB] Courier 2   → user_id={c2_user.id}, courier_id={courier2.id}")

    return dispatcher.id, courier1.id, courier2.id


# ── 2. API: Routes ────────────────────────────────────────────────────────────

def seed_route(courier_id: int, dispatcher_id: int, label: str) -> int:
    body = {
        "courier_id": courier_id,
        "dispatcher_id": dispatcher_id,
        "date": datetime.now(timezone.utc).isoformat(),
        "depot_latitude": DEPOT["lat"],
        "depot_longitude": DEPOT["lon"],
    }
    route = post("/api/v1/routes", body)
    print(f"\n[API] Route oluşturuldu ({label}) → route_id={route['id']}")
    return route["id"]


# ── 3. API: Stops ─────────────────────────────────────────────────────────────

def seed_stops(route_id: int, stops: list[dict]) -> list[int]:
    stop_ids = []
    for i, s in enumerate(stops):
        body = {
            "name": s["name"],
            "address": s["address"],
            "latitude": s["lat"],
            "longitude": s["lon"],
            "sequence": i,
            "time_window_slack_min": 480.0,
        }
        stop = post(f"/api/v1/routes/{route_id}/stops", body)
        stop_ids.append(stop["id"])
        print(f"  [API] Stop ekle → stop_id={stop['id']} ({s['name']})")
    return stop_ids


# ── 4. API: Packages ──────────────────────────────────────────────────────────

def seed_packages(stop_ids: list[int], prefix: str, run_id: str) -> None:
    for i, sid in enumerate(stop_ids):
        body = {
            "tracking_no": f"{prefix}-{run_id}-{i+1:04d}",
            "recipient_name": f"Müşteri {i+1}",
            "weight_kg": round(1.5 + i * 0.3, 1),
        }
        pkg = post(f"/api/v1/stops/{sid}/packages", body)
        print(f"  [API] Paket ekle → package_id={pkg['id']} ({body['tracking_no']})")


# ── 5. GET: Doğrula ───────────────────────────────────────────────────────────

def verify(route_id: int, courier_id: int) -> None:
    route_detail = get(f"/api/v1/routes/{route_id}")
    pp(f"GET /api/v1/routes/{route_id}", route_detail)

    courier_routes = get(f"/api/v1/couriers/{courier_id}/routes")
    pp(f"GET /api/v1/couriers/{courier_id}/routes  ({len(courier_routes)} rota)", courier_routes[0] if courier_routes else {})


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()
    global BASE_URL
    BASE_URL = args.base_url.rstrip("/")

    print("=" * 60)
    print("  SBTU Logistics Seed Script")
    print("=" * 60)

    # Tabloları oluştur (create_tables idempotent)
    create_tables()

    db: Session = SessionLocal()
    try:
        dispatcher_id, courier1_id, courier2_id = seed_users(db)
    finally:
        db.close()

    run_id = str(int(time.time()))[-6:]  # son 6 hane — her run'da farklı

    # Courier 1 → 4 stop
    route1_id = seed_route(courier1_id, dispatcher_id, "Courier 1 / Tarihi Yarımada")
    stop1_ids  = seed_stops(route1_id, STOPS_COURIER_1)
    seed_packages(stop1_ids, "TRK-A", run_id)

    # Courier 2 → 3 stop
    route2_id = seed_route(courier2_id, dispatcher_id, "Courier 2 / Beyoğlu")
    stop2_ids  = seed_stops(route2_id, STOPS_COURIER_2)
    seed_packages(stop2_ids, "TRK-B", run_id)

    # Sonuçları göster
    verify(route1_id, courier1_id)

    # E2E test için state dosyasına yaz
    import pathlib, json as _json
    state = {
        "route1_id": route1_id, "courier1_id": courier1_id,
        "route2_id": route2_id, "courier2_id": courier2_id,
    }
    state_file = pathlib.Path(__file__).parent / ".seed_state.json"
    state_file.write_text(_json.dumps(state, indent=2))

    print("\n" + "=" * 60)
    print("  Seed tamamlandı.")
    print(f"  Route 1 id = {route1_id}  (courier_id={courier1_id})")
    print(f"  Route 2 id = {route2_id}  (courier_id={courier2_id})")
    print(f"  State → {state_file}")
    print("=" * 60)


if __name__ == "__main__":
    main()
