import { create } from 'zustand';
import {
  buildAutoDispatchRequest,
  completeStopRequest,
  DEFAULT_DEPOT,
  fetchAutoDispatch,
  fetchCourierRoutes,
  fetchCouriers,
  fetchFinalRoute,
  fetchModelInfo,
  fetchScenarioReoptimization,
  fetchStopPool,
  postSuggestionDecision,
  fetchRouteLifecycleState,
  startDispatchRoute,
  recalculateRecommendation,
  applyRecommendation,
  resetLifecycle,
  fetchAgentExplanation,
} from '../services/routeService';

const ROUTE_COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#aa3bff'];
const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws/simulation';
const DEFAULT_SCENARIO_CONTROLS = {
  traffic_density: 70,
  accident_severity: 30,
  weather_condition: 'rain',
  weather_severity: 55,
  road_disruption: 25,
  package_load: 60,
  dispatch_hour: 17,
  conservative_mode: false,
};

const DEFAULT_SEGMENT_OVERRIDE = {
  traffic_density: 0,
  accident_severity: 0,
  road_closure: false,
  weather_severity: 0,
  speed_reduction: 0,
  extra_delay_min: 0,
  risk_level: 'low',
  priority: 50,
  road_type: 'urban',
};

const round = (value, digits = 1) => {
  const num = Number(value);
  return Number.isFinite(num) ? Number(num.toFixed(digits)) : 0;
};

const makeInitials = (name, fallback) => {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return `C${fallback}`;
  return parts.slice(0, 2).map((part) => part[0]).join('').toUpperCase();
};

const getStopOrder = (stop, vehicleRoute) => {
  if (Number.isFinite(Number(stop.optimized_position))) return Number(stop.optimized_position);
  if (Number.isFinite(Number(stop.stop_sequence))) return Number(stop.stop_sequence);
  if (vehicleRoute?.stop_names) {
    const index = vehicleRoute.stop_names.indexOf(stop.stop_name);
    if (index !== -1) return index;
  }
  return 9999;
};

const getVehicleStops = (optimizedRoute, vehicleRoute) => {
  return (optimizedRoute || [])
    .filter((stop) => stop.vehicle_id === vehicleRoute.vehicle_id)
    .sort((a, b) => getStopOrder(a, vehicleRoute) - getStopOrder(b, vehicleRoute))
    .map((stop, index) => {
      const travel = Number(stop.planned_travel_min || 0);
      const delay = Number(stop.expected_delay_min || 0);
      return {
        ...stop,
        displaySequence: index + 1,
        etaLabel: delay > 0 ? `+${round(delay)} min delay` : 'On time',
        plannedTravelLabel: travel > 0 ? `${round(travel)} min travel` : 'Travel calculated',
      };
    });
};

const findLatestRoute = (routes = []) => {
  return [...routes].sort((a, b) => {
    const createdA = new Date(a.created_at || 0).getTime();
    const createdB = new Date(b.created_at || 0).getTime();
    return createdB - createdA;
  })[0];
};

const buildOriginalRouteStops = (assignment, stopPoolById, depot) => {
  const originalStops = (assignment.stop_pool_ids || [])
    .map((stopId) => stopPoolById.get(stopId))
    .filter(Boolean);

  return [
    { lat: depot.depot_latitude, lon: depot.depot_longitude, name: 'Depot' },
    ...originalStops.map((stop) => ({
      lat: stop.latitude,
      lon: stop.longitude,
      name: stop.name || `Stop ${stop.id}`,
    })),
  ];
};

const buildFallbackGeometry = (stops) => ({
  type: 'Feature',
  isFallback: true,
  geometry: {
    type: 'LineString',
    coordinates: stops.map((stop) => [stop.lon, stop.lat]),
  },
});

const buildComparison = (originalRoute, optimizedRoute) => {
  const originalDistanceKm = round(originalRoute.distance / 1000, 1);
  const optimizedDistanceKm = round(optimizedRoute.distance_m / 1000, 1);
  const originalDurationMin = Math.round(originalRoute.duration / 60);
  const optimizedDurationMin = Math.round(optimizedRoute.duration_s / 60);
  const distanceDeltaKm = round(originalDistanceKm - optimizedDistanceKm, 1);
  const durationDeltaMin = originalDurationMin - optimizedDurationMin;

  return {
    originalDistanceKm,
    optimizedDistanceKm,
    distanceDeltaKm,
    originalDurationMin,
    optimizedDurationMin,
    durationDeltaMin,
    distanceSaved: distanceDeltaKm > 0,
    durationSaved: durationDeltaMin > 0,
  };
};

const getStatusFromMetrics = (vehicleRoute) => {
  if (vehicleRoute.severe_stop_count > 0 || vehicleRoute.high_risk_stop_count > 0) return 'Needs Review';
  if (vehicleRoute.total_expected_delay_min > 10) return 'Delay Watch';
  return 'On Track';
};

const getStatusTone = (status) => {
  if (status === 'Needs Review') return 'danger';
  if (status === 'Delay Watch') return 'warning';
  return 'success';
};

const asFeature = (geometry) => (geometry ? { type: 'Feature', geometry } : null);

const normalizeStopToken = (value) => {
  if (value === null || value === undefined) return null;
  const raw = String(value).trim();
  if (!raw) return null;
  const tokens = new Set([raw]);
  const match = raw.match(/(\d+)$/);
  if (match) {
    tokens.add(match[1]);
    tokens.add(String(Number(match[1])));
  }
  return tokens;
};

const getStopAliases = (stop = {}) => {
  const aliases = new Set();
  [
    stop.stop_id,
    stop.id,
    stop.stop_name,
    stop.name,
    stop.source_stop_id,
  ].forEach((value) => {
    const tokens = normalizeStopToken(value);
    if (tokens) tokens.forEach((token) => aliases.add(token));
  });
  return aliases;
};

const findMatchingRouteStop = (routeStops = [], candidate = {}) => {
  const candidateAliases = getStopAliases(candidate);
  if (candidateAliases.size === 0) return null;
  return routeStops.find((routeStop) => {
    const routeAliases = getStopAliases(routeStop);
    return [...candidateAliases].some((alias) => routeAliases.has(alias));
  }) || null;
};

