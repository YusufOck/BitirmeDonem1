import React, { useEffect, useMemo } from 'react';
import MapViewer from '../features/dashboard/components/MapViewer';
import { useRouteStore } from '../store/useRouteStore';
import {
  PageHeader, MetricCard, RouteMetricGrid, StopsTable,
  RouteOrderComparison, RouteValidationBanner, EvidencePanel,
  LoadingState, ErrorState,
} from '../components/shared';

const LIFECYCLE_LABELS = {
  planned: 'Planned',
  optimized_available: 'Optimized Available',
  dispatched: 'Dispatched',
  in_progress: 'In Progress',
  recommendation_available: 'Recommendation Available',
  completed: 'Completed',
};

export default function OptimizationPage() {
  const {
    loading, error, fetchData, forceFetchData,
    routes, selectedCourierId,
    scenarioResult, scenarioLoading, scenarioError,
    runScenario, liveCouriers, pendingSuggestions, handleSuggestionDecision,
    routeLifecycleByVehicleId, loadLifecycleState, startDispatch,
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
  const currentLifecycle = selectedRoute ? routeLifecycleByVehicleId[selectedRoute.vehicle_id]?.status || 'planned' : 'planned';

  const isDispatched = currentLifecycle === 'dispatched' || currentLifecycle === 'in_progress';
  const hasOptimizationResult = Boolean(scenarioResult?.scenario_route?.length);

  const handleRunOptimization = async () => {
    if (!selectedRoute) return;
    try {
      await runScenario(selectedRoute.vehicle_id);
      loadLifecycleState(selectedRoute.vehicle_id);
    } catch {
      // error is shown in scenarioError from store
    }
  };

  const handleStartDispatch = async () => {
    if (!selectedRoute) return;
    await startDispatch(selectedRoute.vehicle_id);
  };

  const plannedStops = selectedRoute?.stops || [];
  const baselineStops = scenarioResult?.baseline_route || plannedStops;
  const optimizedStops = scenarioResult?.scenario_route || [];
  const explanation = scenarioResult?.explanation || null;
  const explanationWarning = scenarioResult?.should_answer === false || scenarioResult?.hallucination_risk === 'high';

  if (loading) return <LoadingState text="Loading route data" />;
  if (error) return <ErrorState message={error} onRetry={forceFetchData} />;
  if (!selectedRoute) return <ErrorState message="No route available. Run a dispatch first." onRetry={forceFetchData} />;

  return (
    <div className="dashboard-container">
      <div className="dashboard-shell">
        <PageHeader
          eyebrow="Route planner"
          title="Pre-Dispatch Route Optimization"
          subtitle="Review the planned route, run the optimizer, and compare before/after results."
        >
          <div className="header-metrics">
            <MetricCard
              label="Route health"
              value={`${Math.max(0, 10 - Math.round((selectedRoute.metrics?.highRiskStops || 0) * 2))}/10`}
            />
            <MetricCard
              label="Expected delay"
              value={`${selectedRoute.metrics?.expectedDelayMin || 0} min`}
              tone={selectedRoute.metrics?.expectedDelayMin > 10 ? 'warning' : 'success'}
            />
            <MetricCard
              label="Stops"
              value={selectedRoute.metrics?.stopCount || selectedRoute.stops?.length || 0}
            />
            <MetricCard
              label="Status"
              value={LIFECYCLE_LABELS[currentLifecycle] || currentLifecycle}
              tone={isDispatched ? 'success' : 'neutral'}
            />
          </div>
        </PageHeader>

        {scenarioError ? <div className="inline-error">{scenarioError}</div> : null}

        {/* Lifecycle guidance */}
        {isDispatched && (
          <div style={{ padding: '0.6rem 0.8rem', backgroundColor: '#dbeafe', borderRadius: '6px', border: '1px solid #93c5fd', fontSize: '0.85rem', color: '#1e40af', marginBottom: '1rem' }}>
            This route is already dispatched. Pre-dispatch optimization is disabled. Use <strong>Live Monitor</strong> to recalculate recommendations for active routes.
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
          <section className="page-card page-card--map">
            <div className="section-title-row">
              <div>
                <span className="panel-kicker">{hasOptimizationResult ? 'Planned vs Pre-dispatch Optimized Route' : 'Planned Route'}</span>
                <h2>{hasOptimizationResult ? 'Optimization comparison' : 'Current planned route'}</h2>
              </div>
              <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                <button
                  className="control-btn control-btn--primary"
                  onClick={handleRunOptimization}
                  disabled={scenarioLoading || isDispatched}
                  title={isDispatched ? 'Route is dispatched. Use Live Monitor instead.' : ''}
                >
                  {scenarioLoading ? 'Optimizing...' : 'Run Pre-dispatch Optimization'}
                </button>
                {hasOptimizationResult && !isDispatched && (
                  <button
                    className="control-btn"
                    style={{ backgroundColor: '#10b981', color: 'white', border: 'none', padding: '0.5rem 1rem', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold' }}
                    onClick={handleStartDispatch}
                  >
                    Start Dispatch
                  </button>
                )}
              </div>
            </div>

            {/* Legend */}
            <div style={{ display: 'flex', gap: '1.5rem', padding: '0.5rem 0', fontSize: '0.8rem', color: '#6b7280' }}>
              <span><span style={{ display: 'inline-block', width: 20, height: 3, backgroundColor: '#3b82f6', marginRight: 6, verticalAlign: 'middle' }} />{hasOptimizationResult ? 'Optimized route (primary)' : 'Active route'}</span>
              {hasOptimizationResult && (
                <span><span style={{ display: 'inline-block', width: 20, height: 3, backgroundColor: '#9ca3af', marginRight: 6, verticalAlign: 'middle', borderTop: '2px dashed #9ca3af' }} />Planned route (baseline)</span>
              )}
            </div>

            {/* Route validation */}
            {hasOptimizationResult && (
              <RouteValidationBanner plannedStops={plannedStops} resultStops={optimizedStops} label="Optimized" />
            )}
            
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', gap: '1.5rem', marginTop: '0.5rem' }}>
              <div className="map-frame map-frame--large" style={{ minHeight: '500px' }}>
                <MapViewer
                  routes={routes}
                  selectedCourierId={selectedRoute?.vehicle_id ?? null}
                  liveCouriers={liveCourierArray}
                  pendingSuggestions={pendingSuggestions}
                  handleSuggestionDecision={handleSuggestionDecision}
                  scenarioResult={scenarioResult}
                  showScenario={false}
                  showAlternatives={false}
                  showLiveCouriers={false}
                />
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                <RouteMetricGrid route={selectedRoute} scenarioResult={scenarioResult} />
                <EvidencePanel
                  stops={hasOptimizationResult ? optimizedStops : plannedStops}
                  explanation={explanation}
                  explanationWarning={explanationWarning}
                />
              </div>
            </div>
          </section>

          <section className="page-card page-card--wide">
            <span className="panel-kicker">Stop order comparison</span>
            <h2>Planned vs Optimized stop order</h2>
            <RouteOrderComparison
              baseline={baselineStops}
              scenario={scenarioResult}
            />
          </section>

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
