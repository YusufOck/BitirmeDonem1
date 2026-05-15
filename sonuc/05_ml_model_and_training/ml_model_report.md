# Machine Learning Model & Validation Report

## 1. Objective and Model Selection
The objective of the ML layer is to predict the operational delay (`ml_delay_min`) for a courier at a specific stop based on dynamic contextual features. 

- **Algorithm**: CatBoostRegressor
- **Nature of Task**: Regression (Predicting minutes of delay).
- **Scope**: Stop-level prediction. The model predicts the delay at a specific geographic node based on conditions, which is then fed into the OR-Tools route-level solver.

## 2. Feature Engineering & Target
The model is trained on historical delivery data.

### Input Features:
- `traffic_density` (0-100)
- `weather_severity` (0-100)
- `accident_severity` (0-100)
- `road_disruption` (0-100)
- `package_load` (0-100)
- `base_travel_time_min` (from Mapbox)
- Stop characteristics (historical average dwell time, building type).

### Target Variable:
- `actual_delay_min` (The excess time taken beyond the planned time).

## 3. Training & Data Generation
The model uses a synthetic dataset generator because real historical company data was not available for this MVP. 
- **Script**: `ml/train.py`
- **Data Shape**: 10,000+ historical rows.
- **Train/Test Split**: 80/20.

## 4. Model Evaluation Metrics
The model is evaluated using standard regression metrics, which calculate the difference between the model's prediction and the actual observed delay:
- **MAE (Mean Absolute Error)**: The average absolute difference between predicted and actual delay. E.g., MAE of 3.2 means predictions are off by 3.2 minutes on average.
- **RMSE (Root Mean Square Error)**: Squares the errors before averaging, penalizing large errors (e.g., predicting 5 min when actual is 40 min is penalized heavily).
- **R² (R-Squared)**: Explains the variance in the target variable. A score of 0.85 means 85% of delay variations are explained by the features.
- **P90 Coverage**: The system uses quantile predictions for "Conservative Mode". P90 ensures that 90% of the time, the real delay will be less than or equal to the predicted worst-case delay.

## 5. Penalty for Bad Predictions (Loss Function)
During training, CatBoost uses **RMSE** as its loss function.
- If the model incorrectly predicts 10 mins, but the actual delay is 50 mins, the error is 40.
- The squared error is 1600.
- The gradient boosting algorithm updates its decision trees to minimize this huge penalty on the next iteration.

## 6. How is it Used in the Application?
During an optimization request (`runScenario`), the backend queries the model via `predict.py` for *every potential route leg* in the Mapbox NxN matrix. 
If the ML model predicts 12 minutes of delay at Stop A, OR-Tools adds 12 to the cost of visiting Stop A.

## 7. Model Limitations
- **Synthetic Bias**: The model was trained on data generated with specific assumptions about how weather impacts traffic. Real-world edge cases might not be represented.
- **Stop-Level vs Path-Level**: The model predicts delay *at* a stop and its immediate vicinity, while Mapbox Directions handles the *path* travel time. The system combines these two separate estimates linearly.
