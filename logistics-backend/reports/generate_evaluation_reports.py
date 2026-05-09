"""
Generate academic evaluation figures for the logistics AI system.

The web UI is intentionally kept free of model-validation charts. This script
uses offline comparison data and saves instructor-facing images under
evaluation_results/.
"""

from __future__ import annotations

from pathlib import Path
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import r2_score
from sklearn.model_selection import GroupKFold

ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_DIR = ROOT / "data" / "raw_data"
OUTPUT_DIR = ROOT / "evaluation_results"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml.feature_builder import build_feature_matrix  # noqa: E402
from ml.inference import predict_route  # noqa: E402


DELAY_BUCKETS = [0, 5, 15, 30, 60, 120, np.inf]
DELAY_BUCKET_LABELS = ["0-5", "5-15", "15-30", "30-60", "60-120", "120+"]


def _ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _select_grouped_test_rows(df: pd.DataFrame) -> pd.DataFrame:
    if "route_id" not in df.columns or df["route_id"].nunique() < 5:
        return df.copy()

    groups = df["route_id"].astype(str).values
    split_source = np.zeros(len(df))
    splits = list(GroupKFold(n_splits=5).split(split_source, df["delay_at_stop_min"].values, groups))
    _, test_idx = splits[-1]
    return df.iloc[test_idx].copy()


def _load_delay_eval_data() -> pd.DataFrame:
    df = build_feature_matrix(RAW_DATA_DIR, include_runtime_safe_ratio=True)
    if "delay_at_stop_min" not in df.columns:
        raise ValueError("raw route feature matrix must contain delay_at_stop_min as ground truth")

    sample = _select_grouped_test_rows(df)
    if "route_id" not in sample.columns:
        sample["route_id"] = "single_route"
    if "stop_sequence" in sample.columns:
        sample = sample.sort_values(["route_id", "stop_sequence"]).copy()

    predicted_rows: list[pd.DataFrame] = []
    for route_id, route_df in sample.groupby("route_id", sort=False):
        route_records = route_df.to_dict(orient="records")
        route_predictions = predict_route(route_records)["stop_predictions"]
        current = route_df.copy()
        current["predicted_delay_min"] = [item["expected_delay_min"] for item in route_predictions]
        current["predicted_delay_p90_min"] = [item.get("delay_p90_min", item["expected_delay_min"]) for item in route_predictions]
        current["delay_probability"] = [item["delay_probability"] for item in route_predictions]
        current["risk_level_pred"] = [item["risk_level"] for item in route_predictions]
        current["severity_pred"] = [item["severity"] for item in route_predictions]
        current["eval_route_id"] = route_id
        predicted_rows.append(current)

    sample = pd.concat(predicted_rows, ignore_index=True)
    sample["actual_delay_min_raw"] = pd.to_numeric(sample["delay_at_stop_min"], errors="coerce").fillna(0.0)
    sample["actual_delay_min"] = sample["actual_delay_min_raw"].clip(lower=0.0)
    sample["absolute_error_min"] = (sample["actual_delay_min"] - sample["predicted_delay_min"]).abs()
    if "planned_travel_min" in sample.columns:
        sample["planned_travel_min"] = pd.to_numeric(sample["planned_travel_min"], errors="coerce").fillna(0.0)
    else:
        sample["planned_travel_min"] = 0.0
    if "actual_travel_min" in sample.columns:
        sample["actual_travel_min"] = pd.to_numeric(sample["actual_travel_min"], errors="coerce").fillna(sample["planned_travel_min"])
    else:
        sample["actual_travel_min"] = sample["planned_travel_min"]
    return sample


def _save_actual_vs_predicted(df: pd.DataFrame) -> None:
    plt.figure(figsize=(8, 6))
    plt.scatter(df["actual_delay_min"], df["predicted_delay_min"], alpha=0.72, color="#2563eb")
    max_value = max(float(df["actual_delay_min"].max()), float(df["predicted_delay_min"].max()), 1.0)
    plt.plot([0, max_value], [0, max_value], "--", color="#ef4444", label="ideal prediction")
    plt.title("Actual vs Predicted Stop Delay (Grouped Route Evaluation)")
    plt.xlabel("Actual delay (min)")
    plt.ylabel("Predicted delay (min)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "actual_vs_predicted_delay.png", dpi=180)
    plt.close()


def _save_error_distribution(df: pd.DataFrame) -> None:
    plt.figure(figsize=(8, 5))
    plt.hist(df["absolute_error_min"], bins=24, color="#2563eb", alpha=0.82)
    plt.title("Delay Prediction Absolute Error Distribution")
    plt.xlabel("Absolute error (min)")
    plt.ylabel("Stop count")
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "delay_error_distribution.png", dpi=180)
    plt.close()


def _save_error_by_bucket(df: pd.DataFrame) -> None:
    bucketed = df.copy()
    bucketed["delay_bucket"] = pd.cut(
        bucketed["actual_delay_min"],
        DELAY_BUCKETS,
        labels=DELAY_BUCKET_LABELS,
        right=False,
    )
    rows = []
    for bucket, group in bucketed.groupby("delay_bucket", observed=True):
        if group.empty:
            continue
        rows.append({
            "delay_bucket": str(bucket),
            "row_count": len(group),
            "mae_min": round(float(group["absolute_error_min"].mean()), 4),
            "rmse_min": round(float((group["absolute_error_min"].pow(2).mean()) ** 0.5), 4),
            "mean_actual_min": round(float(group["actual_delay_min"].mean()), 4),
            "mean_predicted_min": round(float(group["predicted_delay_min"].mean()), 4),
        })
    out = pd.DataFrame(rows)
    out.to_csv(OUTPUT_DIR / "error_by_delay_bucket.csv", index=False)
    if out.empty:
        return

    plt.figure(figsize=(8, 5))
    plt.bar(out["delay_bucket"], out["mae_min"], color="#2563eb")
    plt.title("Delay Prediction Error by Actual Delay Bucket")
    plt.xlabel("Actual delay bucket (min)")
    plt.ylabel("MAE (min)")
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "error_by_delay_bucket.png", dpi=180)
    plt.close()


