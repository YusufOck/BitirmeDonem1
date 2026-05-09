"""
Train the v9 logistics delay model.

v9 keeps the v8 runtime-safe feature pipeline, then improves the weak point we
observed in evaluation: high-delay stops were under-estimated by a median-loss
regressor.  The expected-delay regressor now uses squared-error loss with
tail-aware sample weights, and the P90 model is calibrated on a held-out route
fold so worst-case estimates are not decorative.
"""

from __future__ import annotations

from pathlib import Path
import sys

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    mean_absolute_error,
    mean_pinball_loss,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
)
from sklearn.model_selection import GroupKFold


ML_DIR = Path(__file__).resolve().parent
ROOT = ML_DIR.parent
ANALYSIS_DIR = ROOT / "analysis_output"
ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ML_DIR) not in sys.path:
    sys.path.insert(0, str(ML_DIR))

from ml.feature_builder import build_feature_matrix  # noqa: E402
from ml.model_classes import RouteDelayPredictor, _augment_features  # noqa: E402


SEED = 42
DROP_COLS = ["stop_id", "route_id", "delay_at_stop_min", "missed_time_window", "actual_travel_min"]
DELAY_BUCKETS = [0, 5, 15, 30, 60, 120, np.inf]
DELAY_BUCKET_LABELS = ["0-5", "5-15", "15-30", "30-60", "60-120", "120+"]


