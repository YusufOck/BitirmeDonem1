# Scenario Conditions Guide
Dispatchers can simulate or apply live real-world conditions to active routes.
Key parameters:
- Traffic Density (0-100): Increases travel time and delay risk proportionally.
- Accident Severity (0-100): Drastically increases delay risk.
- Weather Severity (0-100): Applied via rain, snow, or fog multipliers to reduce average vehicle speed and increase delay.
- Road Closure (Boolean): A hard constraint penalizing a segment exponentially, forcing the optimizer to route around it.
When conditions are updated, the Agent runs the ML delay predictor over the new features. The OR-Tools algorithm then recalculates the optimal sequence. A recommendation is issued if the new sequence differs from the current active dispatch or if expected delays shift drastically.
