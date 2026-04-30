import { useRouteStore } from '../../../store/useRouteStore';

export function useCourierWebSocket(courierId) {
  const liveCouriers = useRouteStore(s => s.liveCouriers);
  const wsConnected = useRouteStore(s => s.wsConnected);
  const livePosition = liveCouriers[`courier-${courierId}`] ?? null;

  return { livePosition, wsConnected };
}
