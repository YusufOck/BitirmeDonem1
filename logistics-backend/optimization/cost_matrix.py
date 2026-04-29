"""
cost_matrix.py
--------------
Build the (N+1) x (N+1) integer cost matrix for OR-Tools.

Index 0  → depot (all zeros)
Index 1…N → delivery stops (mapped from 0-based stops_data / ml_predictions)

Cost formula:
    cost[i][j] = round((travel_time[i→j] + ml_delay[j]) * 100)

Units: 0.01 minutes as integer  (multiply by 100 to keep 2-decimal precision)
"""

from __future__ import annotations

DEFAULT_TRAVEL_MIN = 30.6   # dataset median fallback when planned_travel_min is missing


def build_cost_matrix(
    stops_data: list[dict],
    ml_predictions: list[dict],
    use_p90: bool = False,
    travel_time_matrix: list[list[float]] | None = None,
) -> list[list[int]]:
    """
    Build an (N+1) x (N+1) integer cost matrix for OR-Tools.

    Parameters
    ----------
    stops_data : list[dict]
        N delivery stop dicts. Each may contain 'planned_travel_min' (float).
    ml_predictions : list[dict]
        Output of predict_route()['stop_predictions'], one per stop.
        Must contain 'expected_delay_min'; optionally 'delay_p90_min'.
    use_p90 : bool
        If True, use delay_p90_min for the ML cost addend (conservative routing).
        Falls back to expected_delay_min when delay_p90_min is None.
    travel_time_matrix : list[list[float]] | None
        Optional NxN float matrix of leg durations (minutes) between delivery
        stops. Rows/cols are 0-based delivery-stop indices.
        If None, planned_travel_min of the destination stop is used as
        travel time from *any* predecessor (approximation for demo / no-Mapbox mode).

    Returns
    -------
    list[list[int]]
        (N+1) x (N+1) matrix. Row/col 0 = depot (all zeros).
        cost[i][j] = round((travel_time_i_to_j + ml_delay_j) * 100)
    """
    n = len(stops_data)
    if n == 0:
        raise ValueError("stops_data must contain at least one stop")
    if len(ml_predictions) != n:
        raise ValueError(
            f"stops_data length ({n}) must match ml_predictions length ({len(ml_predictions)})"
        )
    if travel_time_matrix is not None:
        if len(travel_time_matrix) != n or any(len(row) != n for row in travel_time_matrix):
            raise ValueError(
                f"travel_time_matrix must be {n}x{n} but got shape "
                f"{len(travel_time_matrix)}x{len(travel_time_matrix[0]) if travel_time_matrix else 0}"
            )

    # Extract per-stop planned travel times (fallback when no NxN matrix)
    planned_times: list[float] = [
        float(s.get("planned_travel_min") or DEFAULT_TRAVEL_MIN)
        for s in stops_data
    ]

    # Extract ML delay values per stop
    delays: list[float] = []
    for pred in ml_predictions:
        if use_p90:
            val = pred.get("delay_p90_min")
            if val is None:
                val = pred.get("expected_delay_min", 0.0)
        else:
            val = pred.get("expected_delay_min", 0.0)
        delays.append(max(float(val), 0.0))   # clamp negatives to 0

    # Build (N+1) x (N+1) matrix with depot at index 0
    size = n + 1
    matrix: list[list[int]] = [[0] * size for _ in range(size)]

    for i in range(1, size):      # from-node (1-based delivery index)
        for j in range(1, size):  # to-node
            if i == j:
                matrix[i][j] = 0
                continue
            stop_j = j - 1        # 0-based delivery index for destination
            stop_i = i - 1        # 0-based delivery index for origin

            if travel_time_matrix is not None:
                travel = float(travel_time_matrix[stop_i][stop_j])
            else:
                # Fallback: use destination stop's planned travel time
                travel = planned_times[stop_j]

            raw_cost = travel + delays[stop_j]
            matrix[i][j] = int(round(raw_cost * 100))

    # Depot row (0) and depot column (0) remain all zeros (already initialized)
    return matrix
