import React, { useState } from 'react';

const round = (v, d = 1) => {
  const n = Number(v);
  return Number.isFinite(n) ? Number(n.toFixed(d)) : null;
};

// ── Stop Order Row ────────────────────────────────────────────────────────────
function StopOrderRow({ label, ids, tone }) {
  if (!ids || ids.length === 0) return null;
  const color = tone === 'success' ? '#10b981' : tone === 'warning' ? '#f59e0b' : '#6b7280';
  return (
    <div className="rcd-order-row">
      <span className="rcd-order-label">{label}</span>
      <div className="rcd-order-chain">
        {ids.map((id, i) => (
          <React.Fragment key={i}>
            <span className="rcd-stop-chip">{id || '—'}</span>
            {i < ids.length - 1 && <span className="rcd-arrow" style={{ color }}>→</span>}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

// ── Leg Change Row ────────────────────────────────────────────────────────────
function LegRow({ leg, currentCandidate }) {
  // Find current-order leg for this segment (from current_candidate selected_leg_paths)
  const currentLegs = currentCandidate?.selected_leg_paths || [];
  const currentLeg = currentLegs.find(
    (l) => String(l.from_stop_id) === String(leg.from_stop_id) && String(l.to_stop_id) === String(leg.to_stop_id),
  );

  const recDist = round(leg.distance_km, 2);
  const recDur = round(leg.duration_min, 1);
  const curDist = round(currentLeg?.distance_km, 2);
  const curDur = round(currentLeg?.duration_min, 1);
  const timeSaved = (curDur !== null && recDur !== null) ? round(curDur - recDur, 1) : null;
  const pathRank = Number(leg.path_rank ?? leg.rank ?? 1);
  const reasons = Array.isArray(leg.reasons) ? leg.reasons : [];
  const penaltyMin = round(leg.segment_penalty_min, 2);

  return (
    <div className="rcd-leg-row">
      <div className="rcd-leg-header">
        <span className="rcd-leg-label">{leg.from_stop_id} → {leg.to_stop_id}</span>
        {pathRank > 1 && <span className="rcd-badge rcd-badge--blue">Alt path #{pathRank}</span>}
        {timeSaved !== null && timeSaved > 0 && (
          <span className="rcd-badge rcd-badge--green">−{timeSaved} min</span>
        )}
        {penaltyMin !== null && penaltyMin > 0 && (
          <span className="rcd-badge rcd-badge--orange">+{penaltyMin} min penalty</span>
        )}
      </div>

      <div className="rcd-leg-metrics">
        {(curDist !== null || curDur !== null) && (
          <div className="rcd-leg-metric rcd-leg-metric--current">
            <span className="rcd-metric-label">Current</span>
            <span>{curDist !== null ? `${curDist} km` : '—'}</span>
            <span>{curDur !== null ? `${curDur} min` : '—'}</span>
          </div>
        )}
        <div className="rcd-leg-metric rcd-leg-metric--recommended">
          <span className="rcd-metric-label">Recommended</span>
          <span>{recDist !== null ? `${recDist} km` : '—'}</span>
          <span>{recDur !== null ? `${recDur} min` : '—'}</span>
        </div>
      </div>

      {reasons.length > 0 && (
        <div className="rcd-leg-reasons">
          {reasons.map((r, i) => (
            <span key={i} className="rcd-reason-chip">{r.replaceAll('_', ' ')}</span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────
export default function RecommendationChangeDetails({ scenarioResult }) {
  const [legExpanded, setLegExpanded] = useState(false);

  if (!scenarioResult) return null;

  const {
    order_changed: orderChanged,
    sequence_before: seqBefore,
    sequence_after: seqAfter,
    change_type: changeType,
    no_better_route: noBetterRoute,
    candidates_evaluated: candidatesEval,
    path_alternatives_evaluated: pathAlts,
    stop_orders_evaluated: ordersEval,
    selected_candidate_id: selectedId,
    selected_candidate: selectedCand,
    validation,
    optimization_delta: delta,
    recommendation_allowed: allowed,
    recommendation_reason: reason,
  } = scenarioResult;

  const pathChanged = changeType === 'path_changed' || changeType === 'both_changed';
  const legPaths = selectedCand?.selected_leg_paths || [];
  // Only show legs where alternative path was chosen (rank > 1) OR penalty > 0
  const changedLegs = legPaths.filter((l) => Number(l.path_rank ?? l.rank ?? 1) > 1 || Number(l.segment_penalty_min ?? 0) > 0);
  const currentCand = null; // current candidate leg comparison — use available data from selected_candidate context

  const validationPassed = validation?.valid !== false;
  const missingStops = validation?.missing_stop_ids || [];
  const unexpectedStops = validation?.unexpected_stop_ids || [];

  const orderBadgeText = orderChanged ? 'Stop order changed' : 'Stop order unchanged';
  const orderBadgeTone = orderChanged ? 'warning' : 'success';

  return (
    <div className="rcd-root">

      {/* ── A) Stop Order ── */}
      <div className="rcd-section">
        <div className="rcd-section-header">
          <span className="rcd-section-title">Stop order</span>
          <span className={`rcd-badge rcd-badge--${orderBadgeTone}`}>{orderBadgeText}</span>
        </div>

        {seqBefore && seqBefore.length > 0 && (
          <StopOrderRow label="Before" ids={seqBefore} tone="neutral" />
        )}
        {seqAfter && seqAfter.length > 0 && (
          <StopOrderRow
            label="After"
            ids={seqAfter}
            tone={orderChanged ? 'warning' : 'success'}
          />
        )}

        {!orderChanged && pathChanged && (
          <div className="rcd-note">
            Stop order is unchanged. Only the road path between stops changed.
          </div>
        )}
      </div>

      {/* ── B) Road Path Changes ── */}
      {pathChanged && (
        <div className="rcd-section">
          <div className="rcd-section-header">
            <span className="rcd-section-title">Road path changes</span>
            {changedLegs.length > 0
              ? <span className="rcd-badge rcd-badge--blue">{changedLegs.length} leg{changedLegs.length > 1 ? 's' : ''} changed</span>
              : <span className="rcd-badge rcd-badge--neutral">No alternative legs</span>}
          </div>

          {changedLegs.length === 0 && legPaths.length > 0 && (
            <div className="rcd-note">
              All legs use primary paths. Cost improvement comes from stop order or condition reduction, not alternative road paths.
            </div>
          )}

          {changedLegs.length === 0 && legPaths.length === 0 && (
            <div className="rcd-note">
              Detailed per-leg geometry metrics unavailable from backend for this candidate.
            </div>
          )}

          {changedLegs.length > 0 && (
            <>
              {(legExpanded ? changedLegs : changedLegs.slice(0, 3)).map((leg, i) => (
                <LegRow key={i} leg={leg} currentCandidate={currentCand} />
              ))}
              {changedLegs.length > 3 && (
                <button className="rcd-expand-btn" onClick={() => setLegExpanded((v) => !v)}>
                  {legExpanded ? 'Show less ▲' : `Show ${changedLegs.length - 3} more ▼`}
                </button>
              )}
            </>
          )}
        </div>
      )}

      {/* ── C) Decision Proof ── */}
      <div className="rcd-section">
        <div className="rcd-section-header">
          <span className="rcd-section-title">Decision proof</span>
          <span className={`rcd-badge rcd-badge--${validationPassed ? 'green' : 'danger'}`}>
            {validationPassed ? 'Validation passed' : 'Validation failed'}
          </span>
        </div>

        <div className="rcd-proof-grid">
          {candidatesEval > 0 && (
            <div className="rcd-proof-item">
              <span className="rcd-proof-value">{candidatesEval}</span>
              <span className="rcd-proof-label">Route candidates evaluated</span>
            </div>
          )}
          {ordersEval > 0 && (
            <div className="rcd-proof-item">
              <span className="rcd-proof-value">{ordersEval}</span>
              <span className="rcd-proof-label">Stop orders evaluated</span>
            </div>
          )}
          {pathAlts > 0 && (
            <div className="rcd-proof-item">
              <span className="rcd-proof-value">{pathAlts}</span>
              <span className="rcd-proof-label">Path alternatives evaluated</span>
            </div>
          )}
          {selectedId && (
            <div className="rcd-proof-item">
              <span className="rcd-proof-value" style={{ fontSize: '0.78rem' }}>{selectedId}</span>
              <span className="rcd-proof-label">Selected candidate</span>
            </div>
          )}
          {delta?.improved_stop_count > 0 && (
            <div className="rcd-proof-item">
              <span className="rcd-proof-value" style={{ color: '#10b981' }}>{delta.improved_stop_count}</span>
              <span className="rcd-proof-label">Stops improved</span>
            </div>
          )}
          {delta?.worsened_stop_count > 0 && (
            <div className="rcd-proof-item">
              <span className="rcd-proof-value" style={{ color: '#f59e0b' }}>{delta.worsened_stop_count}</span>
              <span className="rcd-proof-label">Stops worsened</span>
            </div>
          )}
        </div>

        {missingStops.length > 0 && (
          <div className="rcd-validation-error">
            Missing stops: {missingStops.join(', ')}
          </div>
        )}
        {unexpectedStops.length > 0 && (
          <div className="rcd-validation-error">
            Unexpected stops: {unexpectedStops.join(', ')}
          </div>
        )}

        {noBetterRoute && reason && (
          <div className="rcd-note rcd-note--warning">
            {reason}
          </div>
        )}

        {allowed === false && !noBetterRoute && (
          <div className="rcd-note rcd-note--warning">
            Recommendation is blocked. Apply button is disabled.
          </div>
        )}
      </div>
    </div>
  );
}
