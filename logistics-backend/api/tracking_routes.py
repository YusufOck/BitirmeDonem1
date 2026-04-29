"""
tracking_routes.py
------------------
Courier location state endpoints.

REST:
  GET    /api/v1/couriers/locations  — snapshot of all active couriers
  DELETE /api/v1/couriers/{id}       — remove courier from store (post-simulation cleanup)
"""

from fastapi import APIRouter, HTTPException

from api.location_store import courier_store

router = APIRouter(tags=["tracking"])


@router.get(
    "/api/v1/couriers/locations",
    summary="Snapshot of all active courier positions",
)
def get_all_locations():
    couriers = courier_store.get_all()
    return {
        "couriers": couriers,
        "count": len(couriers),
    }


@router.delete(
    "/api/v1/couriers/{courier_id}",
    summary="Remove courier from store (post-simulation cleanup)",
)
async def remove_courier(courier_id: str):
    if not courier_store.remove(courier_id):
        raise HTTPException(status_code=404, detail="Courier not found")
    return {"ok": True}
