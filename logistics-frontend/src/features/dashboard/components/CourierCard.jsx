import React from 'react';
import './CourierCard.css';

export default function CourierCard({ courier, isSelected, onSelect, hasSuggestion }) {
  const { optimizationMetrics } = courier;
  return (
    <div 
      className={`courier-card glass-panel ${courier.currentStatus === 'At Risk' ? 'border-danger' : ''} ${isSelected ? 'selected' : ''} ${hasSuggestion ? 'has-suggestion' : ''}`} 
      style={{ '--route-color': courier.routeColor }}
      onClick={onSelect}
    >
      {hasSuggestion && (
        <div className="suggestion-badge" style={{
          position: 'absolute', top: 0, right: 0, left: 0,
          background: 'var(--primary-accent)', color: 'white',
          fontSize: '0.75rem', fontWeight: 'bold', textAlign: 'center',
          padding: '2px 0', zIndex: 10, animation: 'pulse 2s infinite'
        }}>
          New Route Suggestion!
        </div>
      )}
      <div className="courier-card-header" style={{ marginTop: hasSuggestion ? '12px' : '0' }}>
        <div className="courier-profile">
          <div className="courier-avatar">
            {courier.initials}
          </div>
          <div className="courier-info">
            <h3 className="courier-name">{courier.name}</h3>
            <span className={`courier-status ${courier.currentStatus === 'At Risk' ? 'text-danger' : 'text-primary'}`}>{courier.currentStatus}</span>
          </div>
        </div>
        <div className="stops-badge">
          <span className="stops-count">{courier.stopsRemaining}</span>
          <span className="stops-label">Left</span>
        </div>
      </div>
      
      <div className="stops-timeline">
        {courier.stops.map((stop, index) => {
          const isSevere = stop.severity === 'severe' || stop.will_miss_window;
          const isWarning = stop.expected_delay_min > 0 && !isSevere;
          const isCompleted = stop.status === 'completed';
          
          return (
            <div key={index} className={`timeline-item ${isSevere ? 'severe' : ''} ${isWarning ? 'warning' : ''} ${isCompleted ? 'completed' : ''}`}>
              <div className="timeline-node" style={{
                background: isCompleted ? 'var(--success)' : '',
                borderColor: isCompleted ? 'var(--success)' : ''
              }}>
                {isCompleted && <span style={{ color: 'white', fontSize: '8px', position: 'absolute', top: '-1px', left: '2px' }}>✓</span>}
              </div>
              <div className="timeline-content" style={{ opacity: isCompleted ? 0.6 : 1 }}>
                <span className="stop-name" style={{ textDecoration: isCompleted ? 'line-through' : 'none' }}>
                  {stop.stop_name || 'Unknown Stop'}
                </span>
                {isCompleted ? (
                  <span className="stop-eta text-success">Completed</span>
                ) : stop.expected_delay_min > 0 ? (
                  <span className={`stop-eta ${isSevere ? 'text-danger' : 'text-warning'}`}>
                    Delay: {stop.expected_delay_min} mins
                  </span>
                ) : (
                  <span className="stop-eta text-success">On Time</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {isSelected && optimizationMetrics && (
        <div className="optimization-details">
          <h4>Optimization Analytics</h4>
          <div className="metrics-grid">
            <div className="metric-box text-success">
              <span className="metric-value">{optimizationMetrics.timeSavedMin}m</span>
              <span className="metric-label">Saved</span>
            </div>
            <div className="metric-box text-warning">
              <span className="metric-value">{optimizationMetrics.distanceSavedKm}km</span>
              <span className="metric-label">Less</span>
            </div>
            <div className="metric-box text-primary">
              <span className="metric-value">${optimizationMetrics.moneySaved}</span>
              <span className="metric-label">Saved</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
