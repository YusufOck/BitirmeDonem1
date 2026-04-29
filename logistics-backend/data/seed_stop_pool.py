"""
data/seed_stop_pool.py
----------------------
route_stops.csv'den ilk N stop'u alıp stop_pool tablosuna yazar.
Ayrıca default dispatcher + 2 courier oluşturur (yoksa).

Kullanım:
    python data/seed_stop_pool.py
    python data/seed_stop_pool.py --limit 20
"""

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from database import SessionLocal
from db.models import Courier, CourierStopAssignment, StopPool, User, UserRole


def get_or_create_dispatcher(db) -> User:
    user = db.query(User).filter(User.role == UserRole.dispatcher).first()
    if not user:
        user = User(name="Default Dispatcher", email="dispatcher@sbtu.local", role=UserRole.dispatcher)
        db.add(user)
        db.flush()
        print(f"  Dispatcher oluşturuldu → id={user.id}")
    else:
        print(f"  Dispatcher zaten var → id={user.id}")
    return user


def get_or_create_courier(db, vehicle_id: int) -> Courier:
    email = f"courier{vehicle_id}@sbtu.local"
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(name=f"Courier {vehicle_id}", email=email, role=UserRole.courier)
        db.add(user)
        db.flush()
        courier = Courier(user_id=user.id, vehicle_type="van")
        db.add(courier)
        db.flush()
        print(f"  Courier {vehicle_id} oluşturuldu → courier_id={courier.id}")
    else:
        courier = db.query(Courier).filter(Courier.user_id == user.id).first()
        print(f"  Courier {vehicle_id} zaten var → courier_id={courier.id}")
    return courier


SIVAS_LAT = 39.7477
SIVAS_LON = 37.0179
RADIUS_KM = 2.0


def _random_coord_in_radius(center_lat: float, center_lon: float, radius_km: float):
    import random, math
    r = radius_km / 111.0  # 1 derece ≈ 111 km
    angle = random.uniform(0, 2 * math.pi)
    dist = random.uniform(0, r)
    lat = center_lat + dist * math.cos(angle)
    lon = center_lon + dist * math.sin(angle) / math.cos(math.radians(center_lat))
    return round(lat, 6), round(lon, 6)


def seed(limit: int = 30):
    csv_path = os.path.join(os.path.dirname(__file__), "synthetic", "route_stops.csv")
    df = pd.read_csv(csv_path).head(limit)

    db = SessionLocal()
    try:
        print("\n── Kullanıcılar ─────────────────────────────")
        dispatcher = get_or_create_dispatcher(db)
        courier0 = get_or_create_courier(db, 0)
        courier1 = get_or_create_courier(db, 1)

        print(f"\n── Stop Pool ({limit} stop, Sivas merkezi ±{RADIUS_KM}km) ──────────────────")
        existing = {s.name for s in db.query(StopPool).all()}
        added = 0
        for _, row in df.iterrows():
            name = row["stop_id"]
            if name in existing:
                continue
            lat, lon = _random_coord_in_radius(SIVAS_LAT, SIVAS_LON, RADIUS_KM)
            stop = StopPool(
                name=name,
                address=f"{row['road_type'].capitalize()} bölgesi",
                latitude=lat,
                longitude=lon,
                created_by=dispatcher.id,
            )
            db.add(stop)
            added += 1
        db.flush()
        print(f"  {added} stop eklendi (zaten var olanlar atlandı)")

        db.commit()

        all_stops = db.query(StopPool).order_by(StopPool.id).limit(limit).all()
        half = len(all_stops) // 2
        courier0_stops = all_stops[:half]
        courier1_stops = all_stops[half:]

        print(f"\n── Courier ID'leri ───────────────────────────")
        print(f"  Courier 0 → DB id: {courier0.id}")
        print(f"  Courier 1 → DB id: {courier1.id}")
        print(f"\n── Stop Pool ID'leri ─────────────────────────")
        for s in all_stops:
            print(f"  {s.id}: {s.name} ({s.latitude:.4f}, {s.longitude:.4f})")

        print("\n✅ Seed tamamlandı.")
        print(f"\n📋 full-route çağrısı için örnek body:")
        print(f"""{{
  "courier_id": {courier0.id},
  "date": "2026-04-20",
  "depot_latitude": 39.75,
  "depot_longitude": 37.015,
  "num_vehicles": 1,
  "time_limit_seconds": 10,
  "stops": []
}}""")

    except Exception as e:
        db.rollback()
        print(f"❌ Hata: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30, help="Kaç stop eklensin (default: 30)")
    args = parser.parse_args()
    seed(args.limit)
