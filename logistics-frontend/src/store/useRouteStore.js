import { create } from 'zustand';
import { fetchAutoDispatch, completeStopRequest, requestBody, postSuggestionDecision } from '../services/routeService';

const ROUTE_COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#aa3bff'];
const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws/simulation';

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
  pendingSuggestions: {}, // Maps vehicleId -> suggestion data

  // Live State
  liveCouriers: {}, // Stored as a dictionary for fast O(1) updates
  wsConnected: false,
  _wsRef: null, // Keep a private reference to the WebSocket instance
  isConnecting: false,

  setSelectedCourier: (id) => set((state) => ({
    selectedCourierId: state.selectedCourierId === id ? null : id
  })),

  fetchData: async () => {
    // If we already successfully fetched data, don't refetch automatically
    if (get().hasFetched) return;

    set({ loading: true, error: null });

    try {
      const data = await fetchAutoDispatch();

      let parsedRoutes = [];
      let parsedCouriers = [];
      let parsedPackages = [];

      // 1. Parse Routes for MapViewer
      if (data.vehicle_routes) {
        parsedRoutes = data.vehicle_routes.map((vr, index) => {
          let naiveGeometry = null;
          let currentPosition = null;
          let vehicleType = 'car';
          const vehicleTypes = ['car', 'truck', 'motorcycle'];

          if (vr.geometry && vr.geometry.coordinates && vr.geometry.coordinates.length > 0) {
            const coords = vr.geometry.coordinates;
            // Mock naive geometry as a straight line from first point to last point
            naiveGeometry = {
              type: 'LineString',
              coordinates: [coords[0], coords[coords.length - 1]]
            };
          }

          return {
            id: `route-${vr.vehicle_id}`,
            vehicle_id: vr.vehicle_id,
            color: ROUTE_COLORS[index % ROUTE_COLORS.length],
            geometry: vr.geometry ? {
              type: 'Feature',
              geometry: vr.geometry
            } : null,
            naiveGeometry: naiveGeometry ? {
              type: 'Feature',
              geometry: naiveGeometry
            } : null,
            currentPosition,
            vehicleType,
            stops: (data.optimized_route || [])
              .filter(stop => stop.vehicle_id === vr.vehicle_id)
              .sort((a, b) => {
                if (vr.stop_names) {
                  const idxA = vr.stop_names.indexOf(a.stop_name);
                  const idxB = vr.stop_names.indexOf(b.stop_name);
                  if (idxA !== -1 && idxB !== -1) return idxA - idxB;
                }
                return a.stop_sequence - b.stop_sequence;
              })
          };
        }).filter(r => r.geometry != null);

        // 2. Parse Couriers
        parsedCouriers = data.vehicle_routes.map((vr, index) => {
          const vehicleStops = (data.optimized_route || [])
            .filter(stop => stop.vehicle_id === vr.vehicle_id)
            .sort((a, b) => {
              if (vr.stop_names) {
                const idxA = vr.stop_names.indexOf(a.stop_name);
                const idxB = vr.stop_names.indexOf(b.stop_name);
                if (idxA !== -1 && idxB !== -1) return idxA - idxB;
              }
              return a.stop_sequence - b.stop_sequence;
            });

          // Mock optimization metrics
          const timeSavedMin = Math.floor(Math.random() * 45) + 15; // 15 to 60 mins
          const distanceSavedKm = (Math.random() * 10 + 2).toFixed(1); // 2.0 to 12.0 km
          const moneySaved = (distanceSavedKm * 1.5 + timeSavedMin * 0.5).toFixed(2); // Mock formula

          return {
            id: vr.vehicle_id,
            name: `Courier ${vr.vehicle_id}`,
            initials: `C${vr.vehicle_id}`,
            currentStatus: vr.high_risk_stop_count > 0 ? 'At Risk' : 'En Route',
            stopsRemaining: vr.stop_count,
            routeColor: ROUTE_COLORS[index % ROUTE_COLORS.length],
            stops: vehicleStops,
            stats: {
              totalExpectedDelay: vr.total_expected_delay_min,
              severeStops: vr.severe_stop_count
            },
            optimizationMetrics: {
              timeSavedMin,
              distanceSavedKm,
              moneySaved
            }
          };
        });
      }

      // 3. Parse Packages (Global Manifest)
      if (data.optimized_route) {
        parsedPackages = data.optimized_route;
      }

      set({
        routes: parsedRoutes,
        couriers: parsedCouriers,
        packages: parsedPackages,
        routeSummary: data.route_summary || null,
        explanation: data.explanation || null,
        loading: false,
        hasFetched: true,
      });

    } catch (error) {
      set({ error: error.message, loading: false });
    }
  },

  startSimulation: () => {
    const { routes, _wsRef, isConnecting, wsConnected } = get();

    // Safety check
    if (routes.length === 0) return;

    // SAFETY CHECK: If already connecting or connected, do nothing!
    if (isConnecting || wsConnected) return;

    // Mark as connecting so the UI can update
    set({ isConnecting: true });

    // Clean up any stale, dead sockets just in case
    if (_wsRef && _wsRef.readyState === WebSocket.OPEN) {
      _wsRef.close();
    }

    // Connect to the new simulation endpoint
    const ws = new WebSocket(WS_URL);

    // 1. Send the config as soon as the connection opens
    ws.onopen = () => {
      set({ wsConnected: true, isConnecting: false });

      console.log("route: ", routes)
      const configMsg = {
        vehicles: routes.map((route) => ({
          courier_id: `courier-${route.vehicle_id}`,
          name: `Courier ${route.vehicle_id}`,
          vehicle_id: route.vehicle_id,
          // Grab the raw coordinate array from your parsed geometry
          coordinates: route.geometry.geometry.coordinates,
          color: route.color,
          stops: (route.stops || [])
            .filter(s => s.latitude && s.longitude)
            .map(s => ({
              stop_id: s.stop_id,
              lat: s.latitude,
              lon: s.longitude,
              dwell_seconds: 30
            }))
        })),
        speed_kmh: 60,
        loop: true
      };

      ws.send(JSON.stringify(configMsg));
    };

    // 2. Listen for the position updates
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.type === 'error') {
        console.error("Simulation error from server:", data.message);
      }

      if (data.type === 'started') {
        console.log(`Simulation started for ${data.vehicle_count} vehicles.`);
      }

      // The new backend sends an array of couriers inside "position_update"
      if (data.type === 'position_update') {
        set((state) => {
          let hasChanged = false;
          const patch = {};

          data.couriers.forEach(courier => {
            const existing = state.liveCouriers[courier.courier_id];
            const newLocation = [courier.longitude, courier.latitude];

            // Only update if position actually changed (avoid unnecessary re-renders)
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
                location: newLocation
              };
            }
          });

          if (!hasChanged) return state; // No-op: skip re-render entirely

          return { liveCouriers: { ...state.liveCouriers, ...patch } };
        });
      }

      if (data.type === 'at_stop') {
        // Parse numeric vehicleId from "courier-0" → 0, "courier-1" → 1, etc.
        const vehicleId = parseInt(data.courier_id.replace('courier-', ''), 10);
        console.log(`Vehicle ${vehicleId} arrived at stop ${data.stop_id}`);

        completeStopRequest(data.stop_id)
          .then((response) => {
            const { stop, reopt } = response;

            set((state) => {
              // 1. Mark the completed stop.
              const updatedCouriers = state.couriers.map(courier => {
                if (courier.id === vehicleId) {
                  const completedStops = courier.stops.map(s =>
                    s.stop_id === String(stop.id) ? { ...s, status: 'completed' } : s
                  );
                  const remainingCount = completedStops.filter(s => s.status !== 'completed').length;
                  return { ...courier, stops: completedStops, stopsRemaining: remainingCount };
                }
                return courier;
              });

              // 2. Handle Re-optimization Suggestion
              let newPendingSuggestions = { ...state.pendingSuggestions };
              if (reopt?.triggered && reopt.suggestion_id) {
                console.log(`Re-optimization suggestion generated for vehicle ${vehicleId}`);
                newPendingSuggestions[vehicleId] = { ...reopt, vehicleId };
              }

              return { couriers: updatedCouriers, pendingSuggestions: newPendingSuggestions };
            });
          })
          .catch((error) => {
            console.error("Failed to complete stop or re-optimize:", error);
          });
      }

      if (data.type === 'completed') {
        console.log("Simulation finished route (loop is false).");
      }
    };

    ws.onclose = () => {
      set({ wsConnected: false, isConnecting: false, _wsRef: null, liveCouriers: {} });
    };

    ws.onerror = (err) => {
      console.error('WS Error:', err);
      // Ensure we clear the loading state if it fails
      set({ isConnecting: false });
    };

    set({ _wsRef: ws });
  },

  stopSimulation: () => {
    const { _wsRef } = get();
    if (_wsRef) {
      // 0 = CONNECTING, 1 = OPEN. Only close if it's actually open!
      if (_wsRef.readyState === WebSocket.OPEN || _wsRef.readyState === WebSocket.CONNECTING) {
        _wsRef.close();
      }
    }
  },

  handleSuggestionDecision: async (vehicleId, suggestionId, action) => {
    try {
      await postSuggestionDecision(vehicleId, suggestionId, action);

      set((state) => {
        const suggestion = state.pendingSuggestions[vehicleId];
        const newPendingSuggestions = { ...state.pendingSuggestions };
        delete newPendingSuggestions[vehicleId];

        if (action === 'reject' || !suggestion) {
          return { pendingSuggestions: newPendingSuggestions };
        }

        // Apply accepted suggestion
        const updatedRoutes = state.routes.map(route => {
          if (route.vehicle_id === vehicleId) {
            return {
              ...route,
              geometry: { type: 'Feature', geometry: suggestion.geometry },
              naiveGeometry: suggestion.previous_geometry
                ? { type: 'Feature', geometry: suggestion.previous_geometry }
                : route.naiveGeometry,
            };
          }
          return route;
        });

        const updatedCouriers = state.couriers.map(courier => {
          if (courier.id === vehicleId && suggestion.new_sequence?.length) {
            const completedStops = courier.stops.filter(s => s.status === 'completed');
            const remainingStops = courier.stops.filter(s => s.status !== 'completed');
            
            const seqMap = new Map(
              suggestion.new_sequence.map((stopId, idx) => [String(stopId), idx])
            );
            const remainingReordered = remainingStops.sort((a, b) => {
              const ia = seqMap.has(a.stop_id) ? seqMap.get(a.stop_id) : Infinity;
              const ib = seqMap.has(b.stop_id) ? seqMap.get(b.stop_id) : Infinity;
              return ia - ib;
            });

            return {
              ...courier,
              stops: [...completedStops, ...remainingReordered]
            };
          }
          return courier;
        });

        return {
          routes: updatedRoutes,
          couriers: updatedCouriers,
          pendingSuggestions: newPendingSuggestions
        };
      });
    } catch (error) {
      console.error(`Failed to ${action} suggestion:`, error);
      throw error;
    }
  },

  // Optional escape hatch to force a refetch if needed (e.g. refresh button)
  forceFetchData: async () => {
    set({ hasFetched: false });
    await get().fetchData();
  }
}));