const alignScenarioStopsWithRoute = (scenarioStops = [], routeStops = []) => (
  (scenarioStops || []).map((stop) => {
    const match = findMatchingRouteStop(routeStops, stop);
    if (!match) return stop;
    return {
      ...match,
      ...stop,
      stop_id: match.stop_id,
      stop_name: stop.stop_name || match.stop_name,
      source_stop_id: stop.stop_id,
    };
  })
);

const serializeStopForScenario = (stop, index) => ({
  stop_sequence: Number(stop.stop_sequence || stop.displaySequence || index + 1),
  cumulative_delay_min: Number(stop.cumulative_delay_min || 0),
  prev_stop_delay_min: Number(stop.prev_stop_delay_min || 0),
  time_window_slack_min: Number(stop.time_window_slack_min || 480),
  hist_slack_min: Number(stop.hist_slack_min || 54.69),
  hist_delay_probability: Number(stop.hist_delay_probability || stop.delay_probability || 0.25),
  distance_from_prev_km: stop.distance_from_prev_km ?? null,
  planned_travel_min: stop.planned_travel_min ?? null,
  road_type: stop.road_type ?? null,
  traffic_level: stop.traffic_level ?? null,
  weather_condition: stop.weather_condition ?? null,
  hour_of_day: stop.hour_of_day ?? null,
  day_of_week: stop.day_of_week ?? null,
  congestion_ratio_mean: stop.congestion_ratio_mean ?? null,
  incident_rate: stop.incident_rate ?? null,
  road_surface_condition: stop.road_surface_condition ?? null,
  road_surface_condition_enc: stop.road_surface_condition_enc ?? null,
  delay_risk_score_mean: stop.delay_risk_score_mean ?? null,
  package_count: stop.package_count ?? null,
  package_weight_kg: stop.package_weight_kg ?? null,
  vehicle_type: stop.vehicle_type ?? null,
  road_incident: stop.road_incident ?? null,
  incident_severity: stop.incident_severity ?? null,
  temperature_c: stop.temperature_c ?? null,
  precipitation_mm: stop.precipitation_mm ?? null,
  wind_speed_kmh: stop.wind_speed_kmh ?? null,
  visibility_km: stop.visibility_km ?? null,
  overall_delay_factor: stop.overall_delay_factor ?? null,
  time_window_duration_min: stop.time_window_duration_min ?? null,
  stop_id: stop.stop_id ?? null,
  stop_name: stop.stop_name ?? null,
  latitude: stop.latitude,
  longitude: stop.longitude,
  feature_source: stop.feature_source ?? null,
  delay_factors: Array.isArray(stop.delay_factors) ? stop.delay_factors : [],
});

const getScenarioCoordinates = (scenarioResult) => (
  scenarioResult?.scenarioGeometry?.geometry?.coordinates || []
);

const haversineKm = (lat1, lon1, lat2, lon2) => {
  const radiusKm = 6371;
  const toRad = (value) => (Number(value) * Math.PI) / 180;
  const aLat = toRad(lat1);
  const bLat = toRad(lat2);
  const dLat = toRad(Number(lat2) - Number(lat1));
  const dLon = toRad(Number(lon2) - Number(lon1));
  const a = Math.sin(dLat / 2) ** 2
    + Math.cos(aLat) * Math.cos(bLat) * Math.sin(dLon / 2) ** 2;
  return radiusKm * 2 * Math.asin(Math.sqrt(a));
};

const buildRouteCumulativeKm = (coords = []) => {
  const cumulative = [0];
  for (let index = 1; index < coords.length; index += 1) {
    cumulative.push(
      cumulative[index - 1]
      + haversineKm(coords[index - 1][1], coords[index - 1][0], coords[index][1], coords[index][0]),
    );
  }
  return cumulative;
};

const projectStopProgressKm = (coords = [], cumulative = [], stop = {}) => {
  if (!Array.isArray(coords) || coords.length < 2) return Number.POSITIVE_INFINITY;
  const stopLat = Number(stop.latitude ?? stop.lat);
  const stopLon = Number(stop.longitude ?? stop.lon);
  if (!Number.isFinite(stopLat) || !Number.isFinite(stopLon)) return Number.POSITIVE_INFINITY;

  const refLatRad = (stopLat * Math.PI) / 180;
  const toLocalKm = ([lon, lat]) => ([
    (Number(lon) - stopLon) * 111.32 * Math.cos(refLatRad),
    (Number(lat) - stopLat) * 110.574,
  ]);

  let bestDistanceSq = Number.POSITIVE_INFINITY;
  let bestProgressKm = cumulative[0] || 0;
  for (let index = 0; index < coords.length - 1; index += 1) {
    const [ax, ay] = toLocalKm(coords[index]);
    const [bx, by] = toLocalKm(coords[index + 1]);
    const vx = bx - ax;
    const vy = by - ay;
    const denom = vx * vx + vy * vy;
    const t = denom === 0 ? 0 : Math.max(0, Math.min(1, -((ax * vx + ay * vy) / denom)));
    const px = ax + t * vx;
    const py = ay + t * vy;
    const distanceSq = px * px + py * py;
    if (distanceSq < bestDistanceSq) {
      bestDistanceSq = distanceSq;
      bestProgressKm = (cumulative[index] || 0) + ((cumulative[index + 1] || 0) - (cumulative[index] || 0)) * t;
    }
  }
  return bestProgressKm;
};

const orderStopsByRouteProgress = (stops = [], geometryFeature) => {
  const coords = geometryFeature?.geometry?.coordinates || [];
  if (!Array.isArray(coords) || coords.length < 2) return stops;
  const cumulative = buildRouteCumulativeKm(coords);
  return [...stops]
    .map((stop, originalIndex) => ({
      ...stop,
      _routeProgressKm: projectStopProgressKm(coords, cumulative, stop),
      _originalIndex: originalIndex,
    }))
    .sort((a, b) => (
      (a._routeProgressKm - b._routeProgressKm)
      || (Number(a.optimized_position || 0) - Number(b.optimized_position || 0))
      || (a._originalIndex - b._originalIndex)
    ))
    .map((stop, index) => {
      const routeProgressKm = stop._routeProgressKm;
      const cleanStop = { ...stop };
      delete cleanStop._routeProgressKm;
      delete cleanStop._originalIndex;
      return {
        ...cleanStop,
        optimized_position: index,
        displaySequence: index + 1,
        route_progress_km: Number.isFinite(routeProgressKm) ? round(routeProgressKm, 3) : null,
      };
    });
};

