"""
Build the OR-Tools cost matrix.

Index 0    -> depot
Index 1..N -> delivery stops

The matrix uses an explicit, auditable cost formula from
`optimization.scoring`. Depot-to-first-stop and last-stop-to-depot legs are
included; older versions left those edges at zero and therefore optimized an
incomplete route.
"""

from __future__ import annotations

from optimization.scoring import score_leg


DEFAULT_TRAVEL_MIN = 30.6


def _planned_times(stops_data: list[dict]) -> list[float]:
    return [float(stop.get("planned_travel_min") or DEFAULT_TRAVEL_MIN) for stop in stops_data]


def _depot_to_stop(stop: dict, fallback_min: float) -> float:
    return float(stop.get("depot_to_stop_travel_min") or fallback_min)


def _stop_to_depot(stop: dict, fallback_min: float) -> float:
    return float(stop.get("stop_to_depot_travel_min") or fallback_min)


def build_cost_matrix_with_breakdown(
    stops_data: list[dict],
    ml_predictions: list[dict],
    use_p90: bool = False,
    travel_time_matrix: list[list[float]] | None = None,
) -> tuple[list[list[int]], list[list[dict]]]:
    """
    Return `(matrix, breakdown)`.

    `matrix` is the integer cost matrix consumed by OR-Tools.
    `breakdown` mirrors the matrix shape and stores every cost component in
    minutes, which lets API responses and tests explain why a leg is expensive.
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

    planned = _planned_times(stops_data)
    size = n + 1
    matrix: list[list[int]] = [[0] * size for _ in range(size)]
    breakdown: list[list[dict]] = [[{} for _ in range(size)] for _ in range(size)]

    for from_node in range(size):
        for to_node in range(size):
            if from_node == to_node:
                leg = score_leg(from_node, to_node, 0.0, None, None, use_p90)
            elif to_node == 0:
                origin_idx = from_node - 1
                travel = _stop_to_depot(stops_data[origin_idx], planned[origin_idx])
                leg = score_leg(from_node, to_node, travel, None, None, use_p90)
            else:
                dest_idx = to_node - 1
                if from_node == 0:
                    travel = _depot_to_stop(stops_data[dest_idx], planned[dest_idx])
                elif travel_time_matrix is not None:
                    origin_idx = from_node - 1
                    travel = float(travel_time_matrix[origin_idx][dest_idx])
                else:
                    travel = planned[dest_idx]

                leg = score_leg(
                    from_node=from_node,
                    to_node=to_node,
                    base_travel_min=travel,
                    destination_stop=stops_data[dest_idx],
                    destination_prediction=ml_predictions[dest_idx],
                    use_p90=use_p90,
                )

            matrix[from_node][to_node] = leg.total_cost_units
            breakdown[from_node][to_node] = leg.to_dict()

    return matrix, breakdown


def build_cost_matrix(
    stops_data: list[dict],
    ml_predictions: list[dict],
    use_p90: bool = False,
    travel_time_matrix: list[list[float]] | None = None,
) -> list[list[int]]:
    """Backward-compatible wrapper returning only the integer matrix."""
    matrix, _ = build_cost_matrix_with_breakdown(
        stops_data=stops_data,
        ml_predictions=ml_predictions,
        use_p90=use_p90,
        travel_time_matrix=travel_time_matrix,
    )
    return matrix
