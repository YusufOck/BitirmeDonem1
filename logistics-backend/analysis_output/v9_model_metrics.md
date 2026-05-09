# v9 Runtime-Safe Model Metrics

v9 uses the same runtime-safe feature builder as inference, a tail-aware regressor, and calibrated P90 output. No actual-travel leakage is used for prediction features.

| Metric | Value |
|---|---|
| MAE delay minutes | 5.3299 |
| RMSE delay minutes | 11.8224 |
| Median absolute error minutes | 1.8733 |
| R2 delay | 0.9216 |
| Within 5 minutes | 0.7483 |
| Late precision | 1.0 |
| Late recall | 0.9788 |
| Average precision | 0.9996 |
| High-delay MAE >=60 | 12.0021 |
| Extreme-delay MAE >=120 | 18.7138 |
| P90 coverage | 0.8966 |
| P90 pinball loss | 1.3876 |
| P90 calibration offset min | 1.8103 |
| Threshold | 0.56 |
| Severe threshold minutes | 15.0 |
| Classifier features | 43.0 |
| Regressor features | 39.0 |
| Test rows | 290.0 |
| Test routes | 40.0 |
