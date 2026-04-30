import React, { useEffect } from 'react';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import { useRouteStore } from '../../../store/useRouteStore';
import './AnalyticsDashboard.css';

const formatNumber = (value, digits = 1) => {
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(digits).replace(/\.0$/, '') : '0';
};

export default function AnalyticsDashboard() {
  const { fetchData, hasFetched, loading, error, packages, couriers, routeSummary } = useRouteStore();

  useEffect(() => {
    if (!hasFetched) fetchData();
  }, [fetchData, hasFetched]);

  const totalStops = packages.length;
  const delayedStops = packages.filter((pkg) => Number(pkg.expected_delay_min || 0) > 0).length;
  const onTimeStops = Math.max(totalStops - delayedStops, 0);
  const onTimeRate = totalStops > 0 ? (onTimeStops / totalStops) * 100 : 0;
  const highRiskStops = packages.filter((pkg) => pkg.risk_level === 'high').length;
  const severeStops = packages.filter((pkg) => pkg.severity === 'severe').length;

  const stopRiskData = packages.slice(0, 12).map((pkg, index) => ({
    name: pkg.stop_name || `Stop ${index + 1}`,
    delay: Number(pkg.expected_delay_min || 0),
    risk: Math.round(Number(pkg.delay_probability || 0) * 100),
  }));

  const courierPerformanceData = couriers.map((courier) => {
    const stops = courier.stops || [];
    const delayed = stops.filter((stop) => Number(stop.expected_delay_min || 0) > 0).length;
    return {
      name: courier.name,
      stops: stops.length,
      onTime: Math.max(stops.length - delayed, 0),
      delayed,
    };
  });

  const recentDelays = packages
    .filter((pkg) => Number(pkg.expected_delay_min || 0) > 0)
    .sort((a, b) => Number(b.expected_delay_min || 0) - Number(a.expected_delay_min || 0))
    .slice(0, 5);

  return (
    <div className="analytics-container">
      <header className="analytics-page-header">
        <span className="analytics-eyebrow">Operational Analytics</span>
        <h1>Route Performance</h1>
        <p>These numbers come from the current optimized route, not a static demo dataset.</p>
      </header>

      <main className="analytics-main">
        {error && <div className="analytics-error">{error}</div>}

        <div className="kpi-grid">
          <div className="kpi-card glass-panel">
            <h3>Planned Stops</h3>
            <div className="kpi-value">{loading ? '--' : totalStops}</div>
            <div className="kpi-trend neutral">{couriers.length} active couriers</div>
          </div>
          <div className="kpi-card glass-panel">
            <h3>On-Time Rate</h3>
            <div className="kpi-value">{loading ? '--' : `${formatNumber(onTimeRate)}%`}</div>
            <div className="kpi-trend positive">{onTimeStops} stops on time</div>
          </div>
          <div className="kpi-card glass-panel danger-border">
            <h3>Delay Watch</h3>
            <div className="kpi-value">{loading ? '--' : delayedStops}</div>
            <div className="kpi-trend negative">{formatNumber(routeSummary?.expected_total_delay_min || 0)} expected minutes</div>
          </div>
          <div className="kpi-card glass-panel">
            <h3>High Risk</h3>
            <div className="kpi-value">{loading ? '--' : highRiskStops}</div>
            <div className="kpi-trend neutral">{severeStops} severe stops</div>
          </div>
        </div>

        <div className="charts-grid">
          <div className="chart-container glass-panel">
            <h2>Stop Delay and Risk</h2>
            <div className="chart-wrapper">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={stopRiskData} margin={{ top: 20, right: 30, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                  <XAxis dataKey="name" stroke="var(--text-secondary)" tick={{ fontSize: 11 }} />
                  <YAxis stroke="var(--text-secondary)" />
                  <Tooltip
                    contentStyle={{ backgroundColor: 'var(--surface-color)', borderColor: 'var(--border-color)', color: 'var(--text-primary)' }}
                    itemStyle={{ color: 'var(--text-primary)' }}
                  />
                  <Legend wrapperStyle={{ paddingTop: '20px' }} />
                  <Line type="monotone" dataKey="delay" name="Delay min" stroke="var(--warning)" strokeWidth={3} dot={{ r: 4, fill: 'var(--warning)' }} />
                  <Line type="monotone" dataKey="risk" name="Risk %" stroke="var(--primary-accent)" strokeWidth={3} dot={{ r: 4, fill: 'var(--primary-accent)' }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="chart-container glass-panel">
            <h2>Courier Workload</h2>
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
                  <Bar dataKey="onTime" fill="var(--success)" name="On Time" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="delayed" fill="var(--warning)" name="Delayed" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>

        <div className="delays-section glass-panel">
          <h2>Highest Delay Stops</h2>
          <div className="table-responsive">
            <table className="package-table">
              <thead>
                <tr>
                  <th>Stop</th>
                  <th>Courier</th>
                  <th>Delay</th>
                  <th>Risk</th>
                </tr>
              </thead>
              <tbody>
                {recentDelays.length > 0 ? recentDelays.map((pkg) => (
                  <tr key={pkg.stop_id}>
                    <td className="font-mono text-primary">{pkg.stop_name || pkg.stop_id}</td>
                    <td>{pkg.courierName}</td>
                    <td><span className="text-warning">{formatNumber(pkg.expected_delay_min)} min</span></td>
                    <td><span className={pkg.risk_level === 'high' ? 'text-danger' : 'text-warning'}>{pkg.risk_level}</span></td>
                  </tr>
                )) : (
                  <tr>
                    <td colSpan="4" className="analytics-empty">No delayed stops in the current plan.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </main>
    </div>
  );
}
