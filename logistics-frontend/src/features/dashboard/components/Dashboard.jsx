import React, { useEffect, useMemo, useState } from 'react';
import MapViewer from './MapViewer';
import { useRouteStore } from '../../../store/useRouteStore';
import './Dashboard.css';

const SCENARIO_SLIDERS = [
  { key: 'traffic_density', label: 'Traffic density', help: 'road congestion', min: 0, max: 100 },
  { key: 'accident_severity', label: 'Accident severity', help: 'incident pressure', min: 0, max: 100 },
  { key: 'weather_severity', label: 'Weather severity', help: 'rain, wind, fog, snow impact', min: 0, max: 100 },
  { key: 'road_disruption', label: 'Road disruption', help: 'blocked or slow segments', min: 0, max: 100 },
  { key: 'package_load', label: 'Package load', help: 'vehicle workload', min: 0, max: 100 },
  { key: 'dispatch_hour', label: 'Dispatch hour', help: 'time-of-day demand', min: 0, max: 23, format: (value) => `${String(value).padStart(2, '0')}:00` },
];

const WEATHER_OPTIONS = ['clear', 'cloudy', 'wind', 'fog', 'rain', 'snow'];
const RISK_LEVELS = ['low', 'medium', 'high', 'critical'];

const VIEW_COPY = {
  overview: {
    eyebrow: 'SBTU Logistics Command Center',
    title: 'Operations Overview',
    subtitle: 'Select a courier, inspect the active route, then move to the next workflow step.',
  },
  optimization: {
    eyebrow: 'AI route planning',
    title: 'Route Optimization',
    subtitle: 'Review the loaded route, run the optimizer, and inspect the stop order selected by ML plus OR-Tools.',
  },
  conditions: {
    eyebrow: 'Scenario control',
    title: 'Road Conditions',
    subtitle: 'Manage route-level and segment-level traffic, accident, weather, and closure penalties.',
  },
  comparison: {
    eyebrow: 'Before and after proof',
    title: 'Route Comparison',
    subtitle: 'Compare the original plan, optimized route, and scenario result without visual clutter.',
  },
  'scenario-testing': {
    eyebrow: 'Instructor test mode',
    title: 'Interactive Scenario Testing',
    subtitle: 'Change a real route segment, rerun the optimizer, and verify how the route responds.',
  },
  reports: {
    eyebrow: 'Academic evidence',
    title: 'Model Evaluation Reports',
    subtitle: 'Generate model and RAG evaluation figures outside the courier workflow.',
  },
  settings: {
    eyebrow: 'System setup',
    title: 'Data Source Management',
    subtitle: 'Check which data sources feed routing, ML scoring, explanations, and simulation.',
  },
};

const round = (value, digits = 1) => {
  const number = Number(value);
  return Number.isFinite(number) ? Number(number.toFixed(digits)) : 0;
};

const formatMetric = (value, fallback = '--') => (
  Number.isFinite(Number(value)) ? String(value) : fallback
);

const formatDelta = (value, unit) => {
  if (!Number.isFinite(Number(value))) return '--';
  const sign = Number(value) > 0 ? '+' : '';
  return `${sign}${round(value)}${unit ? ` ${unit}` : ''}`;
};

const getSelectedRoute = (routes, selectedCourierId) => (
  routes.find((route) => route.vehicle_id === selectedCourierId) || routes[0] || null
);

const getSelectedCourier = (couriers, selectedCourierId) => (
  couriers.find((courier) => courier.id === selectedCourierId) || couriers[0] || null
);

const buildSegments = (route, overrides = {}) => {
  const stops = route?.stops || [];
  return stops.slice(0, -1).map((stop, index) => {
    const nextStop = stops[index + 1];
    const segmentKey = `${stop.stop_id || index}-${nextStop.stop_id || index + 1}`;
    return {
      key: segmentKey,
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
        ...(overrides[segmentKey] || {}),
      },
    };
  });
};

function LoadingState() {
  return (
    <div className="dashboard-container">
      <div className="dashboard-shell">
        <section className="command-header">
          <div>
            <span className="dashboard-eyebrow">Loading route intelligence</span>
            <h1>Preparing the dispatch workspace</h1>
            <p>Fetching couriers, stop pool data, Mapbox road geometry, and ML route scoring.</p>
          </div>
        </section>
        <div className="page-card loading-card">
          <span className="loading-bar" />
          <span className="loading-bar loading-bar--short" />
          <span className="loading-bar" />
        </div>
      </div>
    </div>
  );
}

