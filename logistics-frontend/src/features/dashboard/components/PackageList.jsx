import React, { useState } from 'react';
import './PackageList.css';

const formatNumber = (value, digits = 1) => {
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(digits).replace(/\.0$/, '') : '0';
};

const getRiskClass = (riskLevel) => {
  if (riskLevel === 'high') return 'status-pending';
  if (riskLevel === 'low') return 'status-delivered';
  return 'status-in-transit';
};

const getTopSignal = (pkg) => {
  const factors = Array.isArray(pkg.delay_factors) ? pkg.delay_factors : [];
  const priorityFactor = factors.find((factor) => factor.severity === 'danger')
    || factors.find((factor) => factor.severity === 'warning')
    || factors[0];

  if (!priorityFactor) return null;
  return `${priorityFactor.label}: ${priorityFactor.value}`;
};

export default function PackageList({ packages }) {
  const [searchTerm, setSearchTerm] = useState('');

  const filteredPackages = (packages || []).filter((pkg) => {
    const term = searchTerm.toLowerCase();
    return (
      (pkg.stop_id && String(pkg.stop_id).toLowerCase().includes(term)) ||
      (pkg.stop_name && pkg.stop_name.toLowerCase().includes(term)) ||
      (pkg.courierName && pkg.courierName.toLowerCase().includes(term)) ||
      (pkg.risk_level && pkg.risk_level.toLowerCase().includes(term)) ||
      (pkg.severity && pkg.severity.toLowerCase().includes(term))
    );
  });

  return (
    <div className="package-list-container glass-panel">
      <div className="package-list-header">
        <div className="search-bar">
          <svg className="search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="8"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
          <input
            type="text"
            placeholder="Search stops, couriers, risk levels..."
            value={searchTerm}
            onChange={(event) => setSearchTerm(event.target.value)}
          />
        </div>
      </div>

      <div className="table-responsive">
        <table className="package-table">
          <thead>
            <tr>
              <th>Stop ID</th>
              <th>Stop Name</th>
              <th>Courier</th>
              <th>Expected Delay</th>
              <th>Top Signal</th>
              <th>Risk</th>
              <th>Severity</th>
            </tr>
          </thead>
          <tbody>
            {filteredPackages.length > 0 ? (
              filteredPackages.map((pkg, idx) => (
                <tr key={pkg.stop_id || idx}>
                  <td className="font-mono">{pkg.stop_id || `STOP-${idx}`}</td>
                  <td>{pkg.stop_name || 'Unnamed stop'}</td>
                  <td>
                    <span className="courier-chip">{pkg.courierName || `Courier ${pkg.vehicle_id + 1}`}</span>
                  </td>
                  <td className={`eta-cell ${Number(pkg.expected_delay_min || 0) > 0 ? 'text-warning' : 'text-success'}`}>
                    {Number(pkg.expected_delay_min || 0) > 0
                      ? `${formatNumber(pkg.expected_delay_min)} min`
                      : 'On time'}
                  </td>
                  <td>
                    {getTopSignal(pkg)
                      ? <span className="signal-chip">{getTopSignal(pkg)}</span>
                      : <span className="signal-muted">No active signal</span>}
                  </td>
                  <td>
                    <span className={`status-badge ${getRiskClass(pkg.risk_level)}`}>
                      {pkg.risk_level || 'unknown'}
                    </span>
                  </td>
                  <td>
                    <span className={`status-badge ${pkg.severity === 'severe' ? 'status-unassigned border-danger text-danger' : 'status-in-transit'}`}>
                      {pkg.severity || 'unknown'}
                    </span>
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan="7" className="no-results">
                  No stops found matching "{searchTerm}"
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
