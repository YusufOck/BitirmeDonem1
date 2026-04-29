import { useEffect } from 'react';
import { useRouteStore } from '../../../store/useRouteStore';

export function useCourierWebSocket(courierId) {
  const liveCouriers = useRouteStore(s => s.liveCouriers);
  const wsConnected = useRouteStore(s => s.wsConnected);
  const isConnecting = useRouteStore(s => s.isConnecting);
  const routes = useRouteStore(s => s.routes);
  const startSimulation = useRouteStore(s => s.startSimulation);

  useEffect(() => {
    if (wsConnected || isConnecting || routes.length === 0) return;
    startSimulation();
  }, [wsConnected, isConnecting, routes.length, startSimulation]);

  const livePosition = liveCouriers[`courier-${courierId}`] ?? null;

  return { livePosition, wsConnected };
}
