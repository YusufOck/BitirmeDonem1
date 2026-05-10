import React, { useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import MapViewer from '../features/dashboard/components/MapViewer';
import { useRouteStore } from '../store/useRouteStore';
import {
  PageHeader, MetricCard, RouteMetricGrid, StopsTable,
  RouteOrderComparison, RouteValidationBanner, EvidencePanel,
  LoadingState, ErrorState,
} from '../components/shared';

export default function OptimizationPage() {
  const navigate = useNavigate();
  const {
    loading, error, fetchData, forceFetchData,
    routes, selectedCourierId,
    scenarioResult, scenarioResultsByVehicleId, scenarioLoading, scenarioError,
    runScenario, liveCouriers, pendingSuggestions, handleSuggestionDecision,
    routeLifecycleByVehicleId, loadLifecycleState, startDispatch,
    feedbackMessage, feedbackType, clearFeedback,
  } = useRouteStore();

  useEffect(() => { fetchData(); }, [fetchData]);
  useEffect(() => {
    if (selectedCourierId !== null) loadLifecycleState(selectedCourierId);
  }, [selectedCourierId, loadLifecycleState]);

  const liveCourierArray = useMemo(() => Object.values(liveCouriers || {}), [liveCouriers]);
  const selectedRoute = routes.find((r) => r.vehicle_id === selectedCourierId) || routes[0] || null;
  const activeScenarioResult = selectedRoute
    ? scenarioResultsByVehicleId[selectedRoute.vehicle_id]
      || (scenarioResult?.vehicleId === selectedRoute.vehicle_id ? scenarioResult : null)
    : null;
  const currentLifecycle = selectedRoute ? routeLifecycleByVehicleId[selectedRoute.vehicle_id]?.status || 'planned' : 'planned';
  const isDispatched = currentLifecycle === 'dispatched' || currentLifecycle === 'in_progress';
  const hasOptimizationResult = Boolean(activeScenarioResult?.scenario_route?.length);

  const handleRunOptimization = async () => {
    if (!selectedRoute) return;
    await runScenario(selectedRoute.vehicle_id);
    loadLifecycleState(selectedRoute.vehicle_id);
  };

  const handleStartDispatch = async () => {
    if (!selectedRoute) return;
    await startDispatch(selectedRoute.vehicle_id);
  };

  const plannedStops = selectedRoute?.stops || [];
  const optimizedStops = activeScenarioResult?.scenario_route || [];
  const baselineStops = activeScenarioResult?.baseline_route || plannedStops;

  // Validation
  const optimizedValid = !hasOptimizationResult || (() => {
    const pIds = new Set(plannedStops.map((s) => s.stop_id || s.stop_name).filter(Boolean));
    const oIds = new Set(optimizedStops.map((s) => s.stop_id || s.stop_name).filter(Boolean));
    return pIds.size === oIds.size && [...pIds].every((id) => oIds.has(id));
  })();

  if (loading) return <LoadingState text="Loading route data" />;
  if (error) return <ErrorState message={error} onRetry={forceFetchData} />;
  if (!selectedRoute) return <ErrorState message="No route available. Run a dispatch first." onRetry={forceFetchData} />;

  return (
    <div className="dashboard-container">
      <div className="dashboard-shell">
        <PageHeader
          eyebrow="Route planner · Before dispatch"
          title="Pre-Dispatch Route Optimization"
          subtitle="Compare the planned route with the optimized route before starting dispatch."
        >
          <div className="header-metrics">
            <MetricCard label="Stops" value={selectedRoute.metrics?.stopCount || selectedRoute.stops?.length || 0} />
            <MetricCard
              label="Expected delay"
              value={`${selectedRoute.metrics?.expectedDelayMin || 0} min`}
              tone={selectedRoute.metrics?.expectedDelayMin > 10 ? 'warning' : 'success'}
            />
            <MetricCard
              label="Route health"
              value={`${Math.max(0, 10 - Math.round((selectedRoute.metrics?.highRiskStops || 0) * 2))}/10`}
            />
          </div>
        </PageHeader>

        {/* Feedback banner */}
        {feedbackMessage && (
          <div className={`feedback-banner feedback-banner--${feedbackType}`}>
            <span>{feedbackMessage}</span>
            <button onClick={clearFeedback} aria-label="Dismiss">×</button>
            {isDispatched && (
              <button className="control-btn control-btn--primary" style={{ marginLeft: 'auto', padding: '0.4rem 1rem' }} onClick={() => navigate('/scenarios')}>
                Open Live Monitor →
              </button>
            )}
          </div>
        )}

        {scenarioError ? <div className="inline-error">{scenarioError}</div> : null}

        {/* Dispatched warning */}
        {isDispatched && (
          <div className="info-banner info-banner--blue">
            This route is already dispatched. Pre-dispatch optimization is disabled.
            <button className="control-btn control-btn--primary" style={{ marginLeft: 'auto', padding: '0.4rem 1rem' }} onClick={() => navigate('/scenarios')}>
              Open Live Monitor →
            </button>
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.2rem' }}>
          {/* Map section */}
          <section className="page-card page-card--map">
            <div className="section-title-row">
              <div>
                <span className="panel-kicker">{hasOptimizationResult ? 'Planned vs Optimized Route' : 'Planned Route'}</span>
                <h2>{hasOptimizationResult ? 'Optimization comparison' : 'Current planned route'}</h2>
              </div>
              <div className="responsive-btn-row">
                <button
                  className="control-btn control-btn--primary"
                  onClick={handleRunOptimization}
                  disabled={scenarioLoading || isDispatched}
                  title={isDispatched ? 'Route is dispatched. Use Live Monitor instead.' : ''}
                >
                  {scenarioLoading ? 'Optimizing...' : 'Run Pre-dispatch Optimization'}
                </button>
                {hasOptimizationResult && !isDispatched && optimizedValid && (
                  <button className="control-btn dispatch-btn" onClick={handleStartDispatch}>
                    Start Dispatch
                  </button>
                )}
              </div>
            </div>

            {/* Legend */}
            <div className="route-legend">
              <span><span className="legend-swatch legend-swatch--primary" />{hasOptimizationResult ? 'Pre-dispatch Optimized Route' : 'Planned Route'}</span>
              {hasOptimizationResult && (
                <span><span className="legend-swatch legend-swatch--dashed" />Planned Route (baseline)</span>
              )}
            </div>

            {/* Route validation */}
            {hasOptimizationResult && (
              <RouteValidationBanner plannedStops={plannedStops} resultStops={optimizedStops} label="Optimized" />
            )}

            <div className="responsive-map-metrics">
              <div className="map-frame map-frame--large">
                <MapViewer
                  routes={routes}
                  selectedCourierId={selectedRoute?.vehicle_id ?? null}
                  liveCouriers={liveCourierArray}
                  pendingSuggestions={pendingSuggestions}
                  handleSuggestionDecision={handleSuggestionDecision}
                  scenarioResult={activeScenarioResult}
                  showScenario={false}
                  showAlternatives={false}
                  showLiveCouriers={false}
                  legendContext="planner"
                />
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                <RouteMetricGrid route={selectedRoute} scenarioResult={activeScenarioResult} />
                <EvidencePanel
                  stops={hasOptimizationResult ? optimizedStops : plannedStops}
                  explanation={activeScenarioResult?.explanation || null}
                  explanationWarning={activeScenarioResult?.should_answer === false || activeScenarioResult?.hallucination_risk === 'high'}
                />
              </div>
            </div>
          </section>

          {/* Stop order comparison */}
          <section className="page-card page-card--wide">
            <span className="panel-kicker">Stop order comparison</span>
            <h2>Planned vs Optimized stop order</h2>
            <RouteOrderComparison baseline={baselineStops} scenario={activeScenarioResult} />
          </section>

          {/* Stop details table */}
          {(hasOptimizationResult ? optimizedStops : plannedStops).length > 0 && (
            <section className="page-card page-card--wide">
              <span className="panel-kicker">Stop details</span>
              <h2>{hasOptimizationResult ? 'Optimized stop sequence' : 'Planned stop sequence'}</h2>
              <StopsTable stops={hasOptimizationResult ? optimizedStops : plannedStops} compact />
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
