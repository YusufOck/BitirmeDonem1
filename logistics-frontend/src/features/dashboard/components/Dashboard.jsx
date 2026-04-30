import React, { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import './Dashboard.css';
import MapViewer from './MapViewer';
import CourierList from './CourierList';
import PackageList from './PackageList';
import { useRouteStore } from '../../../store/useRouteStore';

const formatNumber = (value, digits = 1) => {
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(digits).replace(/\.0$/, '') : '0';
};

export default function Dashboard() {
  const navigate = useNavigate();
  const {
    loading, error, fetchData, forceFetchData,
    routes, couriers, packages,
    routeSummary,
    selectedCourierId, setSelectedCourier, hasFetched,
    startSimulation, stopSimulation, wsConnected,
    liveCouriers, isConnecting, pendingSuggestions, handleSuggestionDecision,
  } = useRouteStore();

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const activeCouriersList = Object.values(liveCouriers);
  const selectedCourier = couriers.find((courier) => courier.id === selectedCourierId) || couriers[0];
  const selectedRoute = routes.find((route) => route.vehicle_id === selectedCourier?.id) || routes[0];
  const selectedComparison = selectedRoute?.comparison;
  const totalStops = packages.length;
  const delayedStops = packages.filter((pkg) => Number(pkg.expected_delay_min || 0) > 0).length;
  const completedStops = packages.filter((pkg) => pkg.status === 'completed').length;
  const onTimeStops = Math.max(totalStops - delayedStops, 0);
  const deliveryProgress = totalStops > 0 ? Math.round((completedStops / totalStops) * 100) : 0;
  const healthScore = routeSummary
    ? Math.max(0, 10 - Number(routeSummary.overall_risk_score || 0) * 10)
    : 0;
  const routeStatus = routeSummary?.vrp_status === 'success' ? 'Optimized' : 'Needs Review';
  const showDelayAlert = delayedStops > 0;
  const selectedStops = selectedRoute?.stops || [];
  const topDelayStops = selectedStops
    .filter((stop) => Number(stop.expected_delay_min || 0) > 0)
    .sort((a, b) => Number(b.expected_delay_min || 0) - Number(a.expected_delay_min || 0))
    .slice(0, 3);
  const buildDelayFactors = (stop) => {
    const factors = [];
    if (Number.isFinite(Number(stop.delay_probability))) {
      factors.push(`${Math.round(Number(stop.delay_probability) * 100)}% delay probability`);
    }
    if (Number(stop.planned_travel_min || 0) > 0) {
      factors.push(`${formatNumber(stop.planned_travel_min)} min incoming drive`);
    }
    if (Number(stop.time_window_slack_min || 0) <= 90) {
      factors.push(`${formatNumber(stop.time_window_slack_min)} min time-window slack`);
    }
    if (stop.risk_level && stop.risk_level !== 'low') {
      factors.push(`${stop.risk_level} risk level`);
    }
    if (stop.will_miss_window) {
      factors.push('time window at risk');
    }
    return factors.slice(0, 3);
  };

  return (
    <div className="dashboard-container">
      <header className="dashboard-page-header">
        <div>
          <span className="dashboard-eyebrow">SBTU Logistics Control Room</span>
          <h1>Dispatch Center</h1>
          <p className="dashboard-subtitle">
            Real courier assignments, optimized routes, and delay risk from the live backend.
          </p>
        </div>

        <div className="dashboard-header-actions">
          <div className={`live-pill ${wsConnected ? 'live-pill--on' : isConnecting ? 'live-pill--pending' : 'live-pill--off'}`}>
            <span className="live-dot" />
            {wsConnected ? 'Live simulation' : isConnecting ? 'Connecting' : 'Simulation off'}
          </div>
          <button
            className="control-btn control-btn--success"
            onClick={startSimulation}
            disabled={!hasFetched || wsConnected || isConnecting}
          >
            {isConnecting ? 'Starting' : 'Start'}
          </button>
          <button
            className="control-btn control-btn--danger"
            onClick={stopSimulation}
            disabled={!wsConnected && !isConnecting}
          >
            Stop
          </button>
          <button
            className="control-btn control-btn--neutral"
            onClick={forceFetchData}
            disabled={loading}
          >
            Refresh
          </button>
        </div>

        {loading && (
          <div className="loading-bar-wrapper">
            <div className="loading-bar" />
          </div>
        )}

        {error && (
          <div className="dashboard-error-banner">
            <span>{error}</span>
            <button onClick={forceFetchData}>Retry</button>
          </div>
        )}
      </header>

      <main className="dashboard-main">
        {!loading && routeSummary && (
          <section className={`ops-alert ${showDelayAlert ? 'ops-alert--warning' : 'ops-alert--ok'}`}>
            <div>
              <strong>
                {showDelayAlert
                  ? `${delayedStops} stops need delay watch`
                  : 'All planned stops are on track'}
              </strong>
              <span>
                {showDelayAlert
                  ? `${formatNumber(routeSummary.expected_total_delay_min)} total expected delay minutes across ${couriers.length} couriers.`
                  : `${totalStops} stops are planned with no active delay warning.`}
              </span>
            </div>
            {selectedCourier && (
              <button onClick={() => navigate(`/courier/${selectedCourier.id}`)}>
                Open courier view
              </button>
            )}
          </section>
        )}

        {!loading && selectedComparison && (
          <section className="optimization-proof glass-panel">
            <div className="proof-copy">
              <span className="proof-eyebrow">Optimization proof</span>
              <h2>Original plan vs optimized route</h2>
              <p>
                The gray dashed route shows the original stop order from the database.
                The colored route shows the OR-Tools optimized order after ML delay scoring.
              </p>
            </div>
            <div className="proof-metrics">
              <div>
                <span>Original</span>
                <strong>{formatNumber(selectedComparison.originalDistanceKm)} km</strong>
                <small>{selectedComparison.originalDurationMin} min</small>
              </div>
              <div>
                <span>Optimized</span>
                <strong>{formatNumber(selectedComparison.optimizedDistanceKm)} km</strong>
                <small>{selectedComparison.optimizedDurationMin} min</small>
              </div>
              <div className={selectedComparison.distanceSaved ? 'proof-positive' : 'proof-neutral'}>
                <span>Distance impact</span>
                <strong>
                  {selectedComparison.distanceSaved ? '-' : '+'}
                  {formatNumber(Math.abs(selectedComparison.distanceDeltaKm))} km
                </strong>
                <small>{selectedComparison.distanceSaved ? 'saved' : 'trade-off'}</small>
              </div>
              <div className={selectedComparison.durationSaved ? 'proof-positive' : 'proof-neutral'}>
                <span>Time impact</span>
                <strong>
                  {selectedComparison.durationSaved ? '-' : '+'}
                  {Math.abs(selectedComparison.durationDeltaMin)} min
                </strong>
                <small>{selectedComparison.durationSaved ? 'saved' : 'trade-off'}</small>
              </div>
            </div>
          </section>
        )}

        {!loading && routeSummary && (
          <section className="decision-explainers">
            <div className="decision-panel glass-panel">
              <span className="proof-eyebrow">Optimization method</span>
              <h2>What is being optimized?</h2>
              <p>
                The original route is the database stop order. The optimized route is selected by OR-Tools using
                Mapbox road travel times and ML delay scores. The ML model predicts delay risk; OR-Tools chooses the stop order.
              </p>
              <ul>
                <li>Road distance and duration from Mapbox, not straight-line distance.</li>
                <li>Per-stop expected delay and risk from the trained ML model.</li>
                <li>Stop order, previous-leg travel time, time-window slack, and cascading delay pressure.</li>
              </ul>
            </div>

            <div className="decision-panel glass-panel">
              <span className="proof-eyebrow">Expected delay drivers</span>
              <h2>Why does delay appear?</h2>
              {topDelayStops.length > 0 ? (
                <div className="delay-driver-list">
                  {topDelayStops.map((stop) => (
                    <div key={stop.stop_id || stop.stop_name} className="delay-driver-row">
                      <strong>{stop.stop_name || 'Unnamed stop'}</strong>
                      <span>{formatNumber(stop.expected_delay_min)} min expected delay</span>
                      <small>{buildDelayFactors(stop).join(' | ')}</small>
                    </div>
                  ))}
                </div>
              ) : (
                <p>No delayed stops are predicted for the selected courier.</p>
              )}
              <p className="data-note">
                Live accident and weather feeds are not connected yet, so the UI does not claim a specific crash or storm.
              </p>
            </div>
          </section>
        )}

        <section className="dashboard-kpis">
          {loading
            ? Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="small-kpi-card skeleton-card">
                <span className="skeleton-line skeleton-label" />
                <span className="skeleton-line skeleton-value" />
              </div>
            ))
            : routeSummary && (
              <>
                <div className="small-kpi-card small-kpi-card--good">
                  <span className="label">Route Health</span>
                  <span className="value">{formatNumber(healthScore)}/10</span>
                  <span className="kpi-note">Lower risk is better</span>
                </div>
                <div className="small-kpi-card small-kpi-card--warning">
                  <span className="label">Expected Delay</span>
                  <span className="value">{formatNumber(routeSummary.expected_total_delay_min)} min</span>
                  <span className="kpi-note">ML delay prediction</span>
                </div>
                <div className="small-kpi-card">
                  <span className="label">Delayed Stops</span>
                  <span className="value">{delayedStops}</span>
                  <span className="kpi-note">{onTimeStops} currently on time</span>
                </div>
                <div className="small-kpi-card">
                  <span className="label">Delivery Progress</span>
                  <span className="value">{completedStops}/{totalStops}</span>
                  <span className="kpi-note">{deliveryProgress}% completed</span>
                </div>
                <div className="small-kpi-card small-kpi-card--good">
                  <span className="label">Route Status</span>
                  <span className="value">{routeStatus}</span>
                  <span className="kpi-note">OR-Tools result</span>
                </div>
              </>
            )}
        </section>

        <div className="map-and-fleet-container">
          <section className="dashboard-map-section">
            {loading
              ? <div className="skeleton-map"><div className="skeleton-map-pulse" /></div>
              : (
                <MapViewer
                  routes={routes}
                  selectedCourierId={selectedCourierId}
                  liveCouriers={activeCouriersList}
                  pendingSuggestions={pendingSuggestions}
                  handleSuggestionDecision={handleSuggestionDecision}
                />
              )}
          </section>

          <section className="dashboard-couriers-section">
            <div className="section-heading">
              <h2 className="section-title">Active Fleet</h2>
              <span>{couriers.length} couriers</span>
            </div>
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
                : (
                  <CourierList
                    couriers={couriers}
                    selectedCourierId={selectedCourierId}
                    onSelectCourier={setSelectedCourier}
                    pendingSuggestions={pendingSuggestions}
                  />
                )}
            </div>
          </section>
        </div>

        <section className="dashboard-packages-section">
          <div className="section-heading">
            <h2 className="section-title">Stop Manifest</h2>
            <span>{totalStops} planned stops</span>
          </div>
          {loading
            ? Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="skeleton-package-row">
                <span className="skeleton-line" style={{ width: `${50 + i * 8}%` }} />
              </div>
            ))
            : <PackageList packages={packages} />}
        </section>
      </main>
    </div>
  );
}
