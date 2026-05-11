import React, { useEffect, useMemo, useCallback } from 'react';
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

function buildSegments(stops, overrides = {}) {
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
        traffic_density: 0, accident_severity: 0, road_closure: false,
        weather_severity: 0, speed_reduction: 0, extra_delay_min: 0,
        risk_level: 'low', priority: 50, road_type: nextStop.road_type || 'urban',
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
    completedStopIdsByVehicle, completedStopVersion,
  } = useRouteStore();

  useEffect(() => { fetchData(); }, [fetchData]);
  useEffect(() => {
    if (selectedCourierId !== null) loadLifecycleState(selectedCourierId);
  }, [selectedCourierId, loadLifecycleState]);

  const liveCourierArray = useMemo(() => Object.values(liveCouriers || {}), [liveCouriers]);
  const selectedRoute = routes.find((r) => r.vehicle_id === selectedCourierId) || routes[0] || null;
  const currentLifecycle = selectedRoute ? routeLifecycleByVehicleId[selectedRoute.vehicle_id]?.status || 'planned' : 'planned';
  const isActive = currentLifecycle === 'dispatched' || currentLifecycle === 'in_progress';

  // Single source of truth: completedStopIdsByVehicle — version triggers re-render
  const completedStopIds = useMemo(
    () => (selectedRoute ? completedStopIdsByVehicle[selectedRoute.vehicle_id] || new Set() : new Set()),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectedRoute, completedStopIdsByVehicle, completedStopVersion]
  );

  const allStops = useMemo(() => selectedRoute?.stops || [], [selectedRoute?.stops]);
  const completedStops = useMemo(() => allStops.filter((s) => completedStopIds.has(String(s.stop_id))), [allStops, completedStopIds]);
  const remainingStops = useMemo(() => allStops.filter((s) => !completedStopIds.has(String(s.stop_id))), [allStops, completedStopIds]);
  const totalStopCount = completedStopIds.size + remainingStops.length;
  const nextStop = remainingStops[0] || null;
  const allRouteCompleted = isActive && remainingStops.length === 0 && completedStopIds.size > 0;

  const activeControls = useMemo(
    () => (selectedRoute ? scenarioControlsByVehicleId[selectedRoute.vehicle_id] || scenarioControls : scenarioControls),
    [selectedRoute, scenarioControlsByVehicleId, scenarioControls]
  );
  const activeOverrides = useMemo(
    () => (selectedRoute ? segmentOverridesByVehicleId[selectedRoute.vehicle_id] || {} : {}),
    [selectedRoute, segmentOverridesByVehicleId]
  );
  const activeScenarioResult = selectedRoute
    ? scenarioResultsByVehicleId[selectedRoute.vehicle_id]
      || (scenarioResult?.vehicleId === selectedRoute.vehicle_id ? scenarioResult : null)
    : scenarioResult;

  const hasRecommendation = Boolean(activeScenarioResult?.scenario_route?.length);

  // Segments based on remaining stops ONLY — hidden when all completed
  const segments = useMemo(
    () => (remainingStops.length >= 2 ? buildSegments(remainingStops, activeOverrides) : []),
    [remainingStops, activeOverrides]
  );

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

  const handleControlChange = (key, value) => {
    setScenarioControl(key, value, selectedRoute?.vehicle_id ?? null);
  };

  const handleSegmentChange = (segmentKey, patch) => {
    if (!selectedRoute) return;
    setSegmentOverride(selectedRoute.vehicle_id, segmentKey, patch);
  };

  const handleRecalculate = useCallback(async () => {
    if (!selectedRoute) return;
    await runScenario(selectedRoute.vehicle_id);

    // Request AI explanation asynchronously — do NOT block recommendation display
    const sr = useRouteStore.getState().scenarioResultsByVehicleId[selectedRoute.vehicle_id]
      || useRouteStore.getState().scenarioResult;

    if (sr) {
      requestAgentExplanation({
        route_id: selectedRoute.vehicle_id,
        route_state: currentLifecycle,
        scenario_conditions: activeControls,
        before_metrics: { expected_delay_min: selectedRoute.metrics?.expectedDelayMin || 0 },
        after_metrics: { expected_delay_min: (selectedRoute.metrics?.expectedDelayMin || 0) + (sr.delta?.expected_delay_min || 0) },
        stop_order_before: remainingStops.map((s) => s.stop_name || s.stop_id || ''),
        stop_order_after: (sr.scenario_route || []).map((s) => s.stop_name || s.stop_id || ''),
        user_question: 'Why was the route changed?',
      }).catch(() => { /* AI explanation is supplementary */ });
    }
  }, [selectedRoute, currentLifecycle, activeControls, remainingStops, runScenario, requestAgentExplanation]);

  const handleApplyRecommendation = async () => {
    if (!selectedRoute) return;
    await applyLiveRecommendation(selectedRoute.vehicle_id);
  };

  const handleKeepCurrentRoute = () => {
    if (!selectedRoute) return;
    resetScenario(selectedRoute.vehicle_id);
    useRouteStore.getState().setFeedback('Recommendation rejected. Courier continues on current route.', 'info');
  };

  if (loading) return <LoadingState text="Loading route data" />;
  if (error) return <ErrorState message={error} onRetry={forceFetchData} />;
  if (!selectedRoute) return <ErrorState message="No route available." onRetry={forceFetchData} />;

  const recommendedStops = activeScenarioResult?.scenario_route || [];

  // Strict validation: recommendation must contain exactly the remaining stops
  const recommendationValid = !hasRecommendation || (() => {
    const rIds = new Set(remainingStops.map((s) => String(s.stop_id)).filter(Boolean));
    const sIds = new Set(recommendedStops.map((s) => String(s.stop_id || s.stop_name)).filter(Boolean));
    // Check both directions: no missing AND no unexpected stops
    const allPresent = [...rIds].every((id) => sIds.has(id));
    const noExtra = [...sIds].every((id) => rIds.has(id));
    return allPresent && noExtra && rIds.size === sIds.size;
  })();
  const validationMissing = hasRecommendation ? remainingStops.filter((s) => !new Set(recommendedStops.map((r) => String(r.stop_id || r.stop_name))).has(String(s.stop_id))) : [];
  const validationExtra = hasRecommendation ? recommendedStops.filter((s) => !new Set(remainingStops.map((r) => String(r.stop_id))).has(String(s.stop_id || s.stop_name))) : [];

  return (
    <div className="dashboard-container">
      <div className="dashboard-shell">
        <PageHeader
          eyebrow="Live monitor · After dispatch"
          title="Live Monitor & Recommendations"
          subtitle={isActive
            ? `Tracking ${selectedRoute.courierName}. ${completedStopIds.size}/${totalStopCount} stops completed.`
            : 'Dispatch a route first to begin live monitoring.'}
        >
          <div className="header-metrics">
            <MetricCard label="Route" value={selectedRoute.courierName} />
            <MetricCard label="Progress" value={`${completedStopIds.size}/${totalStopCount}`} tone={completedStopIds.size > 0 ? 'success' : 'neutral'} />
            <MetricCard label="Remaining" value={remainingStops.length} />
            <MetricCard label="Tracking" value={simulationRunning ? 'Live' : 'Stopped'} tone={simulationRunning ? 'success' : 'neutral'} />
          </div>
        </PageHeader>

        {feedbackMessage && (
          <div className={`feedback-banner feedback-banner--${feedbackType}`}>
            <span>{feedbackMessage}</span>
            <button onClick={clearFeedback} aria-label="Dismiss">×</button>
          </div>
        )}

        {scenarioError ? <div className="inline-error">{scenarioError}</div> : null}

        {isActive && !simulationRunning && (
          <div className="info-banner info-banner--amber">
            Courier dispatched but live tracking not running.
            <button className="control-btn control-btn--primary" style={{ marginLeft: 'auto', padding: '0.4rem 0.8rem' }} onClick={() => startSimulation(selectedRoute.vehicle_id)}>Start Live Tracking</button>
          </div>
        )}
        {simulationRunning && (
          <div className="info-banner info-banner--green">
            <span className="live-dot" style={{ marginRight: '0.5rem' }} /> Courier is live. Stops marked as visited automatically.
          </div>
        )}

        {/* Courier progress panel */}
        {isActive && (
          <section className="page-card">
            <div className="courier-progress-row">
              <div className="progress-col">
                <span className="panel-kicker">Courier progress</span>
                <div className="progress-bar-container" style={{ marginTop: '0.4rem' }}>
                  <div className="progress-bar-fill" style={{ width: totalStopCount > 0 ? `${(completedStopIds.size / totalStopCount) * 100}%` : '0%' }} />
                </div>
                <span className="progress-text">{completedStopIds.size} / {totalStopCount} stops visited</span>
              </div>

              {nextStop && (
                <div className="next-stop-card">
                  <span className="panel-kicker">Next stop</span>
                  <strong>{nextStop.displaySequence || ''}. {nextStop.stop_name || nextStop.stop_id}</strong>
                </div>
              )}

              <div className="stops-lists-row">
                {completedStops.length > 0 && (
                  <div className="stops-list-mini">
                    <span className="panel-kicker" style={{ color: '#10b981' }}>Completed ({completedStopIds.size})</span>
                    {completedStops.map((s, i) => (
                      <span key={s.stop_id} className="stop-chip stop-chip--completed">✓ {i + 1} — {s.stop_id} — Completed</span>
                    ))}
                  </div>
                )}
                {allRouteCompleted ? (
                  <div className="stops-list-mini">
                    <span className="panel-kicker" style={{ color: '#10b981' }}>All stops delivered</span>
                  </div>
                ) : (
                  <div className="stops-list-mini">
                    <span className="panel-kicker">Remaining ({remainingStops.length})</span>
                    {remainingStops.map((s, i) => (
                      <span key={s.stop_id} className={`stop-chip ${i === 0 ? 'stop-chip--next' : ''}`}>
                        {completedStops.length + i + 1} — {s.stop_id} — {i === 0 ? 'Next' : 'Pending'}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </section>
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
                <strong>How conditions work:</strong> Global conditions affect the full route. Segment overrides affect only selected remaining road sections and <strong>take priority</strong>.
              </div>

              <div className="condition-controls">
                <label className="field-card">
                  <span>Weather type</span>
                  <select value={activeControls?.weather_condition || 'clear'} onChange={(e) => handleControlChange('weather_condition', e.target.value)}>
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
                  <input type="checkbox" checked={Boolean(activeControls?.conservative_mode)} onChange={(e) => handleControlChange('conservative_mode', e.target.checked)} />
                  <span><strong>Conservative mode</strong><small>Use worst-case (P90) delay estimates.</small></span>
                </label>
              </div>
            </div>

            <button className="control-btn control-btn--primary control-btn--full"
              onClick={handleRecalculate} disabled={scenarioLoading || allRouteCompleted}>
              {allRouteCompleted
                ? 'All stops completed'
                : scenarioLoading
                  ? 'Recalculating...'
                  : `Recalculate for ${remainingStops.length} remaining stops`}
            </button>
          </section>

          {/* Center: Map */}
          <section className="page-card page-card--map">
            <div className="section-title-row">
              <div>
                <span className="panel-kicker">{hasRecommendation ? 'Active vs Recommended Route' : 'Active Dispatch Route'}</span>
                <h2>{hasRecommendation ? `Recommendation: ${recommendedStops.length} remaining stops` : selectedRoute.courierName}</h2>
              </div>
              {simulationRunning && <span className="live-pill live-pill--on"><span className="live-dot" /> Live</span>}
            </div>

            <RouteMetricGrid route={selectedRoute} scenarioResult={activeScenarioResult} />

            {hasRecommendation && (
              <RouteValidationBanner plannedStops={remainingStops} resultStops={recommendedStops} label="Recommended" />
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
                showLiveCouriers={simulationRunning}
                completedStopIds={completedStopIds}
                nextStopId={nextStop?.stop_id}
                legendContext="monitor"
              />
            </div>
          </section>

          {/* Right: Segment Editor for remaining stops only */}
          <aside className="page-card page-card--scroll">
            <span className="panel-kicker">Segment overrides</span>
            {allRouteCompleted ? (
              <div className="info-banner info-banner--green" style={{ fontSize: '0.82rem' }}>
                All stops completed. No remaining road sections to configure.
              </div>
            ) : (
              <>
                {completedStops.length > 0 && (
                  <div className="info-banner info-banner--blue" style={{ fontSize: '0.76rem', marginBottom: '0.5rem' }}>
                    Segments are based on the <strong>{remainingStops.length} remaining stops</strong> ({segments.length} editable segments). Completed road sections are not reoptimized.
                  </div>
                )}
                {segments.length > 0 ? (
                  <SegmentEditor segments={segments} onChange={handleSegmentChange} />
                ) : (
                  <div style={{ color: '#6b7280', fontSize: '0.82rem', padding: '1rem 0' }}>
                    Not enough remaining stops to create segments.
                  </div>
                )}
              </>
            )}
          </aside>
        </div>

        {/* Results section */}
        {hasRecommendation && (
          <div className="responsive-results-grid">
            <section className="page-card">
              <span className="panel-kicker">Stop order comparison</span>
              <h2>Remaining: before vs after ({remainingStops.length} → {recommendedStops.length})</h2>
              <RouteOrderComparison baseline={remainingStops} scenario={activeScenarioResult} />
            </section>
            <section className="page-card">
              <span className="panel-kicker">Recommendation explanation</span>
              <h2>What changed and why</h2>

              {activeScenarioResult?.delta && (
                <div className="metric-grid" style={{ marginBottom: '1rem' }}>
                  <MetricCard label="Travel delta" value={`${activeScenarioResult.delta.travel_time_min != null ? (activeScenarioResult.delta.travel_time_min > 0 ? '+' : '') + activeScenarioResult.delta.travel_time_min : '--'} min`} />
                  <MetricCard label="Delay delta" value={`${activeScenarioResult.delta.expected_delay_min != null ? (activeScenarioResult.delta.expected_delay_min > 0 ? '+' : '') + activeScenarioResult.delta.expected_delay_min : '--'} min`} />
                  <MetricCard label="Order changed" value={activeScenarioResult.order_changed ? 'Yes' : 'No'} tone={activeScenarioResult.order_changed ? 'warning' : 'success'} />
                </div>
              )}

              {/* Deterministic explanation always visible immediately */}
              {activeScenarioResult?.deterministic_summary && !agentExplanation && (
                <div className="info-banner info-banner--blue" style={{ marginBottom: '0.5rem', fontSize: '0.82rem' }}>
                  <strong>Deterministic analysis:</strong> {activeScenarioResult.deterministic_summary || 'Route reoptimized under updated conditions.'}
                </div>
              )}

              <AgentExplanationPanel
                explanation={agentExplanation}
                loading={agentExplanationLoading}
                error={agentExplanationError}
              />

              {agentExplanationLoading && (
                <div style={{ fontSize: '0.78rem', color: '#6b7280', marginTop: '0.3rem' }}>
                  <span className="live-dot" style={{ marginRight: '0.4rem', display: 'inline-block', width: 8, height: 8, borderRadius: '50%', backgroundColor: '#3b82f6', animation: 'pulse 1.5s infinite' }} />
                  Optional AI assistant note is being prepared. The deterministic backend explanation is already available.
                </div>
              )}

              <div className="responsive-btn-row" style={{ marginTop: '1rem' }}>
                <button
                  className="control-btn dispatch-btn"
                  onClick={handleApplyRecommendation}
                  disabled={!recommendationValid}
                  title={!recommendationValid ? `Route validation failed. ${validationMissing.length > 0 ? `Missing: ${validationMissing.map(s => s.stop_id).join(', ')}. ` : ''}${validationExtra.length > 0 ? `Unexpected: ${validationExtra.map(s => s.stop_id || s.stop_name).join(', ')}.` : ''}` : ''}
                  style={{ flex: 1, opacity: recommendationValid ? 1 : 0.5 }}
                >
                  {recommendationValid ? 'Apply Recommendation' : 'Apply Blocked — Validation Failed'}
                </button>
                <button className="control-btn control-btn--neutral" onClick={handleKeepCurrentRoute} style={{ flex: 1 }}>
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
