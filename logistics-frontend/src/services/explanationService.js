const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';

const requestJson = async (url, options = {}) => {
  const response = await fetch(url, options);
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `API error: ${response.status}`);
  }
  return response.json();
};

export async function getModelInfo() {
  return requestJson(`${API_BASE_URL}/model-info`);
}

export async function getExplanation(routeSummary, optimizedRoute, useP90 = false) {
  return requestJson(`${API_BASE_URL}/explain`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      route_summary: routeSummary,
      optimized_route: optimizedRoute,
      use_p90: useP90,
    }),
  });
}

export async function checkExplanationHealth() {
  try {
    return await requestJson(`${API_BASE_URL}/explain/health`);
  } catch {
    return { status: 'unreachable', model_ready: false };
  }
}

export async function getSystemHealth() {
  try {
    return await requestJson(`${API_BASE_URL}/health`);
  } catch {
    return { status: 'down', services: {} };
  }
}
