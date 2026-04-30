import React, { useState } from 'react';
import Map, { Source, Layer, Marker } from 'react-map-gl/mapbox';
import './MapViewer.css';
import RouteComparisonCard from './RouteComparisonCard';

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN;

const getVehicleLabel = (type) => {
  switch (type) {
    case 'truck':
      return 'TR';
    case 'motorcycle':
    case 'bike':
      return 'MC';
    case 'van':
      return 'VN';
    case 'car':
    default:
      return 'CR';
  }
};

export default function MapViewer({ routes, selectedCourierId, liveCouriers, pendingSuggestions = {}, handleSuggestionDecision }) {
  const [isProcessing, setIsProcessing] = useState(false);
  const [showOriginal, setShowOriginal] = useState(true);
  const activeSuggestion = selectedCourierId !== null ? pendingSuggestions[selectedCourierId] : null;

  const handleAccept = async () => {
    if (!activeSuggestion) return;
    setIsProcessing(true);
    try {
      await handleSuggestionDecision(selectedCourierId, activeSuggestion.suggestion_id, 'accept');
    } finally {
      setIsProcessing(false);
    }
  };

  const handleReject = async () => {
    if (!activeSuggestion) return;
    setIsProcessing(true);
    try {
      await handleSuggestionDecision(selectedCourierId, activeSuggestion.suggestion_id, 'reject');
    } finally {
      setIsProcessing(false);
    }
  };

  const routesToRender = selectedCourierId !== null
    ? routes.filter((route) => route.id === `route-${selectedCourierId}` || route.vehicle_id === selectedCourierId)
    : routes;

  const liveCouriersToRender = selectedCourierId !== null
    ? liveCouriers.filter((courier) => courier.vehicle_id === selectedCourierId)
    : liveCouriers;

  const originalRouteToRender = selectedCourierId !== null
    ? routes.find((route) => route.id === `route-${selectedCourierId}` || route.vehicle_id === selectedCourierId)?.originalGeometry
    : null;

  return (
    <div className="map-viewer-container">
      <div className="map-proof-controls">
        <div>
          <span className="legend-line legend-line--original" />
          <span>Original plan</span>
        </div>
        <div>
          <span className="legend-line legend-line--optimized" />
          <span>Optimized route</span>
        </div>
        <button onClick={() => setShowOriginal((value) => !value)}>
          {showOriginal ? 'Hide original' : 'Show original'}
        </button>
      </div>

      <Map
        initialViewState={{
          longitude: 37.0150,
          latitude: 39.7505,
          zoom: 11,
        }}
        mapStyle="mapbox://styles/mapbox/dark-v11"
        mapboxAccessToken={MAPBOX_TOKEN}
        style={{ width: '100%', height: '100%' }}
      >
        <RouteComparisonCard
          isVisible={!!activeSuggestion}
          explanation="A resequenced route is ready for the selected courier."
          timeSaved={activeSuggestion ? `${activeSuggestion.estimated_time_savings_min || 0} min` : ''}
          affectedStops={activeSuggestion?.new_sequence?.length || 0}
          onAccept={handleAccept}
          onReject={handleReject}
          isProcessing={isProcessing}
        />

        {showOriginal && originalRouteToRender && (
          <Source
            id="original-route-source"
            type="geojson"
            data={originalRouteToRender}
          >
            <Layer
              id="original-route-layer"
              type="line"
              layout={{
                'line-join': 'round',
                'line-cap': 'round',
              }}
              paint={{
                'line-color': '#d1d5db',
                'line-width': 4,
                'line-opacity': 0.72,
                'line-dasharray': [1.6, 1.4],
              }}
            />
          </Source>
        )}

        {routesToRender && routesToRender.map((routeData, index) => (
          <React.Fragment key={`fragment-${routeData.id || index}`}>
            <Source
              id={`route-${routeData.id || index}`}
              type="geojson"
              data={routeData.geometry}
            >
              <Layer
                id={`layer-${routeData.id || index}`}
                type="line"
                layout={{
                  'line-join': 'round',
                  'line-cap': 'round',
                }}
                paint={{
                  'line-color': routeData.color || '#eb5647',
                  'line-width': selectedCourierId === null ? 5 : 6,
                  'line-opacity': selectedCourierId === null ? 0.78 : 0.92,
                }}
              />
            </Source>

            {routeData.stops && routeData.stops.map((stop, stopIndex) => (
              <Marker
                key={`stop-${routeData.id}-${stop.stop_id || stopIndex}`}
                longitude={stop.longitude}
                latitude={stop.latitude}
                anchor="center"
              >
                <div
                  className={`stop-marker ${Number(stop.expected_delay_min || 0) > 0 ? 'stop-marker--delay' : ''}`}
                  style={{ '--marker-color': routeData.color || '#eb5647' }}
                  title={`${stop.stop_name || 'Stop'} ${Number(stop.expected_delay_min || 0) > 0 ? `(Delay: ${stop.expected_delay_min}m)` : ''}`}
                />
              </Marker>
            ))}
          </React.Fragment>
        ))}

        {liveCouriersToRender
          .filter((courier) => (
            Array.isArray(courier.location) &&
            typeof courier.location[0] === 'number' && !Number.isNaN(courier.location[0]) &&
            typeof courier.location[1] === 'number' && !Number.isNaN(courier.location[1])
          ))
          .map((courier) => {
            const matchingRoute = routes.find((route) => route.vehicle_id === courier.vehicle_id);
            const vType = matchingRoute ? matchingRoute.vehicleType : 'car';

            return (
              <Marker
                key={`live-${courier.courier_id}`}
                longitude={courier.location[0]}
                latitude={courier.location[1]}
                anchor="center"
                style={{ transition: 'transform 0.5s linear' }}
              >
                <div
                  className="courier-marker"
                  style={{
                    borderColor: courier.color || 'var(--primary-accent)',
                  }}
                  title={`${courier.name} - ${courier.speed_kmh} km/h`}
                >
                  {getVehicleLabel(vType)}
                </div>
              </Marker>
            );
          })}

        {activeSuggestion && activeSuggestion.geometry && (
          <Source
            id="suggested-route-source"
            type="geojson"
            data={{ type: 'Feature', geometry: activeSuggestion.geometry }}
          >
            <Layer
              id="suggested-route-layer"
              type="line"
              layout={{
                'line-join': 'round',
                'line-cap': 'round',
              }}
              paint={{
                'line-color': '#10b981',
                'line-width': 6,
                'line-opacity': 0.9,
                'line-dasharray': [2, 1.5],
              }}
            />
          </Source>
        )}
      </Map>

      <div className="map-overlay-layer pointer-events-none"></div>
    </div>
  );
}
