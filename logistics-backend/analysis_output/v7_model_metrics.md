# v7 Model Metrics

Training completed: 2026-04-19

## Classifier
| Metric | Value |
|---|---|
| CLF threshold (optimal) | 0.05 |
| Severity threshold | 24.0 min (optimized, F1=1.00) |
| Features | 43 |

## Regressor (P50)
| Metric | Val | Test |
|---|---|---|
| MAE (min) | 1.699 | **1.332** |
| RMSE (min) | 4.219 | **2.005** |
| R² | 0.983 | **0.9964** |
| Severe Recall | 0.9937 | **0.9942** |
| Within ±2 min | — | 79.3% |
| Within ±5 min | — | 97.2% |
| Severe Precision | — | 0.9607 |
| Severe F1 | — | 0.9771 |
| Features | 39 | |

## P90 Regressor
| Metric | Val | Test |
|---|---|---|
| Coverage @ P50 | 54.8% | 53.4% (target ~50%) ✓ |
| Coverage @ P90 | 70.0% | **74.1%** (target ~90%) ⚠️ |
| Pinball loss (α=0.90) | — | 0.8832 |

> ⚠️ P90 coverage at 74.1% means worst-case estimates are optimistic —
> only 74% of actual delays fall below the P90 prediction (should be 90%).
> Dispatcher should treat `delay_p90_min` as a conservative guide, not a hard bound.

## Top Features (LGBM gain)

### Classifier
1. `travel_delay_ratio` — 741
2. `planned_travel_min` — 566
3. `prev_stop_delay_min` — 488
4. `remaining_slack_net` — 392
5. `delay_to_slack_ratio` — 367
6. `stop_progress_ratio` — 354

### Regressor
1. `planned_travel_min` — 785
2. `travel_delay_ratio` — 588
3. `distance_from_prev_km` — 475
4. `cumulative_delay_min` — 363
5. `weight_per_package` — 269

## Serialized files
- `delay_classifier_v7.pkl`
- `delay_regressor_v7.pkl`
- `delay_regressor_v7_p90.pkl`
- `route_predictor_v7.pkl`
- `clf_threshold_v7.npy`
- `severity_threshold_v7.npy`

## Post-training inference fixes (2026-04-19)
- `inference.py _DEFAULTS`: removed all derived features so `_derive_v5_interaction_features`
  always computes them from actual caller inputs (training script re-introduced the bug).
- `model_classes.py cascade_scale`: capped at 5.0 to prevent explosion when
  `prob_raw ≈ 0` but cascade elevates `prob_adj` (e.g. 0.3 / 1e-6 = 300,000×).
