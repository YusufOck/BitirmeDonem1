const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';

export const requestBody = {
  "depot_latitude": 39.75,
  "depot_longitude": 37.015,
  "date": "2026-04-19",
  "assignments": [
    { "courier_id": 1, "stop_pool_ids": [1, 2, 3, 4, 5] },
    { "courier_id": 2, "stop_pool_ids": [6, 7, 8, 9, 10] }
  ]
}

export const fetchAutoDispatch = async () => {
  try {
    const response = await fetch(`${API_BASE_URL}/auto-dispatch`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(requestBody),
    });

    if (!response.ok) {
      throw new Error(`API error: ${response.status}`);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error('Failed to fetch route data:', error);
    throw error;
  }
};


export const completeStopRequest = async (stopId, actualDelayMin = null) => {
  const response = await fetch(`${API_BASE_URL}/stops/${stopId}/complete`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ actual_delay_min: actualDelayMin }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to complete stop: ${response.status}`);
  }

  return response.json();
};

export const postSuggestionDecision = async (routeId, suggestionId, action) => {
  const response = await fetch(`${API_BASE_URL}/routes/${routeId}/suggestion/${suggestionId}/decision`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ action }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to make decision: ${response.status}`);
  }

  return response.json();
};
