import React, { useEffect } from 'react';
import './Dashboard.css';
import MapViewer from './MapViewer';
import CourierList from './CourierList';
import PackageList from './PackageList';
import { useRouteStore } from '../../../store/useRouteStore';

const formatNumber = (value, digits = 1) => {
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(digits).replace(/\.0$/, '') : '0';
};

const formatSigned = (value, unit = '') => {
  const num = Number(value);
  if (!Number.isFinite(num)) return `0${unit}`;
  const sign = num > 0 ? '+' : '';
  return `${sign}${formatNumber(num)}${unit}`;
};

const SCENARIO_SLIDERS = [
  { key: 'traffic_density', label: 'Traffic density', help: 'road congestion', suffix: '/100', min: 0, max: 100 },
  { key: 'accident_severity', label: 'Accident severity', help: 'incident pressure', suffix: '/100', min: 0, max: 100 },
  { key: 'weather_severity', label: 'Weather severity', help: 'rain, wind, fog, snow impact', suffix: '/100', min: 0, max: 100 },
  { key: 'road_disruption', label: 'Road disruption', help: 'blocked or slow segments', suffix: '/100', min: 0, max: 100 },
  { key: 'package_load', label: 'Package load', help: 'vehicle workload', suffix: '/100', min: 0, max: 100 },
  { key: 'dispatch_hour', label: 'Dispatch hour', help: 'time-of-day demand', suffix: ':00', min: 0, max: 23 },
];

const WEATHER_OPTIONS = ['clear', 'cloudy', 'wind', 'fog', 'rain', 'snow'];

const clampScore = (value) => Math.max(0, Math.min(1, Number(value) || 0));

const getHeatmapTone = (score) => {
  if (score >= 0.9) return 'good';
  if (score >= 0.75) return 'ok';
  if (score >= 0.6) return 'warn';
  return 'bad';
};

const buildDelayFactors = (stop) => {
  if (Array.isArray(stop.delay_factors) && stop.delay_factors.length > 0) {
    return stop.delay_factors.slice(0, 4).map((factor) => ({
      label: factor.label || 'Signal',
      value: factor.value || '',
      impact: factor.impact || '',
      severity: factor.severity || 'info',
    }));
  }

  const factors = [];
  if (Number.isFinite(Number(stop.delay_probability))) {
    factors.push({
      label: 'Delay probability',
      value: `${Math.round(Number(stop.delay_probability) * 100)}%`,
      impact: 'Predicted by the ML model',
      severity: Number(stop.delay_probability) > 0.35 ? 'warning' : 'info',
    });
  }
  if (Number(stop.planned_travel_min || 0) > 0) {
    factors.push({
      label: 'Incoming drive',
      value: `${formatNumber(stop.planned_travel_min)} min`,
      impact: 'Road travel time from Mapbox',
      severity: 'info',
    });
  }
  if (Number(stop.time_window_slack_min || 0) <= 90) {
    factors.push({
      label: 'Time window',
      value: `${formatNumber(stop.time_window_slack_min)} min slack`,
      impact: 'Low slack increases miss-window risk',
      severity: Number(stop.time_window_slack_min || 0) <= 30 ? 'danger' : 'warning',
    });
  }
  if (stop.risk_level && stop.risk_level !== 'low') {
    factors.push({
      label: 'Risk level',
      value: stop.risk_level,
      impact: 'ML stop-level risk class',
      severity: stop.risk_level === 'high' ? 'danger' : 'warning',
    });
  }
  if (stop.will_miss_window) {
    factors.push({
      label: 'Window risk',
      value: 'At risk',
      impact: 'Predicted arrival may miss the delivery window',
      severity: 'danger',
    });
  }
  return factors.slice(0, 3);
};

