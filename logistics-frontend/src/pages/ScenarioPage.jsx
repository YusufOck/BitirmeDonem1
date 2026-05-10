import React, { useEffect, useMemo } from 'react';
import MapViewer from '../features/dashboard/components/MapViewer';
import { useRouteStore } from '../store/useRouteStore';
import {
  PageHeader, MetricCard, RouteMetricGrid, RoutePicker,
  RouteOrderComparison, RouteValidationBanner, SegmentEditor,
  AgentExplanationPanel, LoadingState, ErrorState,
} from '../components/shared';

const SCENARIO_SLIDERS = [
  { key: 'traffic_density', label: 'Traffic density', help: 'road congestion', min: 0, max: 100 },
  { key: 'accident_severity', label: 'Accident severity', help: 'incident severity', min: 0, max: 100 },
  { key: 'weather_severity', label: 'Weather severity', help: 'rain, wind, fog, snow', min: 0, max: 100 },
  { key: 'road_disruption', label: 'Road disruption', help: 'blocked or slow segments', min: 0, max: 100 },
  { key: 'package_load', label: 'Package load', help: 'vehicle workload', min: 0, max: 100 },
  { key: 'dispatch_hour', label: 'Dispatch hour', help: 'time-of-day impact', min: 0, max: 23, format: (v) => `${String(v).padStart(2, '0')}:00` },
];

const WEATHER_OPTIONS = ['clear', 'cloudy', 'wind', 'fog', 'rain', 'snow'];

function buildSegments(route, overrides = {}) {
  const stops = route?.stops || [];
  return stops.slice(0, -1).map((stop, index) => {
    const nextStop = stops[index + 1];
    const segKey = `${stop.stop_id || index}-${nextStop.stop_id || index + 1}`;
    return {
      key: segKey,
      from: stop,
      to: nextStop,
      override: {
        from_stop_id: String(stop.stop_id || index),
        to_stop_id: String(nextStop.stop_id || index + 1),
        traffic_density: 0,
        accident_severity: 0,
        road_closure: false,
        weather_severity: 0,
        speed_reduction: 0,
        extra_delay_min: 0,
        risk_level: 'low',
        priority: 50,
        road_type: nextStop.road_type || 'urban',
        ...(overrides[segKey] || {}),
      },
    };
  });
}