const normalizeScenarioResult = (result, vehicleId, route = null) => {
  const scenarioGeometry = asFeature(result.scenario_geometry);
  const routeStops = route?.stops || [];
  const alignedScenarioRoute = alignScenarioStopsWithRoute(result.scenario_route || [], routeStops);
  return {
    ...result,
    vehicleId,
    baselineGeometry: asFeature(result.baseline_geometry),
    scenarioGeometry,
    baseline_route: alignScenarioStopsWithRoute(result.baseline_route || [], routeStops),
    current_order_route: alignScenarioStopsWithRoute(result.current_order_route || result.baseline_route || [], routeStops),
    scenario_route: orderStopsByRouteProgress(alignedScenarioRoute, scenarioGeometry),
    mapboxAlternatives: (result.mapbox_alternatives || []).map((alternative) => ({
      ...alternative,
      geometry: asFeature(alternative.geometry),
    })),
  };
};

const getControlsForRoute = (state, vehicleId) => {
  if (vehicleId === null || vehicleId === undefined) return state.scenarioControls || DEFAULT_SCENARIO_CONTROLS;
  return state.scenarioControlsByVehicleId?.[vehicleId] || state.scenarioControls || DEFAULT_SCENARIO_CONTROLS;
};

const normalizeSegmentOverrides = (segmentOverrides = {}) => (
  Object.values(segmentOverrides)
    .filter((override) => override?.from_stop_id && override?.to_stop_id)
    .map((override) => ({
      from_stop_id: String(override.from_stop_id),
      to_stop_id: String(override.to_stop_id),
      traffic_density: Number(override.traffic_density || 0),
      accident_severity: Number(override.accident_severity || 0),
      road_closure: Boolean(override.road_closure),
      weather_severity: Number(override.weather_severity || 0),
      speed_reduction: Number(override.speed_reduction || 0),
      extra_delay_min: Number(override.extra_delay_min || 0),
      risk_level: override.risk_level || 'low',
      priority: Number(override.priority || 50),
      road_type: override.road_type || 'urban',
    }))
);

const hasScenarioForRoute = (scenarioResult, route) => (
  scenarioResult?.vehicleId === route.vehicle_id &&
  getScenarioCoordinates(scenarioResult).length >= 2
);

const getBackendRouteId = (state, vehicleId) => (
  state.routes.find((route) => route.vehicle_id === vehicleId)?.routeId ?? vehicleId
);

const getSimulationCoordinates = (route, scenarioResult) => (
  hasScenarioForRoute(scenarioResult, route)
    ? getScenarioCoordinates(scenarioResult)
    : route.geometry.geometry.coordinates
);

const getSimulationStops = (route, scenarioResult) => {
  if (hasScenarioForRoute(scenarioResult, route) && Array.isArray(scenarioResult.scenario_route)) {
    return [...scenarioResult.scenario_route]
      .sort((a, b) => Number(a.optimized_position || 0) - Number(b.optimized_position || 0))
      .filter((stop) => stop.latitude && stop.longitude)
      .map((stop) => ({
        stop_id: stop.stop_id,
        lat: stop.latitude,
        lon: stop.longitude,
        dwell_seconds: 30,
      }));
  }

  return (route.stops || [])
    .filter((stop) => stop.latitude && stop.longitude)
    .map((stop) => ({
      stop_id: stop.stop_id,
      lat: stop.latitude,
      lon: stop.longitude,
      dwell_seconds: 30,
    }));
};

const getOrderedScenarioStops = (scenarioResult) => (
  [...(scenarioResult?.scenario_route || [])]
    .sort((a, b) => Number(a.optimized_position || 0) - Number(b.optimized_position || 0))
    .filter((stop) => stop.stop_id !== null && stop.stop_id !== undefined)
);

const getSegmentKey = (stop, nextStop, index) => (
  `${stop.stop_id || index}-${nextStop.stop_id || index + 1}`
);

const filterSegmentOverridesForStops = (segmentOverrides = {}, remainingStops = []) => {
  const validKeys = new Set(
    remainingStops.slice(0, -1).map((stop, index) => (
      getSegmentKey(stop, remainingStops[index + 1], index)
    )),
  );

  return Object.fromEntries(
    Object.entries(segmentOverrides).filter(([key]) => validKeys.has(key)),
  );
};

const validateScenarioForApply = (route, scenarioResult, completedStopIds = new Set()) => {
  if (!route) {
    return { valid: false, reason: 'Route is not available.' };
  }

  const activeStopIds = (route.stops || [])
    .map((stop) => String(stop.stop_id))
    .filter(Boolean);
  const completedIds = [...completedStopIds].map(String);
  const remainingIds = activeStopIds.filter((stopId) => !completedIds.includes(stopId));
  const recommendedStopIds = getOrderedScenarioStops(scenarioResult)
    .map((stop) => {
      const match = findMatchingRouteStop(route.stops || [], stop);
      return String(match?.stop_id ?? stop.stop_id);
    })
    .filter(Boolean);

  const duplicateRecommended = recommendedStopIds.filter(
    (stopId, index) => recommendedStopIds.indexOf(stopId) !== index,
  );
  const completedInRecommendation = recommendedStopIds.filter((stopId) => completedIds.includes(stopId));
  const missingStops = remainingIds.filter((stopId) => !recommendedStopIds.includes(stopId));
  const unexpectedStops = recommendedStopIds.filter((stopId) => !remainingIds.includes(stopId));

  const valid = (
    duplicateRecommended.length === 0 &&
    completedInRecommendation.length === 0 &&
    missingStops.length === 0 &&
    unexpectedStops.length === 0 &&
    recommendedStopIds.length === remainingIds.length
  );

  return {
    valid,
    reason: valid ? '' : 'Recommendation does not match the remaining delivery stops.',
    activeStopIds,
    completedIds,
    remainingIds,
    recommendedStopIds,
    duplicateRecommended,
    completedInRecommendation,
    missingStops,
    unexpectedStops,
  };
};

const _SIM_DEBUG = false; // Set true to enable WS diagnostics

