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

const LIFECYCLE_TONES = {
  planned: 'neutral',
  dispatched: 'blue',
  in_progress: 'blue',
  recommendation_available: 'warning',
  completed: 'success',
};

export default function DashboardPage() {
  const navigate = useNavigate();
  const {
    loading, error, fetchData, forceFetchData,
    routes, selectedCourierId, setSelectedCourier,
    liveCouriers, pendingSuggestions, handleSuggestionDecision,
    routeLifecycleByVehicleId, loadLifecycleState, startDispatch,
    resetRouteLifecycle, feedbackMessage, feedbackType, clearFeedback,
    simulationRunning, completedStopIdsByVehicle, completedStopVersion,
  } = useRouteStore();

  useEffect(() => { fetchData(); }, [fetchData]);
  useEffect(() => {
    if (selectedCourierId !== null) loadLifecycleState(selectedCourierId);
  }, [selectedCourierId, loadLifecycleState]);

  const liveCourierArray = useMemo(() => Object.values(liveCouriers || {}), [liveCouriers]);
  const selectedRoute = routes.find((r) => r.vehicle_id === selectedCourierId) || routes[0] || null;
  const currentStatus = selectedRoute ? routeLifecycleByVehicleId[selectedRoute.vehicle_id]?.status || 'planned' : 'planned';
  const isDispatched = currentStatus === 'dispatched' || currentStatus === 'in_progress';

  // Single source of truth for completed stops — completedStopVersion triggers re-render
  const completedStopIds = useMemo(
    () => (selectedRoute ? completedStopIdsByVehicle[selectedRoute.vehicle_id] || new Set() : new Set()),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectedRoute, completedStopIdsByVehicle, completedStopVersion]
  );
  const allStops = useMemo(() => selectedRoute?.stops || [], [selectedRoute?.stops]);
  const remainingStops = useMemo(() => allStops.filter((s) => !completedStopIds.has(String(s.stop_id))), [allStops, completedStopIds]);
  const totalStopCount = completedStopIds.size + remainingStops.length;
  const nextStop = remainingStops[0] || null;
  const allRouteCompleted = isDispatched && remainingStops.length === 0 && completedStopIds.size > 0;

  const routeList = routes.map((r) => {
    const cIds = completedStopIdsByVehicle[r.vehicle_id] || new Set();
    return {
      id: r.vehicle_id,
      initials: `C${r.vehicle_id}`,
      name: r.courierName,
      color: r.color,
      status: r.metrics?.highRiskStops > 0 ? 'Needs Review' : 'On Track',
      stopCount: r.stops?.length || 0,
      totalDelay: r.metrics?.expectedDelayMin || 0,
      progress: cIds.size > 0 ? `${cIds.size}/${r.stops?.length || 0}` : null,
    };
  });

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
            <MetricCard label="Total stops" value={routes.reduce((sum, r) => sum + (r.stops?.length || 0), 0)} />
            <MetricCard label="Total delay" value={`${round(routes.reduce((sum, r) => sum + (r.metrics?.expectedDelayMin || 0), 0))} min`} />
          </div>
        </PageHeader>

        {feedbackMessage && (
          <div className={`feedback-banner feedback-banner--${feedbackType}`}>
            <span>{feedbackMessage}</span>
            <button onClick={clearFeedback} aria-label="Dismiss">×</button>
          </div>
        )}

        <section className="page-card workflow-card">
          <span className="panel-kicker">Workflow</span>
          <div className="workflow-steps-row">
            <div className={`workflow-chip ${currentStatus === 'planned' ? 'workflow-chip--active' : ''}`}>
              <span className="workflow-num">1</span>
              <div><strong>Plan</strong><small>Optimize before dispatch.</small></div>
            </div>
            <div className={`workflow-chip ${isDispatched ? 'workflow-chip--active' : ''}`}>
              <span className="workflow-num">2</span>
              <div><strong>Dispatch</strong><small>Start courier. Live tracking begins.</small></div>
            </div>
            <div className={`workflow-chip ${currentStatus === 'recommendation_available' ? 'workflow-chip--active' : ''}`}>
              <span className="workflow-num">3</span>
              <div><strong>Adapt</strong><small>Live Monitor to adjust conditions.</small></div>
            </div>
          </div>
        </section>

        <div className="page-grid page-grid--overview">
          <section className="page-card">
            <span className="panel-kicker">Courier routes</span>
            <h2>Select a route</h2>
            <RoutePicker routes={routeList} selectedId={selectedCourierId} onChange={setSelectedCourier} />
          </section>

          <section className="page-card page-card--map">
            <div className="section-title-row">
              <div>
                <span className="panel-kicker">Route map</span>
                <h2>{selectedRoute?.courierName || 'Select a route'}</h2>
              </div>
              {simulationRunning && <span className="live-pill live-pill--on"><span className="live-dot" /> Live</span>}
            </div>
            <div className="map-frame map-frame--large">
              {selectedRoute ? (
                <MapViewer
                  routes={routes}
                  selectedCourierId={selectedRoute.vehicle_id}
                  liveCouriers={liveCourierArray}
                  pendingSuggestions={pendingSuggestions}
                  handleSuggestionDecision={handleSuggestionDecision}
                  showScenario={false}
                  showLiveCouriers={simulationRunning}
                  completedStopIds={completedStopIds}
                  nextStopId={nextStop?.stop_id}
                />
              ) : (
                <div className="empty-map-placeholder"><p>Select a route to begin.</p></div>
              )}
            </div>
          </section>

          {selectedRoute && (
            <aside className="page-card">
              <span className="panel-kicker">Route status</span>

              <div className={`lifecycle-badge lifecycle-badge--${LIFECYCLE_TONES[currentStatus] || 'neutral'}`}>
                <div>
                  <span className="lifecycle-label">Current status</span>
                  <strong className="lifecycle-value">{LIFECYCLE_LABELS[currentStatus] || currentStatus}</strong>
                </div>
              </div>

              <div className="progress-section">
                <div className="progress-bar-container">
                  <div className="progress-bar-fill" style={{ width: totalStopCount > 0 ? `${(completedStopIds.size / totalStopCount) * 100}%` : '0%' }} />
                </div>
                <span className="progress-text">{completedStopIds.size} / {totalStopCount} stops completed</span>
              </div>

              <div className="metric-grid" style={{ marginTop: '0.6rem' }}>
                <MetricCard label="Delay" value={`${round(selectedRoute.metrics?.expectedDelayMin || 0)} min`} tone={selectedRoute.metrics?.expectedDelayMin > 10 ? 'warning' : 'success'} />
                <MetricCard label="Remaining" value={remainingStops.length} />
              </div>

              {allRouteCompleted ? (
                <div className="next-stop-card" style={{ borderColor: '#10b981' }}>
                  <span className="panel-kicker" style={{ color: '#10b981' }}>Route complete</span>
                  <strong>All {completedStopIds.size} stops delivered ✓</strong>
                </div>
              ) : nextStop && isDispatched ? (
                <div className="next-stop-card">
                  <span className="panel-kicker">Next stop</span>
                  <strong>{nextStop.stop_id || `Stop ${nextStop.stop_id}`}</strong>
                </div>
              ) : null}

              <div className="next-action-hint">
                {currentStatus === 'planned' && 'Open Route Planner to optimize, then start dispatch.'}
                {isDispatched && !allRouteCompleted && 'Courier is en route. Open Live Monitor to track and adapt.'}
                {allRouteCompleted && 'Route completed. All deliveries are done.'}
                {currentStatus === 'recommendation_available' && 'A new recommendation is available. Open Live Monitor.'}
                {currentStatus === 'completed' && 'Route is completed.'}
              </div>

              <div className="action-stack">
                {currentStatus === 'planned' && (
                  <>
                    <button className="control-btn control-btn--primary control-btn--full" onClick={() => navigate('/optimization')}>Open Route Planner</button>
                    <button className="control-btn dispatch-btn control-btn--full" onClick={handleStartDispatch}>Start Dispatch</button>
                  </>
                )}
                {isDispatched && (
                  <button className="control-btn control-btn--primary control-btn--full" onClick={() => navigate('/scenarios')}>Open Live Monitor</button>
                )}
                {(isDispatched || currentStatus === 'completed') && (
                  <button className="control-btn control-btn--neutral control-btn--full" onClick={handleResetLifecycle} style={{ fontSize: '0.78rem' }}>Reset to Planned (Demo)</button>
                )}
              </div>
            </aside>
          )}
        </div>
      </div>
    </div>
  );
}
