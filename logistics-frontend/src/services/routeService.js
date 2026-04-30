const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';
const API_ROOT_URL = API_BASE_URL.replace(/\/api\/v1\/?$/, '');

const DEFAULT_DEPOT = {
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

export const postSuggestionDecision = async (routeId, suggestionId, action) => {
  return requestJson(`${API_BASE_URL}/routes/${routeId}/suggestion/${suggestionId}/decision`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ action }),
  });
};
