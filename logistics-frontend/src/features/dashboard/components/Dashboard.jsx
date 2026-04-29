import React, { useEffect } from 'react';
import './Dashboard.css';
import MapViewer from './MapViewer';
import CourierList from './CourierList';
import PackageList from './PackageList';
import { useRouteStore } from '../../../store/useRouteStore';

export default function Dashboard() {
  const {
    loading, error, fetchData,
    routes, couriers, packages,
    routeSummary, explanation,
    selectedCourierId, setSelectedCourier, hasFetched,
    startSimulation, stopSimulation, wsConnected,
    liveCouriers, isConnecting, pendingSuggestions, handleSuggestionDecision
  } = useRouteStore();

  useEffect(() => {
    fetchData();
    return () => stopSimulation();
  }, [fetchData, stopSimulation]);

  const activeCouriersList = Object.values(liveCouriers);

  return (
    <div className="dashboard-container">
      <header className="dashboard-page-header">
        <h1>Live Dispatch Map</h1>

        {loading && (
          <div className="loading-bar-wrapper">
            <div className="loading-bar" />
          </div>
        )}

        {error && (
          <div className="dashboard-error-banner">
            ⚠ {error}
            <button onClick={() => window.location.reload()}>Retry</button>
          </div>
        )}

        <div className="simulation-controls" style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
          <span style={{ fontWeight: 'bold', color: wsConnected ? '#10b981' : (isConnecting ? '#f59e0b' : '#ef4444') }}>
            {wsConnected ? '🟢 Live' : (isConnecting ? '🟡 Connecting...' : '🔴 Offline')}
          </span>
          <button
            onClick={startSimulation}
            disabled={!hasFetched || wsConnected || isConnecting}
            style={{
              padding: '8px 16px',
              background: '#10b981',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: (wsConnected || isConnecting) ? 'not-allowed' : 'pointer',
              opacity: (wsConnected || isConnecting) ? 0.6 : 1
            }}
          >
            {isConnecting ? 'Starting...' : '▶ Start Sim'}
          </button>

          <button
            onClick={stopSimulation}
            disabled={!wsConnected && !isConnecting}
            style={{
              padding: '8px 16px',
              background: '#ef4444',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: (!wsConnected && !isConnecting) ? 'not-allowed' : 'pointer',
              opacity: (!wsConnected && !isConnecting) ? 0.6 : 1
            }}
          >
            ■ Stop Sim
          </button>
        </div>
      </header>

      <main className="dashboard-main">
        <section className="dashboard-kpis">
          {loading
            ? Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="small-kpi-card skeleton-card">
                  <span className="skeleton-line skeleton-label" />
                  <span className="skeleton-line skeleton-value" />
                </div>
              ))
            : routeSummary && (
                <>
                  <div className="small-kpi-card">
                    <span className="label">Overall Risk Score</span>
                    <span className="value">{routeSummary.overall_risk_score}</span>
                  </div>
                  <div className="small-kpi-card">
                    <span className="label">Total Expected Delay</span>
                    <span className="value">{routeSummary.expected_total_delay_min} min</span>
                  </div>
                  <div className="small-kpi-card">
                    <span className="label">Severe Stops</span>
                    <span className="value text-danger">{routeSummary.severe_stop_count}</span>
                  </div>
                  <div className="small-kpi-card">
                    <span className="label">VRP Status</span>
                    <span className="value">{routeSummary.vrp_status}</span>
                  </div>
                </>
              )
          }
        </section>

        <div className="map-and-fleet-container">
          <section className="dashboard-map-section">
            {loading
              ? <div className="skeleton-map"><div className="skeleton-map-pulse" /></div>
              : <MapViewer routes={routes} selectedCourierId={selectedCourierId} liveCouriers={activeCouriersList} pendingSuggestions={pendingSuggestions} handleSuggestionDecision={handleSuggestionDecision} />
            }
          </section>

          <section className="dashboard-couriers-section">
            <h2 className="section-title">Active Fleet</h2>
            <div className="fleet-scroll-area">
              {loading
                ? Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="skeleton-courier-card">
                      <div className="skeleton-avatar" />
                      <div className="skeleton-courier-lines">
                        <span className="skeleton-line" style={{ width: '60%' }} />
                        <span className="skeleton-line" style={{ width: '40%' }} />
                      </div>
                    </div>
                  ))
                : <CourierList couriers={couriers} selectedCourierId={selectedCourierId} onSelectCourier={setSelectedCourier} pendingSuggestions={pendingSuggestions} />
              }
            </div>
          </section>
        </div>

        <section className="dashboard-packages-section">
          <h2 className="section-title">Global Manifest</h2>
          {loading
            ? Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="skeleton-package-row">
                  <span className="skeleton-line" style={{ width: `${50 + i * 8}%` }} />
                </div>
              ))
            : <PackageList packages={packages} />
          }
        </section>
      </main>
    </div>
  );
}
