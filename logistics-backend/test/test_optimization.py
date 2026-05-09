import pytest
from optimization.vrp_solver import solve_vrp

def test_optimization_solver_basic():
    cm = [[0, 10], [10, 0]]
    tw = [(0, 100), (0, 100)]
    res = solve_vrp(cm, tw, num_vehicles=1, depot=0, time_limit_seconds=5)
    assert len(res["routes"]) == 1
    assert 0 in res["routes"][0]
