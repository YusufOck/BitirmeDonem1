"""
vrp_solver.py
-------------
Pure OR-Tools CVRPTW (Capacitated VRP with Time Windows) solver.

This module has zero knowledge of ML or HTTP — it takes integer matrices
and returns optimized routes. Fully testable in isolation.

Cost/time unit: 0.01 minutes (integers) — must match cost_matrix.py and time_windows.py.
"""

from __future__ import annotations

import time

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

# Penalty for dropping an infeasible stop — high enough to avoid dropping
# unless the problem is truly infeasible for that stop.
_DROP_PENALTY = 100_000_000

# Soft lateness penalty for will_miss stops (per 0.01-min unit of lateness).
# Soft upper bound is set at 0, so this penalises the full cumulative travel
# time to the node.  At 5 000 / unit:
#   • 10-min arrival  (1 000 units) → 5 000 000 cost  (5 % of drop penalty)
#   • 100-min arrival (10 000 units) → 50 000 000 cost (50 % of drop penalty)
# OR-Tools will never prefer dropping over a feasible visit, while the
# quadratic pressure still moves these stops toward the front of the route.
_DEFAULT_SOFT_MISS_PENALTY = 5_000


def _adaptive_time_limit(n_delivery_stops: int, requested: int) -> int:
    """
    Cap the solver time limit to what the instance size actually needs.

    PATH_CHEAPEST_ARC finds near-optimal solutions almost instantly for small
    instances — GLS rarely improves beyond the first solution for <20 stops.
    Running 30 s for a 5-stop problem wastes 29+ seconds for nothing.

    Limits are conservative upper bounds; actual solve times are far lower.
    The requested limit is never exceeded (caller can always ask for less).

        ≤ 5  stops →  2 s   (first solution typically optimal, <50 ms)
        ≤ 10 stops →  5 s   (GLS may improve 1-2 arcs)
        ≤ 20 stops → 15 s   (meaningful search space)
         > 20 stops → requested  (large fleet, keep full budget)
    """
    if n_delivery_stops <= 5:
        cap = 2
    elif n_delivery_stops <= 10:
        cap = 5
    elif n_delivery_stops <= 20:
        cap = 15
    else:
        return requested
    return min(requested, cap)


