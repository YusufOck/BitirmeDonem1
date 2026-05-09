const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';

const requestJson = async (url, options = {}) => {
  const response = await fetch(url, options);
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `API error: ${response.status}`);
  }
  return response.json();
};

export async function runOptimization({ depotLat, depotLon, stops, useP90 = false, timeLimitSeconds = 15 }) {
  return requestJson(`${API_BASE_URL}/full-route`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      depot_latitude: depotLat,
      depot_longitude: depotLon,
      stops,
      num_vehicles: 1,
      time_limit_seconds: timeLimitSeconds,
      use_p90: useP90,
      generate_explanation: false,
    }),
  });
}

export async function runAutoDispatch(body) {
  return requestJson(`${API_BASE_URL}/auto-dispatch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function fetchRouteDetail(routeId) {
  return requestJson(`${API_BASE_URL}/routes/${routeId}`);
}

export async function fetchFinalRouteGeometry(stops) {
  const API_ROOT_URL = API_BASE_URL.replace(/\/api\/v1\/?$/, '');
  return requestJson(`${API_ROOT_URL}/api/route/final`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stops }),
  });
}
