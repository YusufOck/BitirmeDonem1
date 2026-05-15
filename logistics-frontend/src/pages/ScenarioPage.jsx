import React, { useEffect, useMemo, useCallback, useState } from 'react';
import MapViewer from '../features/dashboard/components/MapViewer';
import { useRouteStore } from '../store/useRouteStore';
import {
  PageHeader, MetricCard, RouteMetricGrid, RoutePicker,
  RouteValidationBanner,
  AgentExplanationPanel, LoadingState, ErrorState,
  RecommendationChangeDetails,
} from '../components/shared';

const CONDITION_SLIDERS = [
  { key: 'traffic_density', label: 'Traffic density', help: 'road congestion', min: 0, max: 100 },
  { key: 'accident_severity', label: 'Accident severity', help: 'incident severity', min: 0, max: 100 },
  { key: 'weather_severity', label: 'Weather severity', help: 'rain, wind, fog, snow', min: 0, max: 100 },
  { key: 'road_disruption', label: 'Road disruption', help: 'blocked or slow roads', min: 0, max: 100 },
  { key: 'package_load', label: 'Package load', help: 'vehicle workload', min: 0, max: 100 },
  {
    key: 'dispatch_hour',
    label: 'Dispatch hour',
    help: 'time-of-day impact',
    min: 0,
    max: 23,
    format: (v) => `${String(v).padStart(2, '0')}:00`,
  },
];

const WEATHER_OPTIONS = ['clear', 'cloudy', 'wind', 'fog', 'rain', 'snow'];

const round = (value, digits = 1) => {
  const number = Number(value);
  return Number.isFinite(number) ? Number(number.toFixed(digits)) : 0;
};

// Build segment key used by the store
const makeSegmentKey = (fromStop, toStop) => `${fromStop.stop_id}-${toStop.stop_id}`;