def solve_vrp(
    cost_matrix: list[list[int]],
    time_windows: list[tuple[int, int]],
    num_vehicles: int = 1,
    depot: int = 0,
    time_limit_seconds: int = 30,
    use_gls: bool = True,
    will_miss_flags: list[bool] | None = None,
    soft_miss_penalty: int = _DEFAULT_SOFT_MISS_PENALTY,
) -> dict:
    """
    Solve a CVRPTW using Google OR-Tools routing library.

    Parameters
    ----------
    cost_matrix : list[list[int]]
        Square (N+1)x(N+1) integer matrix (depot at index 0).
        Units: 0.01 minutes. Produced by optimization.cost_matrix.build_cost_matrix().
    time_windows : list[tuple[int, int]]
        Length-(N+1) list of (earliest, latest) integer pairs.
        Same units as cost_matrix. Produced by optimization.time_windows.parse_time_windows().
    num_vehicles : int
        Number of delivery vehicles (default 1 → single-vehicle TSPTW).
    depot : int
        Index of the depot node. Always 0.
    time_limit_seconds : int
        Wall-clock budget for the solver. 30s gives meaningful optimization
        while staying within a synchronous web request timeout.
    use_gls : bool
        If True, apply GUIDED_LOCAL_SEARCH metaheuristic after first solution.
        Best for VRP — escapes local minima via arc-penalty augmentation.
        Set False for tiny instances (< 5 stops) to avoid overhead.
    will_miss_flags : list[bool] | None
        Parallel to time_windows. When True for index k, the stop gets a
        *soft* time-window constraint instead of a hard one:
            • Hard range: (0, capacity)  — never infeasible, never auto-dropped
            • Soft upper bound: 0        — penalises lateness, pushes stop early
        When None, all stops use hard constraints from time_windows.
    soft_miss_penalty : int
        Cost per 0.01-min unit of lateness for soft-window stops.
        Default: 5 000.  Increase to schedule will_miss stops earlier at the
        expense of higher total route cost.

    Returns
    -------
    dict with keys:
        status          : str            — "success" | "no_solution"
        routes          : list[list[int]] — one list per vehicle, includes depot at start/end
                          e.g. [[0, 2, 1, 3, 0]] for a single-vehicle route
        objective_value : int            — total cost in 0.01-min units (0 if no solution)
        solve_time_ms   : int            — wall-clock solver time in milliseconds
        dropped_nodes   : list[int]      — delivery indices not served (empty if all served)
    """
    start_ts = time.time()
    n = len(cost_matrix)
    n_delivery = n - 1   # excludes depot

    # Adaptive time limit — small instances don't need the full budget
    effective_limit = _adaptive_time_limit(n_delivery, time_limit_seconds)
    # For tiny instances, GLS overhead outweighs benefit; PATH_CHEAPEST_ARC
    # already finds the optimal solution, so skip the metaheuristic.
    effective_gls = use_gls and n_delivery > 5

    if n < 2:
        raise ValueError("cost_matrix must be at least 2x2 (depot + 1 delivery stop)")
    if len(time_windows) != n:
        raise ValueError(
            f"time_windows length ({len(time_windows)}) must match cost_matrix size ({n})"
        )
    if will_miss_flags is not None and len(will_miss_flags) != n:
        raise ValueError(
            f"will_miss_flags length ({len(will_miss_flags)}) must match cost_matrix size ({n})"
        )

    # ── 1. Routing index manager ──────────────────────────────────────────────
    manager = pywrapcp.RoutingIndexManager(n, num_vehicles, depot)

    # ── 2. Routing model ──────────────────────────────────────────────────────
    model = pywrapcp.RoutingModel(manager)

    # ── 3. Transit callback (cost matrix lookup) ──────────────────────────────
    def _transit(from_index: int, to_index: int) -> int:
        return cost_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]

    transit_idx = model.RegisterTransitCallback(_transit)
    model.SetArcCostEvaluatorOfAllVehicles(transit_idx)

    # ── 4. Time window dimension ──────────────────────────────────────────────
    model.AddDimension(
        transit_idx,
        slack_max=10_000_000,       # max allowed waiting time at a node
        capacity=10_000_000,        # max cumulative time (effectively uncapped)
        fix_start_cumul_to_zero=True,
        name="Time",
    )
    time_dim = model.GetDimensionOrDie("Time")

    for node_idx in range(n):
        routing_index = manager.NodeToIndex(node_idx)
        tw_open, tw_close = time_windows[node_idx]

        is_soft = (
            will_miss_flags is not None
            and will_miss_flags[node_idx]
            and node_idx != depot
        )

        if is_soft:
            # Unconstrained hard range — stop can never become infeasible due
            # to timing, so OR-Tools will not auto-drop it.
            time_dim.CumulVar(routing_index).SetRange(0, 10_000_000)
            # Soft upper bound at 0: every unit of cumulative travel time to
            # this node adds `soft_miss_penalty` to the objective.  OR-Tools
            # minimises total cost, so it naturally schedules these stops as
            # early in the route as possible.
            time_dim.SetCumulVarSoftUpperBound(routing_index, 0, soft_miss_penalty)
        else:
            time_dim.CumulVar(routing_index).SetRange(tw_open, tw_close)

    # ── 5. Disjunctions — allow dropping infeasible stops ────────────────────
    #   Each delivery stop becomes its own disjunction with a high penalty.
    #   OR-Tools will drop a stop only if no feasible route exists for it.
    for node_idx in range(1, n):   # skip depot (index 0)
        model.AddDisjunction([manager.NodeToIndex(node_idx)], _DROP_PENALTY)

    # ── 6. Search parameters ──────────────────────────────────────────────────
    params = pywrapcp.DefaultRoutingSearchParameters()

    # First solution: PATH_CHEAPEST_ARC greedily extends the route by always
    # picking the lowest-cost outgoing arc. Fast and produces a good seed.
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )

    if effective_gls:
        # GUIDED_LOCAL_SEARCH is the gold standard for VRP metaheuristics.
        # It augments arc costs of locally-optimal edges to escape local minima.
        # Skipped for tiny instances (≤5 stops) where PATH_CHEAPEST_ARC is optimal.
        params.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        )

    params.time_limit.seconds = effective_limit
    params.log_search = False   # suppress verbose solver output in production

    # ── 7. Solve ──────────────────────────────────────────────────────────────
    solution = model.SolveWithParameters(params)
    solve_ms = int((time.time() - start_ts) * 1000)

    if solution is None:
        return {
            "status": "no_solution",
            "routes": [],
            "objective_value": 0,
            "solve_time_ms": solve_ms,
            "dropped_nodes": list(range(1, n)),   # all delivery nodes unserved
            "time_limit_used_s": effective_limit,
        }

    # ── 8. Extract routes ─────────────────────────────────────────────────────
    routes: list[list[int]] = []
    for v in range(num_vehicles):
        route: list[int] = []
        index = model.Start(v)
        while not model.IsEnd(index):
            route.append(manager.IndexToNode(index))
            index = solution.Value(model.NextVar(index))
        route.append(manager.IndexToNode(index))   # append end-depot

        # Only include vehicles that actually serve at least one delivery stop
        if len(route) > 2:
            routes.append(route)

    # ── 9. Identify dropped nodes ─────────────────────────────────────────────
    all_visited: set[int] = {node for route in routes for node in route}
    dropped = [i for i in range(1, n) if i not in all_visited]

    return {
        "status": "success",
        "routes": routes,
        "objective_value": solution.ObjectiveValue(),
        "solve_time_ms": solve_ms,
        "dropped_nodes": dropped,
        "time_limit_used_s": effective_limit,
    }
