import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database import SessionLocal
from db.models import StopPool, Courier, User, UserRole

db = SessionLocal()

# Ensure Courier 2 exists
email = "courier2@sbtu.local"
user = db.query(User).filter(User.email == email).first()
if not user:
    user = User(name="Courier 2", email=email, role=UserRole.courier)
    db.add(user)
    db.flush()
    courier = Courier(user_id=user.id, vehicle_type="van")
    db.add(courier)
    db.flush()
    print("Courier 2 created!")

# Get stops for Courier 2 (stops 11 to 15, since Courier 0 uses 1-5 and Courier 1 uses 6-10)
stops = db.query(StopPool).order_by(StopPool.id).all()
if len(stops) >= 15:
    c2_stops = stops[10:15]
    
    # Depot is 39.75, 37.015
    # Offsets for a polygon with ~400-500 meters between each stop
    # 0.004 degrees is ~400-450 meters
    offsets = [
        (0.004, 0.000),   # ~444m North
        (0.004, 0.005),   # ~425m East from stop 1
        (0.000, 0.005),   # ~444m South from stop 2
        (-0.002, 0.002),  # ~300m South-West from stop 3
        (-0.001, -0.001)  # ~300m West from stop 4 (back towards depot)
    ]
    
    for i, stop in enumerate(c2_stops):
        stop.latitude = 39.75 + offsets[i][0]
        stop.longitude = 37.015 + offsets[i][1]
        # Adding a prefix to name to make it obvious
        if not stop.name.startswith("[C2]"):
            stop.name = f"[C2] {stop.name}"
    
    db.commit()
    print("Successfully configured 5 close-range stops for Courier 2!")
else:
    print("Not enough stops in StopPool. Need at least 15.")
