from datetime import datetime, timezone
from typing import Any


def _courier_color(courier_id: str) -> str:
    """Generate a distinct HSL color from courier_id via golden-ratio hue distribution."""
    h = hash(courier_id) & 0xFFFFFFFF
    hue = (h * 137) % 360  # 137 ≈ golden-angle step keeps adjacent hues far apart
    return f"hsl({hue}, 70%, 55%)"


class CourierLocationStore:
    def __init__(self) -> None:
        self._couriers: dict[str, dict[str, Any]] = {}

    def upsert(self, courier_id: str, data: dict) -> tuple[dict, bool]:
        """Insert or update courier state. Returns (state, is_new)."""
        is_new = courier_id not in self._couriers
        data["color"] = _courier_color(courier_id)
        if is_new:
            data["first_seen"] = datetime.now(timezone.utc).isoformat()
        else:
            data["first_seen"] = self._couriers[courier_id]["first_seen"]
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        self._couriers[courier_id] = data
        return data, is_new

    def get_all(self) -> list[dict]:
        return list(self._couriers.values())

    def remove(self, courier_id: str) -> bool:
        return bool(self._couriers.pop(courier_id, None))


courier_store = CourierLocationStore()
