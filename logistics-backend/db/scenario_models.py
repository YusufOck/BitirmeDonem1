"""
scenario_models.py
------------------
SQLAlchemy ORM models for Scenario Simulation persistence.
"""

from __future__ import annotations

from datetime import datetime
from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base

class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    
    # Store request inputs
    controls: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default='{}')
    segment_overrides: Mapped[list] = mapped_column(JSONB, nullable=False, server_default='[]')
    stops: Mapped[list] = mapped_column(JSONB, nullable=False, server_default='[]')
    
    # Store snapshots of the results
    baseline_summary: Mapped[dict | None] = mapped_column(JSONB)
    scenario_summary: Mapped[dict | None] = mapped_column(JSONB)
    delta: Mapped[dict | None] = mapped_column(JSONB)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
