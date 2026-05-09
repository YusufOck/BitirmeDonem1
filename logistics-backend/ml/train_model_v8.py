"""
Train the runtime-safe v8 delay model.

v8 fixes two presentation-critical problems in v7:
- training and runtime now share the same categorical encodings;
- the model no longer depends on actual travel time leakage.
"""

from __future__ import annotations

from pathlib import Path
import sys

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
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
DROP_COLS = [
    "stop_id",
    "route_id",
    "delay_at_stop_min",
    "missed_time_window",
    "actual_travel_min",
]


def _split_by_route(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    groups = df["route_id"].astype(str).values
    y = df["missed_time_window"].astype(int).values
    splits = list(GroupKFold(n_splits=5).split(np.zeros(len(df)), y, groups))
    _, test_idx = splits[-1]
    _, val_idx = splits[-2]
    held_out = set(test_idx.tolist()) | set(val_idx.tolist())
    train_idx = np.array([idx for idx in range(len(df)) if idx not in held_out])
    return train_idx, val_idx, test_idx


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


def _classification_threshold(y_true: np.ndarray, prob: np.ndarray) -> float:
    best_threshold = 0.5
    best_f1 = -1.0
    y_late = y_true >= 15.0
    for threshold in np.linspace(0.1, 0.9, 81):
        score = f1_score(y_late, prob >= threshold, zero_division=0)
        if score > best_f1:
            best_f1 = score
            best_threshold = float(threshold)
    return round(best_threshold, 4)


def _metrics(y_true: np.ndarray, y_pred: np.ndarray, prob: np.ndarray, threshold: float) -> dict:
    abs_error = np.abs(y_true - y_pred)
    pred_late = prob >= threshold
    return {
        "mae": round(float(mean_absolute_error(y_true, y_pred)), 4),
        "rmse": round(float(mean_squared_error(y_true, y_pred) ** 0.5), 4),
        "median_absolute_error": round(float(np.median(abs_error)), 4),
        "r2": round(float(r2_score(y_true, y_pred)), 4),
        "within_5_min": round(float(np.mean(abs_error <= 5.0)), 4),
        "late_precision": round(float(precision_score(y_true >= 15.0, pred_late, zero_division=0)), 4),
        "late_recall": round(float(recall_score(y_true >= 15.0, pred_late, zero_division=0)), 4),
        "average_precision": round(float(average_precision_score(y_true >= 15.0, prob)), 4),
    }


def _plot_actual_vs_predicted(y_true: np.ndarray, y_pred: np.ndarray) -> None:
    plt.figure(figsize=(8, 6))
    plt.scatter(y_true, y_pred, alpha=0.72, color="#2563eb")
    max_value = max(float(np.max(y_true)), float(np.max(y_pred)), 1.0)
    plt.plot([0, max_value], [0, max_value], "--", color="#ef4444", label="ideal prediction")
    plt.title("v8 Actual vs Predicted Delay")
    plt.xlabel("Actual delay (min)")
    plt.ylabel("Predicted delay (min)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(ANALYSIS_DIR / "v8_actual_vs_predicted_delay.png", dpi=180)
    plt.close()


def main() -> None:
    df_raw = build_feature_matrix(include_runtime_safe_ratio=True)
    df_aug = _augment_features(df_raw)

    train_idx, val_idx, test_idx = _split_by_route(df_raw)

    feature_cols = df_aug.drop(columns=[column for column in DROP_COLS if column in df_aug.columns]).columns.tolist()
    reg_feature_cols = df_raw.drop(columns=[column for column in DROP_COLS if column in df_raw.columns]).columns.tolist()

    X_clf = df_aug[feature_cols]
    X_reg = df_raw[reg_feature_cols]
    y_clf = (df_raw["delay_at_stop_min"].clip(lower=0.0) >= 15.0).astype(int).values
    y_reg = df_raw["delay_at_stop_min"].clip(lower=0.0).values

    clf = HistGradientBoostingClassifier(
        learning_rate=0.055,
        max_iter=280,
        max_leaf_nodes=31,
        l2_regularization=0.04,
        random_state=SEED,
    )
    reg = HistGradientBoostingRegressor(
        loss="absolute_error",
        learning_rate=0.055,
        max_iter=320,
        max_leaf_nodes=31,
        l2_regularization=0.04,
        random_state=SEED,
    )
    p90_reg = HistGradientBoostingRegressor(
        loss="quantile",
        quantile=0.9,
        learning_rate=0.055,
        max_iter=320,
        max_leaf_nodes=31,
        l2_regularization=0.04,
        random_state=SEED,
    )

    clf.fit(X_clf.iloc[train_idx], y_clf[train_idx])
    reg.fit(X_reg.iloc[train_idx], y_reg[train_idx])
    p90_reg.fit(X_reg.iloc[train_idx], y_reg[train_idx])

    val_prob = clf.predict_proba(X_clf.iloc[val_idx])[:, 1]
    threshold = _classification_threshold(y_reg[val_idx], val_prob)
    val_pred = reg.predict(X_reg.iloc[val_idx])
    severe_threshold = _severity_threshold(y_reg[val_idx], val_pred)

    test_prob = clf.predict_proba(X_clf.iloc[test_idx])[:, 1]
    test_pred = np.clip(reg.predict(X_reg.iloc[test_idx]), 0.0, None)
    test_p90 = np.clip(p90_reg.predict(X_reg.iloc[test_idx]), 0.0, None)
    test_y = y_reg[test_idx]
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
    )
    joblib.dump(predictor, ML_DIR / "route_predictor_v8.pkl")

    _plot_actual_vs_predicted(test_y, test_pred)

    report = pd.DataFrame([
        {"metric": "MAE delay minutes", "value": test_metrics["mae"]},
        {"metric": "RMSE delay minutes", "value": test_metrics["rmse"]},
        {"metric": "Median absolute error minutes", "value": test_metrics["median_absolute_error"]},
        {"metric": "R2 delay", "value": test_metrics["r2"]},
        {"metric": "Within 5 minutes", "value": test_metrics["within_5_min"]},
        {"metric": "Late precision", "value": test_metrics["late_precision"]},
        {"metric": "Late recall", "value": test_metrics["late_recall"]},
        {"metric": "Average precision", "value": test_metrics["average_precision"]},
        {"metric": "P90 coverage", "value": round(p90_coverage, 4)},
        {"metric": "P90 pinball loss", "value": round(p90_pinball, 4)},
        {"metric": "Threshold", "value": threshold},
        {"metric": "Severe threshold minutes", "value": severe_threshold},
        {"metric": "Classifier features", "value": len(feature_cols)},
        {"metric": "Regressor features", "value": len(reg_feature_cols)},
        {"metric": "Test rows", "value": len(test_idx)},
        {"metric": "Test routes", "value": df_raw.iloc[test_idx]["route_id"].nunique()},
    ])
    report.to_csv(ANALYSIS_DIR / "v8_model_metrics.csv", index=False)
    markdown_rows = "\n".join(
        f"| {row['metric']} | {row['value']} |"
        for _, row in report.iterrows()
    )
    (ANALYSIS_DIR / "v8_model_metrics.md").write_text(
        "# v8 Runtime-Safe Model Metrics\n\n"
        "This model is trained with the same categorical encodings and runtime-safe "
        "features used by `ml/inference.py`. It does not use actual travel time as "
        "a leakage feature.\n\n"
        "| Metric | Value |\n|---|---|\n"
        + markdown_rows
        + "\n",
        encoding="utf-8",
    )

    print(report.to_string(index=False))
    print(f"Saved model: {ML_DIR / 'route_predictor_v8.pkl'}")


if __name__ == "__main__":
    main()