export default function ScenarioPage() {
  const {
    loading, error, fetchData, forceFetchData,
    routes, selectedCourierId, setSelectedCourier,
    scenarioControls, scenarioControlsByVehicleId,
    scenarioResultsByVehicleId, segmentOverridesByVehicleId,
    scenarioResult, scenarioLoading, scenarioError,
    setScenarioControl, setSegmentOverride, resetScenario, runScenario,
    liveCouriers, pendingSuggestions, handleSuggestionDecision,
    routeLifecycleByVehicleId, loadLifecycleState,
    agentExplanation, agentExplanationLoading, agentExplanationError,
    requestAgentExplanation, applyLiveRecommendation,
    feedbackMessage, feedbackType, clearFeedback,
    simulationRunning, startSimulation,
  } = useRouteStore();

  useEffect(() => { fetchData(); }, [fetchData]);
  useEffect(() => {
    if (selectedCourierId !== null) loadLifecycleState(selectedCourierId);
  }, [selectedCourierId, loadLifecycleState]);

  const liveCourierArray = useMemo(() => Object.values(liveCouriers || {}), [liveCouriers]);
  const selectedRoute = routes.find((r) => r.vehicle_id === selectedCourierId) || routes[0] || null;
  const currentLifecycle = selectedRoute ? routeLifecycleByVehicleId[selectedRoute.vehicle_id]?.status || 'planned' : 'planned';
  const isActive = currentLifecycle === 'dispatched' || currentLifecycle === 'in_progress';

  const activeControls = selectedRoute
    ? scenarioControlsByVehicleId[selectedRoute.vehicle_id] || scenarioControls
    : scenarioControls;
  const activeOverrides = selectedRoute
    ? segmentOverridesByVehicleId[selectedRoute.vehicle_id] || {}
    : {};
  const activeScenarioResult = selectedRoute
    ? scenarioResultsByVehicleId[selectedRoute.vehicle_id] || scenarioResult
    : scenarioResult;

  const hasRecommendation = Boolean(activeScenarioResult?.scenario_route?.length);
  const segments = selectedRoute ? buildSegments(selectedRoute, activeOverrides) : [];
  const routeList = routes.map((r) => ({
    id: r.vehicle_id,
    initials: `C${r.vehicle_id}`,
    name: r.courierName,
    color: r.color,
    status: r.metrics?.highRiskStops > 0 ? 'Needs Review' : 'On Track',
    stopCount: r.metrics?.stopCount || 0,
    totalDelay: r.metrics?.expectedDelayMin || 0,
  }));

  const handleControlChange = (key, value) => {
    setScenarioControl(key, value, selectedRoute?.vehicle_id ?? null);
  };

  const handleSegmentChange = (segmentKey, patch) => {
    if (!selectedRoute) return;
    setSegmentOverride(selectedRoute.vehicle_id, segmentKey, patch);
  };

  const handleRecalculate = async () => {
    if (!selectedRoute) return;
    await runScenario(selectedRoute.vehicle_id);

    const sr = useRouteStore.getState().scenarioResultsByVehicleId[selectedRoute.vehicle_id]
      || useRouteStore.getState().scenarioResult;

    if (sr) {
      const beforeStops = sr.baseline_route || selectedRoute.stops || [];
      const afterStops = sr.scenario_route || selectedRoute.stops || [];
      requestAgentExplanation({
        route_id: selectedRoute.vehicle_id,
        route_state: currentLifecycle,
        scenario_conditions: activeControls,
        before_metrics: { expected_delay_min: selectedRoute.metrics?.expectedDelayMin || 0 },
        after_metrics: { expected_delay_min: (selectedRoute.metrics?.expectedDelayMin || 0) + (sr.delta?.expected_delay_min || 0) },
        stop_order_before: beforeStops.map((s) => s.stop_name || s.stop_id || ''),
        stop_order_after: afterStops.map((s) => s.stop_name || s.stop_id || ''),
        user_question: 'Why was the route changed?',
      });
    }
  };

  const handleApplyRecommendation = async () => {
    if (!selectedRoute) return;
    await applyLiveRecommendation(selectedRoute.vehicle_id);
  };

  const handleKeepCurrentRoute = () => {
    if (!selectedRoute) return;
    resetScenario(selectedRoute.vehicle_id);
    useRouteStore.getState().setFeedback('Recommendation rejected. Courier continues on current route.', 'info');
  };

  const handleStartSim = () => {
    startSimulation();
  };

  if (loading) return <LoadingState text="Loading route data" />;
  if (error) return <ErrorState message={error} onRetry={forceFetchData} />;
  if (!selectedRoute) return <ErrorState message="No route available. Run a dispatch first." onRetry={forceFetchData} />;

  const plannedStops = selectedRoute.stops || [];
  const baselineStops = activeScenarioResult?.baseline_route || plannedStops;
  const recommendedStops = activeScenarioResult?.scenario_route || [];

  // Validation for recommended route
  const recommendationValid = !hasRecommendation || (() => {
    const pIds = new Set(plannedStops.map((s) => s.stop_id || s.stop_name).filter(Boolean));
    const rIds = new Set(recommendedStops.map((s) => s.stop_id || s.stop_name).filter(Boolean));
    return pIds.size === rIds.size && [...pIds].every((id) => rIds.has(id));
  })();

  return (
    <div className="dashboard-container">
      <div className="dashboard-shell">
        <PageHeader
          eyebrow="Live monitor · After dispatch"
          title="Live Monitor & Recommendations"
          subtitle="Monitor courier progress, apply conditions, and get AI-grounded recommendations."
        >
          <div className="header-metrics">
            <MetricCard label="Route" value={selectedRoute.courierName} />
            <MetricCard label="Stops" value={selectedRoute.metrics?.stopCount || 0} />
            <MetricCard
              label="Status"
              value={isActive ? 'In Progress' : currentLifecycle}
              tone={isActive ? 'success' : 'warning'}
            />
            <MetricCard
              label="Tracking"
              value={simulationRunning ? 'Live' : 'Stopped'}
              tone={simulationRunning ? 'success' : 'neutral'}
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

        {scenarioError ? <div className="inline-error">{scenarioError}</div> : null}

        {/* Simulation status */}
        {isActive && !simulationRunning && (
          <div className="info-banner info-banner--amber">
            Courier is dispatched but live tracking is not running.
            <button className="control-btn control-btn--primary" style={{ marginLeft: 'auto', padding: '0.4rem 0.8rem' }} onClick={handleStartSim}>
              Start Live Tracking
            </button>
          </div>
        )}

        {simulationRunning && (
          <div className="info-banner info-banner--green">
            <span className="live-dot" style={{ marginRight: '0.5rem' }} /> Courier simulation is running on the active route.
          </div>
        )}

        <div className="page-grid page-grid--scenario">
          {/* Left: Condition Controls */}
          <section className="page-card page-card--scroll">
            <span className="panel-kicker">Condition setup</span>
            <h2>Route & conditions</h2>
            <RoutePicker routes={routeList} selectedId={selectedCourierId} onChange={setSelectedCourier} />

            <div style={{ marginTop: '1rem' }}>
              <div className="section-title-row">
                <h2>Global conditions</h2>
                <button className="control-btn control-btn--neutral" onClick={handleKeepCurrentRoute}>Reset</button>
              </div>

              <div className="info-banner info-banner--blue" style={{ marginTop: '0.5rem', marginBottom: '0.8rem', fontSize: '0.78rem' }}>
                <strong>How conditions work:</strong> Global conditions affect the full route. Segment overrides (right panel) affect only selected road sections and <strong>take priority</strong>.
              </div>

              <div className="condition-controls">
                <label className="field-card">
                  <span>Weather type</span>
                  <select value={activeControls?.weather_condition || 'clear'}
                    onChange={(e) => handleControlChange('weather_condition', e.target.value)}>
                    {WEATHER_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                </label>

                {SCENARIO_SLIDERS.map((item) => {
                  const value = Number(activeControls?.[item.key] || 0);
                  return (
                    <label className="slider-card" key={item.key}>
                      <span><strong>{item.label}</strong><small>{item.help}</small></span>
                      <b>{item.format ? item.format(value) : `${value}/100`}</b>
                      <input type="range" min={item.min} max={item.max} value={value}
                        onChange={(e) => handleControlChange(item.key, Number(e.target.value))} />
                    </label>
                  );
                })}

                <label className="toggle-card">
                  <input type="checkbox" checked={Boolean(activeControls?.conservative_mode)}
                    onChange={(e) => handleControlChange('conservative_mode', e.target.checked)} />
                  <span>
                    <strong>Conservative mode</strong>
                    <small>Use worst-case (P90) delay estimates.</small>
                  </span>
                </label>
              </div>
            </div>

            <button className="control-btn control-btn--primary control-btn--full"
              onClick={handleRecalculate} disabled={scenarioLoading}>
              {scenarioLoading ? 'Recalculating...' : 'Recalculate Live Recommendation'}
            </button>
          </section>

          {/* Center: Map */}
          <section className="page-card page-card--map">
            <div className="section-title-row">
              <div>
                <span className="panel-kicker">{hasRecommendation ? 'Active vs Recommended Route' : 'Active Dispatch Route'}</span>
                <h2>{hasRecommendation ? 'Recommendation comparison' : 'Current route'}</h2>
              </div>
              {simulationRunning && (
                <span className="live-pill live-pill--on"><span className="live-dot" /> Live</span>
              )}
            </div>

            {/* Legend */}
            <div className="route-legend">
              <span><span className="legend-swatch legend-swatch--primary" />{hasRecommendation ? 'Recommended Route' : 'Active Dispatch Route'}</span>
              {hasRecommendation && (
                <span><span className="legend-swatch legend-swatch--dashed" />Active Dispatch Route (baseline)</span>
              )}
            </div>

            <RouteMetricGrid route={selectedRoute} scenarioResult={activeScenarioResult} />

            {hasRecommendation && (
              <RouteValidationBanner plannedStops={plannedStops} resultStops={recommendedStops} label="Recommended" />
            )}

            <div className="map-frame map-frame--large">
              <MapViewer
                routes={routes}
                selectedCourierId={selectedCourierId}
                liveCouriers={liveCourierArray}
                pendingSuggestions={pendingSuggestions}
                handleSuggestionDecision={handleSuggestionDecision}
                scenarioResult={activeScenarioResult}
                showScenario={Boolean(activeScenarioResult)}
                showAlternatives={false}
                showLiveCouriers={simulationRunning}
              />
            </div>
          </section>

          {/* Right: Segment Editor */}
          <aside className="page-card page-card--scroll">
            <SegmentEditor segments={segments} onChange={handleSegmentChange} />
          </aside>
        </div>

        {/* Results section */}
        {hasRecommendation && (
          <div className="responsive-results-grid">
            <section className="page-card">
              <span className="panel-kicker">Stop order comparison</span>
              <h2>Before vs after ({baselineStops.length} → {recommendedStops.length} stops)</h2>
              <RouteOrderComparison baseline={baselineStops} scenario={activeScenarioResult} />
            </section>
            <section className="page-card">
              <span className="panel-kicker">Recommendation explanation</span>
              <h2>What changed and why</h2>

              {activeScenarioResult?.delta ? (
                <div className="metric-grid" style={{ marginBottom: '1rem' }}>
                  <MetricCard label="Travel delta" value={`${activeScenarioResult.delta.travel_time_min != null ? (activeScenarioResult.delta.travel_time_min > 0 ? '+' : '') + activeScenarioResult.delta.travel_time_min : '--'} min`} />
                  <MetricCard label="Delay delta" value={`${activeScenarioResult.delta.expected_delay_min != null ? (activeScenarioResult.delta.expected_delay_min > 0 ? '+' : '') + activeScenarioResult.delta.expected_delay_min : '--'} min`} />
                  <MetricCard label="Order changed" value={activeScenarioResult.order_changed ? 'Yes' : 'No'} tone={activeScenarioResult.order_changed ? 'warning' : 'success'} />
                </div>
              ) : null}

              <AgentExplanationPanel
                explanation={agentExplanation}
                loading={agentExplanationLoading}
                error={agentExplanationError}
              />

              {/* Apply / Reject buttons */}
              <div className="responsive-btn-row" style={{ marginTop: '1rem' }}>
                <button
                  className="control-btn dispatch-btn"
                  onClick={handleApplyRecommendation}
                  disabled={!recommendationValid}
                  title={!recommendationValid ? 'Route validation failed. Cannot apply.' : ''}
                  style={{ flex: 1 }}
                >
                  Apply Recommendation
                </button>
                <button
                  className="control-btn control-btn--neutral"
                  onClick={handleKeepCurrentRoute}
                  style={{ flex: 1 }}
                >
                  Keep Current Route
                </button>
              </div>
            </section>
          </div>
        )}
      </div>
    </div>
  );
}
