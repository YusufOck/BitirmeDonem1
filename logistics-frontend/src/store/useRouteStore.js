import { create } from 'zustand';
import {
  buildAutoDispatchRequest,
  completeStopRequest,
  fetchAutoDispatch,
  fetchCourierRoutes,
  fetchCouriers,
  fetchFinalRoute,
  fetchStopPool,
  postSuggestionDecision,
} from '../services/routeService';

const ROUTE_COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#aa3bff'];
const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws/simulation';

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

  liveCouriers: {},
  wsConnected: false,
  _wsRef: null,
  isConnecting: false,

  setSelectedCourier: (id) => set((state) => ({
    selectedCourierId: state.selectedCourierId === id ? null : id,
  })),

  fetchData: async () => {
    if (get().hasFetched) return;

    set({ loading: true, error: null });

    try {
      const [dbCouriers, stopPool] = await Promise.all([
        fetchCouriers(),
        fetchStopPool(),
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

      set({
        routes: parsedRoutes,
        couriers: parsedCouriers,
        packages: parsedPackages,
        routeSummary: data.route_summary || null,
        explanation: data.explanation || null,
        selectedCourierId: parsedCouriers[0]?.id ?? null,
        loading: false,
        hasFetched: true,
      });
    } catch (error) {
      set({ error: error.message, loading: false });
    }
  },

  startSimulation: () => {
    const { routes, _wsRef, isConnecting, wsConnected } = get();

    if (routes.length === 0) return;
    if (isConnecting || wsConnected) return;

    set({ isConnecting: true });

    if (_wsRef && _wsRef.readyState === WebSocket.OPEN) {
      _wsRef.close();
    }

    const ws = new WebSocket(WS_URL);

    ws.onopen = () => {
      set({ wsConnected: true, isConnecting: false });

      const configMsg = {
        vehicles: routes.map((route) => ({
          courier_id: `courier-${route.vehicle_id}`,
          name: route.courierName,
          vehicle_id: route.vehicle_id,
          coordinates: route.geometry.geometry.coordinates,
          color: route.color,
          stops: (route.stops || [])
            .filter((stop) => stop.latitude && stop.longitude)
            .map((stop) => ({
              stop_id: stop.stop_id,
              lat: stop.latitude,
              lon: stop.longitude,
              dwell_seconds: 30,
            })),
        })),
        speed_kmh: 60,
        loop: true,
      };

      ws.send(JSON.stringify(configMsg));
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
      set({ wsConnected: false, isConnecting: false, _wsRef: null, liveCouriers: {} });
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
