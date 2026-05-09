const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';
const API_ROOT_URL = API_BASE_URL.replace(/\/api\/v1\/?$/, '');

export const DEFAULT_DEPOT = {
  depot_latitude: 39.75,
  depot_longitude: 37.015,
};

const requestJson = async (url, options = {}) => {
  const response = await fetch(url, options);

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `API error: ${response.status}`);
  }

  return response.json();
};

export const fetchCouriers = async () => {
  return requestJson(`${API_BASE_URL}/couriers`);
};

export const fetchStopPool = async () => {
  return requestJson(`${API_BASE_URL}/stop-pool`);
};

export const fetchCourierRoutes = async (courierId) => {
  return requestJson(`${API_BASE_URL}/couriers/${courierId}/routes`);
};

export const fetchModelInfo = async () => {
  return requestJson(`${API_BASE_URL}/model-info`);
};

export const buildAutoDispatchRequest = (couriers = [], stopPool = []) => {
  const activeCouriers = couriers.slice(0, 2);
  const selectedStops = stopPool.slice(0, activeCouriers.length * 5);

  if (activeCouriers.length === 0) {
    throw new Error('No couriers found in database. Run the seed script first.');
  }

  if (selectedStops.length < activeCouriers.length * 2) {
    throw new Error('Not enough stops in stop pool. Run the seed script first.');
  }

  const stopsPerCourier = Math.ceil(selectedStops.length / activeCouriers.length);

  return {
    ...DEFAULT_DEPOT,
    date: new Date().toISOString().slice(0, 10),
    time_limit_seconds: 15,
    use_p90: false,
    generate_explanation: false,
    assignments: activeCouriers.map((courier, index) => ({
      courier_id: courier.id,
      stop_pool_ids: selectedStops
        .slice(index * stopsPerCourier, (index + 1) * stopsPerCourier)
        .map((stop) => stop.id),
    })),
  };
};

export const fetchAutoDispatch = async (body) => {
  return requestJson(`${API_BASE_URL}/auto-dispatch`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });
};

export const fetchScenarioReoptimization = async (body) => {
  return requestJson(`${API_BASE_URL}/scenario/reoptimize`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });
};

export const fetchFinalRoute = async (stops) => {
  return requestJson(`${API_ROOT_URL}/api/route/final`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ stops }),
  });
};

export const completeStopRequest = async (stopId, actualDelayMin = null) => {
  return requestJson(`${API_BASE_URL}/stops/${stopId}/complete`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ actual_delay_min: actualDelayMin }),
  });
};

export const postSuggestionDecision = async (routeId, decision) => {
  return requestJson(`${API_BASE_URL}/routes/${routeId}/simulation/decision`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision }),
  });
};

// ── Lifecycle MVP API ─────────────────────────────────────────────────────────

export const fetchRouteLifecycleState = async (routeId) => {
  return requestJson(`${API_BASE_URL}/routes/${routeId}/state`);
};

export const startDispatchRoute = async (routeId) => {
  return requestJson(`${API_BASE_URL}/routes/${routeId}/dispatch/start`, { method: 'POST' });
};

export const updateScenarioConditions = async (routeId, conditions) => {
  return requestJson(`${API_BASE_URL}/routes/${routeId}/conditions/update`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(conditions),
  });
};

export const recalculateRecommendation = async (routeId) => {
  return requestJson(`${API_BASE_URL}/routes/${routeId}/recommendation/recalculate`, { method: 'POST' });
};

export const applyRecommendation = async (routeId) => {
  return requestJson(`${API_BASE_URL}/routes/${routeId}/recommendation/apply`, { method: 'POST' });
};

export const resetLifecycle = async (routeId) => {
  return requestJson(`${API_BASE_URL}/routes/${routeId}/lifecycle/reset`, { method: 'POST' });
};

// ── Agent / RAG API ───────────────────────────────────────────────────────────

export const fetchAgentExplanation = async (payload) => {
  return requestJson(`${API_BASE_URL}/agent/recommendation/explain`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
};

export const fetchAgentEvalSummary = async () => {
  return requestJson(`${API_BASE_URL}/agent/evaluation/summary`);
};
