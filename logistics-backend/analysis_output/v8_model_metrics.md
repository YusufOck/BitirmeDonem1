# v8 Runtime-Safe Model Metrics

This model is trained with the same categorical encodings and runtime-safe features used by `ml/inference.py`. It does not use actual travel time as a leakage feature.

| Metric | Value |
|---|---|
| MAE delay minutes | 4.0641 |
| RMSE delay minutes | 10.6723 |
| Median absolute error minutes | 1.0072 |
| R2 delay | 0.9361 |
| Within 5 minutes | 0.8379 |
| Late precision | 1.0 |
| Late recall | 0.9788 |
| Average precision | 0.9994 |
| P90 coverage | 0.7138 |
| P90 pinball loss | 1.304 |
| Threshold | 0.5 |
| Severe threshold minutes | 15.0 |
| Classifier features | 43.0 |
| Regressor features | 39.0 |
| Test rows | 290.0 |
| Test routes | 40.0 |
