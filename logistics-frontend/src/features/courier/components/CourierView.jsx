import React, { useEffect, useState, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useRouteStore } from '../../../store/useRouteStore';
import { useDirections, getCurrentStepIndex, getDistanceToStep } from '../hooks/useDirections';
import { useCourierWebSocket } from '../hooks/useCourierWebSocket';
import CourierMap from './CourierMap';
import NavigationPanel from './NavigationPanel';
import NextStopBanner from './NextStopBanner';
import StopList from './StopList';
import './CourierView.css';

export default function CourierView() {
  const { id } = useParams();
  const courierId = parseInt(id, 10);
  const navigate = useNavigate();

  const fetchData = useRouteStore(s => s.fetchData);
  const loading = useRouteStore(s => s.loading);
  const error = useRouteStore(s => s.error);
  const hasFetched = useRouteStore(s => s.hasFetched);
  const couriers = useRouteStore(s => s.couriers);
  const routes = useRouteStore(s => s.routes);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const courierData = couriers.find(c => c.id === courierId);

  // Get this courier's pre-computed backend route geometry (same source as dispatcher map)
  const backendRoute = routes.find(r => r.vehicle_id === courierId);
  const backendGeometry = backendRoute?.geometry ?? null;
  const routeColor = backendRoute?.color ?? '#3b82f6';

  // Mapbox Directions API: used for turn-by-turn navigation steps
  const { steps, routeGeometry: mapboxGeometry, loading: directionsLoading } = useDirections(courierData?.stops);
  const { livePosition, wsConnected } = useCourierWebSocket(courierId);

  const [currentStepIndex, setCurrentStepIndex] = useState(0);
  const [distanceToNext, setDistanceToNext] = useState(null);
  const stepIndexRef = useRef(0);

  useEffect(() => {
    if (!livePosition || !steps.length) return;
    const newIndex = getCurrentStepIndex(livePosition, steps, stepIndexRef.current);
    stepIndexRef.current = newIndex;
    setCurrentStepIndex(newIndex);
    setDistanceToNext(getDistanceToStep(livePosition, steps[newIndex]));
  }, [livePosition, steps]);

  const stops = courierData?.stops ?? [];
  const totalStops = stops.length;

  const completedStops = steps
    .slice(0, currentStepIndex + 1)
    .filter(s => s.maneuver?.type === 'arrive').length;
  const currentStopDisplayIndex = Math.min(completedStops + 1, totalStops);
  const nextStop = stops[completedStops] ?? null;

  const storeLoading = loading && !hasFetched;

  if (storeLoading) {
    return (
      <div className="courier-view courier-view--loading">
        <div className="courier-spinner" />
        <p className="courier-loading-text">Loading your route...</p>
      </div>
    );
  }

  if (hasFetched && !courierData) {
    return (
      <div className="courier-view courier-view--error">
        <span className="courier-error-icon">⚠</span>
        <p className="courier-error-title">Courier not found</p>
        <p className="courier-error-sub">No route assigned to Courier ID {courierId}.</p>
        <button className="courier-back-btn" onClick={() => navigate('/')}>Back to Dispatcher</button>
      </div>
    );
  }

  if (error) {
    return (
      <div className="courier-view courier-view--error">
        <span className="courier-error-icon">⚠</span>
        <p className="courier-error-title">Failed to load route</p>
        <p className="courier-error-sub">{error}</p>
        <button className="courier-back-btn" onClick={() => fetchData()}>Retry</button>
      </div>
    );
  }

  return (
    <div className="courier-view">
      <header className="courier-topbar">
        <button className="courier-back-icon" onClick={() => navigate('/')} aria-label="Back">
          ←
        </button>
        <span className="courier-title">{courierData?.name ?? `Courier ${courierId}`}</span>
        <span className={`courier-ws-pill ${wsConnected ? 'courier-ws-pill--live' : 'courier-ws-pill--off'}`}>
          {wsConnected ? 'Live' : 'Offline'}
        </span>
        {directionsLoading && <span className="courier-route-loading">Route loading…</span>}
      </header>

      <CourierMap
        backendGeometry={backendGeometry}
        mapboxGeometry={mapboxGeometry}
        routeColor={routeColor}
        stops={stops}
        livePosition={livePosition}
      />

      <NavigationPanel
        step={steps[currentStepIndex]}
        distanceMeters={distanceToNext}
        livePosition={livePosition}
      />

      <NextStopBanner
        stop={nextStop}
        stopIndex={currentStopDisplayIndex}
        totalStops={totalStops}
      />

      <StopList
        stops={stops}
        currentStopIndex={completedStops}
      />
    </div>
  );
}
