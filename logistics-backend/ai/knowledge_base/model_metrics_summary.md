# Model Metrics Summary
The v9 model is the current baseline delay predictor. 
It uses 43 classifier features and 39 regressor features. 
Metrics from `v9_model_metrics.csv` confirm:
- Mean Absolute Error (MAE) for general delay: ~5.3 min.
- R-squared value: 0.92, indicating strong fit.
- High-delay MAE (>= 60 min): ~12.0 min.
- Extreme-delay MAE (>= 120 min): ~18.7 min.
- P90 coverage: ~89.6%, indicating the P90 model is well-calibrated to capture extreme worst-case scenarios.
The classifier has perfect precision (1.0) and high recall (~0.98) for predicting late arrivals.
These metrics mean the dispatcher can trust the general delay risk buckets, but exact minute-by-minute predictions for extreme outliers may vary by up to 18 minutes.
