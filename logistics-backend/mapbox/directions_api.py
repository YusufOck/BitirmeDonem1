import requests
from typing import List, Dict, Optional
from dotenv import load_dotenv
import os

from mapbox.coordinate import Coordinate

def get_final_route(
    ordered_stops: List[Coordinate], 
    access_token: str
) -> Optional[Dict]:
    """
    Fetches the final routable path from Mapbox using an ordered list of stops.
    Returns a dictionary containing the GeoJSON geometry, total distance, and duration.
    """

    if len(ordered_stops) < 2:
        print("Error: Need at least an origin and destination (2 stops).")
        return None
        
    if len(ordered_stops) > 25:
        print(f"Error: Mapbox Directions API limit is 25 waypoints. You provided {len(ordered_stops)}.")
        return None

    # Format the ordered coordinates string
    coords_string = ";".join([c.mapbox_str for c in ordered_stops])
    
    profile = os.getenv("MAPBOX_PROFILE", "driving-traffic")
    
    # Key Parameters for drawing maps
    params = {
        "alternatives": "false", # We only want the single optimal path
        "geometries": "geojson", # GeoJSON is the easiest format to render on frontend maps
        "overview": "full",      # Get the high-resolution route line
        "steps": "false",        # Set to "true" if your couriers need turn-by-turn text instructions
        "access_token": access_token
    }
    
    last_response = None
    for current_profile in dict.fromkeys([profile, "driving"]):
        url = f"https://api.mapbox.com/directions/v5/mapbox/{current_profile}/{coords_string}"
        response = requests.get(url, params=params)
        if response.status_code != 200:
            last_response = response
            continue

        data = response.json()
        
        # The API returns a list of 'routes'. We take the first (and only) one.
        route = data["routes"][0]
        
        # Extracting the most useful parts for the frontend/dashboard
        result = {
            "geometry": route["geometry"],       # The GeoJSON line to draw on the map
            "distance": route["distance"],       # Total distance in meters
            "duration": route["duration"],       # Total duration in seconds
            "weight_name": route["weight_name"]  # Usually 'routability'
        }
        return result

    print(f"Mapbox Directions API Error: {last_response.status_code}")
    print(last_response.text)
    return None


def get_route_alternatives(
    ordered_stops: List[Coordinate],
    access_token: str,
    max_routes: int = 3,
) -> list[Dict]:
    """
    Fetch real alternative road geometries from Mapbox for the same ordered stops.
    If Mapbox cannot produce alternatives for the waypoint set, an empty list is
    returned rather than fabricating extra routes.
    """
    if len(ordered_stops) < 2 or len(ordered_stops) > 25:
        return []

    coords_string = ";".join([c.mapbox_str for c in ordered_stops])
    profile = os.getenv("MAPBOX_PROFILE", "driving-traffic")
    params = {
        "alternatives": "true",
        "geometries": "geojson",
        "overview": "full",
        "steps": "false",
        "access_token": access_token,
    }

    for current_profile in dict.fromkeys([profile, "driving"]):
        url = f"https://api.mapbox.com/directions/v5/mapbox/{current_profile}/{coords_string}"
        response = requests.get(url, params=params)
        if response.status_code != 200:
            continue

        data = response.json()
        routes = data.get("routes", [])
        if len(routes) <= 1:
            return []

        return [
            {
                "rank": index,
                "geometry": route["geometry"],
                "distance": route["distance"],
                "duration": route["duration"],
                "weight_name": route.get("weight_name"),
            }
            for index, route in enumerate(routes[:max_routes], start=1)
        ]

    return []

# --- Example Usage ---
if __name__ == "__main__":
    load_dotenv()
    TOKEN = os.getenv("MAPBOX_TOKEN")
    
    # Imagine OR-Tools decided this is the optimal order:
    final_ordered_route = [
        Coordinate(lat=38.3229, lon=26.7644, name="Urla Center (Start)"),
        Coordinate(lat=38.3166, lon=26.8322, name="Güzelbahçe (Stop 1)"),
        Coordinate(lat=38.3188, lon=26.6388, name="IZTECH Campus (End)")
    ]
    
    route_data = get_final_route(final_ordered_route, TOKEN)
    
    if route_data:
        print(f"Total Route Distance: {route_data['distance'] / 1000:.2f} km")
        print(f"Total Route Duration: {route_data['duration'] / 60:.1f} minutes")
        print("\nGeoJSON Geometry (Ready to be passed to your frontend map):")
        print(route_data["geometry"])
