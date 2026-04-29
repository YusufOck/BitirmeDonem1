import React, { useState } from 'react';
import './StopList.css';

export default function StopList({ stops, currentStopIndex }) {
  const [expanded, setExpanded] = useState(false);

  const remaining = stops?.slice(currentStopIndex) ?? [];
  if (!remaining.length) return null;

  return (
    <div className="stop-list">
      <button
        className="stop-list-toggle"
        onClick={() => setExpanded(v => !v)}
        aria-expanded={expanded}
      >
        <span>Remaining Stops ({remaining.length})</span>
        <span className="stop-list-chevron">{expanded ? '▲' : '▼'}</span>
      </button>

      {expanded && (
        <ul className="stop-list-items">
          {remaining.map((stop, i) => (
            <li key={stop.stop_sequence ?? i} className={`stop-list-item ${i === 0 ? 'stop-list-item--next' : ''}`}>
              <span className="stop-list-seq">{stop.stop_sequence ?? currentStopIndex + i + 1}</span>
              <span className="stop-list-name">{stop.stop_name}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
