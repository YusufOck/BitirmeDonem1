# Time Window Policy
Every delivery stop has a specific time window constraint. 
The system evaluates the `time_window_slack_min` feature, which indicates how much buffer remains before the window is missed. 
If the ML model predicts a delay that consumes all available slack, the stop is flagged with `will_miss_window = true`.
The OR-Tools optimizer heavily penalizes missing a time window. It will aggressively reorder stops to satisfy time windows, even if the total travel distance slightly increases.
Dispatchers should be aware that high traffic scenarios may force the optimizer to drop stops entirely if no feasible route can meet all time windows, though the MVP currently attempts a best-effort traversal.
