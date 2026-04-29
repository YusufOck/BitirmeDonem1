import React, { useEffect, useCallback, useState, useRef } from 'react';
import Map, { Source, Layer, Marker, useMap } from 'react-map-gl/mapbox';
import 'mapbox-gl/dist/mapbox-gl.css';
import './CourierMap.css';

const TOKEN = import.meta.env.VITE_MAPBOX_TOKEN;

function MapController({ livePosition, stops }) {
  const { current: map } = useMap();
  const lastFlyRef = useRef(0);

  useEffect(() => {
    if (!map) return;
    if (livePosition) {
      const now = Date.now();
      if (now - lastFlyRef.current < 800) return;
      lastFlyRef.current = now;
      map.easeTo({ center: [livePosition.longitude, livePosition.latitude], zoom: 15, duration: 600 });
    } else if (stops?.length) {
      const lngs = stops.map(s => s.longitude);
      const lats = stops.map(s => s.latitude);
      map.fitBounds(
        [[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]],
        { padding: 60, duration: 800 }
      );
    }
  }, [livePosition, map, stops]);

  return null;
}

function validCoord(lng, lat) {
  return typeof lng === 'number' && typeof lat === 'number' && !isNaN(lng) && !isNaN(lat);
}

/**
 * Slices a GeoJSON LineString feature to only show the portion AHEAD of the courier.
 * Falls back to the full geometry if no live position yet.
 */
function sliceRouteAhead(geometry, livePosition) {
  if (!geometry || !livePosition) return geometry;
  const coords = geometry.geometry?.coordinates;
  if (!coords?.length) return geometry;

  const { longitude: lng, latitude: lat } = livePosition;
  let minDist = Infinity;
  let closestIdx = 0;
  for (let i = 0; i < coords.length; i++) {
    const d = (coords[i][0] - lng) ** 2 + (coords[i][1] - lat) ** 2;
    if (d < minDist) { minDist = d; closestIdx = i; }
  }

  const ahead = coords.slice(closestIdx);
  if (ahead.length < 2) return null;
  return { ...geometry, geometry: { ...geometry.geometry, coordinates: ahead } };
}

export default function CourierMap({ backendGeometry, mapboxGeometry, routeColor, stops, livePosition }) {
  const [userPanned, setUserPanned] = useState(false);

  const handleRecenter = useCallback(() => {
    setUserPanned(false);
  }, []);

  const validStops = (stops ?? []).filter(s => validCoord(s.longitude, s.latitude));
  const firstStop = validStops[0];

  // Primary display: use backend geometry (same as dispatcher), fall back to mapbox
  const primaryGeometry = backendGeometry ?? mapboxGeometry;

  // Show the road ahead (slice from current position)
  const aheadGeometry = sliceRouteAhead(primaryGeometry, livePosition);
  // Show the full route dimmed behind
  const behindGeometry = primaryGeometry;

  const lineColor = routeColor || '#3b82f6';

  const initialViewState = {
    longitude: firstStop?.longitude ?? 37.015,
    latitude: firstStop?.latitude ?? 39.75,
    zoom: 13,
  };

  return (
    <div className="courier-map-container">
      <Map
        initialViewState={initialViewState}
        style={{ width: '100%', height: '100%' }}
        mapStyle="mapbox://styles/mapbox/dark-v11"
        mapboxAccessToken={TOKEN}
        onDragStart={() => setUserPanned(true)}
      >
        {!userPanned && <MapController livePosition={livePosition} stops={validStops} />}

        {/* Full route — dimmed, shows completed + remaining */}
        {behindGeometry && (
          <Source id="courier-route-behind" type="geojson" data={behindGeometry}>
            <Layer
              id="courier-route-behind-layer"
              type="line"
              layout={{ 'line-join': 'round', 'line-cap': 'round' }}
              paint={{
                'line-color': lineColor,
                'line-width': 5,
                'line-opacity': 0.25,
              }}
            />
          </Source>
        )}

        {/* Route ahead — bright, showing remaining route */}
        {aheadGeometry && (
          <Source id="courier-route-ahead" type="geojson" data={aheadGeometry}>
            <Layer
              id="courier-route-ahead-layer"
              type="line"
              layout={{ 'line-join': 'round', 'line-cap': 'round' }}
              paint={{
                'line-color': lineColor,
                'line-width': 6,
                'line-opacity': 0.9,
              }}
            />
          </Source>
        )}

        {/* Stop markers */}
        {validStops.map((stop, i) => (
          <Marker
            key={stop.stop_sequence ?? i}
            longitude={stop.longitude}
            latitude={stop.latitude}
            anchor="center"
          >
            <div className="stop-marker">
              <span className="stop-marker-num">{stop.stop_sequence ?? i + 1}</span>
            </div>
          </Marker>
        ))}

        {/* Live position marker */}
        {livePosition && validCoord(livePosition.longitude, livePosition.latitude) && (
          <Marker
            longitude={livePosition.longitude}
            latitude={livePosition.latitude}
            anchor="center"
          >
            <div className="live-marker">
              <div className="live-marker-pulse" />
              <div className="live-marker-dot" />
            </div>
          </Marker>
        )}
      </Map>

      {userPanned && (
        <button className="recenter-btn" onClick={handleRecenter}>
          Re-center
        </button>
      )}
    </div>
  );
}