def _split_by_route(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    groups = df["route_id"].astype(str).values
    y = df["delay_at_stop_min"].clip(lower=0.0).values
    splits = list(GroupKFold(n_splits=5).split(np.zeros(len(df)), y, groups))
    _, test_idx = splits[-1]
    _, val_idx = splits[-2]
    held_out = set(test_idx.tolist()) | set(val_idx.tolist())
    train_idx = np.array([idx for idx in range(len(df)) if idx not in held_out])
    return train_idx, val_idx, test_idx


def _sample_weights(y: np.ndarray) -> np.ndarray:
    weights = np.ones(len(y), dtype=float)
    weights[y >= 30.0] = 1.6
    weights[y >= 60.0] = 2.8
    weights[y >= 120.0] = 4.2
    return weights


def _severity_threshold(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    best_threshold = 15.0
    best_f1 = -1.0
    actual_severe = y_true >= 15.0
    for threshold in np.linspace(8.0, 35.0, 55):
        score = f1_score(actual_severe, y_pred >= threshold, zero_division=0)
        if score > best_f1:
            best_f1 = score
            best_threshold = float(threshold)
    return round(best_threshold, 2)


def _classification_threshold(y_true_delay: np.ndarray, prob: np.ndarray) -> float:
    best_threshold = 0.5
    best_f1 = -1.0
    y_late = y_true_delay >= 15.0
    for threshold in np.linspace(0.1, 0.9, 81):
        score = f1_score(y_late, prob >= threshold, zero_division=0)
        if score > best_f1:
            best_f1 = score
            best_threshold = float(threshold)
    return round(best_threshold, 4)


def _metrics(y_true: np.ndarray, y_pred: np.ndarray, prob: np.ndarray, threshold: float) -> dict:
    abs_error = np.abs(y_true - y_pred)
    pred_late = prob >= threshold
    result = {
        "MAE delay minutes": float(mean_absolute_error(y_true, y_pred)),
        "RMSE delay minutes": float(mean_squared_error(y_true, y_pred) ** 0.5),
        "Median absolute error minutes": float(np.median(abs_error)),
        "R2 delay": float(r2_score(y_true, y_pred)),
        "Within 5 minutes": float(np.mean(abs_error <= 5.0)),
        "Late precision": float(precision_score(y_true >= 15.0, pred_late, zero_division=0)),
        "Late recall": float(recall_score(y_true >= 15.0, pred_late, zero_division=0)),
        "Average precision": float(average_precision_score(y_true >= 15.0, prob)),
        "High-delay MAE >=60": float(abs_error[y_true >= 60.0].mean()) if np.any(y_true >= 60.0) else 0.0,
        "Extreme-delay MAE >=120": float(abs_error[y_true >= 120.0].mean()) if np.any(y_true >= 120.0) else 0.0,
    }
    return {key: round(value, 4) for key, value in result.items()}


def _bucket_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> pd.DataFrame:
    df = pd.DataFrame({"actual": y_true, "predicted": y_pred})
    df["bucket"] = pd.cut(df["actual"], DELAY_BUCKETS, labels=DELAY_BUCKET_LABELS, right=False)
    rows = []
    for bucket, group in df.groupby("bucket", observed=True):
        abs_error = (group["actual"] - group["predicted"]).abs()
        rows.append({
            "delay_bucket": str(bucket),
            "row_count": len(group),
            "mae_min": round(float(abs_error.mean()), 4),
            "rmse_min": round(float((abs_error.pow(2).mean()) ** 0.5), 4),
            "mean_actual_min": round(float(group["actual"].mean()), 4),
            "mean_predicted_min": round(float(group["predicted"].mean()), 4),
        })
    return pd.DataFrame(rows)


def _plot_actual_vs_predicted(y_true: np.ndarray, y_pred: np.ndarray) -> None:
    plt.figure(figsize=(8, 6))
    plt.scatter(y_true, y_pred, alpha=0.72, color="#2563eb")
    max_value = max(float(np.max(y_true)), float(np.max(y_pred)), 1.0)
    plt.plot([0, max_value], [0, max_value], "--", color="#ef4444", label="ideal prediction")
    plt.title("v9 Actual vs Predicted Delay")
    plt.xlabel("Actual delay (min)")
    plt.ylabel("Predicted delay (min)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(ANALYSIS_DIR / "v9_actual_vs_predicted_delay.png", dpi=180)
    plt.close()


def _plot_error_by_bucket(bucket_df: pd.DataFrame) -> None:
    plt.figure(figsize=(8, 5))
    plt.bar(bucket_df["delay_bucket"], bucket_df["mae_min"], color="#2563eb")
    plt.title("v9 Delay MAE by Actual Delay Bucket")
    plt.xlabel("Actual delay bucket (min)")
    plt.ylabel("MAE (min)")
    plt.tight_layout()
    plt.savefig(ANALYSIS_DIR / "v9_error_by_delay_bucket.png", dpi=180)
    plt.close()


def _save_feature_importance(model, X_test: pd.DataFrame, y_test: np.ndarray) -> None:
    result = permutation_importance(
        model,
        X_test,
        y_test,
        n_repeats=8,
        random_state=SEED,
        scoring="neg_mean_absolute_error",
    )
    importance = pd.DataFrame({
        "feature": X_test.columns,
        "importance_mean": result.importances_mean,
        "importance_std": result.importances_std,
    }).sort_values("importance_mean", ascending=False)
    importance.to_csv(ANALYSIS_DIR / "v9_feature_importance.csv", index=False)


def main() -> None:
    df_raw = build_feature_matrix(include_runtime_safe_ratio=True)
    df_aug = _augment_features(df_raw)
    train_idx, val_idx, test_idx = _split_by_route(df_raw)

    feature_cols = df_aug.drop(columns=[column for column in DROP_COLS if column in df_aug.columns]).columns.tolist()
    reg_feature_cols = df_raw.drop(columns=[column for column in DROP_COLS if column in df_raw.columns]).columns.tolist()

    X_clf = df_aug[feature_cols]
    X_reg = df_raw[reg_feature_cols]
    y_delay = df_raw["delay_at_stop_min"].clip(lower=0.0).values
    y_late = (y_delay >= 15.0).astype(int)
    weights = _sample_weights(y_delay[train_idx])

    clf = HistGradientBoostingClassifier(
        learning_rate=0.055,
        max_iter=320,
        max_leaf_nodes=31,
        l2_regularization=0.04,
        random_state=SEED,
    )
    reg = HistGradientBoostingRegressor(
        loss="squared_error",
        learning_rate=0.045,
        max_iter=420,
        max_leaf_nodes=31,
        l2_regularization=0.04,
        random_state=SEED,
    )
    p90_reg = HistGradientBoostingRegressor(
        loss="quantile",
        quantile=0.9,
        learning_rate=0.055,
        max_iter=360,
        max_leaf_nodes=31,
        l2_regularization=0.04,
        random_state=SEED,
    )

    clf.fit(X_clf.iloc[train_idx], y_late[train_idx], sample_weight=weights)
    reg.fit(X_reg.iloc[train_idx], y_delay[train_idx], sample_weight=weights)
    p90_reg.fit(X_reg.iloc[train_idx], y_delay[train_idx], sample_weight=weights)

    val_prob = clf.predict_proba(X_clf.iloc[val_idx])[:, 1]
    threshold = _classification_threshold(y_delay[val_idx], val_prob)
    val_pred = np.clip(reg.predict(X_reg.iloc[val_idx]), 0.0, None)
    severe_threshold = _severity_threshold(y_delay[val_idx], val_pred)

    val_p90 = np.clip(p90_reg.predict(X_reg.iloc[val_idx]), 0.0, None)
    p90_offset = max(0.0, float(np.quantile(y_delay[val_idx] - val_p90, 0.90)))

    test_prob = clf.predict_proba(X_clf.iloc[test_idx])[:, 1]
    test_pred = np.clip(reg.predict(X_reg.iloc[test_idx]), 0.0, None)
    test_p90 = np.clip(p90_reg.predict(X_reg.iloc[test_idx]) + p90_offset, 0.0, None)
    test_y = y_delay[test_idx]

    test_metrics = _metrics(test_y, test_pred, test_prob, threshold)
    p90_coverage = float(np.mean(test_y <= test_p90))
    p90_pinball = float(mean_pinball_loss(test_y, test_p90, alpha=0.9))

    predictor = RouteDelayPredictor(
        clf=clf,
        reg=reg,
        threshold=threshold,
        feature_cols=feature_cols,
        reg_feature_cols=reg_feature_cols,
        augment_fn=_augment_features,
        severe_threshold_min=severe_threshold,
        p90_reg=p90_reg,
        p90_offset=p90_offset,
    )
    joblib.dump(predictor, ML_DIR / "route_predictor_v9.pkl")

    _plot_actual_vs_predicted(test_y, test_pred)
    bucket_df = _bucket_metrics(test_y, test_pred)
    bucket_df.to_csv(ANALYSIS_DIR / "v9_error_by_delay_bucket.csv", index=False)
    _plot_error_by_bucket(bucket_df)
    _save_feature_importance(reg, X_reg.iloc[test_idx], test_y)

    report_rows = [
        {"metric": key, "value": value}
        for key, value in test_metrics.items()
    ]
    report_rows.extend([
        {"metric": "P90 coverage", "value": round(p90_coverage, 4)},
        {"metric": "P90 pinball loss", "value": round(p90_pinball, 4)},
        {"metric": "P90 calibration offset min", "value": round(p90_offset, 4)},
        {"metric": "Threshold", "value": threshold},
        {"metric": "Severe threshold minutes", "value": severe_threshold},
        {"metric": "Classifier features", "value": len(feature_cols)},
        {"metric": "Regressor features", "value": len(reg_feature_cols)},
        {"metric": "Test rows", "value": len(test_idx)},
        {"metric": "Test routes", "value": df_raw.iloc[test_idx]["route_id"].nunique()},
    ])
    report = pd.DataFrame(report_rows)
    report.to_csv(ANALYSIS_DIR / "v9_model_metrics.csv", index=False)

    markdown_rows = "\n".join(f"| {row['metric']} | {row['value']} |" for _, row in report.iterrows())
    (ANALYSIS_DIR / "v9_model_metrics.md").write_text(
        "# v9 Runtime-Safe Model Metrics\n\n"
        "v9 uses the same runtime-safe feature builder as inference, a tail-aware "
        "regressor, and calibrated P90 output. No actual-travel leakage is used "
        "for prediction features.\n\n"
        "| Metric | Value |\n|---|---|\n"
        + markdown_rows
        + "\n",
        encoding="utf-8",
    )

    print(report.to_string(index=False))
    print(f"Saved model: {ML_DIR / 'route_predictor_v9.pkl'}")


if __name__ == "__main__":
    main()
