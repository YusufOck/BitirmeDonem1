"""
setup_static.py
---------------
DB'yi temizler ve demo için statik courier/vehicle entity'leri oluşturur.

Courier dağılımı:
  5 × motorcycle  (kapasite  30 kg, şehir içi)
  5 × car         (kapasite 200 kg, şehir/yakın çevre)
  2 × van         (kapasite 800 kg, esnek güzergah)
  2 × truck       (kapasite 3000 kg, uzun mesafe/ağır yük)
  ─────────────────
  14 courier + 1 dispatcher = 15 kişi

Çalıştır:
    python test/setup_static.py
"""

import sys
sys.path.insert(0, ".")

from database import SessionLocal, create_tables
from db.models import Package, Stop, Route, Courier, User, UserRole

# ── Statik entity tanımları ───────────────────────────────────────────────────

DISPATCHER = {
    "name":  "Ali Kaya",
    "email": "ali.kaya@sbtu.test",
    "role":  UserRole.dispatcher,
}

COURIERS = [
    # ── Motorcycle (5 kişi) — hafif kargo, şehir içi ─────────────────────────
    {"name": "Ahmet Yılmaz",  "email": "ahmet@sbtu.test",   "vehicle_type": "motorcycle", "plate": "34 MK 001", "capacity_kg": 30.0},
    {"name": "Fatma Demir",   "email": "fatma@sbtu.test",   "vehicle_type": "motorcycle", "plate": "34 MK 002", "capacity_kg": 30.0},
    {"name": "Burak Arslan",  "email": "burak@sbtu.test",   "vehicle_type": "motorcycle", "plate": "34 MK 003", "capacity_kg": 30.0},
    {"name": "Elif Çetin",    "email": "elif@sbtu.test",    "vehicle_type": "motorcycle", "plate": "34 MK 004", "capacity_kg": 30.0},
    {"name": "Serkan Koç",    "email": "serkan@sbtu.test",  "vehicle_type": "motorcycle", "plate": "34 MK 005", "capacity_kg": 30.0},

    # ── Car (5 kişi) — orta yük, şehir/yakın çevre ───────────────────────────
    {"name": "Mehmet Çelik",  "email": "mehmet@sbtu.test",  "vehicle_type": "car", "plate": "34 CR 001", "capacity_kg": 200.0},
    {"name": "Zeynep Şahin",  "email": "zeynep@sbtu.test",  "vehicle_type": "car", "plate": "34 CR 002", "capacity_kg": 200.0},
    {"name": "Emre Kaya",     "email": "emre@sbtu.test",    "vehicle_type": "car", "plate": "34 CR 003", "capacity_kg": 200.0},
    {"name": "Selin Öztürk",  "email": "selin@sbtu.test",   "vehicle_type": "car", "plate": "34 CR 004", "capacity_kg": 200.0},
    {"name": "Okan Yıldız",   "email": "okan@sbtu.test",    "vehicle_type": "car", "plate": "34 CR 005", "capacity_kg": 200.0},

    # ── Van (2 kişi) — ağır yük, esnek güzergah ──────────────────────────────
    {"name": "Can Polat",     "email": "can@sbtu.test",     "vehicle_type": "van", "plate": "34 VN 001", "capacity_kg": 800.0},
    {"name": "Ayşe Erdoğan",  "email": "ayse@sbtu.test",    "vehicle_type": "van", "plate": "34 VN 002", "capacity_kg": 800.0},

    # ── Truck (2 kişi) — çok ağır yük, uzun mesafe ───────────────────────────
    {"name": "Murat Doğan",   "email": "murat@sbtu.test",   "vehicle_type": "truck", "plate": "34 TK 001", "capacity_kg": 3000.0},
    {"name": "Hande Aydın",   "email": "hande@sbtu.test",   "vehicle_type": "truck", "plate": "34 TK 002", "capacity_kg": 3000.0},
]


def clean_db(db) -> None:
    print("DB temizleniyor...")
    db.query(Package).delete()
    db.query(Stop).delete()
    db.query(Route).delete()
    db.query(Courier).delete()
    db.query(User).delete()
    db.commit()
    print("  Tüm tablolar temizlendi.")


def seed_static(db) -> dict:
    # Dispatcher
    dispatcher = User(
        name=DISPATCHER["name"],
        email=DISPATCHER["email"],
        role=DISPATCHER["role"],
    )
    db.add(dispatcher)
    db.flush()
    print(f"\n[Dispatcher] {dispatcher.name} → user_id={dispatcher.id}")

    # Couriers
    courier_map = {}  # vehicle_type → [courier_id, ...]
    print()
    for c in COURIERS:
        user = User(name=c["name"], email=c["email"], role=UserRole.courier)
        db.add(user)
        db.flush()

        courier = Courier(
            user_id=user.id,
            vehicle_type=c["vehicle_type"],
            plate=c["plate"],
            capacity_kg=c["capacity_kg"],
        )
        db.add(courier)
        db.flush()

        courier_map.setdefault(c["vehicle_type"], []).append(courier.id)
        print(f"  [{c['vehicle_type']:10s}] {c['name']:16s}  courier_id={courier.id}  {c['capacity_kg']:>6.0f}kg  {c['plate']}")

    db.commit()
    return {"dispatcher_id": dispatcher.id, "courier_map": courier_map}


def main():
    print("=" * 60)
    print("  SBTU Logistics — Static Setup")
    print("=" * 60)

    create_tables()
    db = SessionLocal()
    try:
        clean_db(db)
        result = seed_static(db)
    finally:
        db.close()

    print("\n" + "=" * 60)
    print("  Özet:")
    for vtype, ids in result["courier_map"].items():
        print(f"  {vtype:10s} → {len(ids)} courier  ids={ids}")
    print(f"\n  dispatcher_id = {result['dispatcher_id']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
