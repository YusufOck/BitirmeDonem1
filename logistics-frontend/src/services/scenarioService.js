const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';

const requestJson = async (url, options = {}) => {
  const response = await fetch(url, options);
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `API error: ${response.status}`);
  }
  return response.json();
};

export async function runScenario({ depotLat, depotLon, stops, controls, segmentOverrides = [], timeLimitSeconds = 15 }) {
  return requestJson(`${API_BASE_URL}/scenario/reoptimize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      depot_latitude: depotLat,
      depot_longitude: depotLon,
      stops,
      controls,
      segment_overrides: segmentOverrides,
      time_limit_seconds: timeLimitSeconds,
    }),
  });
}

export async function createScenario({ name, controls, segmentOverrides, stops }) {
  return requestJson(`${API_BASE_URL}/scenario/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, controls, segment_overrides: segmentOverrides, stops }),
  });
}

export async function listScenarios() {
  return requestJson(`${API_BASE_URL}/scenario/list`);
}

export async function getScenario(scenarioId) {
  return requestJson(`${API_BASE_URL}/scenario/${scenarioId}`);
}

export async function updateScenarioSegment(scenarioId, segmentOverride) {
  return requestJson(`${API_BASE_URL}/scenario/${scenarioId}/update-segment`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(segmentOverride),
  });
}