def _save_travel_time_comparison(df: pd.DataFrame) -> None:
    output_path = OUTPUT_DIR / "planned_vs_actual_travel_time.png"
    if "actual_travel_min" not in df.columns or df["actual_travel_min"].equals(df["planned_travel_min"]):
        if output_path.exists():
            output_path.unlink()
        return

    route_sample = df.head(50).copy()
    plt.figure(figsize=(10, 5))
    plt.plot(route_sample.index, route_sample["planned_travel_min"], label="planned travel", color="#2563eb")
    plt.plot(route_sample.index, route_sample["actual_travel_min"], label="actual travel", color="#f59e0b")
    plt.title("Planned vs Actual Travel Time")
    plt.xlabel("Stop sample")
    plt.ylabel("Travel time (min)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()


def _save_summary_csv(df: pd.DataFrame) -> None:
    mae = float(df["absolute_error_min"].mean())
    rmse = float(((df["actual_delay_min"] - df["predicted_delay_min"]) ** 2).mean() ** 0.5)
    median_ae = float(df["absolute_error_min"].median())
    within_5 = float((df["absolute_error_min"] <= 5).mean())
    r2 = float(r2_score(df["actual_delay_min"], df["predicted_delay_min"]))
    high = df[df["actual_delay_min"] >= 60.0]
    extreme = df[df["actual_delay_min"] >= 120.0]
    p90_coverage = float((df["actual_delay_min"] <= df["predicted_delay_p90_min"]).mean()) if "predicted_delay_p90_min" in df.columns else 0.0

    summary = pd.DataFrame([
        {"metric": "MAE delay minutes", "value": round(mae, 4)},
        {"metric": "RMSE delay minutes", "value": round(rmse, 4)},
        {"metric": "Median absolute error minutes", "value": round(median_ae, 4)},
        {"metric": "R2 delay", "value": round(r2, 4)},
        {"metric": "Within 5 minutes", "value": round(within_5, 4)},
        {"metric": "High-delay MAE >=60", "value": round(float(high["absolute_error_min"].mean()), 4) if not high.empty else 0.0},
        {"metric": "Extreme-delay MAE >=120", "value": round(float(extreme["absolute_error_min"].mean()), 4) if not extreme.empty else 0.0},
        {"metric": "P90 coverage", "value": round(p90_coverage, 4)},
        {"metric": "High-delay rows >=60", "value": len(high)},
        {"metric": "Extreme-delay rows >=120", "value": len(extreme)},
        {"metric": "Evaluation rows", "value": len(df)},
        {"metric": "Evaluation routes", "value": df["eval_route_id"].nunique()},
        {"metric": "Evaluation method", "value": "GroupKFold held-out routes; predicted route-by-route"},
    ])
    summary.to_csv(OUTPUT_DIR / "evaluation_summary.csv", index=False)
    df[[
        "eval_route_id",
        "stop_id",
        "stop_sequence",
        "actual_delay_min",
        "predicted_delay_min",
        "absolute_error_min",
        "delay_probability",
        "risk_level_pred",
        "severity_pred",
    ]].to_csv(OUTPUT_DIR / "delay_predictions_eval.csv", index=False)


def _write_data_gap_report() -> None:
    report = """# Evaluation Data Gap Report

Generated by `reports/generate_evaluation_reports.py`.

## Available comparison data
- Stop-level actual delay: `data/raw_data/route_stops.csv::delay_at_stop_min`
- Stop-level predicted delay: generated by `ml.inference.predict_route`
- Delay evaluation method: held-out route groups are selected with `GroupKFold`; predictions are generated route-by-route so route cascade logic is measured correctly.
- Feature method: same runtime-safe feature builder as v8 training; no actual-travel leakage is used for delay prediction.

## Missing comparison data
- RAG ground-truth question/answer pairs are not present in this repository.
- Retriever relevance labels are not present.
- Hallucination grader expected-vs-generated answer labels are not present.
- Ground-truth optimized route order labels are not present.

## Required format for future RAG/hallucination evaluation
Create `data/rag_evaluation_cases.csv` with:

`question,expected_context_ids,expected_answer,generated_answer,retrieved_context_ids,answer_supported`

The reporting script can then generate context recall, context precision, answer faithfulness, and hallucination-rate graphs from real labels instead of invented UI metrics.
"""
    (OUTPUT_DIR / "data_gap_report.md").write_text(report, encoding="utf-8")


def main() -> None:
    _ensure_output_dir()
    plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "ggplot")

    df = _load_delay_eval_data()
    _save_actual_vs_predicted(df)
    _save_error_distribution(df)
    _save_error_by_bucket(df)
    _save_travel_time_comparison(df)
    _save_summary_csv(df)
    _write_data_gap_report()

    print(f"Evaluation reports written to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
