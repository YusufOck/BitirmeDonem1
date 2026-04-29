import React from 'react';
import './RouteComparisonCard.css';

export default function RouteComparisonCard({
  timeSaved,
  moneySaved,
  kmsDifference,
  explanation,
  isVisible,
  onAccept,
  onReject,
  isProcessing
}) {
  if (!isVisible) return null;

  return (
    <div className="route-card-container">
      {/* Explanation Header */}
      <p className="route-card-explanation">
        {explanation}
      </p>

      {/* Metrics Grid */}
      <div className="route-card-metrics">

        {/* Time Row */}
        <div className="route-card-row">
          <span className="route-card-label">
            ⏱️ Time Saved
          </span>
          <span className="route-card-value-positive">
            {timeSaved}
          </span>
        </div>

        {/* Distance Row */}
        <div className="route-card-row">
          <span className="route-card-label">
            📏 Distance
          </span>
          <span className="route-card-value-negative">
            {kmsDifference}
          </span>
        </div>

        {/* Money Row */}
        <div className="route-card-row">
          <span className="route-card-label">
            💰 Cost Saved
          </span>
          <span className="route-card-value-positive">
            {moneySaved}
          </span>
        </div>

      </div>

      <div className="route-card-actions" style={{ display: 'flex', gap: '10px', marginTop: '15px' }}>
        <button 
          onClick={onReject} 
          disabled={isProcessing}
          style={{ flex: 1, padding: '8px', background: 'var(--danger)', color: 'white', border: 'none', borderRadius: '4px', cursor: isProcessing ? 'not-allowed' : 'pointer' }}
        >
          Reject
        </button>
        <button 
          onClick={onAccept} 
          disabled={isProcessing}
          style={{ flex: 1, padding: '8px', background: 'var(--success)', color: 'white', border: 'none', borderRadius: '4px', cursor: isProcessing ? 'not-allowed' : 'pointer' }}
        >
          {isProcessing ? 'Processing...' : 'Accept'}
        </button>
      </div>
    </div>
  );
}