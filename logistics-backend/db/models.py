"""
models.py
---------
SQLAlchemy ORM models for SBTU Logistics.

Tables:
    users     → dispatcher or courier accounts (no auth yet, role field only)
    couriers  → courier-specific info (vehicle, capacity) linked to a user
    routes    → delivery routes assigned to couriers by dispatchers
    stops     → ordered delivery stops within a route
    packages  → packages to be delivered at a stop
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── Enums ─────────────────────────────────────────────────────────────────────

class UserRole(str, enum.Enum):
    dispatcher = "dispatcher"
    courier    = "courier"


class RouteStatus(str, enum.Enum):
    planned   = "planned"
    active    = "active"
    completed = "completed"
    cancelled = "cancelled"


class StopStatus(str, enum.Enum):
    pending   = "pending"
    completed = "completed"
    skipped   = "skipped"


class PackageStatus(str, enum.Enum):
    pending   = "pending"
    delivered = "delivered"
    failed    = "failed"


# ── Models ────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    courier: Mapped[Courier | None] = relationship("Courier", back_populates="user", uselist=False)


class Courier(Base):
    __tablename__ = "couriers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    vehicle_type: Mapped[str | None] = mapped_column(String(50))   # car, bike, van, etc.
    plate: Mapped[str | None] = mapped_column(String(20))
    capacity_kg: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship("User", back_populates="courier")
    routes: Mapped[list[Route]] = relationship("Route", back_populates="courier")


class Route(Base):
    __tablename__ = "routes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    courier_id: Mapped[int] = mapped_column(ForeignKey("couriers.id"), nullable=False)
    dispatcher_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[RouteStatus] = mapped_column(Enum(RouteStatus), default=RouteStatus.planned, nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    depot_latitude: Mapped[float | None] = mapped_column(Float)
    depot_longitude: Mapped[float | None] = mapped_column(Float)
    # Final optimization result stored when route completes
    total_distance_m: Mapped[float | None] = mapped_column(Float)
    total_duration_s: Mapped[float | None] = mapped_column(Float)
    total_delay_min: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    courier: Mapped[Courier] = relationship("Courier", back_populates="routes")
    stops: Mapped[list[Stop]] = relationship("Stop", back_populates="route", order_by="Stop.sequence")


class Stop(Base):
    __tablename__ = "stops"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)       # optimized order
    name: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    time_window_slack_min: Mapped[float] = mapped_column(Float, default=480.0)
    status: Mapped[StopStatus] = mapped_column(Enum(StopStatus), default=StopStatus.pending, nullable=False)
    # Filled when stop is completed
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime)
    actual_delay_min: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    route: Mapped[Route] = relationship("Route", back_populates="stops")
    packages: Mapped[list[Package]] = relationship("Package", back_populates="stop")


class StopPool(Base):
    __tablename__ = "stop_pool"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    assignments: Mapped[list[CourierStopAssignment]] = relationship("CourierStopAssignment", back_populates="stop")


class CourierStopAssignment(Base):
    __tablename__ = "courier_stop_assignments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    courier_id: Mapped[int] = mapped_column(ForeignKey("couriers.id"), nullable=False)
    stop_pool_id: Mapped[int] = mapped_column(ForeignKey("stop_pool.id"), nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    courier: Mapped[Courier] = relationship("Courier")
    stop: Mapped[StopPool] = relationship("StopPool", back_populates="assignments")


class ReoptFeatureLog(Base):
    """
    Re-opt sırasında kullanılan feature'ları saklar.
    Model lifecycle için: drift tespiti + yeniden eğitim veri kaynağı.
    complete_stop sonrası async olarak yazılır — real-time path'i yavaşlatmaz.
    """
    __tablename__ = "reopt_feature_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stop_id: Mapped[int] = mapped_column(ForeignKey("stops.id"), nullable=False)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id"), nullable=False)
    logged_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Stop context
    stop_sequence: Mapped[int | None] = mapped_column(Integer)
    road_type: Mapped[str | None] = mapped_column(String(20))
    hour_of_day: Mapped[int | None] = mapped_column(Integer)
    day_of_week: Mapped[int | None] = mapped_column(Integer)
    planned_travel_min: Mapped[float | None] = mapped_column(Float)
    distance_from_prev_km: Mapped[float | None] = mapped_column(Float)
    time_window_slack_min: Mapped[float | None] = mapped_column(Float)
    actual_delay_min: Mapped[float | None] = mapped_column(Float)
    cumulative_delay_min: Mapped[float | None] = mapped_column(Float)

    # Pipeline features (CSV lookup)
    congestion_ratio_mean: Mapped[float | None] = mapped_column(Float)
    incident_rate: Mapped[float | None] = mapped_column(Float)
    road_surface_condition_enc: Mapped[int | None] = mapped_column(Integer)
    delay_risk_score_mean: Mapped[float | None] = mapped_column(Float)
    hist_delay_probability: Mapped[float | None] = mapped_column(Float)
    hist_slack_min: Mapped[float | None] = mapped_column(Float)

    # Stop sequential context
    prev_stop_delay_min: Mapped[float | None] = mapped_column(Float)

    # Package context
    package_weight_kg: Mapped[float | None] = mapped_column(Float)

    # Route context
    weather_condition: Mapped[str | None] = mapped_column(String(20))
    temperature_c: Mapped[float | None] = mapped_column(Float)
    wind_speed_kmh: Mapped[float | None] = mapped_column(Float)
    precipitation_mm: Mapped[float | None] = mapped_column(Float)
    road_incident: Mapped[int | None] = mapped_column(Integer)
    incident_severity: Mapped[float | None] = mapped_column(Float)
    overall_delay_factor: Mapped[float | None] = mapped_column(Float)

    # Vehicle context
    vehicle_type_enc: Mapped[int | None] = mapped_column(Integer)

    # Traffic context (pipeline'dan)
    traffic_level: Mapped[str | None] = mapped_column(String(20))

    # Re-opt sonucu
    reopt_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    time_savings_min: Mapped[float | None] = mapped_column(Float)
    missed_time_window: Mapped[bool | None] = mapped_column(Boolean)


class Package(Base):
    __tablename__ = "packages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stop_id: Mapped[int] = mapped_column(ForeignKey("stops.id"), nullable=False)
    tracking_no: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    recipient_name: Mapped[str | None] = mapped_column(String(200))
    weight_kg: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[PackageStatus] = mapped_column(Enum(PackageStatus), default=PackageStatus.pending, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    stop: Mapped[Stop] = relationship("Stop", back_populates="packages")
