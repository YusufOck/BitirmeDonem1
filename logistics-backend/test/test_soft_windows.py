"""
test_soft_windows.py
--------------------
Isolated tests for the soft time-window refactor.
Tests parse_time_windows and solve_vrp directly — no ML model needed.

Run: python test_soft_windows.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from optimization.time_windows import parse_time_windows, DEPOT_MAX
from optimization.vrp_solver import solve_vrp

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

results = []

def check(name, cond, detail=""):
    status = PASS if cond else FAIL
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    results.append(cond)

print("\n=== 1. parse_time_windows ===\n")

stops = [
    {"time_window_slack_min": 30.0},   # normal
    {"time_window_slack_min": 20.0},   # will_miss
    {"time_window_slack_min": 45.0},   # normal
]
preds = [
    {"will_miss_window": False},
    {"will_miss_window": True},
    {"will_miss_window": False},
]

windows, flags = parse_time_windows(stops, preds)

check("returns two lists", isinstance(windows, list) and isinstance(flags, list))
check("length N+1 (4 = depot + 3 stops)", len(windows) == 4 and len(flags) == 4)
check("depot window unconstrained", windows[0] == (0, DEPOT_MAX))
check("depot flag is False", flags[0] == False)

check("normal stop → hard slack window", windows[1] == (0, 3000), f"got {windows[1]}")
check("normal stop flag False", flags[1] == False)

check("will_miss stop → unconstrained hard window", windows[2] == (0, DEPOT_MAX), f"got {windows[2]}")
check("will_miss stop flag True", flags[2] == True)

check("normal stop → hard slack window", windows[3] == (0, 4500), f"got {windows[3]}")
check("normal stop flag False", flags[3] == False)

print("\n=== 2. solve_vrp — all normal stops (baseline, no regression) ===\n")

# 3 stops, all reachable within generous slack
# travel: depot→A=10, A→B=10, B→C=10 (minutes × 100)
n = 4  # depot + 3
cost = [
    [0,    1000, 1500, 2000],
    [1000, 0,    1000, 1500],
    [1500, 1000, 0,    1000],
    [2000, 1500, 1000, 0   ],
]
tw = [(0, DEPOT_MAX), (0, 5000), (0, 5000), (0, 5000)]
flags_normal = [False, False, False, False]

res = solve_vrp(cost, tw, will_miss_flags=flags_normal, time_limit_seconds=5)
check("status success", res["status"] == "success", res["status"])
check("all 3 stops served", res["dropped_nodes"] == [], f"dropped={res['dropped_nodes']}")

print("\n=== 3. solve_vrp — will_miss stop with long travel (core bug fix) ===\n")
# Stop 1 has will_miss=True, but the minimum travel time is 2000 units (20 min).
# Old behaviour: hard window (0, 500) → OR-Tools drops stop 1.
# New behaviour: soft window → stop 1 stays in route.

cost2 = [
    [0,    2000, 1000, 1000],  # depot → stop1 costs 20 min
    [2000, 0,    1000, 1000],
    [1000, 1000, 0,    1000],
    [1000, 1000, 1000, 0   ],
]
# tight hard window that would cause the old 5-min drop:
tw_old = [(0, DEPOT_MAX), (0, 500), (0, 5000), (0, 5000)]  # 500 = 5 min
flags_miss = [False, True, False, False]

# Simulate old behaviour: hard windows, no soft flags
res_old = solve_vrp(cost2, tw_old, will_miss_flags=None, time_limit_seconds=5)
old_dropped = 1 in res_old["dropped_nodes"]
check("OLD behaviour: stop1 gets dropped with 5-min hard window", old_dropped,
      f"dropped={res_old['dropped_nodes']}")

# New behaviour: soft windows via will_miss_flags
tw_new = [(0, DEPOT_MAX), (0, DEPOT_MAX), (0, 5000), (0, 5000)]
res_new = solve_vrp(cost2, tw_new, will_miss_flags=flags_miss, time_limit_seconds=5)
new_dropped = 1 in res_new.get("dropped_nodes", [])
check("NEW behaviour: stop1 stays in route with soft window", not new_dropped,
      f"dropped={res_new['dropped_nodes']}")

print("\n=== 4. solve_vrp — will_miss stop scheduled EARLY ===\n")
# Stop 3 is will_miss=True, stops 1 and 2 are normal.
# Soft penalty at 0 should push stop 3 toward the front of the route.

cost3 = [
    [0,    1000, 1000, 1000],
    [1000, 0,    1000, 1000],
    [1000, 1000, 0,    1000],
    [1000, 1000, 1000, 0   ],
]
tw3 = [(0, DEPOT_MAX), (0, 5000), (0, 5000), (0, DEPOT_MAX)]
flags3 = [False, False, False, True]

res3 = solve_vrp(cost3, tw3, will_miss_flags=flags3, time_limit_seconds=5)
check("status success", res3["status"] == "success")
check("no drops", res3["dropped_nodes"] == [], f"dropped={res3['dropped_nodes']}")

if res3["routes"]:
    route = res3["routes"][0]
    # Exclude depot (0) from both ends
    delivery_order = [n for n in route if n != 0]
    will_miss_pos = delivery_order.index(3) if 3 in delivery_order else -1
    check(
        "will_miss stop (node 3) scheduled first or second",
        will_miss_pos <= 1,
        f"delivery order={delivery_order}, will_miss at pos {will_miss_pos}",
    )

print("\n=== 5. solve_vrp — will_miss_flags=None backward-compat ===\n")
res5 = solve_vrp(cost, tw, will_miss_flags=None, time_limit_seconds=5)
check("works without will_miss_flags", res5["status"] == "success")
check("all stops served", res5["dropped_nodes"] == [], f"dropped={res5['dropped_nodes']}")

# ── Summary ──────────────────────────────────────────────────────────────────
total = len(results)
passed = sum(results)
print(f"\n{'='*50}")
print(f"  {passed}/{total} passed")
if passed == total:
    print(f"  \033[92mAll tests passed.\033[0m")
else:
    print(f"  \033[91m{total - passed} test(s) FAILED.\033[0m")
    sys.exit(1)
