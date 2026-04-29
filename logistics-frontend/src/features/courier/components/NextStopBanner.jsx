import React from 'react';
import './NextStopBanner.css';

export default function NextStopBanner({ stop, stopIndex, totalStops }) {
  if (!stop) return null;

  return (
    <div className="next-stop-banner">
      <span className="next-stop-label">NEXT STOP</span>
      <span className="next-stop-name">{stop.stop_name}</span>
      <span className="next-stop-progress">
        Stop {stopIndex} of {totalStops}
      </span>
    </div>
  );
}
