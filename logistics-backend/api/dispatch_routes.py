"""
dispatch_routes.py
------------------
Dispatcher endpoints.

    GET    /api/v1/routes/{id}   → route detayı (stop listesiyle)
    POST   /api/v1/stop-pool     → stop havuzuna yeni durak ekle
    GET    /api/v1/stop-pool     → tüm stop'ları listele
    GET    /api/v1/couriers      → tüm courier listesi
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from database import get_db
from db.models import (
    Courier, Package, Route, Stop, StopPool, User,
)

router = APIRouter(prefix="/api/v1", tags=["dispatcher"])


# ── Response schemas ──────────────────────────────────────────────────────────

class PackageOut(BaseModel):
    id: int
    tracking_no: str
    recipient_name: str | None
    weight_kg: float | None
    notes: str | None
    status: str

    model_config = {"from_attributes": True}


class StopOut(BaseModel):
    id: int
    sequence: int
    name: str | None
    address: str | None
    latitude: float
    longitude: float
    time_window_slack_min: float
    status: str
    arrived_at: datetime | None
    actual_delay_min: float | None
    packages: list[PackageOut] = []

    model_config = {"from_attributes": True}


class RouteOut(BaseModel):
    id: int
    courier_id: int
    dispatcher_id: int
    status: str
    date: datetime
    depot_latitude: float | None
    depot_longitude: float | None
    total_distance_m: float | None
    total_duration_s: float | None
    total_delay_min: float | None
    created_at: datetime
    stops: list[StopOut] = []

    model_config = {"from_attributes": True}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/routes/{route_id}", response_model=RouteOut,
            summary="Route detayını getir (stop listesiyle)")
def get_route(route_id: int, db: Session = Depends(get_db)):
    route = (
        db.query(Route)
        .options(selectinload(Route.stops).selectinload(Stop.packages))
        .filter(Route.id == route_id)
        .first()
    )
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    return route


# ── Stop Pool ─────────────────────────────────────────────────────────────────

class CreateStopPoolRequest(BaseModel):
    name: str
    address: str | None = None
    latitude: float
    longitude: float
    dispatcher_id: int


class StopPoolOut(BaseModel):
    id: int
    name: str
    address: str | None
    latitude: float
    longitude: float
    created_by: int
    created_at: datetime

    model_config = {"from_attributes": True}


@router.post("/stop-pool", response_model=StopPoolOut, status_code=201,
             summary="Stop havuzuna yeni durak ekle")
def create_stop_pool(body: CreateStopPoolRequest, db: Session = Depends(get_db)):
    dispatcher = db.get(User, body.dispatcher_id)
    if not dispatcher:
        raise HTTPException(status_code=404, detail="Dispatcher not found")
    stop = StopPool(
        name=body.name,
        address=body.address,
        latitude=body.latitude,
        longitude=body.longitude,
        created_by=body.dispatcher_id,
    )
    db.add(stop)
    db.commit()
    db.refresh(stop)
    return stop


@router.get("/stop-pool", response_model=list[StopPoolOut],
            summary="Tüm stop havuzunu listele")
def list_stop_pool(db: Session = Depends(get_db)):
    return db.query(StopPool).order_by(StopPool.id).all()


# ── Couriers ──────────────────────────────────────────────────────────────────

class CourierOut(BaseModel):
    id: int
    user_id: int
    name: str
    email: str
    vehicle_type: str | None
    plate: str | None

    model_config = {"from_attributes": True}


@router.get("/couriers", response_model=list[CourierOut],
            summary="Tüm courier listesi")
def list_couriers(db: Session = Depends(get_db)):
    from sqlalchemy.orm import joinedload
    couriers = db.query(Courier).options(joinedload(Courier.user)).order_by(Courier.id).all()
    return [
        CourierOut(
            id=c.id,
            user_id=c.user_id,
            name=c.user.name,
            email=c.user.email,
            vehicle_type=c.vehicle_type,
            plate=c.plate,
        )
        for c in couriers
    ]