function ErrorState({ message, onRetry }) {
  return (
    <div className="dashboard-container">
      <div className="dashboard-shell">
        <section className="page-card error-card">
          <span className="dashboard-eyebrow">Backend connection failed</span>
          <h1>Route data could not be loaded</h1>
          <p>{message}</p>
          <button className="control-btn control-btn--primary" onClick={onRetry}>Retry</button>
        </section>
      </div>
    </div>
  );
}

function Header({ view, routeSummary, selectedCourier, wsConnected, isConnecting, onStart, onStop, onRefresh }) {
  const copy = VIEW_COPY[view] || VIEW_COPY.overview;
  return (
    <section className="command-header">
      <div className="command-title">
        <span className="dashboard-eyebrow">{copy.eyebrow}</span>
        <h1>{copy.title}</h1>
        <p>{copy.subtitle}</p>
      </div>

      <div className="header-metrics">
        <MetricCard label="Route health" value={`${Math.max(0, 10 - Math.round((routeSummary?.overall_risk_score || 0) * 10))}/10`} />
        <MetricCard label="Expected delay" value={`${round(routeSummary?.expected_total_delay_min || selectedCourier?.stats?.totalExpectedDelay || 0)} min`} tone="warning" />
        <MetricCard label="Progress" value={`${selectedCourier?.stats?.completedStops || 0}/${selectedCourier?.stats?.totalStops || 0}`} />
      </div>

      <div className="header-actions">
        <span className={`live-pill ${wsConnected ? 'live-pill--on' : isConnecting ? 'live-pill--pending' : ''}`}>
          <span className="live-dot" />
          {wsConnected ? 'Simulation on' : isConnecting ? 'Connecting' : 'Simulation off'}
        </span>
        <button className="control-btn control-btn--primary" onClick={onStart} disabled={wsConnected || isConnecting}>Start</button>
        <button className="control-btn control-btn--danger" onClick={onStop} disabled={!wsConnected && !isConnecting}>Stop</button>
        <button className="control-btn control-btn--neutral" onClick={onRefresh}>Refresh</button>
      </div>
    </section>
  );
}

