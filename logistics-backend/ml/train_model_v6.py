"""
train_model_v6.py
==================
v6 model training script.

Key fix over v5: replaces _ArcsinhRegressor (broken for negative targets) with a
Hurdle approach:
  - Regressor trained ONLY on delay_at_stop_min > 0 samples, log1p transform
  - Classifier trained with 20 base + 4 augmented features
  - P90 quantile regressor on the same positive subset
  - Group split / GroupKFold by route_id to prevent data leakage
"""

import os
import sys
import warnings
import numpy as np
import pandas as pd
import joblib
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.model_selection import GroupKFold, GroupShuffleSplit
from sklearn.metrics import (
    roc_auc_score, f1_score, precision_score, recall_score,
    mean_absolute_error, mean_squared_error,
)

warnings.filterwarnings("ignore")

_ML_DIR = os.path.dirname(os.path.abspath(__file__))
if _ML_DIR not in sys.path:
    sys.path.insert(0, _ML_DIR)

from ml.model_classes import RouteDelayPredictor, _PreFitCalibrator, _augment_features, _Log1pRegressor

# ── Paths ──────────────────────────────────────────────────────────────────────
DATA_PATH  = os.path.join(_ML_DIR, "..", "data", "merged_features_v2.csv")
OUTPUT_PKL = os.path.join(_ML_DIR, "route_predictor_v6.pkl")

# ── Feature sets ───────────────────────────────────────────────────────────────
BASE_FEATURES = [
    "distance_from_prev_km", "planned_travel_min", "stop_sequence",
    "road_type", "hour_of_day", "day_of_week",
    "traffic_level", "weather_condition",
    "is_rush_hour", "precipitation_mm", "hist_delay_probability",
    "cumulative_delay_min", "prev_stop_delay_min", "time_window_slack_min",
    "stop_progress_ratio", "planned_speed_kmh", "hist_slack_min",
    "is_high_risk_weather", "is_urban_road", "traffic_weather_risk",
]

AUGMENTED_FEATURES = [
    "delay_to_slack_ratio",
    "remaining_slack_net",
    "is_already_critical",
    "stop_delay_momentum",
]

CLF_FEATURES = BASE_FEATURES + AUGMENTED_FEATURES  # 24
REG_FEATURES = BASE_FEATURES                        # 20

# ── Hyperparameters ────────────────────────────────────────────────────────────
CLF_PARAMS = dict(
    n_estimators=500, learning_rate=0.05, num_leaves=31,
    min_child_samples=20, subsample=0.8, colsample_bytree=0.8,
    class_weight="balanced", random_state=42, verbose=-1,
)
REG_PARAMS = dict(
    n_estimators=300, learning_rate=0.05, num_leaves=15,
    min_child_samples=10, subsample=0.8, random_state=42, verbose=-1,
)
P90_PARAMS = dict(
    n_estimators=300, learning_rate=0.05, num_leaves=15,
    objective="quantile", alpha=0.90, random_state=42, verbose=-1,
)


# ── Training-time augmentation (route-aware shift for momentum) ────────────────

