import React, { useState } from 'react';
import Map, { Source, Layer, Marker } from 'react-map-gl/mapbox';
import './MapViewer.css';
import RouteComparisonCard from './RouteComparisonCard';

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN;

const renderCourierIcon = (type) => {
  switch (type) {
    case 'truck': return '🚚';
    case 'motorcycle': return '🏍️';
    case 'car': default: return '🚗';
    case 'van': return '🚐';
  }
};

export default function MapViewer({ routes, selectedCourierId, liveCouriers, pendingSuggestions = {}, handleSuggestionDecision }) {
  const [isProcessing, setIsProcessing] = useState(false);

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
    ? routes.filter(r => r.id === `route-${selectedCourierId}` || r.vehicle_id === selectedCourierId)
    : routes;

  // 2. Filter live markers so they disappear if another courier is selected
  const liveCouriersToRender = selectedCourierId !== null
    ? liveCouriers.filter(c => c.vehicle_id === selectedCourierId)
    : liveCouriers;

  const naiveRouteToRender = selectedCourierId !== null
    ? routes.find(r => r.id === `route-${selectedCourierId}` || r.vehicle_id === selectedCourierId)?.naiveGeometry
    : null;

  const firstRoute = routesToRender?.[0];
  let startPoint = null;
  let endPoint = null;

  if (firstRoute?.stops?.length > 0) {
    const firstStop = firstRoute.stops[0];
    const lastStop = firstRoute.stops[firstRoute.stops.length - 1];

    startPoint = [firstStop.latitude, firstStop.longitude];
    endPoint = [lastStop.latitude, lastStop.longitude];

  } else if (firstRoute?.geometry?.geometry?.coordinates?.length > 0) {
    // Ultimate fallback if stops array is empty but geometry exists
    const coords = firstRoute.geometry.geometry.coordinates;
    startPoint = [coords[0][1], coords[0][0]];
    endPoint = [coords[coords.length - 1][1], coords[coords.length - 1][0]];
  }


  return (
    <div className="map-viewer-container">
      <Map
        initialViewState={{
          longitude: 37.0150,
          latitude: 39.7505,
          zoom: 11
        }}
        mapStyle="mapbox://styles/mapbox/dark-v11"
        mapboxAccessToken={MAPBOX_TOKEN}
        style={{ width: '100%', height: '100%' }}
      >
        <RouteComparisonCard
          isVisible={!!activeSuggestion}
          explanation="A new optimized route is available to reduce expected delay."
          timeSaved={activeSuggestion ? `${activeSuggestion.estimated_time_savings_min || 0} mins` : ''}
          kmsDifference="-1.2 km"
          moneySaved="$2.50"
          onAccept={handleAccept}
          onReject={handleReject}
          isProcessing={isProcessing}
        />
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
                  'line-cap': 'round'
                }}
                paint={{
                  'line-color': routeData.color || '#eb5647',
                  'line-width': 5,
                  'line-opacity': 0.8
                }}
              />
            </Source>

            {routeData.stops && routeData.stops.map((stop, sIndex) => (
              <Marker
                key={`stop-${routeData.id}-${stop.stop_id || sIndex}`}
                longitude={stop.longitude}
                latitude={stop.latitude}
                anchor="center"
              >
                <div
                  className="stop-marker"
                  style={{
                    backgroundColor: routeData.color || '#eb5647',
                    border: '2px solid var(--surface-color)',
                    width: '12px',
                    height: '12px',
                    borderRadius: '50%',
                    boxShadow: '0 0 4px rgba(0,0,0,0.5)',
                    cursor: 'pointer'
                  }}
                  title={`${stop.stop_name || 'Stop'} ${stop.expected_delay_min > 0 ? `(Delay: ${stop.expected_delay_min}m)` : ''}`}
                />
              </Marker>
            ))}
          </React.Fragment>
        ))}

        {liveCouriersToRender
          .filter(courier =>
            Array.isArray(courier.location) &&
            typeof courier.location[0] === 'number' && !isNaN(courier.location[0]) &&
            typeof courier.location[1] === 'number' && !isNaN(courier.location[1])
          )
          .map((courier) => {
          // Attempt to find the matching static route to grab the specific vehicleType (car/truck/etc)
          const matchingRoute = routes.find(r => r.vehicle_id === courier.vehicle_id);
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
                {renderCourierIcon(vType)}
              </div>
            </Marker>
          );
        })}

        {naiveRouteToRender && (
          <Source
            id="naive-route-source"
            type="geojson"
            data={naiveRouteToRender}
          >
            <Layer
              id="naive-route-layer"
              type="line"
              layout={{
                'line-join': 'round',
                'line-cap': 'round'
              }}
              paint={{
                'line-color': '#707070',
                'line-width': 4,
                'line-opacity': 0.6,
                'line-dasharray': [2, 2]
              }}
            />
          </Source>
        )}

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
                'line-cap': 'round'
              }}
              paint={{
                'line-color': '#10b981',
                'line-width': 6,
                'line-opacity': 0.9,
                'line-dasharray': [2, 1.5]
              }}
            />
          </Source>
        )}
      </Map>

      {/* Overlay to give it a premium feel */}
      <div className="map-overlay-layer pointer-events-none"></div>
    </div>
  );
}
