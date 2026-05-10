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

from optimization.cost_matrix import build_cost_matrix_with_breakdown
from optimization.scoring import route_cost_from_sequence, sequence_edges
from optimization.time_windows import parse_time_windows
from optimization.vrp_solver import solve_vrp
from api.logging_config import setup_logger

logger = setup_logger("logistics.route_optimizer")


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
        cost_matrix, cost_breakdown = build_cost_matrix_with_breakdown(
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
                    "feature_source": original_stop.get("feature_source"),
                    "road_type": original_stop.get("road_type"),
                    "traffic_level": original_stop.get("traffic_level"),
                    "weather_condition": original_stop.get("weather_condition"),
                    "congestion_ratio_mean": original_stop.get("congestion_ratio_mean"),
                    "road_incident": original_stop.get("road_incident"),
                    "incident_severity": original_stop.get("incident_severity"),
                    "precipitation_mm": original_stop.get("precipitation_mm"),
                    "wind_speed_kmh": original_stop.get("wind_speed_kmh"),
                    "visibility_km": original_stop.get("visibility_km"),
                    "package_count": original_stop.get("package_count"),
                    "package_weight_kg": original_stop.get("package_weight_kg"),
                    "delay_factors": original_stop.get("delay_factors", []),
                    # ML prediction fields
                    "delay_probability": ml_pred["delay_probability"],
                    "expected_delay_min": ml_pred["expected_delay_min"],
                    "delay_p90_min": ml_pred.get("delay_p90_min"),
                    "calibration_applied": ml_pred.get("calibration_applied", False),
                    "calibration_reasons": ml_pred.get("calibration_reasons", []),
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
        comparison = self._build_optimization_comparison(
            stops_prepared=stops_prepared,
            vrp_result=vrp_result,
            cost_matrix=cost_matrix,
            cost_breakdown=cost_breakdown,
            num_vehicles=num_vehicles,
        )
        route_summary["optimization_status"] = comparison["status"]
        route_summary["optimization_improvement_pct"] = comparison["improvement_pct"]

        return {
            "ml_predictions": ml_result,
            "vrp_result": vrp_result,
            "optimized_route": optimized_route,
            "route_summary": route_summary,
            "optimization_comparison": comparison,
            "use_p90": use_p90,
            "num_vehicles": num_vehicles,
        }

    def _build_optimization_comparison(
        self,
        stops_prepared: list[dict],
        vrp_result: dict,
        cost_matrix: list[list[int]],
        cost_breakdown: list[list[dict]],
        num_vehicles: int,
    ) -> dict:
        n = len(stops_prepared)
        original_sequence = [0] + list(range(1, n + 1)) + [0]
        original_cost = route_cost_from_sequence(cost_matrix, original_sequence)
        optimized_sequences = vrp_result.get("routes") or []
        optimized_cost = round(
            sum(route_cost_from_sequence(cost_matrix, route) for route in optimized_sequences),
            4,
        )
        improvement_min = round(original_cost - optimized_cost, 4)
        improvement_pct = round((improvement_min / original_cost) * 100.0, 2) if original_cost > 0 else 0.0

        def _name(node: int):
            stop = stops_prepared[node - 1]
            return stop.get("stop_id") or stop.get("stop_name") or node

        original_order = [_name(node) for node in original_sequence if node != 0]
        optimized_order = [
            [_name(node) for node in route if node != 0]
            for route in optimized_sequences
        ]

        warnings: list[str] = []
        if not optimized_sequences:
            warnings.append("OR-Tools did not return a feasible route.")
        if vrp_result.get("dropped_nodes"):
            warnings.append(f"{len(vrp_result['dropped_nodes'])} stop(s) were dropped by the solver.")
        if improvement_min <= 0.1:
            warnings.append("Optimized route is not materially cheaper than the original order.")
        if num_vehicles > 1:
            warnings.append("Original-order baseline is sequential; optimized cost may use multiple vehicles.")

        original_edges = set(sequence_edges(original_sequence))
        changed_edges: list[dict] = []
        top_cost_drivers: list[dict] = []
        for route in optimized_sequences:
            for edge in sequence_edges(route):
                leg = cost_breakdown[edge[0]][edge[1]]
                if edge not in original_edges:
                    changed_edges.append({
                        "from_node": edge[0],
                        "to_node": edge[1],
                        "cost_min": leg.get("total_cost_min"),
                        "base_travel_min": leg.get("base_travel_min"),
                        "predicted_delay_min": leg.get("predicted_delay_min"),
                        "reasons": leg.get("reasons", []),
                    })
                if leg.get("reasons"):
                    top_cost_drivers.append({
                        "from_node": edge[0],
                        "to_node": edge[1],
                        "cost_min": leg.get("total_cost_min"),
                        "reasons": leg.get("reasons", []),
                    })
        top_cost_drivers.sort(key=lambda row: float(row.get("cost_min") or 0.0), reverse=True)

        status = "improved" if improvement_min > 0.1 and not vrp_result.get("dropped_nodes") else "not_improved"
        if not optimized_sequences:
            status = "infeasible"
        confidence = "high"
        if warnings:
            confidence = "medium"
        if status != "improved" or vrp_result.get("dropped_nodes"):
            confidence = "low"

        return {
            "status": status,
            "confidence": confidence,
            "cost_formula": (
                "base_travel_time + predicted_delay + traffic_penalty + accident_penalty + "
                "road_closure_penalty + weather_penalty + risk_penalty + priority_penalty + "
                "constraint_penalty"
            ),
            "original_order": original_order,
            "optimized_order": optimized_order,
            "original_cost_min": original_cost,
            "optimized_cost_min": optimized_cost,
            "improvement_min": improvement_min,
            "improvement_pct": improvement_pct,
            "changed_edges": changed_edges[:12],
            "top_cost_drivers": top_cost_drivers[:12],
            "warnings": warnings,
        }