export default function ScenarioPage() {
  const {
    loading, error, fetchData, forceFetchData,
    routes, selectedCourierId, setSelectedCourier,
    scenarioControls, scenarioControlsByVehicleId,
    scenarioResultsByVehicleId,
    scenarioResult, scenarioLoading, scenarioError,
    setScenarioControl, setSegmentOverride, resetScenario, runScenario,
    segmentOverridesByVehicleId,
    liveCouriers, pendingSuggestions, handleSuggestionDecision,
    routeLifecycleByVehicleId, loadLifecycleState,
    agentExplanation, agentExplanationLoading, agentExplanationError,
    requestAgentExplanation, applyLiveRecommendation,
    feedbackMessage, feedbackType, clearFeedback,
    simulationRunning, startSimulation,
    completedStopIdsByVehicle, completedStopVersion,
  } = useRouteStore();

  // Local UI state for scope selector
  const [conditionScope, setConditionScope] = useState('route'); // 'route' | 'segment'
  const [selectedSegmentKey, setSelectedSegmentKey] = useState('');

  useEffect(() => { fetchData(); }, [fetchData]);
  useEffect(() => {
    if (selectedCourierId !== null) loadLifecycleState(selectedCourierId);
  }, [selectedCourierId, loadLifecycleState]);

  const liveCourierArray = useMemo(() => Object.values(liveCouriers || {}), [liveCouriers]);
  const selectedRoute = routes.find((r) => r.vehicle_id === selectedCourierId) || routes[0] || null;
  const currentLifecycle = selectedRoute
    ? routeLifecycleByVehicleId[selectedRoute.vehicle_id]?.status || 'planned'
    : 'planned';
  const isActive = currentLifecycle === 'dispatched' || currentLifecycle === 'in_progress';

  const completedStopIds = useMemo(
    () => (selectedRoute ? completedStopIdsByVehicle[selectedRoute.vehicle_id] || new Set() : new Set()),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectedRoute, completedStopIdsByVehicle, completedStopVersion],
  );

  const allStops = useMemo(() => selectedRoute?.stops || [], [selectedRoute?.stops]);
  const completedStops = useMemo(
    () => allStops.filter((s) => completedStopIds.has(String(s.stop_id))),
    [allStops, completedStopIds],
  );
  const remainingStops = useMemo(
    () => allStops.filter((s) => !completedStopIds.has(String(s.stop_id))),
    [allStops, completedStopIds],
  );

  const totalStopCount = completedStopIds.size + remainingStops.length;
  const nextStop = remainingStops[0] || null;
  const allRouteCompleted = isActive && remainingStops.length === 0 && completedStopIds.size > 0;

  // Build segment options from remaining stops (consecutive pairs)
  const segmentOptions = useMemo(() => {
    if (remainingStops.length < 2) return [];
    return remainingStops.slice(0, -1).map((stop, i) => {
      const next = remainingStops[i + 1];
      const key = makeSegmentKey(stop, next);
      const label = `${stop.stop_id} → ${next.stop_id}`;
      return { key, label, fromStop: stop, toStop: next };
    });
  }, [remainingStops]);

  // Derive effective segment key — auto-select first if none chosen yet
  const effectiveSegmentKey = useMemo(() => {
    if (conditionScope !== 'segment' || segmentOptions.length === 0) return '';
    if (selectedSegmentKey && segmentOptions.some((s) => s.key === selectedSegmentKey)) return selectedSegmentKey;
    return segmentOptions[0].key;
  }, [conditionScope, segmentOptions, selectedSegmentKey]);

  const activeControls = useMemo(
    () => (selectedRoute
      ? scenarioControlsByVehicleId[selectedRoute.vehicle_id] || scenarioControls
      : scenarioControls),
    [selectedRoute, scenarioControlsByVehicleId, scenarioControls],
  );

  // Current segment override values (for display when segment scope active)
  const activeSegmentOverride = useMemo(() => {
    if (!selectedRoute || !effectiveSegmentKey) return null;
    return segmentOverridesByVehicleId[selectedRoute.vehicle_id]?.[effectiveSegmentKey] || null;
  }, [selectedRoute, effectiveSegmentKey, segmentOverridesByVehicleId]);

  const activeScenarioResult = selectedRoute
    ? scenarioResultsByVehicleId[selectedRoute.vehicle_id]
      || (scenarioResult?.vehicleId === selectedRoute.vehicle_id ? scenarioResult : null)
    : scenarioResult;

  const hasRecommendation = Boolean(activeScenarioResult?.scenario_route?.length);

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

  // Handle condition value change — routes to global OR segment store based on scope
  const handleConditionChange = useCallback((key, value) => {
    if (conditionScope === 'route' || !effectiveSegmentKey) {
      setScenarioControl(key, value, selectedRoute?.vehicle_id ?? null);
    } else {
      const seg = segmentOptions.find((s) => s.key === effectiveSegmentKey);
      if (!seg || !selectedRoute) return;
      setSegmentOverride(selectedRoute.vehicle_id, effectiveSegmentKey, {
        from_stop_id: String(seg.fromStop.stop_id),
        to_stop_id: String(seg.toStop.stop_id),
        [key]: value,
      });
    }
  }, [conditionScope, effectiveSegmentKey, segmentOptions, selectedRoute, setScenarioControl, setSegmentOverride]);

  // Get displayed value for a condition key (global or segment-specific)
  const getDisplayValue = useCallback((key) => {
    if (conditionScope === 'segment' && activeSegmentOverride) {
      const segVal = activeSegmentOverride[key];
      if (segVal !== undefined && segVal !== null) return Number(segVal);
    }
    return Number(activeControls?.[key] ?? 0);
  }, [conditionScope, activeSegmentOverride, activeControls]);

  const handleRecalculate = useCallback(async () => {
    if (!selectedRoute) return;
    await runScenario(selectedRoute.vehicle_id);

    const sr = useRouteStore.getState().scenarioResultsByVehicleId[selectedRoute.vehicle_id]
      || useRouteStore.getState().scenarioResult;

    if (sr) {
      const beforeDelay = sr.optimization_delta?.current_operational_delay_min
        ?? sr.baseline_metrics?.expected_delay_min
        ?? remainingStops.reduce((sum, s) => sum + Number(s.expected_delay_min || 0), 0);
      const afterDelay = sr.optimization_delta?.optimized_operational_delay_min
        ?? sr.scenario_metrics?.expected_delay_min
        ?? (sr.scenario_route || []).reduce((sum, s) => sum + Number(s.expected_delay_min || 0), 0);

      requestAgentExplanation({
        route_id: selectedRoute.vehicle_id,
        route_state: currentLifecycle,
        scenario_conditions: activeControls,
        before_metrics: { expected_delay_min: beforeDelay },
        after_metrics: { expected_delay_min: afterDelay },
        stop_order_before: remainingStops.map((s) => s.stop_name || s.stop_id || ''),
        stop_order_after: (sr.scenario_route || []).map((s) => s.stop_name || s.stop_id || ''),
        user_question: 'Why is this recommendation useful, or why should we keep the current route?',
      }).catch(() => {});
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
  const recommendationAllowed = !hasRecommendation || activeScenarioResult?.recommendation_allowed !== false;
  const recommendationValid = !hasRecommendation || (() => {
    const rIds = new Set(remainingStops.map((s) => String(s.stop_id)).filter(Boolean));
    const sIds = new Set(recommendedStops.map((s) => String(s.stop_id || s.stop_name)).filter(Boolean));
    return recommendationAllowed && [...rIds].every((id) => sIds.has(id)) && [...sIds].every((id) => rIds.has(id)) && rIds.size === sIds.size;
  })();
  const applyBlocked = !recommendationValid || !recommendationAllowed;

  // ── Metric mapping: all three values from the SAME comparison basis ──────────
  // optimization_delta is keyed on "same_updated_conditions" — use it exclusively.
  const currentDelayRisk = round(
    activeScenarioResult?.optimization_delta?.current_operational_delay_min
    ?? activeScenarioResult?.schedule_comparison?.current_order?.total_operational_delay_min
    ?? activeScenarioResult?.baseline_metrics?.expected_delay_min
    ?? selectedRoute.metrics?.expectedDelayMin
    ?? 0,
  );
  const recommendedDelayRisk = round(
    activeScenarioResult?.optimization_delta?.optimized_operational_delay_min
    ?? activeScenarioResult?.schedule_comparison?.optimized_order?.total_operational_delay_min
    ?? activeScenarioResult?.scenario_metrics?.expected_delay_min
    ?? 0,
  );
  // Saving = pure delay difference (not route cost — route cost includes road duration + penalties)
  // This ensures: current - recommended = saving (always consistent math)
  const estimatedSaving = round(
    activeScenarioResult?.optimization_delta?.operational_delay_saved_min
    ?? (currentDelayRisk - recommendedDelayRisk),
  );

  const recommendationStatus = hasRecommendation
    ? (applyBlocked ? 'Blocked' : recommendationAllowed ? 'Ready' : 'Keep current')
    : 'None';
  const recommendationTone = hasRecommendation
    ? (applyBlocked || !recommendationAllowed ? 'warning' : 'success')
    : 'neutral';

  // Active weather/conservative come always from global controls
  const weatherValue = activeControls?.weather_condition ?? 'clear';
  const conservativeValue = Boolean(activeControls?.conservative_mode);

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
            <MetricCard label="Courier" value={selectedRoute.courierName} />
            <MetricCard label="Tracking" value={simulationRunning ? 'Live' : 'Stopped'} tone={simulationRunning ? 'success' : 'neutral'} />
            <MetricCard label="Progress" value={`${completedStopIds.size}/${totalStopCount}`} tone={completedStopIds.size > 0 ? 'success' : 'neutral'} />
            <MetricCard label="Remaining" value={remainingStops.length} />
            <MetricCard label="Current delay risk" value={`${round(currentDelayRisk)} min`} tone="warning" />
            <MetricCard label="Recommendation" value={recommendationStatus} tone={recommendationTone} />
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
            <button
              className="control-btn control-btn--primary"
              style={{ marginLeft: 'auto', padding: '0.4rem 0.8rem' }}
              onClick={() => startSimulation(selectedRoute.vehicle_id)}
            >
              Start Live Tracking
            </button>
          </div>
        )}

        {simulationRunning && (
          <div className="info-banner info-banner--green">
            <span className="live-dot" style={{ marginRight: '0.5rem' }} />
            Courier is live. Stops marked as visited automatically.
          </div>
        )}

        {isActive && (
          <section className="live-progress-strip">
            <div className="courier-progress-row">
              <div className="progress-col">
                <span className="panel-kicker">Courier progress</span>
                <div className="progress-bar-container" style={{ marginTop: '0.4rem' }}>
                  <div
                    className="progress-bar-fill"
                    style={{ width: totalStopCount > 0 ? `${(completedStopIds.size / totalStopCount) * 100}%` : '0%' }}
                  />
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
          {/* ── Left: Route & Conditions ── */}
          <section className="page-card page-card--scroll">
            <span className="panel-kicker">Condition setup</span>
            <h2>Route &amp; conditions</h2>

            <RoutePicker routes={routeList} selectedId={selectedCourierId} onChange={setSelectedCourier} />

            {/* ── A) Scope Selector ── */}
            <div className="scope-selector">
              <span className="panel-kicker" style={{ display: 'block', marginBottom: '0.5rem' }}>Apply conditions to</span>
              <div className="scope-options">
                <label className={`scope-option ${conditionScope === 'route' ? 'scope-option--active' : ''}`}>
                  <input
                    type="radio"
                    name="conditionScope"
                    value="route"
                    checked={conditionScope === 'route'}
                    onChange={() => setConditionScope('route')}
                  />
                  <span>Entire remaining route</span>
                </label>
                <label className={`scope-option ${conditionScope === 'segment' ? 'scope-option--active' : ''}`}>
                  <input
                    type="radio"
                    name="conditionScope"
                    value="segment"
                    checked={conditionScope === 'segment'}
                    onChange={() => setConditionScope('segment')}
                  />
                  <span>Selected segment</span>
                </label>
              </div>

              {/* ── B) Segment Dropdown ── */}
              {conditionScope === 'segment' && (
                <div style={{ marginTop: '0.65rem' }}>
                  {segmentOptions.length === 0 ? (
                    <div className="info-banner info-banner--amber" style={{ fontSize: '0.8rem' }}>
                      No segments available (need ≥2 remaining stops).
                    </div>
                  ) : (
                    <label className="field-card">
                      <span>Segment</span>
                      <select
                        value={effectiveSegmentKey}
                        onChange={(e) => setSelectedSegmentKey(e.target.value)}
                      >
                        {segmentOptions.map((seg) => (
                          <option key={seg.key} value={seg.key}>{seg.label}</option>
                        ))}
                      </select>
                    </label>
                  )}
                  {activeSegmentOverride && (
                    <div className="info-banner info-banner--blue" style={{ marginTop: '0.45rem', fontSize: '0.78rem' }}>
                      Segment override active — values below show this segment&apos;s settings.
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* ── C) Condition Controls ── */}
            <div className="condition-panel-body">
              <div className="section-title-row">
                <div>
                  <span className="panel-kicker">
                    {conditionScope === 'segment' && effectiveSegmentKey
                      ? `Conditions for: ${segmentOptions.find((s) => s.key === effectiveSegmentKey)?.label || 'segment'}`
                      : 'Global conditions'}
                  </span>
                  <h2>Route conditions</h2>
                </div>
                <button className="control-btn control-btn--neutral" onClick={handleKeepCurrentRoute}>Reset</button>
              </div>

              {conditionScope === 'route' && (
                <div className="info-banner info-banner--blue condition-note">
                  <strong>Entire route:</strong> conditions applied to all remaining stops.
                </div>
              )}
              {conditionScope === 'segment' && effectiveSegmentKey && (
                <div className="info-banner info-banner--blue condition-note">
                  <strong>Segment only:</strong> these conditions override this segment in the recalculation.
                </div>
              )}

              {/* Weather type — always global */}
              <div className="condition-controls condition-controls--compact" style={{ marginTop: '0.65rem' }}>
                <label className="field-card">
                  <span>Weather type <small style={{ color: '#94a3b8' }}>(global)</small></span>
                  <select
                    value={weatherValue}
                    onChange={(e) => setScenarioControl('weather_condition', e.target.value, selectedRoute?.vehicle_id ?? null)}
                  >
                    {WEATHER_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                </label>

                {/* All numeric sliders — routed to global or segment based on scope */}
                {CONDITION_SLIDERS.map((item) => {
                  const value = getDisplayValue(item.key);
                  return (
                    <label className="slider-card" key={item.key}>
                      <span>
                        <strong>{item.label}</strong>
                        <small>{item.help}</small>
                      </span>
                      <b>{item.format ? item.format(value) : `${value}/100`}</b>
                      <input
                        type="range"
                        min={item.min}
                        max={item.max}
                        value={value}
                        onChange={(e) => handleConditionChange(item.key, Number(e.target.value))}
                      />
                    </label>
                  );
                })}

                {/* Conservative mode — always global */}
                <label className="toggle-card">
                  <input
                    type="checkbox"
                    checked={conservativeValue}
                    onChange={(e) => setScenarioControl('conservative_mode', e.target.checked, selectedRoute?.vehicle_id ?? null)}
                  />
                  <span>
                    <strong>Conservative mode</strong>
                    <small>Use worst-case (P90) delay estimates. (global)</small>
                  </span>
                </label>
              </div>
            </div>

            <button
              className="control-btn control-btn--primary control-btn--full"
              onClick={handleRecalculate}
              disabled={scenarioLoading || allRouteCompleted}
            >
              {allRouteCompleted
                ? 'All stops completed'
                : scenarioLoading
                  ? 'Recalculating...'
                  : `Recalculate for ${remainingStops.length} remaining stops`}
            </button>
          </section>

          {/* ── Right: Map ── */}
          <section className="page-card page-card--map">
            <div className="section-title-row">
              <div>
                <span className="panel-kicker">
                  {hasRecommendation ? 'Active vs Recommended Route' : 'Active Dispatch Route'}
                </span>
                <h2>
                  {hasRecommendation
                    ? `Recommendation: ${recommendedStops.length} remaining stops`
                    : selectedRoute.courierName}
                </h2>
              </div>
              {simulationRunning && (
                <span className="live-pill live-pill--on"><span className="live-dot" /> Live</span>
              )}
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
        </div>

        {/* ── Recommendation Panel ── */}
        {hasRecommendation && (
          <section className="page-card" style={{ marginTop: '0.8rem' }}>
            <div className="section-title-row" style={{ marginBottom: '0.75rem' }}>
              <div>
                <span className="panel-kicker">Recommendation Available</span>
                <h2>Optimization Results &amp; Explanation</h2>
              </div>
            </div>

            {/* 1. Metric cards — all from the same comparison basis (optimization_delta) */}
            <div className="metric-grid" style={{ marginBottom: '1rem' }}>
              <MetricCard
                label="Current route delay (scenario)"
                value={`${currentDelayRisk} min`}
                tone="warning"
                subvalue="active route under updated conditions"
              />
              <MetricCard
                label="Recommended route delay"
                value={`${recommendedDelayRisk} min`}
                tone={recommendedDelayRisk < currentDelayRisk ? 'success' : 'neutral'}
                subvalue="recommended route under same conditions"
              />
              <MetricCard
                label="Delay saving"
                value={estimatedSaving > 0 ? `−${estimatedSaving} min` : estimatedSaving === 0 ? '0 min' : `+${Math.abs(estimatedSaving)} min`}
                tone={estimatedSaving > 0 ? 'success' : 'warning'}
                subvalue={`current delay − recommended delay (${currentDelayRisk} − ${recommendedDelayRisk})`}
              />
              <MetricCard
                label="Change type"
                value={String(activeScenarioResult?.change_type || 'None').replaceAll('_', ' ')}
                tone={activeScenarioResult?.no_better_route ? 'warning' : 'blue'}
              />
            </div>

            {/* 2. Change details: stop order diff + road path changes + decision proof */}
            <RecommendationChangeDetails scenarioResult={activeScenarioResult} />

            {/* 3. Explanation */}
            <div className="explanation-container" style={{ background: '#f8fbff', padding: '1rem', borderRadius: '8px', border: '1px solid #e2e8f0', marginTop: '1rem', marginBottom: '1rem' }}>
              {activeScenarioResult?.explanation && !agentExplanation && (
                <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#1e40af', marginBottom: '0.5rem' }}>
                  {activeScenarioResult.explanation}
                </div>
              )}
              {activeScenarioResult?.recommendation_reason && (
                <div style={{ fontSize: '0.82rem', color: '#374151', marginBottom: '0.5rem' }}>
                  <strong>Backend reasoning:</strong> {activeScenarioResult.recommendation_reason}
                </div>
              )}
              <AgentExplanationPanel explanation={agentExplanation} loading={agentExplanationLoading} error={agentExplanationError} />
              {agentExplanationLoading && (
                <div style={{ fontSize: '0.8rem', color: '#6b7280', marginTop: '0.5rem' }}>
                  <span className="live-dot" style={{ display: 'inline-block', marginRight: '0.4rem', width: 8, height: 8, borderRadius: '50%', backgroundColor: '#3b82f6' }} />
                  Generating AI explanation...
                </div>
              )}
            </div>


            <div className="responsive-btn-row">
              <button
                className="control-btn dispatch-btn"
                onClick={handleApplyRecommendation}
                disabled={applyBlocked}
                title={!recommendationValid ? 'Validation failed' : ''}
                style={{ flex: 1, minHeight: '44px', opacity: applyBlocked ? 0.5 : 1 }}
              >
                {!recommendationAllowed
                  ? 'No Better Route - Keep Current'
                  : recommendationValid
                    ? 'Apply Recommendation'
                    : 'Apply Blocked - Validation Failed'}
              </button>
              <button
                className="control-btn control-btn--neutral"
                onClick={handleKeepCurrentRoute}
                style={{ flex: 1, minHeight: '44px' }}
              >
                Keep Current Route
              </button>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}