import React, { useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import MapViewer from '../features/dashboard/components/MapViewer';
import { useRouteStore } from '../store/useRouteStore';
import { PageHeader, MetricCard, RoutePicker, RouteMetricGrid, LoadingState, ErrorState } from '../components/shared';

const round = (v, d = 1) => {
  const n = Number(v);
  return Number.isFinite(n) ? Number(n.toFixed(d)) : 0;
};

const LIFECYCLE_LABELS = {
  planned: 'Planned',
  optimized_available: 'Optimized Available',
  dispatched: 'Dispatched',
  in_progress: 'In Progress',
  recommendation_available: 'Recommendation Available',
  completed: 'Completed',
};

const LIFECYCLE_TONES = {
  planned: 'neutral',
  optimized_available: 'warning',
  dispatched: 'success',
  in_progress: 'success',
  recommendation_available: 'warning',
  completed: 'neutral',
};

function OverviewMetricCards({ routes }) {
  const totalStops = routes.reduce((sum, r) => sum + (r.metrics?.stopCount || r.stops?.length || 0), 0);
  const highRiskCount = routes.reduce((sum, r) => sum + (r.metrics?.highRiskStops || 0), 0);
  const totalDelay = routes.reduce((sum, r) => sum + (r.metrics?.expectedDelayMin || 0), 0);

  return (
    <div className="header-metrics">
      <MetricCard label="Active routes" value={routes.length} />
      <MetricCard label="Total stops" value={totalStops} />
      <MetricCard
        label="Delayed / at-risk"
        value={`${round(totalDelay)} min`}
        tone={totalDelay > 15 ? 'warning' : 'success'}
        subvalue={highRiskCount > 0 ? `${highRiskCount} risky stops` : 'All clear'}
      />
    </div>
  );
}

function NextActionHint({ status }) {
  const hints = {
    planned: 'Open Route Planner to run pre-dispatch optimization.',
    optimized_available: 'Review the optimized route and Start Dispatch.',
    dispatched: 'Open Live Monitor to track and respond to conditions.',
    in_progress: 'Open Live Monitor to track and respond to conditions.',
    recommendation_available: 'A route recommendation is available. Open Live Monitor to review.',
    completed: 'Route is completed.',
  };
  return (
    <div style={{ padding: '0.6rem 0.8rem', backgroundColor: '#fef3c7', borderRadius: '6px', border: '1px solid #fbbf24', fontSize: '0.85rem', color: '#92400e' }}>
      <strong>Next action: </strong>{hints[status] || 'Select a route to begin.'}
    </div>
  );
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const {
    loading, error, fetchData, forceFetchData,
    routes, selectedCourierId, setSelectedCourier,
    liveCouriers, pendingSuggestions, handleSuggestionDecision,
    routeLifecycleByVehicleId, loadLifecycleState, startDispatch,
    resetRouteLifecycle,
  } = useRouteStore();

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useEffect(() => {
    if (selectedCourierId !== null) {
      loadLifecycleState(selectedCourierId);
    }
  }, [selectedCourierId, loadLifecycleState]);

  const liveCourierArray = useMemo(() => Object.values(liveCouriers || {}), [liveCouriers]);
  const selectedRoute = routes.find((r) => r.vehicle_id === selectedCourierId) || routes[0] || null;
  const currentStatus = selectedRoute ? routeLifecycleByVehicleId[selectedRoute.vehicle_id]?.status || 'planned' : 'planned';

  const routeList = routes.map((r) => ({
    id: r.vehicle_id,
    initials: `C${r.vehicle_id}`,
    name: r.courierName,
    color: r.color,
    status: r.metrics?.highRiskStops > 0 ? 'Needs Review' : 'On Track',
    stopCount: r.metrics?.stopCount || r.stops?.length || 0,
    totalDelay: r.metrics?.expectedDelayMin || 0,
  }));

  const handleStartDispatch = async () => {
    if (!selectedRoute) return;
    await startDispatch(selectedRoute.vehicle_id);
  };

  const handleResetLifecycle = async () => {
    if (!selectedRoute) return;
    await resetRouteLifecycle(selectedRoute.vehicle_id);
  };

  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} onRetry={forceFetchData} />;

  return (
    <div className="dashboard-container">
      <div className="dashboard-shell">
        <PageHeader
          eyebrow="Logistics Command Center"
          title="Operations Dashboard"
          subtitle="Select a route to inspect, then proceed to optimization or live monitoring."
        >
          <OverviewMetricCards routes={routes} />
        </PageHeader>

        <div className="page-grid page-grid--overview">
          <section className="page-card">
            <span className="panel-kicker">Routes</span>
            <h2>Active courier routes</h2>
            <p>Select a route to view on the map.</p>
            <RoutePicker
              routes={routeList}
              selectedId={selectedCourierId}
              onChange={setSelectedCourier}
            />
          </section>

          <section className="page-card page-card--map">
            <div className="section-title-row">
              <div>
                <span className="panel-kicker">Route map</span>
                <h2>{selectedRoute?.courierName || 'Select a route'}</h2>
              </div>
              <small>Active route view</small>
            </div>
            <div className="map-frame" style={{ minHeight: '400px' }}>
              {selectedRoute ? (
                <MapViewer
                  routes={routes}
                  selectedCourierId={selectedRoute?.vehicle_id ?? null}
                  liveCouriers={liveCourierArray}
                  pendingSuggestions={pendingSuggestions}
                  handleSuggestionDecision={handleSuggestionDecision}
                  showScenario={false}
                  showAlternatives={false}
                  showLiveCouriers={false}
                />
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', backgroundColor: '#f9fafb', color: '#6b7280', padding: '2rem', textAlign: 'center', borderRadius: '8px' }}>
                  <p>No route selected. Please select an active route from the left panel to begin.</p>
                </div>
              )}
            </div>
          </section>

          {selectedRoute && (
            <aside className="page-card" style={{ display: 'flex', flexDirection: 'column' }}>
              <span className="panel-kicker">Route Overview</span>
              <h2>Selected route details</h2>

              {/* Lifecycle Status Badge */}
              <div style={{ marginTop: '0.8rem', padding: '0.8rem', backgroundColor: '#e0e7ff', borderRadius: '6px', border: '1px solid #c7d2fe' }}>
                <div style={{ fontSize: '0.75rem', fontWeight: 'bold', textTransform: 'uppercase', color: '#4338ca', marginBottom: '4px' }}>Lifecycle Status</div>
                <div style={{ fontSize: '1.1rem', fontWeight: 'bold', color: '#312e81' }}>
                  {LIFECYCLE_LABELS[currentStatus] || currentStatus}
                </div>
              </div>

              <NextActionHint status={currentStatus} />

              <div style={{ marginTop: '0.8rem', flex: 1 }}>
                <RouteMetricGrid route={selectedRoute} scenarioResult={null} />
              </div>

              {/* Quick Action Buttons */}
              <div style={{ marginTop: '1.5rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                <button
                  className="control-btn control-btn--primary control-btn--full"
                  onClick={() => navigate('/optimization')}
                >
                  Open Route Planner
                </button>

                {(currentStatus === 'planned' || currentStatus === 'optimized_available') && (
                  <button
                    className="control-btn control-btn--full"
                    style={{ backgroundColor: '#10b981', color: 'white', border: 'none', padding: '0.8rem', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold' }}
                    onClick={handleStartDispatch}
                  >
                    Start Dispatch
                  </button>
                )}

                <button
                  className="control-btn control-btn--full"
                  style={{ backgroundColor: '#6366f1', color: 'white', border: 'none', padding: '0.8rem', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold' }}
                  onClick={() => navigate('/scenarios')}
                >
                  Open Live Monitor
                </button>

                {(currentStatus === 'dispatched' || currentStatus === 'in_progress' || currentStatus === 'completed') && (
                  <button
                    className="control-btn control-btn--full"
                    style={{ backgroundColor: '#6b7280', color: 'white', border: 'none', padding: '0.8rem', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold', fontSize: '0.8rem' }}
                    onClick={handleResetLifecycle}
                  >
                    Reset to Planned (Demo)
                  </button>
                )}
              </div>
            </aside>
          )}
        </div>
      </div>
    </div>
  );
}
