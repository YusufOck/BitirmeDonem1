# Optimization Methodology

This backend uses a hybrid AI plus operations-research pipeline:

1. Mapbox Matrix computes road-based travel time and distance between stops.
2. The CSV feature pipeline enriches stops with traffic, weather, incident, package, and historical delay features.
3. The trained ML delay model predicts expected delay per stop.
4. Scenario controls and segment overrides modify the feature set and travel-time matrix.
5. OR-Tools solves the stop order by minimizing road travel time plus ML-predicted delay cost.
6. Mapbox Directions draws the final road geometry for the selected order.

## Delay Calculation

The delay model receives structured features such as:

- traffic level and congestion ratio,
- accident severity and incident rate,
- weather condition, visibility, precipitation, and wind,
- package count and package weight,
- time-window slack,
- historical delay probability,
- planned travel time and distance from the previous stop.

For normal dispatch, these features come from the project CSV pipeline and Mapbox road metrics. For scenario testing, the user-controlled road conditions are converted into the same model features before prediction.

## Route Score

The optimizer does not use straight-line distance as its main route cost. It uses a travel-time matrix produced from Mapbox road routing. The cost for moving from one stop to another is:

`road travel time + predicted delay at the destination stop`

When a user adds a segment accident, closure, speed reduction, or manual delay, the backend modifies the affected matrix cell before OR-Tools solves. A closed segment receives a very high cost, so it is avoided when a feasible alternative exists.

## Explainability

The scenario endpoint returns:

- baseline route metrics,
- scenario route metrics,
- before and after stop sequences,
- factor impacts,
- route delta values,
- a plain-English explanation of why the route changed or did not change.

## Cost Formula
The optimization cost formula is: `(travel_time + expected_delay) * 100` plus soft penalties for traffic, weather, accident, road closure, and high priority. Time window constraints are modeled as soft penalties for lateness.

## Model Evaluation and Training
The default model is `route_predictor_v9.pkl`, a runtime-safe model with no `actual_travel_min` leakage.
Latest reproducible metrics (GroupKFold held-out evaluation):
- MAE: 5.33 min | RMSE: 11.82 | R^2: 0.9216
- High-delay MAE (>=60 min): 12.0 min
- Extreme-delay MAE (>=120 min): 18.71 min
- P90 coverage: 89.66%

To retrain: `python ml/train_model_v9.py`
To evaluate: `python reports/generate_evaluation_reports.py`

## Hallucination Prevention
The `/explain` endpoint uses a local Ollama instance. Grounding checks prevent hallucinations by ensuring the LLM only refers to stops that exist in the optimization result. If the model fabricates stops, the explanation is blocked and flagged as high hallucination risk. Full document-retrieval RAG is not yet active, but the scaffolding is present in `reports/evaluate_explainer.py`.

## Scenario Persistence
Scenarios can be persisted in the database via `/api/v1/scenario/create`. Segment overrides modify the travel-time matrix before the OR-Tools solver runs.

## Testing
Run backend tests with `pytest`:
```bash
python -m pytest test/ -v
```
