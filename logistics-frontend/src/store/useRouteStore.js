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

const normalizeScenarioResult = (result, vehicleId) => ({
  ...result,
  vehicleId,
  baselineGeometry: asFeature(result.baseline_geometry),
  scenarioGeometry: asFeature(result.scenario_geometry),
  mapboxAlternatives: (result.mapbox_alternatives || []).map((alternative) => ({
    ...alternative,
    geometry: asFeature(alternative.geometry),
  })),
});

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
  simulationRouteMode: 'optimized',
  feedbackMessage: null,
  feedbackType: 'info',

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

    // Get completed stop IDs from courier data
    const courier = state.couriers.find((c) => c.id === selectedRoute.vehicle_id);
    const completedStopIds = new Set(
      (courier?.stops || [])
        .filter((s) => s.status === 'completed')
        .map((s) => String(s.stop_id))
    );

    // Only send remaining (non-completed) stops for reoptimization
    const remainingStops = selectedRoute.stops
      .filter((s) => !completedStopIds.has(String(s.stop_id)));

    if (remainingStops.length === 0) {
      set({ scenarioError: 'All stops are completed. No remaining stops to optimize.', scenarioLoading: false });
      return;
    }

    try {
      const controls = getControlsForRoute(state, selectedRoute.vehicle_id);
      const segmentOverrides = normalizeSegmentOverrides(
        state.segmentOverridesByVehicleId[selectedRoute.vehicle_id] || {},
      );
      const result = await fetchScenarioReoptimization({
        ...DEFAULT_DEPOT,
        stops: remainingStops.map(serializeStopForScenario),
        controls,
        segment_overrides: segmentOverrides,
        time_limit_seconds: 15,
      });
      const normalizedResult = normalizeScenarioResult(result, selectedRoute.vehicle_id);

      if (state.wsConnected && state._wsRef?.readyState === WebSocket.OPEN) {
        const coordinates = getScenarioCoordinates(normalizedResult);
        if (coordinates.length >= 2) {
          state._wsRef.send(JSON.stringify({
            type: 'reroute',
            courier_id: `courier-${selectedRoute.vehicle_id}`,
            coordinates,
            stops: getSimulationStops(selectedRoute, normalizedResult),
            route_source: 'scenario',
          }));
        }
      }

      set({
        scenarioResult: selectedRoute.vehicle_id === get().selectedCourierId
          ? normalizedResult
          : get().scenarioResult,
        scenarioResultsByVehicleId: {
          ...get().scenarioResultsByVehicleId,
          [selectedRoute.vehicle_id]: normalizedResult,
        },
        scenarioLoading: false,
        simulationRouteMode: state.wsConnected ? 'scenario' : state.simulationRouteMode,
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
    set({ agentExplanationLoading: true, agentExplanationError: null });
    try {
      const result = await fetchAgentExplanation(payload);
      set({ agentExplanation: result, agentExplanationLoading: false });
      return result;
    } catch (err) {
      set({ agentExplanationError: err.message, agentExplanationLoading: false });
      throw err;
    }
  },

  loadLifecycleState: async (routeId) => {
    try {
      const stateData = await fetchRouteLifecycleState(routeId);
      set((state) => ({
        routeLifecycleByVehicleId: {
          ...state.routeLifecycleByVehicleId,
          [routeId]: stateData,
        },
      }));
    } catch (err) {
      console.error('Failed to load lifecycle state:', err);
    }
  },

  startDispatch: async (routeId) => {
    try {
      const stateData = await startDispatchRoute(routeId);
      set((state) => ({
        routeLifecycleByVehicleId: {
          ...state.routeLifecycleByVehicleId,
          [routeId]: stateData,
        },
        feedbackMessage: 'Courier dispatched. Live tracking started.',
        feedbackType: 'success',
      }));
      // Start courier simulation after dispatch
      get().startSimulation();
    } catch (err) {
      console.error('Failed to start dispatch:', err);
      set({ feedbackMessage: 'Failed to start dispatch: ' + err.message, feedbackType: 'error' });
    }
  },

  recalculateLiveRecommendation: async (routeId) => {
    try {
      const stateData = await recalculateRecommendation(routeId);
      set((state) => ({
        routeLifecycleByVehicleId: {
          ...state.routeLifecycleByVehicleId,
          [routeId]: stateData,
        },
      }));
    } catch (err) {
      console.error('Failed to recalculate recommendation:', err);
    }
  },

  applyLiveRecommendation: async (routeId) => {
    try {
      const stateData = await applyRecommendation(routeId);
      const state = get();
      const scenarioResult = state.scenarioResultsByVehicleId[routeId] || state.scenarioResult;
      const route = state.routes.find((r) => r.vehicle_id === routeId);

      // Update lifecycle and mark recommendation applied
      const updates = {
        routeLifecycleByVehicleId: {
          ...state.routeLifecycleByVehicleId,
          [routeId]: stateData,
        },
        feedbackMessage: 'Recommendation applied. Active route updated.',
        feedbackType: 'success',
      };

      // If scenario has new geometry, update the route to use it as active
      if (scenarioResult?.scenarioGeometry && route) {
        const updatedRoutes = state.routes.map((r) => {
          if (r.vehicle_id !== routeId) return r;
          return {
            ...r,
            originalGeometry: r.geometry,
            geometry: scenarioResult.scenarioGeometry,
            stops: scenarioResult.scenario_route || r.stops,
          };
        });
        updates.routes = updatedRoutes;
      }

      // Clear recommendation from results (it's now the active route)
      const nextResults = { ...state.scenarioResultsByVehicleId };
      delete nextResults[routeId];
      updates.scenarioResultsByVehicleId = nextResults;
      updates.scenarioResult = routeId === state.selectedCourierId ? null : state.scenarioResult;

      set(updates);

      // Rebind simulation to the updated active route
      if (state.wsConnected && state._wsRef?.readyState === WebSocket.OPEN && route) {
        const updatedRoute = get().routes.find((r) => r.vehicle_id === routeId);
        if (updatedRoute) {
          const coords = updatedRoute.geometry?.geometry?.coordinates || [];
          if (coords.length >= 2) {
            state._wsRef.send(JSON.stringify({
              type: 'reroute',
              courier_id: `courier-${routeId}`,
              coordinates: coords,
              stops: getSimulationStops(updatedRoute, null),
              route_source: 'applied',
            }));
          }
        }
      } else {
        // Restart simulation with updated routes
        get().stopSimulation();
        setTimeout(() => get().startSimulation(), 300);
      }
    } catch (err) {
      console.error('Failed to apply recommendation:', err);
      set({ feedbackMessage: 'Failed to apply recommendation: ' + err.message, feedbackType: 'error' });
    }
  },

  resetRouteLifecycle: async (routeId) => {
    try {
      const stateData = await resetLifecycle(routeId);
      set((state) => ({
        routeLifecycleByVehicleId: {
          ...state.routeLifecycleByVehicleId,
          [routeId]: stateData,
        },
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
        loading: false,
        hasFetched: true,
      });
    } catch (error) {
      set({ error: error.message, loading: false });
    }
  },

  startSimulation: () => {
    const {
      routes,
      _wsRef,
      isConnecting,
      wsConnected,
      scenarioResult,
      scenarioResultsByVehicleId,
    } = get();

    if (routes.length === 0) return;
    if (isConnecting || wsConnected) return;

    set({ isConnecting: true });

    if (_wsRef && _wsRef.readyState === WebSocket.OPEN) {
      _wsRef.close();
    }

    const ws = new WebSocket(WS_URL);

    ws.onopen = () => {
      const vehicles = routes.map((route) => {
        const routeScenario = scenarioResultsByVehicleId[route.vehicle_id] || scenarioResult;
        const usesScenario = hasScenarioForRoute(routeScenario, route);
        return {
          courier_id: `courier-${route.vehicle_id}`,
          name: route.courierName,
          vehicle_id: route.vehicle_id,
          coordinates: getSimulationCoordinates(route, routeScenario),
          color: usesScenario ? '#22d3ee' : route.color,
          route_source: usesScenario ? 'scenario' : 'optimized',
          stops: getSimulationStops(route, routeScenario),
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
        simulationRouteMode: vehicles.some((vehicle) => vehicle.route_source === 'scenario')
          ? 'scenario'
          : 'optimized',
      });
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.type === 'error') {
        console.error('Simulation error from server:', data.message);
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

        completeStopRequest(data.stop_id)
          .then((response) => {
            const { stop, reopt } = response;

            set((state) => {
              const updatedCouriers = state.couriers.map((courier) => {
                if (courier.id !== vehicleId) return courier;

                const completedStops = courier.stops.map((currentStop) => (
                  String(currentStop.stop_id) === String(stop.id)
                    ? { ...currentStop, status: 'completed' }
                    : currentStop
                ));
                const remainingCount = completedStops.filter((currentStop) => currentStop.status !== 'completed').length;

                return {
                  ...courier,
                  stops: completedStops,
                  stopsRemaining: remainingCount,
                  stats: {
                    ...courier.stats,
                    completedStops: completedStops.length - remainingCount,
                  },
                };
              });

              const updatedPackages = state.packages.map((pkg) => (
                String(pkg.stop_id) === String(stop.id)
                  ? { ...pkg, status: 'completed' }
                  : pkg
              ));

              const newPendingSuggestions = { ...state.pendingSuggestions };
              if (reopt?.triggered && reopt.suggestion_id) {
                newPendingSuggestions[vehicleId] = { ...reopt, vehicleId };
              }

              return {
                couriers: updatedCouriers,
                packages: updatedPackages,
                pendingSuggestions: newPendingSuggestions,
              };
            });
          })
          .catch((error) => {
            console.error('Failed to complete stop or re-optimize:', error);
          });
      }
    };

    ws.onclose = () => {
      set({ wsConnected: false, isConnecting: false, simulationRunning: false, _wsRef: null, liveCouriers: {}, simulationRouteMode: 'optimized' });
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

          return {
            ...route,
            geometry: { type: 'Feature', geometry: suggestion.geometry },
            naiveGeometry: suggestion.previous_geometry
              ? { type: 'Feature', geometry: suggestion.previous_geometry }
              : route.naiveGeometry,
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
