import React, { useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import MapViewer from '../features/dashboard/components/MapViewer';
import { useRouteStore } from '../store/useRouteStore';
import { PageHeader, MetricCard, RoutePicker, LoadingState, ErrorState } from '../components/shared';

const round = (v, d = 1) => {
  const n = Number(v);
  return Number.isFinite(n) ? Number(n.toFixed(d)) : 0;
};

const LIFECYCLE_LABELS = {
  planned: 'Planned',
  optimized_available: 'Ready to Dispatch',
  dispatched: 'In Progress',
  in_progress: 'In Progress',
  recommendation_available: 'Recommendation Available',
  completed: 'Completed',
};

const LIFECYCLE_ICONS = {
  planned: '📋',
  optimized_available: '✅',
  dispatched: '🚚',
  in_progress: '🚚',
  recommendation_available: '⚡',
  completed: '🏁',
};

export default function DashboardPage() {
  const navigate = useNavigate();
  const {
    loading, error, fetchData, forceFetchData,
    routes, selectedCourierId, setSelectedCourier,
    liveCouriers, pendingSuggestions, handleSuggestionDecision,
    routeLifecycleByVehicleId, loadLifecycleState, startDispatch,
    resetRouteLifecycle, feedbackMessage, feedbackType, clearFeedback,
    simulationRunning,
  } = useRouteStore();

  useEffect(() => { fetchData(); }, [fetchData]);
  useEffect(() => {
    if (selectedCourierId !== null) loadLifecycleState(selectedCourierId);
  }, [selectedCourierId, loadLifecycleState]);

  const liveCourierArray = useMemo(() => Object.values(liveCouriers || {}), [liveCouriers]);
  const selectedRoute = routes.find((r) => r.vehicle_id === selectedCourierId) || routes[0] || null;
  const currentStatus = selectedRoute ? routeLifecycleByVehicleId[selectedRoute.vehicle_id]?.status || 'planned' : 'planned';
  const isDispatched = currentStatus === 'dispatched' || currentStatus === 'in_progress';

  const totalStops = routes.reduce((sum, r) => sum + (r.metrics?.stopCount || r.stops?.length || 0), 0);
  const totalDelay = routes.reduce((sum, r) => sum + (r.metrics?.expectedDelayMin || 0), 0);

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
          eyebrow="Operations control center"
          title="Dispatch Dashboard"
          subtitle="Select a route, then plan, dispatch, or monitor."
        >
          <div className="header-metrics">
            <MetricCard label="Routes" value={routes.length} />
            <MetricCard label="Total stops" value={totalStops} />
            <MetricCard
              label="Total delay"
              value={`${round(totalDelay)} min`}
              tone={totalDelay > 15 ? 'warning' : 'success'}
            />
          </div>
        </PageHeader>

        {/* Feedback banner */}
        {feedbackMessage && (
          <div className={`feedback-banner feedback-banner--${feedbackType}`}>
            <span>{feedbackMessage}</span>
            <button onClick={clearFeedback} aria-label="Dismiss">×</button>
          </div>
        )}

        {/* Workflow explanation */}
        <section className="page-card workflow-card">
          <span className="panel-kicker">How it works</span>
          <div className="workflow-steps-row">
            <div className="workflow-chip">
              <span className="workflow-num">1</span>
              <div><strong>Plan</strong><small>Open Route Planner to optimize before dispatch.</small></div>
            </div>
            <div className="workflow-chip">
              <span className="workflow-num">2</span>
              <div><strong>Dispatch</strong><small>Start the courier. Live tracking begins.</small></div>
            </div>
            <div className="workflow-chip">
              <span className="workflow-num">3</span>
              <div><strong>Adapt</strong><small>Use Live Monitor to adjust when conditions change.</small></div>
            </div>
          </div>
        </section>

        <div className="page-grid page-grid--overview">
          {/* Route picker */}
          <section className="page-card">
            <span className="panel-kicker">Courier routes</span>
            <h2>Select a route</h2>
            <RoutePicker routes={routeList} selectedId={selectedCourierId} onChange={setSelectedCourier} />
          </section>

          {/* Map */}
          <section className="page-card page-card--map">
            <div className="section-title-row">
              <div>
                <span className="panel-kicker">Route map</span>
                <h2>{selectedRoute?.courierName || 'Select a route'}</h2>
              </div>
              {simulationRunning && (
                <span className="live-pill live-pill--on"><span className="live-dot" /> Live</span>
              )}
            </div>
            <div className="map-frame map-frame--large">
              {selectedRoute ? (
                <MapViewer
                  routes={routes}
                  selectedCourierId={selectedRoute?.vehicle_id ?? null}
                  liveCouriers={liveCourierArray}
                  pendingSuggestions={pendingSuggestions}
                  handleSuggestionDecision={handleSuggestionDecision}
                  showScenario={false}
                  showAlternatives={false}
                  showLiveCouriers={simulationRunning}
                />
              ) : (
                <div className="empty-map-placeholder">
                  <p>Select a route from the left panel to begin.</p>
                </div>
              )}
            </div>
          </section>

          {/* Status + Actions */}
          {selectedRoute && (
            <aside className="page-card">
              <span className="panel-kicker">Route status</span>

              {/* Lifecycle badge */}
              <div className="lifecycle-badge">
                <span className="lifecycle-icon">{LIFECYCLE_ICONS[currentStatus] || '📋'}</span>
                <div>
                  <span className="lifecycle-label">Current status</span>
                  <strong className="lifecycle-value">{LIFECYCLE_LABELS[currentStatus] || currentStatus}</strong>
                </div>
              </div>

              {/* Quick metrics */}
              <div className="metric-grid" style={{ marginTop: '0.8rem' }}>
                <MetricCard label="Stops" value={selectedRoute.metrics?.stopCount || 0} />
                <MetricCard
                  label="Delay"
                  value={`${round(selectedRoute.metrics?.expectedDelayMin || 0)} min`}
                  tone={selectedRoute.metrics?.expectedDelayMin > 10 ? 'warning' : 'success'}
                />
              </div>

              {/* Next action hint */}
              <div className="next-action-hint">
                {currentStatus === 'planned' && 'Open Route Planner to optimize, then start dispatch.'}
                {currentStatus === 'optimized_available' && 'Route is optimized. Start dispatch when ready.'}
                {(currentStatus === 'dispatched' || currentStatus === 'in_progress') && 'Courier is en route. Open Live Monitor to track and adapt.'}
                {currentStatus === 'recommendation_available' && 'A new recommendation is available. Open Live Monitor to review.'}
                {currentStatus === 'completed' && 'Route is completed.'}
              </div>

              {/* Action buttons */}
              <div className="action-stack">
                {(currentStatus === 'planned' || currentStatus === 'optimized_available') && (
                  <>
                    <button className="control-btn control-btn--primary control-btn--full" onClick={() => navigate('/optimization')}>
                      Open Route Planner
                    </button>
                    <button className="control-btn control-btn--full dispatch-btn" onClick={handleStartDispatch}>
                      Start Dispatch
                    </button>
                  </>
                )}
                {isDispatched && (
                  <button className="control-btn control-btn--primary control-btn--full" onClick={() => navigate('/scenarios')}>
                    Open Live Monitor
                  </button>
                )}
                {currentStatus === 'recommendation_available' && (
                  <button className="control-btn control-btn--primary control-btn--full" onClick={() => navigate('/scenarios')}>
                    Review Recommendation
                  </button>
                )}
                {(isDispatched || currentStatus === 'completed') && (
                  <button className="control-btn control-btn--neutral control-btn--full" onClick={handleResetLifecycle} style={{ fontSize: '0.78rem' }}>
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
