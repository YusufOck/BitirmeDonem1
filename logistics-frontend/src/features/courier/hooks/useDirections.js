import { useState, useEffect, useRef } from 'react';

const TOKEN = import.meta.env.VITE_MAPBOX_TOKEN;
const BASE_URL = 'https://api.mapbox.com/directions/v5/mapbox/driving';
const CHUNK_SIZE = 25;
const ARRIVAL_THRESHOLD_M = 30;

function haversineMeters(lat1, lng1, lat2, lng2) {
  const R = 6371000;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLng = ((lng2 - lng1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

export function getCurrentStepIndex(livePosition, steps, lastIndex = 0) {
  if (!livePosition || !steps?.length) return 0;
  const { longitude, latitude } = livePosition;
  for (let i = lastIndex; i < steps.length; i++) {
    const [sLng, sLat] = steps[i].maneuver.location;
    if (haversineMeters(latitude, longitude, sLat, sLng) > ARRIVAL_THRESHOLD_M) {
      return i;
    }
  }
  return steps.length - 1;
}

export function getDistanceToStep(livePosition, step) {
  if (!livePosition || !step) return null;
  const [sLng, sLat] = step.maneuver.location;
  return haversineMeters(livePosition.latitude, livePosition.longitude, sLat, sLng);
}

function formatWaypoints(stops) {
  return stops.map(s => `${s.longitude},${s.latitude}`).join(';');
}

async function fetchChunk(stops) {
  const coords = formatWaypoints(stops);
  const url = `${BASE_URL}/${coords}?steps=true&waypoints_per_route=true&geometries=geojson&language=tr&banner_instructions=true&access_token=${TOKEN}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Directions API error: ${res.status}`);
  return res.json();
}

function chunkStops(stops) {
  if (stops.length <= CHUNK_SIZE) return [stops];
  const chunks = [];
  let i = 0;
  while (i < stops.length) {
    chunks.push(stops.slice(i, i + CHUNK_SIZE));
    i += CHUNK_SIZE - 1; // 1-stop overlap
    if (i + 1 >= stops.length) break;
  }
  // Ensure last stop is included
  const last = chunks[chunks.length - 1];
  if (last[last.length - 1] !== stops[stops.length - 1]) {
    chunks.push(stops.slice(-(Math.min(2, stops.length))));
  }
  return chunks;
}

export function useDirections(stops) {
  const [steps, setSteps] = useState([]);
  const [routeGeometry, setRouteGeometry] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const abortRef = useRef(null);

  useEffect(() => {
    if (!stops || stops.length < 2) {
      setSteps([]);
      setRouteGeometry(null);
      return;
    }

    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const sorted = [...stops].sort((a, b) => a.stop_sequence - b.stop_sequence);
    const chunks = chunkStops(sorted);

    setLoading(true);
    setError(null);

    (async () => {
      try {
        const results = [];
        for (const chunk of chunks) {
          if (controller.signal.aborted) return;
          const data = await fetchChunk(chunk);
          results.push(data);
        }

        if (controller.signal.aborted) return;

        const allSteps = [];
        const allCoords = [];

        for (const data of results) {
          if (!data.routes?.length) continue;
          const route = data.routes[0];
          for (const leg of route.legs) {
            for (const step of leg.steps) {
              allSteps.push(step);
            }
          }
          const coords = route.geometry?.coordinates;
          if (coords) {
            // Avoid duplicate junction point between chunks
            if (allCoords.length > 0) {
              allCoords.push(...coords.slice(1));
            } else {
              allCoords.push(...coords);
            }
          }
        }

        setSteps(allSteps);
        setRouteGeometry(
          allCoords.length > 1
            ? { type: 'Feature', geometry: { type: 'LineString', coordinates: allCoords } }
            : null
        );
      } catch (err) {
        if (!controller.signal.aborted) setError(err.message);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();

    return () => controller.abort();
  }, [stops]);

  return { steps, routeGeometry, loading, error };
}
