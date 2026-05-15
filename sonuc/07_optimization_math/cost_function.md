# Mathematical Foundation of Route Optimization

## 1. Overview
The logistics application uses a deterministic Operations Research (OR) approach powered by Google's OR-Tools, specifically formulating the problem as a Vehicle Routing Problem (VRP) with Time Windows.

## 2. Core Mechanism
The objective of the routing engine is to minimize the **Total Route Cost**. The cost is not simply the geographical distance; it is a composite score that mathematically blends real-world travel times, ML-predicted delay risks, and dynamically scaled penalties for adverse conditions.

## 3. The Objective Function
The OR-Tools solver seeks a routing sequence `S` that minimizes the total cost `C(S)`:

```math
Minimize: C(S) = \sum_{i=1}^{N-1} Cost(S_i, S_{i+1}) + \sum_{j=1}^{N} Penalty(S_j)
```

## 4. Leg Cost Components
For any two stops `A` and `B`, the cost to traverse from `A` to `B` is defined as:

```
Cost(A, B) = Base Travel Time(A, B) * Scenario Multiplier
             + ML Predicted Delay(B)
             + Schedule Penalty(B)
```

### 4.1. Base Travel Time
Sourced directly from Mapbox Matrix API (`duration`).

### 4.2. Scenario Multiplier (Travel Matrix Inflation)
When global conditions change (e.g., rain, accidents), travel times increase globally. 
The system inflates base times before they even reach the solver:
```
Multiplier = 1.0 
             + (traffic_density / 100) * 0.45 
             + (accident_severity / 100) * 0.25 
             + (weather_severity / 100) * 0.35 
             + (road_disruption / 100) * 0.18
```
*Example: If weather is maxed (100), travel times increase by 35%.*

### 4.3. ML Predicted Delay
The `ml_delay_min` (e.g., 10 minutes) is output by the CatBoost ML model based on features specific to Stop B. This is added directly to the cost.

## 5. Penalty Components

### 5.1. Schedule Penalty (Time Window Enforcement)
Every stop has a `time_window_slack_min`. If the cumulative arrival time at a stop exceeds its slack, a massive exponential penalty applies.
```
Arrival ETA = Previous Arrival ETA + Cost(A, B)
Slack = max(5.0, Base Slack - traffic_density * 0.10 - road_disruption * 0.12)
Schedule Delay = max(0.0, (Arrival ETA) - Slack)
```
If a stop is placed last, and `Arrival ETA` exceeds `Slack`, `Schedule Delay` spikes (e.g., 88 mins). This teaches the solver to prioritize tight-window stops earlier in the route.

### 5.2. Segment Overrides
If a user applies a specific segment override (e.g., "Closure" between Stop A and Stop B), a massive arbitrary penalty is added to that specific graph edge, guaranteeing the solver will avoid that connection.
```
Closure Penalty = 9999.0
```

## 6. Why Does a Lower Cost Equal a Better Route?
The solver considers all permutations. By artificially increasing costs on paths with high ML delays, traffic, or time-window violations, the solver naturally routes the courier around risky paths to minimize the numerical sum, resulting in a safer, faster route.
