# models.py
from pydantic import BaseModel

class Coordinate(BaseModel):
    lat: float
    lon: float
    name: str = "Unnamed Stop"

    @property
    def mapbox_str(self) -> str:
        """Returns format required by Mapbox: Longitude,Latitude"""
        return f"{self.lon},{self.lat}"

    @property
    def standard_str(self) -> str:
        """Returns standard format (Google/OR-Tools): Latitude,Longitude"""
        return f"{self.lat},{self.lon}"