def _augment_train(df: pd.DataFrame) -> pd.DataFrame:
    """Augment features for training. Uses groupby-shift for stop_delay_momentum."""
    df = df.copy()
    df["delay_to_slack_ratio"] = (
        df["cumulative_delay_min"] / df["time_window_slack_min"].clip(lower=1.0)
    )
    df["remaining_slack_net"] = df["hist_slack_min"] - df["cumulative_delay_min"]
    df["is_already_critical"] = (
        df["cumulative_delay_min"] > df["hist_slack_min"]
    ).astype(int)
    prev_cum = df.groupby("route_id")["cumulative_delay_min"].shift(1).fillna(0)
    df["stop_delay_momentum"] = df["prev_stop_delay_min"] - prev_cum
    return df


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Smart Logistics — Train Model v6 (Hurdle Regressor)")
    print("=" * 60)

    # 1. Load & augment
    df = pd.read_csv(DATA_PATH)
    print(f"\nData: {len(df)} rows, {df['route_id'].nunique()} routes")
    print(f"  missed_time_window=1 : {df['missed_time_window'].sum()} "
          f"({df['missed_time_window'].mean():.1%})")
    print(f"  delay_at_stop_min > 0: {(df['delay_at_stop_min'] > 0).sum()} rows")

    df = _augment_train(df)

    # 2. Group split 80/20 by route_id
    groups = df["route_id"].values
    gss = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
    train_idx, test_idx = next(gss.split(df, groups=groups))
    train_df = df.iloc[train_idx].reset_index(drop=True)
    test_df  = df.iloc[test_idx].reset_index(drop=True)

    train_csv = os.path.join(_ML_DIR, "train_set_v6.csv")
    test_csv  = os.path.join(_ML_DIR, "test_set_v6.csv")
    train_df.to_csv(train_csv, index=False)
    test_df.to_csv(test_csv,  index=False)

    print(f"\nSplit — train: {len(train_df)} rows "
          f"({train_df['route_id'].nunique()} routes), "
          f"test: {len(test_df)} rows "
          f"({test_df['route_id'].nunique()} routes)")
    print(f"  Saved: {train_csv}")
    print(f"  Saved: {test_csv}")

    X_train_clf = train_df[CLF_FEATURES]
    y_train_clf = train_df["missed_time_window"]
    X_test_clf  = test_df[CLF_FEATURES]
    y_test_clf  = test_df["missed_time_window"]

    pos_train = train_df["delay_at_stop_min"] > 0
    pos_test  = test_df["delay_at_stop_min"]  > 0
    X_train_reg = train_df.loc[pos_train, REG_FEATURES]
    y_train_reg = train_df.loc[pos_train, "delay_at_stop_min"]
    X_test_reg  = test_df.loc[pos_test,  REG_FEATURES]
    y_test_reg  = test_df.loc[pos_test,  "delay_at_stop_min"]
    print(f"  Regressor train samples (delay>0): {len(X_train_reg)}")

    # 3. 5-fold GroupKFold cross-validation
    print("\n--- Cross-Validation (5-fold GroupKFold) ---")
    gkf = GroupKFold(n_splits=5)
    train_groups = train_df["route_id"].values
    cv_auc, cv_rmse = [], []

    for fold, (tr_i, val_i) in enumerate(gkf.split(train_df, groups=train_groups)):
        ft, fv = train_df.iloc[tr_i], train_df.iloc[val_i]

        clf_cv = LGBMClassifier(**CLF_PARAMS)
        clf_cv.fit(ft[CLF_FEATURES], ft["missed_time_window"])
        cv_auc.append(roc_auc_score(
            fv["missed_time_window"],
            clf_cv.predict_proba(fv[CLF_FEATURES])[:, 1],
        ))

        pos_t = ft["delay_at_stop_min"] > 0
        pos_v = fv["delay_at_stop_min"] > 0
        fold_rmse = None
        if pos_t.sum() > 10 and pos_v.sum() > 0:
            reg_cv = LGBMRegressor(**REG_PARAMS)
            reg_cv.fit(ft.loc[pos_t, REG_FEATURES], np.log1p(ft.loc[pos_t, "delay_at_stop_min"]))
            pv = np.expm1(reg_cv.predict(fv.loc[pos_v, REG_FEATURES]))
            fold_rmse = float(np.sqrt(mean_squared_error(fv.loc[pos_v, "delay_at_stop_min"], pv)))
            cv_rmse.append(fold_rmse)

        rmse_str = f", RMSE={fold_rmse:.4f}" if fold_rmse is not None else ""
        print(f"  Fold {fold + 1}: AUC={cv_auc[-1]:.4f}{rmse_str}")

    print(f"  CV mean AUC  : {np.mean(cv_auc):.4f} ± {np.std(cv_auc):.4f}")
    if cv_rmse:
        print(f"  CV mean RMSE : {np.mean(cv_rmse):.4f} ± {np.std(cv_rmse):.4f}")

    # 4. Train final classifier + isotonic calibration
    print("\n--- Training Classifier ---")
    base_clf = LGBMClassifier(**CLF_PARAMS)
    base_clf.fit(X_train_clf, y_train_clf)
    calibrated_clf = _PreFitCalibrator(base_clf)
    calibrated_clf.fit(X_train_clf, y_train_clf)
    print("  Isotonic calibration applied (_PreFitCalibrator)")

    # 5. Optimize threshold on training set (F1) — no separate val split
    train_probs = calibrated_clf.predict_proba(X_train_clf)[:, 1]
    best_thresh, best_f1 = 0.5, 0.0
    for t in np.arange(0.10, 0.91, 0.01):
        f1 = f1_score(y_train_clf, (train_probs >= t).astype(int), zero_division=0)
        if f1 > best_f1:
            best_f1, best_thresh = f1, float(round(t, 4))
    print(f"  Optimal threshold: {best_thresh:.4f}  (train F1={best_f1:.4f})")

    # 6. Train hurdle regressor (log1p on delay>0 subset)
    print("\n--- Training Hurdle Regressor (log1p, delay>0) ---")
    base_reg = LGBMRegressor(**REG_PARAMS)
    base_reg.fit(X_train_reg, np.log1p(y_train_reg))
    reg = _Log1pRegressor(base_reg)
    print(f"  Trained on {len(X_train_reg)} samples")

    # 7. Train P90 quantile regressor
    print("\n--- Training P90 Quantile Regressor ---")
    p90_reg = LGBMRegressor(**P90_PARAMS)
    p90_reg.fit(X_train_reg, y_train_reg)
    print(f"  Trained on {len(X_train_reg)} samples")

    # 8. Evaluate on test set
    print("\n--- Test Set Metrics ---")
    test_probs = calibrated_clf.predict_proba(X_test_clf)[:, 1]
    test_preds = (test_probs >= best_thresh).astype(int)

    auc       = roc_auc_score(y_test_clf, test_probs)
    f1        = f1_score(y_test_clf, test_preds, zero_division=0)
    precision = precision_score(y_test_clf, test_preds, zero_division=0)
    recall    = recall_score(y_test_clf, test_preds, zero_division=0)

    pred_pos = np.maximum(reg.predict(X_test_reg), 0.0)
    mae  = mean_absolute_error(y_test_reg, pred_pos)
    rmse = float(np.sqrt(mean_squared_error(y_test_reg, pred_pos)))

    print(f"  AUC             : {auc:.4f}")
    print(f"  F1  (t={best_thresh:.2f})  : {f1:.4f}")
    print(f"  Precision       : {precision:.4f}")
    print(f"  Recall          : {recall:.4f}")
    print(f"  MAE (delay>0)   : {mae:.4f} min")
    print(f"  RMSE(delay>0)   : {rmse:.4f} min")
    print(f"  Threshold       : {best_thresh:.4f}")

    # 9. Feature importance top-10
    print("\n--- Classifier Feature Importance (Top 10) ---")
    imp_pairs = sorted(
        zip(CLF_FEATURES, base_clf.feature_importances_), key=lambda x: -x[1]
    )[:10]
    for name, imp in imp_pairs:
        print(f"  {name:<32} {imp:.0f}")

    # 10. Build and save predictor
    print("\n--- Building RouteDelayPredictor v6 ---")
    predictor = RouteDelayPredictor(
        clf              = calibrated_clf,
        reg              = reg,
        threshold        = best_thresh,
        feature_cols     = CLF_FEATURES,
        reg_feature_cols = REG_FEATURES,
        augment_fn       = _augment_features,  # model_classes version — inference-safe (no route_id needed)
        p90_reg          = p90_reg,
    )
    joblib.dump(predictor, OUTPUT_PKL)
    print(f"  Saved: {OUTPUT_PKL}")

    # 11. Sanity check
    print("\n--- Sanity Check ---")

    low_risk_df = pd.DataFrame([{
        "distance_from_prev_km": 15.0, "planned_travel_min": 20.0,
        "stop_sequence": 2, "road_type": 3, "hour_of_day": 10,
        "day_of_week": 2, "traffic_level": 3, "weather_condition": 0,
        "is_rush_hour": 0, "precipitation_mm": 0.0,
        "hist_delay_probability": 0.05, "cumulative_delay_min": 0.0,
        "prev_stop_delay_min": 0.0, "time_window_slack_min": 90.0,
        "stop_progress_ratio": 0.3, "planned_speed_kmh": 45.0,
        "hist_slack_min": 70.0, "is_high_risk_weather": 0,
        "is_urban_road": 1, "traffic_weather_risk": 2,
    }])

    high_risk_df = pd.DataFrame([{
        "distance_from_prev_km": 8.0, "planned_travel_min": 25.0,
        "stop_sequence": 5, "road_type": 3, "hour_of_day": 17,
        "day_of_week": 1, "traffic_level": 0, "weather_condition": 3,
        "is_rush_hour": 1, "precipitation_mm": 8.5,
        "hist_delay_probability": 0.75, "cumulative_delay_min": 20.0,
        "prev_stop_delay_min": 12.0, "time_window_slack_min": 15.0,
        "stop_progress_ratio": 0.7, "planned_speed_kmh": 19.2,
        "hist_slack_min": 10.0, "is_high_risk_weather": 1,
        "is_urban_road": 1, "traffic_weather_risk": 16,
    }])

    low_pred  = predictor.predict_route(low_risk_df)["stop_predictions"][0]
    high_pred = predictor.predict_route(high_risk_df)["stop_predictions"][0]

    print(f"  Low  risk: delay_prob={low_pred['delay_probability']:.4f}, "
          f"expected_delay={low_pred['expected_delay_min']:.2f} min, "
          f"will_miss={low_pred['will_miss_window']}")
    print(f"  High risk: delay_prob={high_pred['delay_probability']:.4f}, "
          f"expected_delay={high_pred['expected_delay_min']:.2f} min, "
          f"will_miss={high_pred['will_miss_window']}")

    assert high_pred["delay_probability"] > 0.3, (
        f"FAIL: high risk delay_probability={high_pred['delay_probability']:.4f} <= 0.3"
    )
    assert high_pred["expected_delay_min"] > 0, (
        f"FAIL: high risk expected_delay_min={high_pred['expected_delay_min']:.4f} <= 0"
    )
    if high_pred["will_miss_window"]:
        assert high_pred["expected_delay_min"] > 0, (
            f"FAIL: will_miss_window=True but expected_delay_min="
            f"{high_pred['expected_delay_min']}"
        )

    print("\n  All sanity checks PASSED.")
    print("\n" + "=" * 60)
    print("  v6 training complete.")
    print("=" * 60)
    print(
        "\nNOTE: Update inference.py _get_predictor() to load "
        "'route_predictor_v6.pkl' first."
    )


if __name__ == "__main__":
    main()
