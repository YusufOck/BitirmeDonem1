import React, { useState } from 'react';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import './AnalyticsDashboard.css';

// Mock Data
const onTimeData = [
  { name: 'Mon', rate: 92 },
  { name: 'Tue', rate: 94 },
  { name: 'Wed', rate: 89 },
  { name: 'Thu', rate: 95 },
  { name: 'Fri', rate: 91 },
  { name: 'Sat', rate: 98 },
  { name: 'Sun', rate: 96 },
];

const courierPerformanceData = [
  { name: 'Alex J.', deliveries: 145, onTime: 140 },
  { name: 'Sarah D.', deliveries: 132, onTime: 130 },
  { name: 'Mike T.', deliveries: 120, onTime: 105 },
  { name: 'Elena R.', deliveries: 158, onTime: 154 },
];

export default function AnalyticsDashboard() {
  return (
    <div className="analytics-container">
      <header className="analytics-page-header">
        <h1>System Analytics</h1>
      </header>

      <main className="analytics-main">
        {/* KPI Scorecards */}
        <div className="kpi-grid">
          <div className="kpi-card glass-panel">
            <h3>Total Packages Delivered</h3>
            <div className="kpi-value">5,423</div>
            <div className="kpi-trend positive">↑ 12% from last week</div>
          </div>
          <div className="kpi-card glass-panel">
            <h3>Overall On-Time Rate</h3>
            <div className="kpi-value">94.2%</div>
            <div className="kpi-trend positive">↑ 1.5% from last week</div>
          </div>
          <div className="kpi-card glass-panel danger-border">
            <h3>Active Delays</h3>
            <div className="kpi-value">12</div>
            <div className="kpi-trend negative">↓ 4 less than yesterday</div>
          </div>
          <div className="kpi-card glass-panel">
            <h3>Active Couriers</h3>
            <div className="kpi-value">24</div>
            <div className="kpi-trend neutral">No change</div>
          </div>
        </div>

        {/* Charts Row */}
        <div className="charts-grid">
          <div className="chart-container glass-panel">
            <h2>On-Time Delivery Rate (Last 7 Days)</h2>
            <div className="chart-wrapper">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={onTimeData} margin={{ top: 20, right: 30, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                  <XAxis dataKey="name" stroke="var(--text-secondary)" />
                  <YAxis stroke="var(--text-secondary)" domain={[80, 100]} />
                  <Tooltip
                    contentStyle={{ backgroundColor: 'var(--surface-color)', borderColor: 'var(--border-color)', color: 'var(--text-primary)' }}
                    itemStyle={{ color: 'var(--text-primary)' }}
                  />
                  <Line type="monotone" dataKey="rate" stroke="var(--primary-accent)" strokeWidth={3} dot={{ r: 6, fill: 'var(--primary-accent)', strokeWidth: 2, stroke: 'var(--surface-color)' }} activeDot={{ r: 8 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="chart-container glass-panel">
            <h2>Courier Performance (Deliveries vs. On-Time)</h2>
            <div className="chart-wrapper">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={courierPerformanceData} margin={{ top: 20, right: 30, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                  <XAxis dataKey="name" stroke="var(--text-secondary)" />
                  <YAxis stroke="var(--text-secondary)" />
                  <Tooltip
                    contentStyle={{ backgroundColor: 'var(--surface-color)', borderColor: 'var(--border-color)', color: 'var(--text-primary)' }}
                  />
                  <Legend wrapperStyle={{ paddingTop: '20px' }} />
                  <Bar dataKey="deliveries" fill="var(--text-tertiary)" name="Total Deliveries" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="onTime" fill="var(--success)" name="On Time" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>

        {/* Recent Delays Table */}
        <div className="delays-section glass-panel">
          <h2>Recent Package Delays</h2>
          <div className="table-responsive">
            <table className="package-table">
              <thead>
                <tr>
                  <th>Tracking ID</th>
                  <th>Courier</th>
                  <th>Delay Reason</th>
                  <th>Expected ETA</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="font-mono text-primary">PKG-1088</td>
                  <td>Alex J.</td>
                  <td>Traffic incident on D300</td>
                  <td><span className="text-danger">14:00 (+2h)</span></td>
                </tr>
                <tr>
                  <td className="font-mono text-primary">PKG-1092</td>
                  <td>Sarah D.</td>
                  <td>Customer not at home</td>
                  <td><span className="text-warning">Pending Delivery</span></td>
                </tr>
                <tr>
                  <td className="font-mono text-primary">PKG-1045</td>
                  <td>Mike T.</td>
                  <td>Vehicle breakdown</td>
                  <td><span className="text-danger">Tomorrow Morning</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </main>
    </div>
  );
}
