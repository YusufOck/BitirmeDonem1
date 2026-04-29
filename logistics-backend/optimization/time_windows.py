"""
time_windows.py
---------------
Convert stop-level slack values into OR-Tools integer time windows.

Unit: same as cost_matrix — 0.01 minutes (multiply by 100).

Index 0 → depot: unconstrained (0, DEPOT_MAX)
Index k → delivery stop k-1:
    will_miss_window=True  →  (0, DEPOT_MAX)   [hard unconstrained; solver adds soft penalty]
    otherwise              →  (0, time_window_slack_min * 100)

Returns a tuple: (windows, will_miss_flags)
  windows        : list[tuple[int, int]] — length N+1
  will_miss_flags: list[bool]            — length N+1, index 0 always False (depot)
"""

from __future__ import annotations

DEPOT_MAX = 10_000_000          # effectively unconstrained depot window
DEFAULT_SLACK_MIN = 480.0       # fallback when time_window_slack_min is absent (8 hours — effectively unconstrained)


def parse_time_windows(
    stops_data: list[dict],
    ml_predictions: list[dict],
) -> tuple[list[tuple[int, int]], list[bool]]:
    """
    Convert stop slack into OR-Tools integer time windows.

    Parameters
    ----------
    stops_data : list[dict]
        N stop dicts. Each may contain 'time_window_slack_min' (float, minutes).
    ml_predictions : list[dict]
        Output of predict_route()['stop_predictions'].
        Uses 'will_miss_window' (bool) to select constraint type.

    Returns
    -------
    windows : list[tuple[int, int]]
        Length N+1. Index 0 = depot = (0, DEPOT_MAX).
        will_miss stops: (0, DEPOT_MAX) — hard range unconstrained; soft
            penalty applied by solve_vrp() incentivises early scheduling.
        normal stops: (0, effective_slack * 100) — hard upper bound.
    will_miss_flags : list[bool]
        Parallel to windows. True for stops where will_miss_window=True.
        Index 0 (depot) is always False.
    """
    n = len(stops_data)
    if len(ml_predictions) != n:
        raise ValueError(
            f"stops_data length ({n}) must match ml_predictions length ({len(ml_predictions)})"
        )

    windows: list[tuple[int, int]] = [(0, DEPOT_MAX)]   # depot at index 0
    will_miss_flags: list[bool] = [False]               # depot never flagged

    for stop, pred in zip(stops_data, ml_predictions):
        slack = float(stop.get("time_window_slack_min") or DEFAULT_SLACK_MIN)
        will_miss = bool(pred.get("will_miss_window", False))

        if will_miss:
            # Leave hard range fully open; solve_vrp applies a soft upper bound
            # at 0 so OR-Tools strongly prefers visiting early but never makes
            # the stop infeasible (avoiding automatic drop).
            windows.append((0, DEPOT_MAX))
        else:
            windows.append((0, int(round(slack * 100))))

        will_miss_flags.append(will_miss)

    return windows, will_miss_flags
