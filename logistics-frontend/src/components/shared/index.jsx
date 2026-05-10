import React, { useState } from 'react';
import '../../features/dashboard/components/Dashboard.css';

const round = (value, digits = 1) => {
  const number = Number(value);
  return Number.isFinite(number) ? Number(number.toFixed(digits)) : 0;
};

export function MetricCard({ label, value, subvalue, tone = 'neutral' }) {
  return (
    <div className={`metric-card metric-card--${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {subvalue ? <small>{subvalue}</small> : null}
    </div>
  );
}

export function RouteMetricGrid({ route, scenarioResult }) {
  const comparison = route?.comparison || {};
  const scenarioMetrics = scenarioResult?.scenarioMetrics || scenarioResult?.scenario_metrics;
  return (
    <div className="metric-grid">
      <MetricCard
        label="Original"
        value={`${comparison.originalDistanceKm ?? '--'} km`}
        subvalue={`${comparison.originalDurationMin ?? '--'} min`}
      />
      <MetricCard
        label="Optimized"
        value={`${comparison.optimizedDistanceKm || route?.metrics?.distanceKm || '--'} km`}
        subvalue={`${comparison.optimizedDurationMin || route?.metrics?.durationMin || '--'} min`}
        tone="blue"
      />
      <MetricCard
        label="Scenario"
        value={scenarioMetrics ? `${scenarioMetrics.distance_km} km` : '--'}
        subvalue={scenarioMetrics ? `${scenarioMetrics.duration_min} min` : 'not run'}
        tone="cyan"
      />
      <MetricCard
        label="Delay impact"
        value={scenarioResult?.delta?.expected_delay_min != null
          ? `${round(scenarioResult.delta.expected_delay_min)} min`
          : `${round(route?.metrics?.expectedDelayMin || 0)} min`}
        subvalue={scenarioResult ? 'scenario vs baseline' : 'ML prediction'}
        tone={scenarioResult?.delta?.expected_delay_min > 0 ? 'warning' : 'success'}
      />
    </div>
  );
}

export function RoutePicker({ routes, selectedId, onChange }) {
  return (
    <div className="route-picker">
      {routes.map((route) => (
        <button
          key={route.id}
          className={`route-picker-card ${route.id === selectedId ? 'active' : ''}`}
          onClick={() => onChange(route.id)}
          type="button"
        >
          <span className="route-avatar" style={{ '--route-color': route.color }}>{route.initials}</span>
          <span>
            <strong>{route.name}</strong>
            <small>{route.status} · {route.stopCount} stops</small>
          </span>
          <b>{round(route.totalDelay)} min</b>
        </button>
      ))}
    </div>
  );
}

export function StopsTable({ stops, compact = false }) {
  if (!stops?.length) return null;
  return (
    <div className={`table-shell ${compact ? 'table-shell--compact' : ''}`}>
      <table>
        <thead>
          <tr>
            <th>Order</th>
            <th>Stop</th>
            <th>Delay</th>
            <th>Top factor</th>
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
                <td>{topFactor
                  ? <span className={`signal-chip signal-chip--${topFactor.severity || 'info'}`}>{topFactor.label}</span>
                  : '--'}
                </td>
                <td><span className={`risk-pill risk-pill--${stop.risk_level || 'low'}`}>{stop.risk_level || 'low'}</span></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Route Validation ─────────────────────────────────────────────────────────

export function RouteValidationBanner({ plannedStops, resultStops, label = 'Optimized' }) {
  if (!plannedStops?.length || !resultStops?.length) return null;

  const plannedIds = new Set(plannedStops.map((s) => s.stop_id || s.stop_name || s.name).filter(Boolean));
  const resultIds = new Set(resultStops.map((s) => s.stop_id || s.stop_name || s.name).filter(Boolean));

  const missingFromResult = [...plannedIds].filter((id) => !resultIds.has(id));
  const extraInResult = [...resultIds].filter((id) => !plannedIds.has(id));

  const countMismatch = plannedStops.length !== resultStops.length;

  if (!countMismatch && missingFromResult.length === 0 && extraInResult.length === 0) {
    return (
      <div style={{ padding: '0.5rem 0.8rem', backgroundColor: '#f0fdf4', borderRadius: '6px', border: '1px solid #86efac', fontSize: '0.8rem', color: '#166534', marginBottom: '0.5rem' }}>
        ✓ {label} route validated: all {resultStops.length} stops present.
      </div>
    );
  }

  return (
    <div style={{ padding: '0.6rem 0.8rem', backgroundColor: '#fef2f2', borderRadius: '6px', border: '1px solid #fca5a5', fontSize: '0.8rem', color: '#991b1b', marginBottom: '0.5rem' }}>
      <strong>⚠ Route validation warning:</strong>
      {countMismatch && (
        <div>Planned: {plannedStops.length} stops → {label}: {resultStops.length} stops</div>
      )}
      {missingFromResult.length > 0 && (
        <div>Missing stops in {label.toLowerCase()} route: {missingFromResult.join(', ')}</div>
      )}
      {extraInResult.length > 0 && (
        <div>Unexpected stops in {label.toLowerCase()} route: {extraInResult.join(', ')}</div>
      )}
    </div>
  );
}

// ── Route Order Comparison ───────────────────────────────────────────────────

export function RouteOrderComparison({ baseline, scenario }) {
  const before = scenario?.baseline_route || baseline || [];
  // Fix: scenario_route may be an array; do NOT fallback to scenario object
  const after = Array.isArray(scenario?.scenario_route) ? scenario.scenario_route : [];
  const hasResult = after.length > 0;

  return (
    <div className="order-comparison">
      <div style={{ backgroundColor: '#f9fafb', padding: '1rem', borderRadius: '8px' }}>
        <span className="panel-kicker">Before optimization</span>
        <h3 style={{ marginBottom: '0.5rem' }}>Original order ({before.length} stops)</h3>
        <ol style={{ paddingLeft: '1.2rem', margin: 0, display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
          {before.map((stop, index) => (
            <li key={`baseline-${stop.stop_id || index}`}>
              {stop.stop_name || stop.stop_id || `Stop ${index + 1}`}
            </li>
          ))}
        </ol>
      </div>
      <div style={{ backgroundColor: '#eff6ff', padding: '1rem', borderRadius: '8px', border: '1px solid #bfdbfe' }}>
        <span className="panel-kicker">After optimization</span>
        <h3 style={{ marginBottom: '0.5rem', color: hasResult ? '#1e40af' : '#6b7280' }}>
          {hasResult ? `Optimized order (${after.length} stops)` : 'No result yet'}
        </h3>
        {hasResult && before.length !== after.length && (
          <RouteValidationBanner plannedStops={before} resultStops={after} label="Optimized" />
        )}
        <ol style={{ paddingLeft: '1.2rem', margin: 0, display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
          {(hasResult ? after : before).map((stop, index) => (
            <li key={`after-${stop.stop_id || index}`} style={{ fontWeight: hasResult ? '600' : '400' }}>
              {stop.stop_name || stop.stop_id || `Stop ${index + 1}`}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

export function EvidencePanel({ stops, explanation, explanationWarning }) {
  return (
    <div className="evidence-stack">
      <div className="page-card page-card--soft">
        <span className="panel-kicker">Optimization method</span>
        <h2>How routes are optimized</h2>
        <p>
          Mapbox provides real road travel time. The ML model predicts delay risk per stop.
          OR-Tools selects the optimal stop order using combined travel time and predicted delay costs.
        </p>
      </div>
      <div className="page-card page-card--soft">
        <span className="panel-kicker">Delay drivers</span>
        <h2>Top delay contributors</h2>
        <div className="driver-list">
          {(stops || []).slice(0, 4).map((stop, index) => (
            <div className="driver-row" key={`${stop.stop_id || index}-driver`}>
              <strong>{stop.stop_name || stop.stop_id}</strong>
              <span>{round(stop.expected_delay_min || 0)} min expected</span>
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
      {explanation ? (
        <div className="page-card page-card--soft">
          <span className="panel-kicker">Optimization explanation</span>
          {explanationWarning ? (
            <>
              <div className="inline-error" style={{ marginBottom: '0.6rem' }}>
                Hallucination risk detected or grounding score too low. AI explanation hidden.
              </div>
              <p>The optimizer modified the route sequence to minimize overall cost, balancing direct travel distance against predicted traffic or scenario delays.</p>
            </>
          ) : (
            <p>{explanation}</p>
          )}
        </div>
      ) : null}
    </div>
  );
}

export function SegmentEditor({ segments, onChange }) {
  const RISK_LEVELS = ['low', 'medium', 'high', 'critical'];
  if (!segments?.length) return null;

  return (
    <div className="segment-editor">
      <div className="section-title-row">
        <div>
          <span className="panel-kicker">Segment overrides</span>
          <h2>Editable road sections</h2>
        </div>
        <small>{segments.length} segments</small>
      </div>
      <p style={{ fontSize: '0.8rem', color: '#6b7280', marginBottom: '0.5rem' }}>
        Segment overrides apply only to selected road sections and <strong>take priority over global conditions</strong>.
      </p>
      <div className="segment-table">
        <table>
          <thead>
            <tr>
              <th>Segment</th>
              <th>Traffic</th>
              <th>Accident</th>
              <th>Closure</th>
              <th>Delay (min)</th>
              <th>Speed %</th>
              <th>Risk</th>
            </tr>
          </thead>
          <tbody>
            {segments.map((segment, index) => (
              <tr key={segment.key}>
                <td>
                  <strong>{index + 1}. {segment.from?.stop_name || segment.from?.stop_id}</strong>
                  <small> to {segment.to?.stop_name || segment.to?.stop_id}</small>
                </td>
                <td>
                  <input type="number" min="0" max="100" value={segment.override.traffic_density}
                    onChange={(e) => onChange(segment.key, { ...segment.override, traffic_density: Number(e.target.value) })} />
                </td>
                <td>
                  <input type="number" min="0" max="100" value={segment.override.accident_severity}
                    onChange={(e) => onChange(segment.key, { ...segment.override, accident_severity: Number(e.target.value) })} />
                </td>
                <td>
                  <input type="checkbox" checked={Boolean(segment.override.road_closure)}
                    onChange={(e) => onChange(segment.key, { ...segment.override, road_closure: e.target.checked })} />
                </td>
                <td>
                  <input type="number" min="0" max="240" value={segment.override.extra_delay_min}
                    onChange={(e) => onChange(segment.key, { ...segment.override, extra_delay_min: Number(e.target.value) })} />
                </td>
                <td>
                  <input type="number" min="0" max="100" value={segment.override.speed_reduction}
                    onChange={(e) => onChange(segment.key, { ...segment.override, speed_reduction: Number(e.target.value) })} />
                </td>
                <td>
                  <select value={segment.override.risk_level}
                    onChange={(e) => onChange(segment.key, { ...segment.override, risk_level: e.target.value })}>
                    {RISK_LEVELS.map((r) => <option key={r} value={r}>{r}</option>)}
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

// ── Agent Explanation Panel ──────────────────────────────────────────────────

export function AgentExplanationPanel({ explanation, loading, error }) {
  const [showTechnical, setShowTechnical] = useState(false);

  if (loading) {
    return (
      <div style={{ padding: '1rem', backgroundColor: '#f3f4f6', borderRadius: '6px', textAlign: 'center', color: '#6b7280' }}>
        Generating explanation...
      </div>
    );
  }

  if (error) {
    return (
      <div className="inline-error" style={{ marginBottom: '0.6rem' }}>
        Explanation request failed: {error}
      </div>
    );
  }

  if (!explanation) return null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
      {/* Deterministic explanation — always shown */}
      <div style={{ padding: '1rem', backgroundColor: '#f0fdf4', borderRadius: '6px', border: '1px solid #86efac' }}>
        <div style={{ fontSize: '0.75rem', fontWeight: 'bold', textTransform: 'uppercase', color: '#166534', marginBottom: '4px' }}>Deterministic Explanation</div>
        <p style={{ margin: 0, color: '#14532d', fontSize: '0.9rem' }}>{explanation.deterministic_explanation}</p>
      </div>

      {/* AI explanation — only if allowed */}
      {explanation.should_show_ai && explanation.ai_explanation && (
        <div style={{ padding: '1rem', backgroundColor: '#eff6ff', borderRadius: '6px', border: '1px solid #93c5fd' }}>
          <div style={{ fontSize: '0.75rem', fontWeight: 'bold', textTransform: 'uppercase', color: '#1e40af', marginBottom: '4px' }}>AI Explanation</div>
          <p style={{ margin: 0, color: '#1e3a5f', fontSize: '0.9rem' }}>{explanation.ai_explanation}</p>
        </div>
      )}

      {/* Fallback warning */}
      {explanation.fallback_used && (
        <div style={{ padding: '0.6rem 0.8rem', backgroundColor: '#fef3c7', borderRadius: '6px', border: '1px solid #fbbf24', fontSize: '0.8rem', color: '#92400e' }}>
          AI explanation was blocked or unavailable; deterministic explanation is shown.
          {explanation.generation_error && <span style={{ display: 'block', marginTop: '4px', fontSize: '0.75rem' }}>Reason: {explanation.generation_error}</span>}
        </div>
      )}

      {/* Technical details accordion */}
      <button
        onClick={() => setShowTechnical(!showTechnical)}
        style={{ alignSelf: 'flex-start', background: 'none', border: '1px solid #d1d5db', padding: '0.4rem 0.8rem', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8rem', color: '#6b7280' }}
      >
        {showTechnical ? '▼ Hide technical details' : '▶ Show technical details'}
      </button>

      {showTechnical && (
        <div style={{ padding: '0.8rem', backgroundColor: '#f9fafb', borderRadius: '6px', fontSize: '0.8rem', color: '#374151' }}>
          <div><strong>Generation status:</strong> {explanation.generation_status}</div>
          <div><strong>Ollama model:</strong> {explanation.ollama_model_used || '—'}</div>
          <div><strong>Retriever grade:</strong> {explanation.retriever_grade?.pass ? 'Pass' : 'Fail'} (score: {explanation.retriever_grade?.score})</div>
          <div><strong>Hallucination grade:</strong> {explanation.hallucination_grade?.hallucination_risk}</div>
          <div><strong>Answer grade:</strong> {explanation.answer_grade?.pass ? 'Pass' : 'Fail'} (score: {explanation.answer_grade?.score})</div>
          {explanation.retrieved_context?.length > 0 && (
            <div style={{ marginTop: '0.5rem' }}>
              <strong>Retrieved context sources:</strong>
              <ul style={{ margin: '0.3rem 0', paddingLeft: '1.2rem' }}>
                {explanation.retrieved_context.map((c, i) => (
                  <li key={i}>{c.filename} (score: {c.score?.toFixed(4)})</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function PageHeader({ eyebrow, title, subtitle, children }) {
  return (
    <section className="command-header">
      <div className="command-title">
        <span className="dashboard-eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>
      {children}
    </section>
  );
}

export function LoadingState({ text = 'Loading route intelligence' }) {
  return (
    <div className="dashboard-container">
      <div className="dashboard-shell">
        <section className="command-header">
          <div>
            <span className="dashboard-eyebrow">{text}</span>
            <h1>Preparing workspace</h1>
            <p>Fetching route data and model information.</p>
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

export function ErrorState({ message, onRetry }) {
  return (
    <div className="dashboard-container">
      <div className="dashboard-shell">
        <section className="page-card error-card">
          <span className="dashboard-eyebrow">Connection failed</span>
          <h1>Could not load route data</h1>
          <p>{message}</p>
          <button className="control-btn control-btn--primary" onClick={onRetry}>Retry</button>
        </section>
      </div>
    </div>
  );
}
