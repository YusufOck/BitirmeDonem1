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
  } = useRouteStore();

  useEffect(() => { fetchData(); }, [fetchData]);

  useEffect(() => {
    if (selectedCourierId !== null) {
      loadLifecycleState(selectedCourierId);
    }
  }, [selectedCourierId, loadLifecycleState]);

  const liveCourierArray = useMemo(() => Object.values(liveCouriers || {}), [liveCouriers]);
  const selectedRoute = routes.find((r) => r.vehicle_id === selectedCourierId) || routes[0] || null;
  const currentLifecycle = selectedRoute ? routeLifecycleByVehicleId[selectedRoute.vehicle_id]?.status || 'planned' : 'planned';

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

    // After recalculation, request agent explanation
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
  };

  if (loading) return <LoadingState text="Loading route data" />;
  if (error) return <ErrorState message={error} onRetry={forceFetchData} />;
  if (!selectedRoute) return <ErrorState message="No route available. Run a dispatch first." onRetry={forceFetchData} />;

  const plannedStops = selectedRoute.stops || [];
  const baselineStops = activeScenarioResult?.baseline_route || plannedStops;
  const recommendedStops = activeScenarioResult?.scenario_route || [];

  return (
    <div className="dashboard-container">
      <div className="dashboard-shell">
        <PageHeader
          eyebrow="Live dispatch monitor"
          title="Live Monitor & Recommendations"
          subtitle="Apply traffic, weather, accident, or closure conditions and get AI-grounded recommendations."
        >
          <div className="header-metrics">
            <MetricCard label="Route" value={selectedRoute.courierName} />
            <MetricCard label="Stops" value={selectedRoute.metrics?.stopCount || 0} />
            <MetricCard
              label="Status"
              value={currentLifecycle === 'dispatched' || currentLifecycle === 'in_progress' ? 'Active Dispatch' : currentLifecycle}
              tone={currentLifecycle === 'dispatched' || currentLifecycle === 'in_progress' ? 'success' : 'warning'}
            />
            <MetricCard
              label="Recommendation"
              value={hasRecommendation ? 'Ready' : 'Not run'}
              tone={hasRecommendation ? 'success' : 'neutral'}
            />
          </div>
        </PageHeader>

        {scenarioError ? <div className="inline-error">{scenarioError}</div> : null}

        <div className="page-grid page-grid--scenario">
          {/* Left: Condition Controls */}
          <section className="page-card page-card--scroll">
            <span className="panel-kicker">Condition setup</span>
            <h2>Select route and apply conditions</h2>
            <RoutePicker routes={routeList} selectedId={selectedCourierId} onChange={setSelectedCourier} />

            <div style={{ marginTop: '1rem' }}>
              <div className="section-title-row">
                <h2>Global conditions</h2>
                <button className="control-btn control-btn--neutral" onClick={handleKeepCurrentRoute}>Reset</button>
              </div>

              <div style={{ padding: '0.5rem 0.8rem', backgroundColor: '#eff6ff', borderRadius: '6px', border: '1px solid #bfdbfe', fontSize: '0.8rem', color: '#1e40af', marginBottom: '0.8rem' }}>
                <strong>How conditions work:</strong> Global conditions affect the full route. Segment overrides (right panel) affect only selected road sections and <strong>take priority</strong> over global values.
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
              {scenarioLoading ? 'Recalculating...' : 'Recalculate Recommendation'}
            </button>
          </section>

          {/* Center: Map */}
          <section className="page-card page-card--map">
            <div className="section-title-row">
              <div>
                <span className="panel-kicker">{hasRecommendation ? 'Active vs Recommended Route' : 'Active Dispatch Route'}</span>
                <h2>{hasRecommendation ? 'Recommendation comparison' : 'Current route'}</h2>
              </div>
              <small>{hasRecommendation ? 'Recommendation ready' : 'No recommendation yet'}</small>
            </div>

            <div style={{ display: 'flex', gap: '1.5rem', padding: '0.5rem 0', fontSize: '0.8rem', color: '#6b7280' }}>
              <span><span style={{ display: 'inline-block', width: 20, height: 3, backgroundColor: '#3b82f6', marginRight: 6, verticalAlign: 'middle' }} />{hasRecommendation ? 'Recommended route' : 'Active route'}</span>
              {hasRecommendation && (
                <span><span style={{ display: 'inline-block', width: 20, height: 3, backgroundColor: '#9ca3af', marginRight: 6, verticalAlign: 'middle', borderTop: '2px dashed #9ca3af' }} />Current active route (baseline)</span>
              )}
            </div>

            <RouteMetricGrid route={selectedRoute} scenarioResult={activeScenarioResult} />

            {/* Route validation */}
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
                showLiveCouriers={false}
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
          <div className="page-grid" style={{ gridTemplateColumns: '1fr 1fr', marginTop: '0.85rem' }}>
            <section className="page-card">
              <span className="panel-kicker">Stop order comparison</span>
              <h2>Before vs after ({baselineStops.length} → {recommendedStops.length} stops)</h2>
              <RouteOrderComparison baseline={baselineStops} scenario={activeScenarioResult} />
            </section>
            <section className="page-card">
              <span className="panel-kicker">Recommendation explanation</span>
              <h2>What changed and why</h2>

              {/* Delta metrics */}
              {activeScenarioResult?.delta ? (
                <div className="metric-grid" style={{ marginBottom: '1rem' }}>
                  <MetricCard label="Travel delta" value={`${activeScenarioResult.delta.travel_time_min != null ? (activeScenarioResult.delta.travel_time_min > 0 ? '+' : '') + activeScenarioResult.delta.travel_time_min : '--'} min`} />
                  <MetricCard label="Delay delta" value={`${activeScenarioResult.delta.expected_delay_min != null ? (activeScenarioResult.delta.expected_delay_min > 0 ? '+' : '') + activeScenarioResult.delta.expected_delay_min : '--'} min`} />
                  <MetricCard label="Order changed" value={activeScenarioResult.order_changed ? 'Yes' : 'No'} tone={activeScenarioResult.order_changed ? 'warning' : 'success'} />
                </div>
              ) : null}

              {/* Agent Explanation */}
              <AgentExplanationPanel
                explanation={agentExplanation}
                loading={agentExplanationLoading}
                error={agentExplanationError}
              />

              {/* Apply / Reject buttons */}
              <div style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem' }}>
                <button
                  className="control-btn control-btn--primary"
                  onClick={handleApplyRecommendation}
                  style={{ flex: 1, backgroundColor: '#10b981', borderColor: '#10b981' }}
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