function MetricCard({ label, value, subvalue, tone = 'neutral' }) {
  return (
    <div className={`metric-card metric-card--${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {subvalue ? <small>{subvalue}</small> : null}
    </div>
  );
}

function RoutePicker({ couriers, selectedCourierId, setSelectedCourier }) {
  return (
    <div className="route-picker">
      {couriers.map((courier) => (
        <button
          key={courier.id}
          className={`route-picker-card ${courier.id === selectedCourierId ? 'active' : ''}`}
          onClick={() => setSelectedCourier(courier.id)}
          type="button"
        >
          <span className="route-avatar" style={{ '--route-color': courier.routeColor }}>{courier.initials}</span>
          <span>
            <strong>{courier.name}</strong>
            <small>{courier.currentStatus} · {courier.stopsRemaining} stops left</small>
          </span>
          <b>{round(courier.stats.totalExpectedDelay)} min</b>
        </button>
      ))}
    </div>
  );
}

function WorkflowSteps() {
  const steps = [
    ['1', 'Select route', 'Choose the courier route you want to inspect.'],
    ['2', 'Edit conditions', 'Set traffic, weather, accident, or segment closure.'],
    ['3', 'Run optimizer', 'ML predicts delay; OR-Tools solves stop order.'],
    ['4', 'Compare result', 'Check distance, duration, delay, risk, and stop order.'],
  ];
  return (
    <div className="workflow-grid">
      {steps.map(([number, title, text]) => (
        <div className="workflow-step" key={number}>
          <span>{number}</span>
          <strong>{title}</strong>
          <small>{text}</small>
        </div>
      ))}
    </div>
  );
}

function RouteMetricGrid({ route, scenarioResult }) {
  const comparison = route?.comparison || {};
  const scenarioMetrics = scenarioResult?.scenarioMetrics;
  return (
    <div className="metric-grid">
      <MetricCard label="Original" value={`${formatMetric(comparison.originalDistanceKm)} km`} subvalue={`${formatMetric(comparison.originalDurationMin)} min`} />
      <MetricCard label="Optimized" value={`${formatMetric(comparison.optimizedDistanceKm || route?.metrics?.distanceKm)} km`} subvalue={`${formatMetric(comparison.optimizedDurationMin || route?.metrics?.durationMin)} min`} tone="blue" />
      <MetricCard
        label="Scenario"
        value={scenarioMetrics ? `${scenarioMetrics.distance_km} km` : '--'}
        subvalue={scenarioMetrics ? `${scenarioMetrics.duration_min} min` : 'not run'}
        tone="cyan"
      />
      <MetricCard
        label="Delay impact"
        value={scenarioResult ? formatDelta(scenarioResult.delta?.expected_delay_min, 'min') : `${round(route?.metrics?.expectedDelayMin || 0)} min`}
        subvalue={scenarioResult ? 'scenario vs baseline' : 'ML prediction'}
        tone={scenarioResult?.delta?.expected_delay_min > 0 ? 'warning' : 'success'}
      />
    </div>
  );
}

function StopsTable({ route, scenarioResult, compact = false }) {
  const scenarioStops = scenarioResult?.scenario_route;
  const stops = Array.isArray(scenarioStops) && scenarioStops.length > 0
    ? [...scenarioStops].sort((a, b) => Number(a.optimized_position || 0) - Number(b.optimized_position || 0))
    : route?.stops || [];

  return (
    <div className={`table-shell ${compact ? 'table-shell--compact' : ''}`}>
      <table>
        <thead>
          <tr>
            <th>Order</th>
            <th>Stop</th>
            <th>Delay</th>
            <th>Top signal</th>
            <th>Risk</th>
          </tr>
        </thead>
        <tbody>
          {stops.map((stop, index) => {
            const factors = stop.delay_factors || [];
            const topFactor = factors[0];
            return (
              <tr key={`${stop.stop_id || index}-${index}`}>
                <td><span className="order-pill">{index + 1}</span></td>
                <td>
                  <strong>{stop.stop_name || stop.stop_id || `Stop ${index + 1}`}</strong>
                  <small>{stop.plannedTravelLabel || `${round(stop.planned_travel_min || 0)} min travel`}</small>
                </td>
                <td>{round(stop.expected_delay_min || 0)} min</td>
                <td>{topFactor ? <span className={`signal-chip signal-chip--${topFactor.severity || 'info'}`}>{topFactor.label}</span> : 'None'}</td>
                <td><span className={`risk-pill risk-pill--${stop.risk_level || 'low'}`}>{stop.risk_level || 'low'}</span></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function RouteOrderComparison({ route, scenarioResult }) {
  const baseline = scenarioResult?.baseline_route || route?.stops || [];
  const scenario = scenarioResult?.scenario_route || [];
  return (
    <div className="order-comparison">
      <div>
        <span className="panel-kicker">Before optimization</span>
        <h3>Original / baseline order</h3>
        <ol>
          {baseline.map((stop, index) => (
            <li key={`baseline-${stop.stop_id || index}`}>{stop.stop_name || stop.stop_id || `Stop ${index + 1}`}</li>
          ))}
        </ol>
      </div>
      <div>
        <span className="panel-kicker">After optimization</span>
        <h3>{scenario.length ? 'Scenario order' : 'Optimized order'}</h3>
        <ol>
          {(scenario.length ? scenario : route?.stops || []).map((stop, index) => (
            <li key={`scenario-${stop.stop_id || index}`}>{stop.stop_name || stop.stop_id || `Stop ${index + 1}`}</li>
          ))}
        </ol>
      </div>
    </div>
  );
}

function ConditionControls({ controls, onChange }) {
  return (
    <div className="condition-controls">
      <label className="field-card">
        <span>Weather type</span>
        <select
          value={controls.weather_condition}
          onChange={(event) => onChange('weather_condition', event.target.value)}
        >
          {WEATHER_OPTIONS.map((option) => (
            <option value={option} key={option}>{option}</option>
          ))}
        </select>
      </label>

      {SCENARIO_SLIDERS.map((item) => {
        const value = Number(controls[item.key] || 0);
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
              onChange={(event) => onChange(item.key, Number(event.target.value))}
            />
          </label>
        );
      })}

      <label className="toggle-card">
        <input
          type="checkbox"
          checked={Boolean(controls.conservative_mode)}
          onChange={(event) => onChange('conservative_mode', event.target.checked)}
        />
        <span>
          <strong>Conservative mode</strong>
          <small>Optimize with worst-case delay estimate instead of average delay.</small>
        </span>
      </label>
    </div>
  );
}

function SegmentEditor({ route, overrides, setSegmentOverride }) {
  const segments = buildSegments(route, overrides);
  if (!route) return null;

  return (
    <div className="segment-editor">
      <div className="section-title-row">
        <div>
          <span className="panel-kicker">Segment overrides</span>
          <h2>Editable road sections</h2>
        </div>
        <small>{segments.length} stop-to-stop segments</small>
      </div>
      <div className="segment-table">
        <table>
          <thead>
            <tr>
              <th>Segment</th>
              <th>Traffic</th>
              <th>Accident</th>
              <th>Closure</th>
              <th>Delay</th>
              <th>Speed</th>
              <th>Risk</th>
            </tr>
          </thead>
          <tbody>
            {segments.map((segment, index) => (
              <tr key={segment.key}>
                <td>
                  <strong>{index + 1}. {segment.from.stop_name}</strong>
                  <small>to {segment.to.stop_name}</small>
                </td>
                <td>
                  <input
                    type="number"
                    min="0"
                    max="100"
                    value={segment.override.traffic_density}
                    onChange={(event) => setSegmentOverride(route.vehicle_id, segment.key, {
                      ...segment.override,
                      traffic_density: Number(event.target.value),
                    })}
                  />
                </td>
                <td>
                  <input
                    type="number"
                    min="0"
                    max="100"
                    value={segment.override.accident_severity}
                    onChange={(event) => setSegmentOverride(route.vehicle_id, segment.key, {
                      ...segment.override,
                      accident_severity: Number(event.target.value),
                    })}
                  />
                </td>
                <td>
                  <input
                    type="checkbox"
                    checked={Boolean(segment.override.road_closure)}
                    onChange={(event) => setSegmentOverride(route.vehicle_id, segment.key, {
                      ...segment.override,
                      road_closure: event.target.checked,
                    })}
                  />
                </td>
                <td>
                  <input
                    type="number"
                    min="0"
                    max="240"
                    value={segment.override.extra_delay_min}
                    onChange={(event) => setSegmentOverride(route.vehicle_id, segment.key, {
                      ...segment.override,
                      extra_delay_min: Number(event.target.value),
                    })}
                  />
                </td>
                <td>
                  <input
                    type="number"
                    min="0"
                    max="100"
                    value={segment.override.speed_reduction}
                    onChange={(event) => setSegmentOverride(route.vehicle_id, segment.key, {
                      ...segment.override,
                      speed_reduction: Number(event.target.value),
                    })}
                  />
                </td>
                <td>
                  <select
                    value={segment.override.risk_level}
                    onChange={(event) => setSegmentOverride(route.vehicle_id, segment.key, {
                      ...segment.override,
                      risk_level: event.target.value,
                    })}
                  >
                    {RISK_LEVELS.map((risk) => <option key={risk} value={risk}>{risk}</option>)}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ConditionsMatrix({ routes, controlsByRoute, resultsByRoute, onSelect, selectedCourierId }) {
  return (
    <div className="condition-matrix">
      {routes.map((route) => {
        const controls = controlsByRoute[route.vehicle_id] || {};
        const result = resultsByRoute[route.vehicle_id];
        return (
          <button
            type="button"
            className={`condition-route-card ${route.vehicle_id === selectedCourierId ? 'active' : ''}`}
            key={route.vehicle_id}
            onClick={() => onSelect(route.vehicle_id)}
          >
            <span className="route-color-bar" style={{ '--route-color': route.color }} />
            <strong>{route.courierName}</strong>
            <small>{route.metrics.stopCount} stops · {round(route.metrics.distanceKm)} km</small>
            <div>
              <span>Traffic {controls.traffic_density ?? 0}</span>
              <span>Accident {controls.accident_severity ?? 0}</span>
              <span>{controls.weather_condition || 'clear'}</span>
            </div>
            <b>{result ? 'scenario ready' : 'not tested'}</b>
          </button>
        );
      })}
    </div>
  );
}

function EvidencePanel({ route, scenarioResult }) {
  const topStops = scenarioResult?.scenario_route || route?.stops || [];
  return (
    <div className="evidence-stack">
      <div className="page-card page-card--soft">
        <span className="panel-kicker">Optimization method</span>
        <h2>What is being optimized?</h2>
        <p>
          Mapbox provides road travel time. The trained ML model predicts delay risk per stop.
          OR-Tools selects the stop order using road-time and ML delay costs.
        </p>
      </div>
      <div className="page-card page-card--soft">
        <span className="panel-kicker">Delay drivers</span>
        <h2>Why does delay appear?</h2>
        <div className="driver-list">
          {topStops.slice(0, 4).map((stop, index) => (
            <div className="driver-row" key={`${stop.stop_id || index}-driver`}>
              <strong>{stop.stop_name || stop.stop_id}</strong>
              <span>{round(stop.expected_delay_min || 0)} min expected delay</span>
              <div>
                {(stop.delay_factors || []).slice(0, 4).map((factor) => (
                  <small className={`signal-chip signal-chip--${factor.severity || 'info'}`} key={`${factor.label}-${factor.value}`}>
                    {factor.label}: {factor.value}
                  </small>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
      {scenarioResult ? (
        <div className="page-card page-card--soft">
          <span className="panel-kicker">Scenario explanation</span>
          <p>{scenarioResult.explanation}</p>
        </div>
      ) : null}
    </div>
  );
}

function OverviewView({
  routes,
  couriers,
  selectedCourierId,
  selectedRoute,
  selectedCourier,
  setSelectedCourier,
  scenarioResult,
  liveCourierArray,
  pendingSuggestions,
  handleSuggestionDecision,
}) {
  return (
    <div className="page-grid page-grid--overview">
      <section className="page-card">
        <span className="panel-kicker">Step 1</span>
        <h2>Select an active route</h2>
        <p>Start with the courier route you want to optimize or test.</p>
        <RoutePicker couriers={couriers} selectedCourierId={selectedCourierId} setSelectedCourier={setSelectedCourier} />
      </section>

      <section className="page-card page-card--map">
        <div className="section-title-row">
          <div>
            <span className="panel-kicker">Live route map</span>
            <h2>{selectedCourier?.name || 'Selected courier'}</h2>
          </div>
          <small>Original + optimized only</small>
        </div>
        <div className="map-frame">
          <MapViewer
            routes={routes}
            selectedCourierId={selectedRoute?.vehicle_id ?? null}
            liveCouriers={liveCourierArray}
            pendingSuggestions={pendingSuggestions}
            handleSuggestionDecision={handleSuggestionDecision}
            scenarioResult={scenarioResult}
            showScenario={false}
            showAlternatives={false}
          />
        </div>
      </section>

      <aside className="page-card">
        <span className="panel-kicker">Workflow</span>
        <h2>Recommended next action</h2>
        <WorkflowSteps />
        <RouteMetricGrid route={selectedRoute} scenarioResult={scenarioResult} />
      </aside>
    </div>
  );
}

function OptimizationView({
  routes,
  selectedRoute,
  selectedCourierId,
  scenarioResult,
  runScenario,
  scenarioLoading,
  liveCourierArray,
  pendingSuggestions,
  handleSuggestionDecision,
}) {
  return (
    <div className="page-grid page-grid--two">
      <section className="page-card page-card--map">
        <div className="section-title-row">
          <div>
            <span className="panel-kicker">Optimization proof</span>
            <h2>Original vs optimized route</h2>
          </div>
          <button className="control-btn control-btn--primary" onClick={() => runScenario(selectedCourierId)} disabled={scenarioLoading}>
            {scenarioLoading ? 'Optimizing...' : 'Run AI Optimization'}
          </button>
        </div>
        <RouteMetricGrid route={selectedRoute} scenarioResult={scenarioResult} />
        <div className="map-frame map-frame--large">
          <MapViewer
            routes={routes}
            selectedCourierId={selectedRoute?.vehicle_id ?? null}
            liveCouriers={liveCourierArray}
            pendingSuggestions={pendingSuggestions}
            handleSuggestionDecision={handleSuggestionDecision}
            scenarioResult={scenarioResult}
            showScenario={false}
            showAlternatives={false}
          />
        </div>
      </section>
      <aside className="page-card">
        <EvidencePanel route={selectedRoute} scenarioResult={scenarioResult} />
      </aside>
      <section className="page-card page-card--wide">
        <RouteOrderComparison route={selectedRoute} scenarioResult={scenarioResult} />
      </section>
    </div>
  );
}

function ConditionsView({
  routes,
  selectedRoute,
  selectedCourierId,
  setSelectedCourier,
  controls,
  controlsByRoute,
  resultsByRoute,
  setScenarioControl,
  resetScenario,
  runScenario,
  scenarioLoading,
  segmentOverrides,
  setSegmentOverride,
}) {
  return (
    <div className="page-grid page-grid--conditions">
      <section className="page-card">
        <span className="panel-kicker">All routes</span>
        <h2>Condition matrix</h2>
        <p>Every route keeps its own traffic, weather, accident, and segment settings.</p>
        <ConditionsMatrix
          routes={routes}
          controlsByRoute={controlsByRoute}
          resultsByRoute={resultsByRoute}
          onSelect={setSelectedCourier}
          selectedCourierId={selectedCourierId}
        />
        <button className="control-btn control-btn--neutral control-btn--full" type="button" onClick={() => runScenario(selectedCourierId)}>
          Test selected route
        </button>
      </section>

      <section className="page-card page-card--scroll">
        <div className="section-title-row">
          <div>
            <span className="panel-kicker">Route-level conditions</span>
            <h2>{selectedRoute?.courierName || 'Selected route'}</h2>
          </div>
          <button className="control-btn control-btn--neutral" type="button" onClick={() => resetScenario(selectedCourierId)}>Reset</button>
        </div>
        <ConditionControls
          controls={controls}
          onChange={(key, value) => setScenarioControl(key, value, selectedCourierId)}
        />
        <button className="control-btn control-btn--primary control-btn--full" type="button" onClick={() => runScenario(selectedCourierId)} disabled={scenarioLoading}>
          {scenarioLoading ? 'Running optimizer...' : 'Apply and run optimizer'}
        </button>
      </section>

      <section className="page-card page-card--scroll">
        <SegmentEditor
          route={selectedRoute}
          overrides={segmentOverrides}
          setSegmentOverride={setSegmentOverride}
        />
      </section>
    </div>
  );
}

function ComparisonView({
  routes,
  selectedRoute,
  selectedCourierId,
  scenarioResult,
  liveCourierArray,
  pendingSuggestions,
  handleSuggestionDecision,
}) {
  const [showScenario, setShowScenario] = useState(false);
  return (
    <div className="page-grid page-grid--comparison">
      <section className="page-card page-card--map">
        <div className="section-title-row">
          <div>
            <span className="panel-kicker">Clean map comparison</span>
            <h2>Original vs optimized route</h2>
          </div>
          {scenarioResult ? (
            <button className="control-btn control-btn--neutral" type="button" onClick={() => setShowScenario((value) => !value)}>
              {showScenario ? 'Hide scenario' : 'Show scenario'}
            </button>
          ) : null}
        </div>
        <RouteMetricGrid route={selectedRoute} scenarioResult={showScenario ? scenarioResult : null} />
        <div className="map-frame map-frame--large">
          <MapViewer
            routes={routes}
            selectedCourierId={selectedCourierId}
            liveCouriers={liveCourierArray}
            pendingSuggestions={pendingSuggestions}
            handleSuggestionDecision={handleSuggestionDecision}
            scenarioResult={scenarioResult}
            showScenario={showScenario}
            showAlternatives={false}
          />
        </div>
      </section>
      <aside className="page-card page-card--scroll">
        <RouteOrderComparison route={selectedRoute} scenarioResult={showScenario ? scenarioResult : null} />
      </aside>
      <section className="page-card page-card--wide">
        <StopsTable route={selectedRoute} scenarioResult={showScenario ? scenarioResult : null} compact />
      </section>
    </div>
  );
}

function ScenarioTestingView({
  routes,
  selectedRoute,
  selectedCourierId,
  setSelectedCourier,
  controls,
  setScenarioControl,
  segmentOverrides,
  setSegmentOverride,
  runScenario,
  scenarioResult,
  scenarioLoading,
  liveCourierArray,
  pendingSuggestions,
  handleSuggestionDecision,
}) {
  return (
    <div className="page-grid page-grid--scenario">
      <section className="page-card">
        <span className="panel-kicker">Test steps</span>
        <h2>Run a controlled what-if test</h2>
        <ol className="step-list">
          <li>Select the route to test.</li>
          <li>Edit global conditions or a specific segment.</li>
          <li>Run the optimizer and compare the route.</li>
        </ol>
        <RoutePicker couriers={routes.map((route) => ({
          id: route.vehicle_id,
          initials: `C${route.vehicle_id}`,
          name: route.courierName,
          currentStatus: route.metrics.expectedDelayMin > 10 ? 'Delay Watch' : 'On Track',
          stopsRemaining: route.metrics.stopCount,
          routeColor: route.color,
          stats: { totalExpectedDelay: route.metrics.expectedDelayMin },
        }))} selectedCourierId={selectedCourierId} setSelectedCourier={setSelectedCourier} />
        <ConditionControls
          controls={controls}
          onChange={(key, value) => setScenarioControl(key, value, selectedCourierId)}
        />
        <button className="control-btn control-btn--primary control-btn--full" type="button" onClick={() => runScenario(selectedCourierId)} disabled={scenarioLoading}>
          {scenarioLoading ? 'Testing scenario...' : 'Run test scenario'}
        </button>
      </section>

      <section className="page-card page-card--map">
        <div className="section-title-row">
          <div>
            <span className="panel-kicker">Scenario map</span>
            <h2>Route response to changed conditions</h2>
          </div>
          <small>{scenarioResult ? 'Scenario route enabled' : 'Run a test first'}</small>
        </div>
        <RouteMetricGrid route={selectedRoute} scenarioResult={scenarioResult} />
        <div className="map-frame map-frame--large">
          <MapViewer
            routes={routes}
            selectedCourierId={selectedCourierId}
            liveCouriers={liveCourierArray}
            pendingSuggestions={pendingSuggestions}
            handleSuggestionDecision={handleSuggestionDecision}
            scenarioResult={scenarioResult}
            showScenario={Boolean(scenarioResult)}
            showAlternatives={false}
          />
        </div>
      </section>

      <aside className="page-card page-card--scroll">
        <SegmentEditor
          route={selectedRoute}
          overrides={segmentOverrides}
          setSegmentOverride={setSegmentOverride}
        />
      </aside>
    </div>
  );
}

function ReportsView({ modelInfo }) {
  return (
    <div className="page-grid page-grid--reports">
      <section className="page-card">
        <span className="panel-kicker">User UI cleanup</span>
        <h2>Evaluation graphs are no longer shown to couriers</h2>
        <p>
          Model graphs belong to backend reporting, not the daily route workflow.
          Use the Python report script to generate actual-vs-predicted figures from evaluation data.
        </p>
      </section>
      <section className="page-card">
        <span className="panel-kicker">Run command</span>
        <h2>Generate academic graphs</h2>
        <pre>python reports/generate_evaluation_reports.py</pre>
        <p>Outputs are saved under <code>logistics-backend/evaluation_results/</code>.</p>
      </section>
      <section className="page-card page-card--wide">
        <span className="panel-kicker">Current model metadata</span>
        <div className="settings-grid">
          <MetricCard label="Model version" value={modelInfo?.model_version || modelInfo?.version || 'unknown'} />
          <MetricCard label="Model type" value={modelInfo?.model_type || modelInfo?.model_name || 'delay predictor'} />
          <MetricCard label="Feature count" value={modelInfo?.feature_count ?? '--'} />
          <MetricCard label="RAG dataset" value="not present" tone="warning" subvalue="report script creates a data-gap note" />
        </div>
      </section>
    </div>
  );
}

function SettingsView() {
  const dataSources = [
    ['PostgreSQL', 'couriers, routes, stops, lifecycle state'],
    ['Mapbox Matrix/Directions', 'road duration, distance, geometry, alternatives'],
    ['CSV feature pipeline', 'traffic, weather, incident, historical delay features'],
    ['ML model files', 'trained delay prediction model and metadata'],
    ['Redis', 'simulation/cache support when enabled'],
    ['Ollama explainer', 'optional natural language route explanation'],
  ];
  return (
    <div className="page-grid page-grid--settings">
      <section className="page-card page-card--wide">
        <span className="panel-kicker">Data source map</span>
        <h2>What the system depends on</h2>
        <div className="source-grid">
          {dataSources.map(([name, description]) => (
            <div className="source-card" key={name}>
              <strong>{name}</strong>
              <small>{description}</small>
            </div>
          ))}
        </div>
      </section>
      <section className="page-card">
        <span className="panel-kicker">Important configuration</span>
        <h2>Environment variables</h2>
        <ul className="plain-list">
          <li><code>DATABASE_URL</code></li>
          <li><code>MAPBOX_TOKEN</code></li>
          <li><code>REDIS_URL</code></li>
          <li><code>OLLAMA_BASE_URL</code></li>
          <li><code>OLLAMA_MODEL</code></li>
        </ul>
      </section>
      <section className="page-card">
        <span className="panel-kicker">Known limitation</span>
        <h2>Real traffic integration</h2>
        <p>
          Live road APIs can be connected through the same scenario/data layer.
          Until then, manual segment overrides are stored as auditable penalties and sent to the optimizer.
        </p>
      </section>
    </div>
  );
}

export default function Dashboard({ view = 'overview' }) {
  const {
    loading,
    error,
    fetchData,
    forceFetchData,
    routes,
    couriers,
    routeSummary,
    selectedCourierId,
    setSelectedCourier,
    pendingSuggestions,
    handleSuggestionDecision,
    startSimulation,
    stopSimulation,
    wsConnected,
    isConnecting,
    liveCouriers,
    scenarioControls,
    scenarioControlsByVehicleId,
    scenarioResultsByVehicleId,
    segmentOverridesByVehicleId,
    scenarioResult,
    scenarioLoading,
    scenarioError,
    setScenarioControl,
    setSegmentOverride,
    resetScenario,
    runScenario,
    modelInfo,
  } = useRouteStore();

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const liveCourierArray = useMemo(() => Object.values(liveCouriers || {}), [liveCouriers]);
  const selectedRoute = getSelectedRoute(routes, selectedCourierId);
  const selectedCourier = getSelectedCourier(couriers, selectedCourierId);
  const activeScenarioResult = selectedRoute
    ? scenarioResultsByVehicleId[selectedRoute.vehicle_id] || scenarioResult
    : scenarioResult;
  const activeControls = selectedRoute
    ? scenarioControlsByVehicleId[selectedRoute.vehicle_id] || scenarioControls
    : scenarioControls;
  const activeSegmentOverrides = selectedRoute
    ? segmentOverridesByVehicleId[selectedRoute.vehicle_id] || {}
    : {};

  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} onRetry={forceFetchData} />;

  const headerProps = {
    view,
    routeSummary,
    selectedCourier,
    wsConnected,
    isConnecting,
    onStart: startSimulation,
    onStop: stopSimulation,
    onRefresh: forceFetchData,
  };

  return (
    <div className="dashboard-container">
      <div className="dashboard-shell">
        <Header {...headerProps} />
        {scenarioError ? <div className="inline-error">{scenarioError}</div> : null}

        {view === 'overview' && (
          <OverviewView
            routes={routes}
            couriers={couriers}
            selectedCourierId={selectedCourierId}
            selectedRoute={selectedRoute}
            selectedCourier={selectedCourier}
            setSelectedCourier={setSelectedCourier}
            scenarioResult={activeScenarioResult}
            liveCourierArray={liveCourierArray}
            pendingSuggestions={pendingSuggestions}
            handleSuggestionDecision={handleSuggestionDecision}
          />
        )}

        {view === 'optimization' && (
          <OptimizationView
            routes={routes}
            selectedRoute={selectedRoute}
            selectedCourierId={selectedCourierId}
            scenarioResult={activeScenarioResult}
            runScenario={runScenario}
            scenarioLoading={scenarioLoading}
            liveCourierArray={liveCourierArray}
            pendingSuggestions={pendingSuggestions}
            handleSuggestionDecision={handleSuggestionDecision}
          />
        )}

        {view === 'conditions' && (
          <ConditionsView
            routes={routes}
            selectedRoute={selectedRoute}
            selectedCourierId={selectedCourierId}
            setSelectedCourier={setSelectedCourier}
            controls={activeControls}
            controlsByRoute={scenarioControlsByVehicleId}
            resultsByRoute={scenarioResultsByVehicleId}
            setScenarioControl={setScenarioControl}
            resetScenario={resetScenario}
            runScenario={runScenario}
            scenarioLoading={scenarioLoading}
            segmentOverrides={activeSegmentOverrides}
            setSegmentOverride={setSegmentOverride}
          />
        )}

        {view === 'comparison' && (
          <ComparisonView
            routes={routes}
            selectedRoute={selectedRoute}
            selectedCourierId={selectedCourierId}
            scenarioResult={activeScenarioResult}
            liveCourierArray={liveCourierArray}
            pendingSuggestions={pendingSuggestions}
            handleSuggestionDecision={handleSuggestionDecision}
          />
        )}

        {view === 'scenario-testing' && (
          <ScenarioTestingView
            routes={routes}
            selectedRoute={selectedRoute}
            selectedCourierId={selectedCourierId}
            setSelectedCourier={setSelectedCourier}
            controls={activeControls}
            setScenarioControl={setScenarioControl}
            segmentOverrides={activeSegmentOverrides}
            setSegmentOverride={setSegmentOverride}
            runScenario={runScenario}
            scenarioResult={activeScenarioResult}
            scenarioLoading={scenarioLoading}
            liveCourierArray={liveCourierArray}
            pendingSuggestions={pendingSuggestions}
            handleSuggestionDecision={handleSuggestionDecision}
          />
        )}

        {view === 'reports' && <ReportsView modelInfo={modelInfo} />}
        {view === 'settings' && <SettingsView />}

      </div>
    </div>
  );
}