export const useRouteStore = create((set, get) => ({
  hasFetched: false,
  loading: false,
  error: null,

  routes: [],
  couriers: [],
  packages: [],
  routeSummary: null,
  explanation: null,
  selectedCourierId: null,
  pendingSuggestions: {},
  scenarioControls: DEFAULT_SCENARIO_CONTROLS,
  scenarioControlsByVehicleId: {},
  scenarioResultsByVehicleId: {},
  segmentOverridesByVehicleId: {},
  routeLifecycleByVehicleId: {},
  scenarioResult: null,
  scenarioLoading: false,
  scenarioError: null,
  modelInfo: null,

  // Simulation & feedback state
  liveCouriers: {},
  wsConnected: false,
  _wsRef: null,
  isConnecting: false,
  simulationRunning: false,
  simulationVehicleId: null,
  simulationRouteMode: 'optimized',
  feedbackMessage: null,
  feedbackType: 'info',

  // Completion tracking — single source of truth keyed by vehicleId
  completedStopIdsByVehicle: {},
  completedStopVersion: 0, // Increment on every completion for reliable Zustand reactivity

  getCompletedStopIds: (vehicleId) => {
    return get().completedStopIdsByVehicle[vehicleId] || new Set();
  },

  setFeedback: (message, type = 'success') => set({ feedbackMessage: message, feedbackType: type }),
  clearFeedback: () => set({ feedbackMessage: null }),

  setSelectedCourier: (id) => set((state) => {
    const nextId = id;
    return {
      selectedCourierId: nextId,
      scenarioControls: getControlsForRoute(state, nextId),
      scenarioResult: nextId === null ? null : state.scenarioResultsByVehicleId[nextId] || null,
    };
  }),

  setScenarioControl: (key, value, vehicleId = null) => set((state) => {
    const targetVehicleId = vehicleId ?? state.selectedCourierId;
    const currentControls = getControlsForRoute(state, targetVehicleId);
    const nextControls = {
      ...currentControls,
      [key]: value,
    };
    const nextResults = { ...state.scenarioResultsByVehicleId };

    if (targetVehicleId !== null && targetVehicleId !== undefined) {
      delete nextResults[targetVehicleId];
    }

    return {
      scenarioControls: targetVehicleId === state.selectedCourierId || targetVehicleId === null
        ? nextControls
        : state.scenarioControls,
      scenarioControlsByVehicleId: targetVehicleId !== null && targetVehicleId !== undefined
        ? {
          ...state.scenarioControlsByVehicleId,
          [targetVehicleId]: nextControls,
        }
        : state.scenarioControlsByVehicleId,
      scenarioResultsByVehicleId: nextResults,
      scenarioResult: targetVehicleId === state.selectedCourierId ? null : state.scenarioResult,
      scenarioError: null,
    };
  }),

  setSegmentOverride: (vehicleId, segmentKey, patch) => set((state) => {
    const currentByRoute = state.segmentOverridesByVehicleId[vehicleId] || {};
    const currentOverride = currentByRoute[segmentKey] || DEFAULT_SEGMENT_OVERRIDE;
    const nextResults = { ...state.scenarioResultsByVehicleId };
    delete nextResults[vehicleId];

    return {
      segmentOverridesByVehicleId: {
        ...state.segmentOverridesByVehicleId,
        [vehicleId]: {
          ...currentByRoute,
          [segmentKey]: {
            ...currentOverride,
            ...patch,
          },
        },
      },
      scenarioResultsByVehicleId: nextResults,
      scenarioResult: vehicleId === state.selectedCourierId ? null : state.scenarioResult,
      scenarioError: null,
    };
  }),

  resetScenario: (vehicleId = null) => set((state) => {
    const targetVehicleId = vehicleId ?? state.selectedCourierId;
    const nextResults = { ...state.scenarioResultsByVehicleId };
    const nextOverrides = { ...state.segmentOverridesByVehicleId };
    const nextControlsByVehicle = { ...state.scenarioControlsByVehicleId };

    if (targetVehicleId !== null && targetVehicleId !== undefined) {
      delete nextResults[targetVehicleId];
      delete nextOverrides[targetVehicleId];
      nextControlsByVehicle[targetVehicleId] = DEFAULT_SCENARIO_CONTROLS;
    }

    return {
      scenarioControls: DEFAULT_SCENARIO_CONTROLS,
      scenarioControlsByVehicleId: nextControlsByVehicle,
      scenarioResultsByVehicleId: nextResults,
      segmentOverridesByVehicleId: nextOverrides,
      scenarioResult: targetVehicleId === state.selectedCourierId ? null : state.scenarioResult,
      scenarioError: null,
    };
  }),

  runScenario: async (vehicleId = null) => {
    const state = get();
    const targetVehicleId = vehicleId ?? state.selectedCourierId;
    const selectedRoute = state.routes.find((route) => route.vehicle_id === targetVehicleId) || state.routes[0];
    if (!selectedRoute || !selectedRoute.stops?.length) {
      set({ scenarioError: 'No route is selected for scenario analysis.' });
      return;
    }

    set({ scenarioLoading: true, scenarioError: null });

    // Get completed stop IDs from store (single source of truth)
    const completedStopIds = state.completedStopIdsByVehicle[selectedRoute.vehicle_id] || new Set();

    // Only send remaining (non-completed) stops for reoptimization
    const remainingStops = selectedRoute.stops
      .filter((s) => !completedStopIds.has(String(s.stop_id)));

    if (remainingStops.length === 0) {
      set({ scenarioError: 'All stops are completed. No remaining stops to optimize.', scenarioLoading: false });
      return;
    }

    try {
      const controls = getControlsForRoute(state, selectedRoute.vehicle_id);
      const activeSegmentOverrides = filterSegmentOverridesForStops(
        state.segmentOverridesByVehicleId[selectedRoute.vehicle_id] || {},
        remainingStops,
      );
      const segmentOverrides = normalizeSegmentOverrides(
        activeSegmentOverrides,
      );
      const liveCourier = state.liveCouriers[`courier-${selectedRoute.vehicle_id}`];
      const liveLocation = Array.isArray(liveCourier?.location) ? liveCourier.location : null;
      const routeStart = liveLocation && Number.isFinite(Number(liveLocation[0])) && Number.isFinite(Number(liveLocation[1]))
        ? {
          depot_latitude: Number(liveLocation[1]),
          depot_longitude: Number(liveLocation[0]),
        }
        : DEFAULT_DEPOT;
      const result = await fetchScenarioReoptimization({
        ...routeStart,
        stops: remainingStops.map(serializeStopForScenario),
        controls,
        segment_overrides: segmentOverrides,
        time_limit_seconds: 15,
      });
      const normalizedResult = normalizeScenarioResult(result, selectedRoute.vehicle_id, selectedRoute);

      set({
        scenarioResult: selectedRoute.vehicle_id === get().selectedCourierId
          ? normalizedResult
          : get().scenarioResult,
        scenarioResultsByVehicleId: {
          ...get().scenarioResultsByVehicleId,
          [selectedRoute.vehicle_id]: normalizedResult,
        },
        scenarioLoading: false,
        simulationRouteMode: state.simulationRouteMode,
      });
    } catch (error) {
      set({
        scenarioError: error.message,
        scenarioLoading: false,
      });
    }
  },

  runAllScenarios: async () => {
    const routes = get().routes;
    for (const route of routes) {
      await get().runScenario(route.vehicle_id);
    }
  },



  // ── Lifecycle Actions (MVP) ────────────────────────────────────────────────

  agentExplanation: null,
  agentExplanationLoading: false,
  agentExplanationError: null,

  requestAgentExplanation: async (payload) => {
    set({ agentExplanation: null, agentExplanationLoading: true, agentExplanationError: null });

    // No frontend timeout — the backend has its own 120s timeout.
    // Deterministic explanation is shown immediately via scenarioResult.
    // AI explanation will appear when the Ollama LLM finishes generating.
    try {
      const result = await fetchAgentExplanation(payload);
      set({ agentExplanation: result, agentExplanationLoading: false });
      return result;
    } catch (err) {
      if (_SIM_DEBUG) console.warn('[EXPLAIN] AI explanation request failed:', err.message);
      set({
        agentExplanationLoading: false,
        agentExplanationError: null,
      });
    }
  },

  loadLifecycleState: async (vehicleId) => {
    try {
      const apiRouteId = getBackendRouteId(get(), vehicleId);
      const stateData = await fetchRouteLifecycleState(apiRouteId);
      set((state) => ({
        routeLifecycleByVehicleId: {
          ...state.routeLifecycleByVehicleId,
          [vehicleId]: stateData,
        },
      }));
    } catch (err) {
      console.error('Failed to load lifecycle state:', err);
    }
  },

  startDispatch: async (vehicleId) => {
    try {
      const apiRouteId = getBackendRouteId(get(), vehicleId);
      const stateData = await startDispatchRoute(apiRouteId);
      set((state) => ({
        routeLifecycleByVehicleId: {
          ...state.routeLifecycleByVehicleId,
          [vehicleId]: stateData,
        },
        feedbackMessage: 'Courier dispatched. Live tracking started.',
        feedbackType: 'success',
      }));
      // Start courier simulation after dispatch
      get().startSimulation(vehicleId);
    } catch (err) {
      console.error('Failed to start dispatch:', err);
      set({ feedbackMessage: 'Failed to start dispatch: ' + err.message, feedbackType: 'error' });
    }
  },

  recalculateLiveRecommendation: async (vehicleId) => {
    try {
      const apiRouteId = getBackendRouteId(get(), vehicleId);
      const stateData = await recalculateRecommendation(apiRouteId);
      set((state) => ({
        routeLifecycleByVehicleId: {
          ...state.routeLifecycleByVehicleId,
          [vehicleId]: stateData,
        },
      }));
    } catch (err) {
      console.error('Failed to recalculate recommendation:', err);
    }
  },

  applyLiveRecommendation: async (vehicleId) => {
    try {
      const state = get();
      const route = state.routes.find((r) => r.vehicle_id === vehicleId);
      const scenarioResult = state.scenarioResultsByVehicleId[vehicleId]
        || (state.scenarioResult?.vehicleId === vehicleId ? state.scenarioResult : null);
      const completedIds = state.completedStopIdsByVehicle[vehicleId] || new Set();
      const validation = validateScenarioForApply(route, scenarioResult, completedIds);

      if (!validation.valid) {
        set({
          feedbackMessage: [
            validation.reason,
            validation.missingStops?.length ? `Missing: ${validation.missingStops.join(', ')}` : '',
            validation.unexpectedStops?.length ? `Unexpected: ${validation.unexpectedStops.join(', ')}` : '',
            validation.completedInRecommendation?.length
              ? `Already completed: ${validation.completedInRecommendation.join(', ')}`
              : '',
          ].filter(Boolean).join(' '),
          feedbackType: 'error',
        });
        return;
      }

      const apiRouteId = getBackendRouteId(state, vehicleId);
      const stateData = await applyRecommendation(apiRouteId, {
        active_stop_ids: validation.activeStopIds,
        completed_stop_ids: validation.completedIds,
        recommended_stop_ids: validation.recommendedStopIds,
        scenario_geometry: scenarioResult?.scenarioGeometry?.geometry || null,
        scenario_summary: {
          ...(scenarioResult?.scenario_summary || scenarioResult?.scenarioSummary || {}),
          recommendation_allowed: scenarioResult?.recommendation_allowed !== false,
          recommendation_status: scenarioResult?.recommendation_status || 'recommended',
          optimization_delta: scenarioResult?.optimization_delta || null,
        },
      });

      // Build updated routes — MERGE completed stops with scenario route
      // so route.stops always contains ALL stops (completed + remaining).
      let updatedRoutes = state.routes;
      if (scenarioResult && route) {
        updatedRoutes = state.routes.map((r) => {
          if (r.vehicle_id !== vehicleId) return r;
          const newGeometry = scenarioResult.scenarioGeometry || r.geometry;
          const scenarioStops = getOrderedScenarioStops(scenarioResult);
          const scenarioStopIds = new Set(scenarioStops.map((s) => String(s.stop_id)));

          // Collect completed stops from current route that are NOT in scenario result
          const completedStopsToPreserve = r.stops
            .filter((s) => completedIds.has(String(s.stop_id)) && !scenarioStopIds.has(String(s.stop_id)))
            .map((s) => ({ ...s, status: 'completed' }));

          // Merge: completed stops first, then scenario stops with status correction
          const mergedStops = [
            ...completedStopsToPreserve,
            ...scenarioStops.map((s, index) => ({
              ...s,
              status: 'pending',
              displaySequence: completedStopsToPreserve.length + index + 1,
            })),
          ];

          return {
            ...r,
            originalGeometry: r.originalGeometry || r.geometry,
            geometry: newGeometry ? { ...newGeometry, _ts: Date.now() } : r.geometry,
            stops: mergedStops,
          };
        });
      }

      // Clear recommendation from results (it's now the active route)
      const nextResults = { ...state.scenarioResultsByVehicleId };
      delete nextResults[vehicleId];

      set({
        routes: updatedRoutes,
        routeLifecycleByVehicleId: {
          ...state.routeLifecycleByVehicleId,
          [vehicleId]: stateData,
        },
        scenarioResultsByVehicleId: nextResults,
        scenarioResult: vehicleId === state.selectedCourierId ? null : state.scenarioResult,
        feedbackMessage: scenarioResult?.scenarioGeometry
          ? 'Recommendation applied. Active route updated.'
          : 'Route conditions updated. Stop order/conditions were applied.',
        feedbackType: 'success',
      });

      // Rebind simulation to the updated active route
      if (
        state.wsConnected
        && state._wsRef?.readyState === WebSocket.OPEN
        && state.simulationVehicleId === vehicleId
      ) {
        const updatedRoute = get().routes.find((r) => r.vehicle_id === vehicleId);
        if (updatedRoute) {
          const coords = updatedRoute.geometry?.geometry?.coordinates || [];
          if (coords.length >= 2) {
            state._wsRef.send(JSON.stringify({
              type: 'reroute',
              courier_id: `courier-${vehicleId}`,
              coordinates: coords,
              stops: getSimulationStops(updatedRoute, null)
                .filter((s) => !completedIds.has(String(s.stop_id))),
              route_source: 'applied',
            }));
          }
        }
      } else {
        get().stopSimulation();
        setTimeout(() => get().startSimulation(vehicleId), 300);
      }
    } catch (err) {
      console.error('Failed to apply recommendation:', err);
      set({ feedbackMessage: 'Failed to apply recommendation: ' + err.message, feedbackType: 'error' });
    }
  },

  resetRouteLifecycle: async (vehicleId) => {
    try {
      const apiRouteId = getBackendRouteId(get(), vehicleId);
      const stateData = await resetLifecycle(apiRouteId);
      set((state) => ({
        routeLifecycleByVehicleId: {
          ...state.routeLifecycleByVehicleId,
          [vehicleId]: stateData,
        },
        completedStopIdsByVehicle: {
          ...state.completedStopIdsByVehicle,
          [vehicleId]: new Set(),
        },
        completedStopVersion: state.completedStopVersion + 1,
      }));
    } catch (err) {
      console.error('Failed to reset lifecycle:', err);
    }
  },

  fetchData: async () => {
    if (get().hasFetched) return;

    set({ loading: true, error: null });

    try {
      const [dbCouriers, stopPool, modelInfo] = await Promise.all([
        fetchCouriers(),
        fetchStopPool(),
        fetchModelInfo().catch(() => null),
      ]);
      const requestBody = buildAutoDispatchRequest(dbCouriers, stopPool);
      const data = await fetchAutoDispatch(requestBody);

      const courierRoutes = await Promise.all(
        requestBody.assignments.map((assignment) => fetchCourierRoutes(assignment.courier_id)),
      );

      const assignmentByVehicleId = new Map(
        requestBody.assignments.map((assignment, vehicleId) => [vehicleId, assignment]),
      );
      const courierById = new Map(dbCouriers.map((courier) => [courier.id, courier]));
      const stopPoolById = new Map(stopPool.map((stop) => [stop.id, stop]));
      const latestRouteByVehicleId = new Map(
        courierRoutes.map((routes, vehicleId) => [vehicleId, findLatestRoute(routes)]),
      );
      const originalRoutes = await Promise.all(
        requestBody.assignments.map(async (assignment) => {
          const originalStops = buildOriginalRouteStops(assignment, stopPoolById, requestBody);
          try {
            const route = await fetchFinalRoute(originalStops);
            return {
              ...route,
              geometry: { type: 'Feature', geometry: route.geometry },
              stops: originalStops,
            };
          } catch (error) {
            console.warn('Failed to fetch original route geometry:', error);
            return {
              distance: 0,
              duration: 0,
              geometry: buildFallbackGeometry(originalStops),
              stops: originalStops,
            };
          }
        }),
      );

      const parsedRoutes = (data.vehicle_routes || [])
        .map((vehicleRoute, index) => {
          const assignment = assignmentByVehicleId.get(vehicleRoute.vehicle_id);
          const courier = courierById.get(assignment?.courier_id);
          const vehicleStops = getVehicleStops(data.optimized_route, vehicleRoute);
          const coords = vehicleRoute.geometry?.coordinates || [];
          const routeId = latestRouteByVehicleId.get(vehicleRoute.vehicle_id)?.id;
          const originalRoute = originalRoutes[vehicleRoute.vehicle_id];

          return {
            id: `route-${vehicleRoute.vehicle_id}`,
            routeId,
            vehicle_id: vehicleRoute.vehicle_id,
            courierDbId: assignment?.courier_id,
            courierName: courier?.name || `Courier ${vehicleRoute.vehicle_id + 1}`,
            color: ROUTE_COLORS[index % ROUTE_COLORS.length],
            geometry: vehicleRoute.geometry ? {
              type: 'Feature',
              geometry: vehicleRoute.geometry,
            } : null,
            naiveGeometry: coords.length > 1 ? {
              type: 'Feature',
              geometry: {
                type: 'LineString',
                coordinates: [coords[0], coords[coords.length - 1]],
              },
            } : null,
            originalGeometry: originalRoute?.geometry || null,
            currentPosition: null,
            vehicleType: courier?.vehicle_type || 'car',
            metrics: {
              distanceKm: round(vehicleRoute.distance_m / 1000, 1),
              durationMin: Math.round(vehicleRoute.duration_s / 60),
              expectedDelayMin: round(vehicleRoute.total_expected_delay_min, 1),
              worstCaseDelayMin: round(vehicleRoute.total_worst_case_delay_min, 1),
              severeStops: vehicleRoute.severe_stop_count,
              highRiskStops: vehicleRoute.high_risk_stop_count,
              stopCount: vehicleRoute.stop_count,
            },
            comparison: originalRoute ? buildComparison(originalRoute, vehicleRoute) : null,
            stops: vehicleStops,
          };
        })
        .filter((route) => route.geometry != null);

      const initialCompletedByVehicleId = parsedRoutes.reduce((acc, route) => {
        const completedIds = new Set(
          (route.stops || [])
            .filter((stop) => stop.status === 'completed')
            .map((stop) => String(stop.stop_id)),
        );
        if (completedIds.size > 0) {
          acc[route.vehicle_id] = completedIds;
        }
        return acc;
      }, {});

      const parsedCouriers = parsedRoutes.map((route) => {
        const status = getStatusFromMetrics({
          severe_stop_count: route.metrics.severeStops,
          high_risk_stop_count: route.metrics.highRiskStops,
          total_expected_delay_min: route.metrics.expectedDelayMin,
        });
        const completedStops = route.stops.filter((stop) => stop.status === 'completed').length;

        return {
          id: route.vehicle_id,
          routeId: route.routeId,
          courierDbId: route.courierDbId,
          name: route.courierName,
          initials: makeInitials(route.courierName, route.vehicle_id + 1),
          vehicleType: route.vehicleType,
          currentStatus: status,
          statusTone: getStatusTone(status),
          stopsRemaining: Math.max(route.stops.length - completedStops, 0),
          routeColor: route.color,
          stops: route.stops,
          stats: {
            completedStops,
            totalStops: route.stops.length,
            totalExpectedDelay: route.metrics.expectedDelayMin,
            severeStops: route.metrics.severeStops,
            highRiskStops: route.metrics.highRiskStops,
          },
          routeMetrics: route.metrics,
          comparison: route.comparison,
        };
      });

      const courierNameByVehicleId = new Map(parsedCouriers.map((courier) => [courier.id, courier.name]));
      const parsedPackages = (data.optimized_route || []).map((stop) => ({
        ...stop,
        courierName: courierNameByVehicleId.get(stop.vehicle_id) || `Courier ${stop.vehicle_id + 1}`,
      }));
      const scenarioControlsByVehicleId = parsedRoutes.reduce((acc, route) => {
        acc[route.vehicle_id] = {
          ...DEFAULT_SCENARIO_CONTROLS,
          ...(get().scenarioControlsByVehicleId?.[route.vehicle_id] || {}),
        };
        return acc;
      }, {});

      set({
        routes: parsedRoutes,
        couriers: parsedCouriers,
        packages: parsedPackages,
        routeSummary: data.route_summary || null,
        explanation: data.explanation || null,
        modelInfo,
        selectedCourierId: parsedCouriers[0]?.id ?? null,
        scenarioControls: getControlsForRoute(
          { scenarioControls: DEFAULT_SCENARIO_CONTROLS, scenarioControlsByVehicleId },
          parsedCouriers[0]?.id ?? null,
        ),
        scenarioControlsByVehicleId,
        scenarioResultsByVehicleId: {},
        segmentOverridesByVehicleId: {},
        scenarioResult: null,
        scenarioError: null,
        completedStopIdsByVehicle: initialCompletedByVehicleId,
        completedStopVersion: get().completedStopVersion + 1,
        loading: false,
        hasFetched: true,
      });
    } catch (error) {
      set({ error: error.message, loading: false });
    }
  },

  startSimulation: (vehicleId = null) => {
    const {
      routes,
      _wsRef,
      isConnecting,
      wsConnected,
      completedStopIdsByVehicle,
      selectedCourierId,
    } = get();

    if (routes.length === 0) return;
    if (isConnecting || wsConnected) return;

    const targetVehicleId = vehicleId ?? selectedCourierId ?? routes[0]?.vehicle_id;
    const routesToSimulate = routes.filter((route) => route.vehicle_id === targetVehicleId);
    if (routesToSimulate.length === 0) return;

    set({ isConnecting: true });

    if (_wsRef && _wsRef.readyState === WebSocket.OPEN) {
      _wsRef.close();
    }

    const ws = new WebSocket(WS_URL);

    ws.onopen = () => {
      const vehicles = routesToSimulate.map((route) => {
        const completedIds = completedStopIdsByVehicle[route.vehicle_id] || new Set();
        return {
          courier_id: `courier-${route.vehicle_id}`,
          name: route.courierName,
          vehicle_id: route.vehicle_id,
          coordinates: getSimulationCoordinates(route, null),
          color: route.color,
          route_source: 'active',
          stops: getSimulationStops(route, null)
            .filter((stop) => !completedIds.has(String(stop.stop_id))),
        };
      });

      const configMsg = {
        vehicles,
        speed_kmh: 60,
        loop: true,
      };

      ws.send(JSON.stringify(configMsg));
      set({
        wsConnected: true,
        isConnecting: false,
        simulationRunning: true,
        simulationVehicleId: targetVehicleId,
        simulationRouteMode: 'optimized',
      });
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.type === 'error') {
        console.error('Simulation error from server:', data.message);
      }

      if (_SIM_DEBUG && data.type !== 'position_update') {
        console.log('[WS]', data.type, data);
      }

      if (data.type === 'position_update') {
        set((state) => {
          let hasChanged = false;
          const patch = {};

          data.couriers.forEach((courier) => {
            const existing = state.liveCouriers[courier.courier_id];
            const newLocation = [courier.longitude, courier.latitude];

            if (
              !existing ||
              existing.location?.[0] !== newLocation[0] ||
              existing.location?.[1] !== newLocation[1] ||
              existing.speed_kmh !== courier.speed_kmh
            ) {
              hasChanged = true;
              patch[courier.courier_id] = {
                ...existing,
                ...courier,
                location: newLocation,
              };
            }
          });

          if (!hasChanged) return state;
          return { liveCouriers: { ...state.liveCouriers, ...patch } };
        });
      }

      if (data.type === 'at_stop') {
        const vehicleId = parseInt(data.courier_id.replace('courier-', ''), 10);
        const completedStopId = String(data.stop_id);

        if (_SIM_DEBUG) {
          console.log(
            `[WS AT_STOP] vehicle=${vehicleId} stop=${completedStopId}`,
            `completed=${data.completed_count}/${data.total_count}`,
          );
        }

        // IMMEDIATELY mark stop as completed in store (single source of truth)
        set((state) => {
          // Update completedStopIdsByVehicle
          const prevCompleted = state.completedStopIdsByVehicle[vehicleId] || new Set();
          if (prevCompleted.has(completedStopId)) return state; // Already completed
          const nextCompleted = new Set(prevCompleted);
          nextCompleted.add(completedStopId);

          // Update routes[].stops — the data that MapViewer renders
          const updatedRoutes = state.routes.map((r) => {
            if (r.vehicle_id !== vehicleId) return r;
            return {
              ...r,
              stops: r.stops.map((s) =>
                String(s.stop_id) === completedStopId
                  ? { ...s, status: 'completed' }
                  : s
              ),
            };
          });

          // Update couriers[].stops
          const updatedCouriers = state.couriers.map((c) => {
            if (c.id !== vehicleId) return c;
            const updatedStops = (c.stops || []).map((s) =>
              String(s.stop_id) === completedStopId
                ? { ...s, status: 'completed' }
                : s
            );
            return { ...c, stops: updatedStops };
          });

          const nextVersion = state.completedStopVersion + 1;

          if (_SIM_DEBUG) {
            console.log(
              `[STORE] completedStopIds[${vehicleId}] size=${nextCompleted.size}`,
              `version=${nextVersion}`,
              `ids=[${[...nextCompleted].join(', ')}]`,
            );
          }

          // Clear stale scenario results — old recommendation is no longer valid
          // because the remaining stops have changed.
          const nextScenarioResults = { ...state.scenarioResultsByVehicleId };
          if (nextScenarioResults[vehicleId]) {
            delete nextScenarioResults[vehicleId];
            if (_SIM_DEBUG) console.log(`[STORE] Cleared stale scenario for vehicle ${vehicleId}`);
          }

          return {
            completedStopIdsByVehicle: {
              ...state.completedStopIdsByVehicle,
              [vehicleId]: nextCompleted,
            },
            completedStopVersion: nextVersion,
            routes: updatedRoutes,
            couriers: updatedCouriers,
            scenarioResultsByVehicleId: nextScenarioResults,
            scenarioResult: state.selectedCourierId === vehicleId ? null : state.scenarioResult,
          };
        });

        // Fire-and-forget REST call for backend persistence
        completeStopRequest(data.stop_id).catch((error) => {
          console.warn('Backend stop completion call failed (UI already updated):', error.message);
        });
      }
    };

    ws.onclose = () => {
      set({ wsConnected: false, isConnecting: false, simulationRunning: false, simulationVehicleId: null, _wsRef: null, liveCouriers: {}, simulationRouteMode: 'optimized' });
    };

    ws.onerror = (err) => {
      console.error('WS Error:', err);
      set({ isConnecting: false });
    };

    set({ _wsRef: ws });
  },

  stopSimulation: () => {
    const { _wsRef } = get();
    if (_wsRef && (_wsRef.readyState === WebSocket.OPEN || _wsRef.readyState === WebSocket.CONNECTING)) {
      _wsRef.close();
    }
    set({
      wsConnected: false,
      isConnecting: false,
      simulationRunning: false,
      simulationVehicleId: null,
      _wsRef: null,
      liveCouriers: {},
    });
  },

  handleSuggestionDecision: async (vehicleId, suggestionId, action) => {
    const routeId = get().routes.find((route) => route.vehicle_id === vehicleId)?.routeId;
    if (!routeId) {
      throw new Error('Route ID not found for suggestion decision.');
    }

    try {
      await postSuggestionDecision(routeId, suggestionId, action);

      set((state) => {
        const suggestion = state.pendingSuggestions[vehicleId];
        const newPendingSuggestions = { ...state.pendingSuggestions };
        delete newPendingSuggestions[vehicleId];

        if (action === 'reject' || !suggestion) {
          return { pendingSuggestions: newPendingSuggestions };
        }

        const updatedRoutes = state.routes.map((route) => {
          if (route.vehicle_id !== vehicleId) return route;

          let nextStops = route.stops;
          if (suggestion.new_sequence?.length) {
            const completedStops = route.stops.filter((stop) => stop.status === 'completed');
            const remainingStops = route.stops.filter((stop) => stop.status !== 'completed');
            const seqMap = new Map(
              suggestion.new_sequence.map((stopId, idx) => [String(stopId), idx]),
            );
            nextStops = [
              ...completedStops,
              ...remainingStops.sort((a, b) => {
                const first = seqMap.has(String(a.stop_id)) ? seqMap.get(String(a.stop_id)) : Infinity;
                const second = seqMap.has(String(b.stop_id)) ? seqMap.get(String(b.stop_id)) : Infinity;
                return first - second;
              }),
            ];
          }

          return {
            ...route,
            geometry: { type: 'Feature', geometry: suggestion.geometry },
            naiveGeometry: suggestion.previous_geometry
              ? { type: 'Feature', geometry: suggestion.previous_geometry }
              : route.naiveGeometry,
            stops: nextStops,
          };
        });

        const updatedCouriers = state.couriers.map((courier) => {
          if (courier.id !== vehicleId || !suggestion.new_sequence?.length) return courier;

          const completedStops = courier.stops.filter((stop) => stop.status === 'completed');
          const remainingStops = courier.stops.filter((stop) => stop.status !== 'completed');
          const seqMap = new Map(
            suggestion.new_sequence.map((stopId, idx) => [String(stopId), idx]),
          );
          const remainingReordered = remainingStops.sort((a, b) => {
            const first = seqMap.has(String(a.stop_id)) ? seqMap.get(String(a.stop_id)) : Infinity;
            const second = seqMap.has(String(b.stop_id)) ? seqMap.get(String(b.stop_id)) : Infinity;
            return first - second;
          });

          return {
            ...courier,
            stops: [...completedStops, ...remainingReordered],
          };
        });

        return {
          routes: updatedRoutes,
          couriers: updatedCouriers,
          pendingSuggestions: newPendingSuggestions,
        };
      });
    } catch (error) {
      console.error(`Failed to ${action} suggestion:`, error);
      throw error;
    }
  },

  forceFetchData: async () => {
    set({ hasFetched: false });
    await get().fetchData();
  },
}));
