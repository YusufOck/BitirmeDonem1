import React from 'react';
import './CourierCard.css';

const formatNumber = (value, digits = 1) => {
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(digits).replace(/\.0$/, '') : '0';
};

export default function CourierCard({ courier, isSelected, onSelect, hasSuggestion }) {
  const metrics = courier.routeMetrics || {};
  const comparison = courier.comparison;
  const statusTone = courier.statusTone || 'success';

  return (
    <div
      className={`courier-card glass-panel courier-card--${statusTone} ${isSelected ? 'selected' : ''} ${hasSuggestion ? 'has-suggestion' : ''}`}
      style={{ '--route-color': courier.routeColor }}
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') onSelect();
      }}
    >
      {hasSuggestion && (
        <div className="suggestion-badge">
          New route suggestion
        </div>
      )}

      <div className="courier-card-header">
        <div className="courier-profile">
          <div className="courier-avatar">
            {courier.initials}
          </div>
          <div className="courier-info">
            <h3 className="courier-name">{courier.name}</h3>
            <span className={`courier-status courier-status--${statusTone}`}>
              {courier.currentStatus}
            </span>
          </div>
        </div>
        <div className="stops-badge">
          <span className="stops-count">{courier.stopsRemaining}</span>
          <span className="stops-label">Left</span>
        </div>
      </div>

      <div className="courier-mini-stats">
        <span>{formatNumber(metrics.expectedDelayMin)} min delay</span>
        <span>{formatNumber(metrics.distanceKm)} km</span>
        <span>{metrics.durationMin || 0} min route</span>
      </div>

      <div className="stops-timeline">
        {courier.stops.map((stop, index) => {
          const isSevere = stop.severity === 'severe' || stop.will_miss_window;
          const isWarning = Number(stop.expected_delay_min || 0) > 0 && !isSevere;
          const isCompleted = stop.status === 'completed';

          return (
            <div key={stop.stop_id || index} className={`timeline-item ${isSevere ? 'severe' : ''} ${isWarning ? 'warning' : ''} ${isCompleted ? 'completed' : ''}`}>
              <div className="timeline-node">
                <span>{isCompleted ? '' : stop.displaySequence || index + 1}</span>
              </div>
              <div className="timeline-content">
                <span className="stop-name">
                  {stop.stop_name || 'Unknown Stop'}
                </span>
                <div className="stop-meta-row">
                  <span className={isCompleted ? 'text-success' : isSevere ? 'text-danger' : isWarning ? 'text-warning' : 'text-success'}>
                    {isCompleted ? 'Completed' : stop.etaLabel}
                  </span>
                  <span>{stop.plannedTravelLabel}</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {isSelected && (
        <div className="optimization-details">
          <h4>Route Metrics</h4>
          <div className="metrics-grid">
            <div className="metric-box">
              <span className="metric-value">{formatNumber(metrics.expectedDelayMin)}m</span>
              <span className="metric-label">Predicted Delay</span>
            </div>
            <div className="metric-box">
              <span className="metric-value">{formatNumber(metrics.distanceKm)}km</span>
              <span className="metric-label">Distance</span>
            </div>
            <div className="metric-box">
              <span className="metric-value">{metrics.durationMin || 0}m</span>
              <span className="metric-label">Duration</span>
            </div>
          </div>
          {comparison && (
            <div className="comparison-strip">
              <span>Original: {formatNumber(comparison.originalDistanceKm)} km / {comparison.originalDurationMin} min</span>
              <span>Optimized: {formatNumber(comparison.optimizedDistanceKm)} km / {comparison.optimizedDurationMin} min</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
