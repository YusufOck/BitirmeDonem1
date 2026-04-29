import React, { useState } from 'react';
import './PackageList.css';

export default function PackageList({ packages }) {
  const [searchTerm, setSearchTerm] = useState('');

  const filteredPackages = (packages || []).filter(pkg => 
    (pkg.stop_id && pkg.stop_id.toLowerCase().includes(searchTerm.toLowerCase())) ||
    (pkg.stop_name && pkg.stop_name.toLowerCase().includes(searchTerm.toLowerCase())) ||
    (pkg.risk_level && pkg.risk_level.toLowerCase().includes(searchTerm.toLowerCase())) ||
    (pkg.severity && pkg.severity.toLowerCase().includes(searchTerm.toLowerCase()))
  );

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
            placeholder="Search stops, IDs, risk levels..." 
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </div>
      </div>
      
      <div className="table-responsive">
        <table className="package-table">
          <thead>
            <tr>
              <th>Stop ID</th>
              <th>Stop Name</th>
              <th>Assigned Courier</th>
              <th>Expected Delay</th>
              <th>Risk Level</th>
              <th>Severity</th>
            </tr>
          </thead>
          <tbody>
            {filteredPackages.length > 0 ? (
              filteredPackages.map((pkg, idx) => (
                <tr key={pkg.stop_id || idx}>
                  <td className="font-mono">{pkg.stop_id || `STOP-${idx}`}</td>
                  <td>{pkg.stop_name}</td>
                  <td>
                    <span className="courier-chip">Courier {pkg.vehicle_id}</span>
                  </td>
                  <td className={`eta-cell ${pkg.expected_delay_min > 0 ? 'text-warning' : 'text-success'}`}>
                    {pkg.expected_delay_min > 0 ? `${pkg.expected_delay_min} min` : 'On Time'}
                  </td>
                  <td>
                    <span className={`status-badge status-${pkg.risk_level === 'high' ? 'pending' : (pkg.risk_level === 'low' ? 'delivered' : 'in-transit')}`}>
                      {pkg.risk_level}
                    </span>
                  </td>
                  <td>
                    <span className={`status-badge ${pkg.severity === 'severe' ? 'status-unassigned border-danger text-danger' : 'status-in-transit'}`}>
                      {pkg.severity}
                    </span>
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan="6" className="no-results">
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