export default function Dashboard() {
  const {
    loading, error, fetchData, forceFetchData,
    routes, couriers, packages,
    routeSummary,
    selectedCourierId, setSelectedCourier, hasFetched,
    startSimulation, stopSimulation, wsConnected,
    liveCouriers, isConnecting, pendingSuggestions, handleSuggestionDecision,
    scenarioControls, setScenarioControl, runScenario, resetScenario,
    scenarioResult, scenarioLoading, scenarioError, simulationRouteMode, modelInfo,
  } = useRouteStore();

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const activeCouriersList = Object.values(liveCouriers);
  const selectedCourier = couriers.find((courier) => courier.id === selectedCourierId) || couriers[0];
  const selectedRoute = routes.find((route) => route.vehicle_id === selectedCourier?.id) || routes[0];
  const selectedComparison = selectedRoute?.comparison;
  const selectedStops = selectedRoute?.stops || [];
  const totalStops = packages.length;
  const delayedStops = packages.filter((pkg) => Number(pkg.expected_delay_min || 0) > 0).length;
  const completedStops = packages.filter((pkg) => pkg.status === 'completed').length;
  const deliveryProgress = totalStops > 0 ? Math.round((completedStops / totalStops) * 100) : 0;
  const healthScore = routeSummary
    ? Math.max(0, 10 - Number(routeSummary.overall_risk_score || 0) * 10)
    : 0;
  const routeStatus = routeSummary?.vrp_status === 'success' ? 'Optimized' : 'Needs review';
  const topDelayStops = selectedStops
    .filter((stop) => Number(stop.expected_delay_min || 0) > 0)
    .sort((a, b) => Number(b.expected_delay_min || 0) - Number(a.expected_delay_min || 0))
    .slice(0, 4);
  const baselineMetrics = scenarioResult?.baseline_metrics;
  const scenarioMetrics = scenarioResult?.scenario_metrics;
  const scenarioDelta = scenarioResult?.delta || {};
  const factorImpacts = scenarioResult?.factor_impacts || [];
  const activeSuggestionCount = Object.keys(pendingSuggestions || {}).length;
  const evaluation = modelInfo?.evaluation;
  const evaluationHeatmap = evaluation ? [
    {
      label: 'MAE',
      value: `${formatNumber(evaluation.regressor.test_mae_min, 2)} min`,
      score: clampScore(1 - evaluation.regressor.test_mae_min / 10),
    },
    {
      label: 'RMSE',
      value: `${formatNumber(evaluation.regressor.test_rmse_min, 2)} min`,
      score: clampScore(1 - evaluation.regressor.test_rmse_min / 10),
    },
    {
      label: 'R2',
      value: formatNumber(evaluation.regressor.test_r2, 3),
      score: clampScore(evaluation.regressor.test_r2),
    },
    {
      label: 'Recall',
      value: formatNumber(evaluation.risk_classifier.severe_recall, 3),
      score: clampScore(evaluation.risk_classifier.severe_recall),
    },
    {
      label: 'Precision',
      value: formatNumber(evaluation.risk_classifier.severe_precision, 3),
      score: clampScore(evaluation.risk_classifier.severe_precision),
    },
    {
      label: 'Within 5m',
      value: `${Math.round(evaluation.regressor.within_5_min * 100)}%`,
      score: clampScore(evaluation.regressor.within_5_min),
    },
    {
      label: 'P90 cover',
      value: `${Math.round(evaluation.p90.coverage * 100)}%`,
      score: clampScore(evaluation.p90.coverage),
    },
  ] : [];

  const updateScenarioNumber = (key) => (event) => {
    setScenarioControl(key, Number(event.target.value));
  };
  const updateScenarioValue = (key) => (event) => {
    setScenarioControl(key, event.target.value);
  };
  const updateScenarioBoolean = (key) => (event) => {
    setScenarioControl(key, event.target.checked);
  };

  return (
    <div className="dashboard-container">
      <div className="dashboard-shell">
        <header className="control-header">
          <div className="control-title">
            <span className="dashboard-eyebrow">SBTU Logistics AI Console</span>
            <h1>Route Scenario Control Room</h1>
            <p>Change route conditions, run the ML-backed optimizer, and verify the result on one screen.</p>
          </div>

          <div className="header-metrics" aria-label="Current route metrics">
            <div className="header-metric">
              <span>Route health</span>
              <strong>{formatNumber(healthScore)}/10</strong>
            </div>
            <div className="header-metric header-metric--warning">
              <span>Expected delay</span>
              <strong>{formatNumber(routeSummary?.expected_total_delay_min || 0)} min</strong>
            </div>
            <div className="header-metric">
              <span>Progress</span>
              <strong>{completedStops}/{totalStops}</strong>
            </div>
          </div>

          <div className="dashboard-header-actions">
            <div className={`live-pill ${wsConnected ? 'live-pill--on' : isConnecting ? 'live-pill--pending' : 'live-pill--off'}`}>
              <span className="live-dot" />
              {wsConnected
                ? simulationRouteMode === 'scenario'
                  ? 'Scenario route live'
                  : 'Optimized route live'
                : isConnecting ? 'Connecting' : 'Simulation off'}
            </div>
            <button
              className="control-btn control-btn--success"
              onClick={startSimulation}
              disabled={!hasFetched || wsConnected || isConnecting}
            >
              {isConnecting ? 'Starting' : 'Start'}
            </button>
            <button
              className="control-btn control-btn--danger"
              onClick={stopSimulation}
              disabled={!wsConnected && !isConnecting}
            >
              Stop
            </button>
            <button
              className="control-btn control-btn--neutral"
              onClick={forceFetchData}
              disabled={loading}
            >
              Refresh
            </button>
          </div>
        </header>

        {loading && (
          <div className="loading-bar-wrapper">
            <div className="loading-bar" />
          </div>
        )}

        {error && (
          <div className="dashboard-error-banner">
            <span>{error}</span>
            <button onClick={forceFetchData}>Retry</button>
          </div>
        )}

        <main className="command-workspace">
          <aside className="workspace-panel scenario-panel">
            <div className="panel-heading">
              <span className="panel-kicker">AI Scenario Lab</span>
              <h2>Road Conditions</h2>
              <p>Each change becomes an ML feature, then the backend solves a new route.</p>
            </div>

            <div className="selected-route-chip">
              <span>Selected courier</span>
              <strong>{selectedCourier?.name || 'No route selected'}</strong>
            </div>

            <div className="scenario-controls-stack">
              <label className="scenario-select">
                <span>Weather type</span>
                <select value={scenarioControls.weather_condition} onChange={updateScenarioValue('weather_condition')}>
                  {WEATHER_OPTIONS.map((weather) => (
                    <option key={weather} value={weather}>{weather}</option>
                  ))}
                </select>
              </label>

              {SCENARIO_SLIDERS.map((control) => {
                const value = scenarioControls[control.key];
                const displayValue = control.key === 'dispatch_hour'
                  ? `${String(value).padStart(2, '0')}${control.suffix}`
                  : `${value}${control.suffix}`;

                return (
                  <label key={control.key} className="scenario-slider">
                    <span>
                      <b>{control.label}</b>
                      <small>{control.help}</small>
                    </span>
                    <strong>{displayValue}</strong>
                    <input
                      type="range"
                      min={control.min}
                      max={control.max}
                      value={value}
                      onChange={updateScenarioNumber(control.key)}
                    />
                  </label>
                );
              })}

              <label className="scenario-toggle">
                <input
                  type="checkbox"
                  checked={scenarioControls.conservative_mode}
                  onChange={updateScenarioBoolean('conservative_mode')}
                />
                <span>
                  <strong>Conservative P90 mode</strong>
                  <small>Optimize for worst-case delay instead of average delay.</small>
                </span>
              </label>
            </div>

            {scenarioError && (
              <div className="scenario-error">{scenarioError}</div>
            )}

            <div className="scenario-action-row">
              <button
                className="control-btn control-btn--neutral"
                onClick={resetScenario}
                disabled={scenarioLoading}
              >
                Reset
              </button>
              <button
                className="control-btn control-btn--success control-btn--wide"
                onClick={runScenario}
                disabled={scenarioLoading || !selectedRoute}
              >
                {scenarioLoading ? 'Optimizing...' : 'Run AI Optimizer'}
              </button>
            </div>

            <div className="scenario-proof-box">
              <span>Route sources</span>
              <dl className="route-source-list">
                <div>
                  <dt>Original</dt>
                  <dd>Database stop order, drawn with Mapbox road geometry.</dd>
                </div>
                <div>
                  <dt>Optimized</dt>
                  <dd>Baseline ML delay scores plus OR-Tools stop ordering.</dd>
                </div>
                <div>
                  <dt>Scenario</dt>
                  <dd>Your road conditions, ML re-scoring, OR-Tools reorder, Mapbox redraw.</dd>
                </div>
              </dl>
            </div>
          </aside>

          <section className="workspace-panel map-workbench">
            <div className="map-toolbar">
              <div>
                <span className="panel-kicker">Optimization Proof</span>
                <h2>Original vs Optimized vs Scenario</h2>
              </div>

              <div className="proof-stat-grid">
                <div className="proof-stat">
                  <span>Original</span>
                  <strong>{formatNumber(selectedComparison?.originalDistanceKm || 0)} km</strong>
                  <small>{selectedComparison?.originalDurationMin || 0} min</small>
                </div>
                <div className="proof-stat proof-stat--blue">
                  <span>Optimized</span>
                  <strong>{formatNumber(selectedComparison?.optimizedDistanceKm || 0)} km</strong>
                  <small>{selectedComparison?.optimizedDurationMin || 0} min</small>
                </div>
                <div className="proof-stat proof-stat--cyan">
                  <span>Scenario delay</span>
                  <strong>{scenarioMetrics ? `${formatNumber(scenarioMetrics.expected_delay_min)} min` : '--'}</strong>
                  <small>{scenarioResult ? formatSigned(scenarioDelta.expected_delay_min, ' min') : 'not run'}</small>
                </div>
                <div className="proof-stat proof-stat--green">
                  <span>Order</span>
                  <strong>{scenarioResult ? (scenarioResult.order_changed ? 'Changed' : 'Stable') : routeStatus}</strong>
                  <small>{activeSuggestionCount} live suggestions</small>
                </div>
              </div>
            </div>

            <div className="map-canvas-panel">
              {loading
                ? <div className="skeleton-map"><div className="skeleton-map-pulse" /></div>
                : (
                  <MapViewer
                    routes={routes}
                    selectedCourierId={selectedCourierId}
                    liveCouriers={activeCouriersList}
                    pendingSuggestions={pendingSuggestions}
                    handleSuggestionDecision={handleSuggestionDecision}
                    scenarioResult={scenarioResult}
                  />
                )}
            </div>
          </section>

          <aside className="insight-column">
            <section className="workspace-panel fleet-panel">
              <div className="section-heading">
                <div>
                  <span className="panel-kicker">Fleet</span>
                  <h2>Active Couriers</h2>
                </div>
                <span>{couriers.length} total</span>
              </div>
              <div className="fleet-scroll-area">
                {loading
                  ? Array.from({ length: 3 }).map((_, i) => (
                    <div key={i} className="skeleton-courier-card">
                      <div className="skeleton-avatar" />
                      <div className="skeleton-courier-lines">
                        <span className="skeleton-line" style={{ width: '65%' }} />
                        <span className="skeleton-line" style={{ width: '42%' }} />
                      </div>
                    </div>
                  ))
                  : (
                    <CourierList
                      couriers={couriers}
                      selectedCourierId={selectedCourierId}
                      onSelectCourier={setSelectedCourier}
                      pendingSuggestions={pendingSuggestions}
                    />
                  )}
              </div>
            </section>

            <section className="workspace-panel delay-panel">
              <div className="section-heading">
                <div>
                  <span className="panel-kicker">ML Evidence</span>
                  <h2>Delay Drivers</h2>
                </div>
                <span>{delayedStops} watched</span>
              </div>

              <div className="delay-driver-list">
                {topDelayStops.length > 0 ? topDelayStops.map((stop) => (
                  <div key={stop.stop_id || stop.stop_name} className="delay-driver-row">
                    <div>
                      <strong>{stop.stop_name || 'Unnamed stop'}</strong>
                      <span>{formatNumber(stop.expected_delay_min)} min expected delay</span>
                    </div>
                    <div className="factor-chip-row">
                      {buildDelayFactors(stop).map((factor, index) => (
                        <span
                          key={`${factor.label}-${index}`}
                          className={`factor-chip factor-chip--${factor.severity}`}
                          title={factor.impact}
                        >
                          <b>{factor.label}</b> {factor.value}
                        </span>
                      ))}
                    </div>
                  </div>
                )) : (
                  <div className="empty-state">No predicted delay for the selected courier.</div>
                )}
              </div>

              {scenarioResult && (
                <div className="scenario-evidence-card">
                  <h3>Scenario decision</h3>
                  <p>{scenarioResult.explanation}</p>
                  <div className="factor-impact-list">
                    {factorImpacts.map((factor) => (
                      <span key={factor.label} className={`factor-chip factor-chip--${factor.severity}`}>
                        <b>{factor.label}</b> {factor.after}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </section>
          </aside>

          <section className="workspace-panel manifest-panel">
            <div className="section-heading">
              <div>
                <span className="panel-kicker">Manifest</span>
                <h2>Stops</h2>
              </div>
              <span>{deliveryProgress}% completed</span>
            </div>
            {loading
              ? Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="skeleton-package-row">
                  <span className="skeleton-line" style={{ width: `${50 + i * 8}%` }} />
                </div>
              ))
              : <PackageList packages={packages} />}
          </section>

          <section className="workspace-panel summary-panel">
            <div className="section-heading">
              <div>
                <span className="panel-kicker">Scenario Result</span>
                <h2>Impact</h2>
              </div>
              <span>{scenarioResult ? 'computed' : 'waiting'}</span>
            </div>

            <div className="impact-grid">
              <div className="impact-card">
                <span>Delay change</span>
                <strong className={Number(scenarioDelta.expected_delay_min) > 0 ? 'text-warning' : 'text-success'}>
                  {scenarioResult ? formatSigned(scenarioDelta.expected_delay_min, ' min') : '--'}
                </strong>
                <small>{baselineMetrics ? `${formatNumber(baselineMetrics.expected_delay_min)} min baseline` : 'Run a scenario first'}</small>
              </div>
              <div className="impact-card">
                <span>Duration change</span>
                <strong className={Number(scenarioDelta.duration_min) > 0 ? 'text-warning' : 'text-success'}>
                  {scenarioResult ? formatSigned(scenarioDelta.duration_min, ' min') : '--'}
                </strong>
                <small>{scenarioMetrics ? `${formatNumber(scenarioMetrics.duration_min)} min scenario` : 'Mapbox route result'}</small>
              </div>
              <div className="impact-card">
                <span>Distance change</span>
                <strong className={Number(scenarioDelta.distance_km) > 0 ? 'text-warning' : 'text-success'}>
                  {scenarioResult ? formatSigned(scenarioDelta.distance_km, ' km') : '--'}
                </strong>
                <small>{scenarioMetrics ? `${formatNumber(scenarioMetrics.distance_km)} km scenario` : 'Road distance'}</small>
              </div>
              <div className="impact-card">
                <span>Alternatives</span>
                <strong>{scenarioResult?.mapboxAlternatives?.length || 0}</strong>
                <small>Mapbox road alternatives</small>
              </div>
            </div>

            {evaluation && (
              <div className="model-evaluation">
                <div className="model-evaluation-header">
                  <span>Model validation</span>
                  <strong>v7 / {evaluation.trained_at}</strong>
                </div>
                <div className="evaluation-heatmap">
                  {evaluationHeatmap.map((metric) => (
                    <div
                      key={metric.label}
                      className={`evaluation-cell evaluation-cell--${getHeatmapTone(metric.score)}`}
                    >
                      <span>{metric.label}</span>
                      <strong>{metric.value}</strong>
                      <small>{formatNumber(metric.score, 3)}</small>
                    </div>
                  ))}
                </div>
                <p>{evaluation.p90.note}</p>
              </div>
            )}
          </section>
        </main>
      </div>
    </div>
  );
}
