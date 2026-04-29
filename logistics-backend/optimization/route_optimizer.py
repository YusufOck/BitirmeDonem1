"""
route_optimizer.py
------------------
High-level orchestration layer: ML inference → cost matrix → VRP → merged result.

This is the only module in optimization/ that imports from ml/.
"""

from __future__ import annotations

import sys
import os

# Ensure the project root is on sys.path so `ml` package is importable
# regardless of the working directory the caller uses.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from ml.inference import predict_route  # noqa: E402

from optimization.cost_matrix import build_cost_matrix
from optimization.time_windows import parse_time_windows
from optimization.vrp_solver import solve_vrp


class RouteOptimizer:
    """
    Orchestrates the full ML → OR-Tools optimization pipeline.

    Usage
    -----
    optimizer = RouteOptimizer()          # instantiate once (model loaded at import time)
    result = optimizer.optimize(stops)    # call per request
    """

    def optimize(
        self,
        stops_input: list[dict],
        use_p90: bool = False,
        num_vehicles: int = 1,
        time_limit_seconds: int = 30,
        travel_time_matrix: list[list[float]] | None = None,
    ) -> dict:
        """
        Full pipeline: ML prediction → cost matrix → VRP → merged result.

        Parameters
        ----------
        stops_input : list[dict]
            Delivery stop feature dicts in the format accepted by predict_route().
            Do NOT include the depot — it is prepended internally.
            At minimum each stop needs the required ML fields
            (stop_sequence, cumulative_delay_min, etc.).
        use_p90 : bool
            Route conservatively using P90 (worst-case) delay estimates.
        num_vehicles : int
            Number of delivery vehicles.
        time_limit_seconds : int
            OR-Tools solver wall-clock budget in seconds.
        travel_time_matrix : list[list[float]] | None
            NxN matrix of leg durations between delivery stops (minutes).
            If None, planned_travel_min per destination stop is used as fallback.

        Returns
        -------
        dict with keys:
            ml_predictions   : dict            — raw predict_route() output
            vrp_result       : dict            — raw solve_vrp() output
            optimized_route  : list[dict]      — stops in OR-Tools order + ML fields
            route_summary    : dict            — ML route_summary merged with VRP metadata
            use_p90          : bool
            num_vehicles     : int
        """
        if len(stops_input) < 2:
            raise ValueError("At least 2 delivery stops are required for optimization")

        # ── Step 0: Prepare stops for ML inference ────────────────────────────
        # - Strip None values so ml/inference.py can apply its own defaults
        # - Compute stop_progress_ratio if not provided
        total = len(stops_input)
        stops_prepared: list[dict] = []
        for i, raw_stop in enumerate(stops_input):
            # Remove keys with None values — ML inference fills defaults itself
            stop = {k: v for k, v in raw_stop.items() if v is not None}
            if "stop_progress_ratio" not in stop:
                stop["stop_progress_ratio"] = (i + 1) / total
            stops_prepared.append(stop)

        # ── Step 1: ML inference ──────────────────────────────────────────────
        ml_result = predict_route(stops_prepared)
        stop_preds: list[dict] = ml_result["stop_predictions"]

        # ── Step 2: Build integer cost matrix (N+1 × N+1) ────────────────────
        cost_matrix = build_cost_matrix(
            stops_data=stops_prepared,
            ml_predictions=stop_preds,
            use_p90=use_p90,
            travel_time_matrix=travel_time_matrix,
        )

        # ── Step 3: Parse time windows ────────────────────────────────────────
        time_windows, will_miss_flags = parse_time_windows(
            stops_data=stops_prepared,
            ml_predictions=stop_preds,
        )

        # ── Step 4: Solve VRP ─────────────────────────────────────────────────
        vrp_result = solve_vrp(
            cost_matrix=cost_matrix,
            time_windows=time_windows,
            num_vehicles=num_vehicles,
            time_limit_seconds=time_limit_seconds,
            will_miss_flags=will_miss_flags,
        )

        # ── Step 5: Merge VRP sequence with ML predictions ────────────────────
        optimized_route: list[dict] = []

        for vehicle_idx, route in enumerate(vrp_result["routes"]):
            delivery_position = 0
            for node_index in route:
                if node_index == 0:
                    continue   # skip depot entries
                stop_idx = node_index - 1   # convert 1-based matrix index → 0-based

                original_stop = stops_prepared[stop_idx]
                ml_pred = stop_preds[stop_idx]

                optimized_route.append({
                    "optimized_position": delivery_position,
                    "vehicle_id": vehicle_idx,
                    "original_stop_index": stop_idx,
                    # Display / passthrough fields
                    "stop_id": original_stop.get("stop_id"),
                    "stop_name": original_stop.get("stop_name"),
                    "latitude": original_stop.get("latitude"),
                    "longitude": original_stop.get("longitude"),
                    "stop_sequence": original_stop.get("stop_sequence"),
                    "planned_travel_min": original_stop.get("planned_travel_min"),
                    "time_window_slack_min": original_stop.get("time_window_slack_min", 60.0),
                    # ML prediction fields
                    "delay_probability": ml_pred["delay_probability"],
                    "expected_delay_min": ml_pred["expected_delay_min"],
                    "delay_p90_min": ml_pred.get("delay_p90_min"),
                    "will_miss_window": ml_pred["will_miss_window"],
                    "risk_level": ml_pred["risk_level"],
                    "severity": ml_pred["severity"],
                })
                delivery_position += 1

        # Sort by vehicle then position for deterministic output order
        optimized_route.sort(key=lambda s: (s["vehicle_id"], s["optimized_position"]))

        # ── Step 6: Build unified route summary ───────────────────────────────
        ml_summary: dict = ml_result.get("route_summary", {})
        route_summary = {
            **ml_summary,
            "vrp_status": vrp_result["status"],
            "vrp_objective_value": vrp_result["objective_value"],
            "vrp_solve_time_ms": vrp_result["solve_time_ms"],
            # Convert 1-based dropped matrix indices back to 0-based stop indices
            "dropped_stop_indices": [i - 1 for i in vrp_result["dropped_nodes"]],
            "cost_mode": "p90" if use_p90 else "expected",
        }

        return {
            "ml_predictions": ml_result,
            "vrp_result": vrp_result,
            "optimized_route": optimized_route,
            "route_summary": route_summary,
            "use_p90": use_p90,
            "num_vehicles": num_vehicles,
        }
