import requests
from typing import List, Tuple, Optional
from dotenv import load_dotenv
import os

from mapbox.coordinate import Coordinate

def get_weight_matrices(
    coords: List[Coordinate], 
    access_token: str
) -> Tuple[Optional[List[List[float]]], Optional[List[List[float]]]]:
    
    if len(coords) > 25:
        print(f"Error: Mapbox Matrix API limit is 25. You provided {len(coords)}.")
        return None, None
    if len(coords) < 2:
        print("Error: Need at least 2 coordinates to form a matrix.")
        return None, None

    # Format the coordinates string
    coords_string = ";".join([c.mapbox_str for c in coords])
    
    # Build the request
    url = f"https://api.mapbox.com/directions-matrix/v1/mapbox/driving/{coords_string}"
    params = {
        "annotations": "duration,distance", 
        "access_token": access_token
    }
    
    response = requests.get(url, params=params)
    
    if response.status_code == 200:
        data = response.json()
        return data.get("durations"), data.get("distances")
    else:
        print(f"Mapbox API Error: {response.status_code}")
        print(response.text)
        return None, None

# Example Usage 
if __name__ == "__main__":
    load_dotenv()
    TOKEN = os.getenv("MAPBOX_TOKEN")
    
    route_stops = [
        Coordinate(lat=38.3229, lon=26.7644, name="Urla Center"),
        Coordinate(lat=38.3188, lon=26.6388, name="IZTECH Campus"),
        Coordinate(lat=38.3166, lon=26.8322, name="Güzelbahçe")
    ]
    
    durations, distances = get_weight_matrices(route_stops, TOKEN)
    
    if durations and distances:
        print("--- DURATION MATRIX (Seconds) ---")
        for row in durations:
            print(row)
            
        print("\n--- DISTANCE MATRIX (Meters) ---")
        for row in distances:
            print(row)