import React from 'react';
import './NavigationPanel.css';

function getManeuverIcon(type, modifier) {
  if (type === 'arrive') return '●';
  if (type === 'depart') return '↑';
  if (type === 'roundabout' || type === 'rotary') return '↻';
  if (!modifier) return '↑';
  switch (modifier) {
    case 'left': return '←';
    case 'slight left': return '↖';
    case 'sharp left': return '↰';
    case 'right': return '→';
    case 'slight right': return '↗';
    case 'sharp right': return '↱';
    case 'uturn': return '↩';
    default: return '↑';
  }
}

function formatDistance(meters) {
  if (meters == null) return '';
  if (meters < 1000) return `in ${Math.round(meters)}m`;
  return `in ${(meters / 1000).toFixed(1)}km`;
}

export default function NavigationPanel({ step, distanceMeters, livePosition }) {
  if (!livePosition) {
    return (
      <div className="nav-panel nav-panel--waiting">
        <span className="nav-panel-icon">📡</span>
        <div className="nav-panel-text">
          <span className="nav-panel-instruction">Connecting...</span>
          <span className="nav-panel-distance">Waiting for live position</span>
        </div>
      </div>
    );
  }

  if (!step) return null;

  const icon = getManeuverIcon(step.maneuver?.type, step.maneuver?.modifier);
  const instruction = step.maneuver?.instruction ?? step.name ?? 'Continue';
  const distance = formatDistance(distanceMeters);

  return (
    <div className="nav-panel">
      <span className="nav-panel-icon" aria-hidden="true">{icon}</span>
      <div className="nav-panel-text">
        <span className="nav-panel-instruction">{instruction}</span>
        {distance && <span className="nav-panel-distance">{distance}</span>}
      </div>
    </div>
  );
}
