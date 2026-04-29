import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.fleet_routes import router as fleet_router
from api.routes import router as optimization_router
from api.tracking_routes import router as tracking_router
from database import get_db
from api.dispatch_routes import router as dispatch_router
from api.courier_routes import router as courier_router
from api.simulation_routes import router as simulation_router

from dotenv import load_dotenv
from mapbox.coordinate import Coordinate
from mapbox.directions_api import get_final_route

load_dotenv()
MAPBOX_TOKEN = os.getenv("MAPBOX_TOKEN", "")

logger = logging.getLogger(__name__)

openapi_tags = [
    {
        "name": "optimization",
        "description": "End-to-end route planning pipeline — Mapbox Matrix → ML delay prediction → OR-Tools VRP → Mapbox Directions.",
    },
    {
        "name": "ml",
        "description": "ML model metadata, feature info, and warm-up.",
    },
    {
        "name": "ai",
        "description": "Ollama-powered natural language explanations for dispatchers.",
    },
    {
        "name": "fleet",
        "description": "Fleet-level risk monitoring and route lifecycle management.",
    },
    {
        "name": "dispatcher",
        "description": "Route and stop CRUD operations for dispatchers.",
    },
    {
        "name": "courier",
        "description": "Courier-facing route retrieval and stop completion with live re-optimization.",
    },
    {
        "name": "tracking",
        "description": "Real-time courier location updates (REST) and live WebSocket stream for the dispatcher map.",
    },
    {
        "name": "scenario",
        "description": "What-if scenario analysis — compare baseline vs. modified route without calling Mapbox.",
    },
    {
        "name": "simulation",
        "description": "Demo simulation — move couriers along planned route geometries at configurable speed.",
    },
    {
        "name": "system",
        "description": "System health checks, frontend configuration, and service status.",
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    from database import create_tables
    create_tables()
    logger.info("DB tables ensured.")
    from ml.inference import _get_predictor
    logger.info("Loading ML model...")
    _get_predictor()
    logger.info("ML model ready.")
    from api.data_pipeline import pipeline
    logger.info("Loading data pipeline...")
    pipeline.load()
    logger.info("Data pipeline ready.")
    yield


app = FastAPI(
    title="SBTU Logistics API",
    description=(
        "Route optimization backend for last-mile delivery.\n\n"
        "**Pipeline:** Mapbox Matrix → ML delay prediction → OR-Tools CVRPTW → Mapbox Directions\n\n"
        "**Features:** real-time courier tracking, live re-optimization, what-if scenario analysis, "
        "Ollama-powered AI explanations, and demo simulation."
    ),
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=openapi_tags,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(optimization_router)
app.include_router(tracking_router)
app.include_router(fleet_router)
app.include_router(dispatch_router)
app.include_router(courier_router)
app.include_router(simulation_router)



class RouteRequest(BaseModel):
    stops: List[Coordinate]


@app.get("/api/status", tags=["system"], summary="API liveness check")
def get_status():
    return {
        "message": "FastAPI is up and running!",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/api/db-status", tags=["system"], summary="Database connectivity check")
def get_db_status(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"database": "connected"}

@app.post("/api/route/final", tags=["system"], summary="Fetch Mapbox Directions for a coordinate list")
async def fetch_final_route(request: RouteRequest) -> Dict[str, Any]:
    
    route_data = get_final_route(request.stops, MAPBOX_TOKEN)
    
    if route_data is None:
        # the script returns None if it hits a limit or Mapbox error
        raise HTTPException(
            status_code=400, 
            detail="Failed to generate route. Check backend logs for Mapbox API errors or coordinate limits."
        )
        
    # Append the stop names for the frontend sanity check, then return
    route_data["stops_ordered"] = [stop.name for stop in request.stops]
    return route_data


# ── Frontend static files ─────────────────────────────────────────────────────
_DIST = os.path.join(os.path.dirname(__file__), "dist")

if os.path.isdir(_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(_DIST, "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        return FileResponse(os.path.join(_DIST, "index.html"))
