import React from 'react';
import './RouteComparisonCard.css';

export default function RouteComparisonCard({
  timeSaved,
  affectedStops,
  explanation,
  isVisible,
  onAccept,
  onReject,
  isProcessing,
}) {
  if (!isVisible) return null;

  return (
    <div className="route-card-container">
      <p className="route-card-explanation">
        {explanation}
      </p>

      <div className="route-card-metrics">
        <div className="route-card-row">
          <span className="route-card-label">Delay reduction</span>
          <span className="route-card-value-positive">{timeSaved}</span>
        </div>
        <div className="route-card-row">
          <span className="route-card-label">Stops reordered</span>
          <span className="route-card-value-neutral">{affectedStops}</span>
        </div>
      </div>

      <div className="route-card-actions">
        <button
          className="route-card-button route-card-button--reject"
          onClick={onReject}
          disabled={isProcessing}
        >
          Reject
        </button>
        <button
          className="route-card-button route-card-button--accept"
          onClick={onAccept}
          disabled={isProcessing}
        >
          {isProcessing ? 'Processing' : 'Accept'}
        </button>
      </div>
    </div>
  );
}
