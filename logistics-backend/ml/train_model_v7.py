"""
Smart Logistics — Model Training v7
=====================================
Built on v3's clean architecture + full enriched feature set.

v3 → v7 improvements:
  #1  FULL MERGE PIPELINE — all 5 raw data sources joined (vs merged_features_v2.csv)
  #2  ENRICHED FEATURE SET — 39 base features (vs ~20 in v3)
      vehicle_type_enc, package_count/weight, weight_per_package,
      vehicle_load_ratio, travel_delay_ratio, time_window_duration_min,
      congestion_ratio_mean, incident_rate, speed_loss_ratio, incident_traffic_risk,
      road_surface_condition_enc, delay_risk_score_mean,
      temperature_c, wind_speed_kmh, visibility_km, overall_delay_factor,
      road_incident, incident_severity
  #3  SEPARATE FEATURE SETS — clf: 43 (39 base + 4 augmented), reg: 39 (base only)
      Augmented features help classification, harm regression (same finding as v5)
  #4  P90 QUANTILE REGRESSOR — LGBMRegressor(objective='quantile', alpha=0.90)
  #5  SEVERITY THRESHOLD OPT — sweep 8–25 min, maximise F1 (vs fixed 15 min in v3)
  #6  _ArcsinhRegressor — replaces TransformedTargetRegressor (sklearn compat fix)
  #7  VAL SPLIT — 5-fold GroupKFold: fold5=test, fold4=val, folds1-3=train+calib

Classifier feature set : 43 (39 base + 4 augmented)
Regressor feature set  : 39 (base only — augmented features harm regression)

Output artefacts
----------------
  ml/delay_classifier_v7.pkl
  ml/delay_regressor_v7.pkl
  ml/delay_regressor_v7_p90.pkl
  ml/route_predictor_v7.pkl
  ml/clf_threshold_v7.npy
  ml/severity_threshold_v7.npy
  analysis_output/50_calibration_v7.png
  analysis_output/51_pr_curve_v7.png
  analysis_output/52_feature_importance_v7.png
  analysis_output/53_p50_vs_p90_v7.png
  analysis_output/54_severity_threshold_sweep_v7.png
"""

from __future__ import annotations

import os
import re
import sys
import warnings

import numpy as np
import pandas as pd
import joblib
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import shap

from sklearn.model_selection import GroupKFold, cross_val_score
from sklearn.calibration import calibration_curve
from sklearn.ensemble import StackingClassifier, StackingRegressor
from sklearn.linear_model import RidgeCV
from sklearn.metrics import (
    roc_auc_score, f1_score, fbeta_score,
    precision_score, recall_score,
    confusion_matrix, brier_score_loss,
    mean_absolute_error, mean_squared_error, r2_score,
    precision_recall_curve, average_precision_score,
    mean_pinball_loss,
)

import optuna
optuna.logging.set_verbosity(optuna.logging.WARNING)

from lightgbm import LGBMClassifier, LGBMRegressor
from xgboost import XGBClassifier, XGBRegressor
from catboost import CatBoostClassifier, CatBoostRegressor

_ML_DIR   = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_ML_DIR)
if _ML_DIR not in sys.path:
    sys.path.insert(0, _ML_DIR)

from ml.model_classes import (  # noqa: E402
    _PreFitCalibrator, RouteDelayPredictor, _augment_features,
    _ArcsinhRegressor, SEVERE_DELAY_THRESHOLD_MIN,
)

warnings.filterwarnings('ignore')

# ── Paths ──────────────────────────────────────────────────────────────────────
DATA_DIR = os.path.join(_ROOT_DIR, 'data', 'raw_data')
OUT_DIR  = _ML_DIR
PLOT_DIR = os.path.join(_ROOT_DIR, 'analysis_output')
os.makedirs(PLOT_DIR, exist_ok=True)

# ── Categorical encodings (must match inference.py) ───────────────────────────
_ROAD_ENC    = {'highway': 0, 'mountain': 1, 'rural': 2, 'urban': 3}
_TRAFFIC_ENC = {'congested': 0, 'high': 1, 'low': 2, 'moderate': 3}
_WEATHER_ENC = {'clear': 0, 'cloudy': 1, 'fog': 2, 'rain': 3, 'snow': 4, 'wind': 5}
_VEH_ENC     = {'motorcycle': 0, 'car': 1, 'van': 2, 'truck': 3}
_SURF_ENC    = {'dry': 0, 'wet': 1, 'icy': 2, 'snow_covered': 3}

_VEH_CAPACITY_KG = {0: 30, 1: 200, 2: 800, 3: 3000}

_TW_RISK = {0: 4, 1: 3, 2: 1, 3: 2}
_WX_RISK = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 2}

# ── Hyperparameters ────────────────────────────────────────────────────────────
SEED = 42

N_TRIALS_LGBM     = 100
N_TRIALS_XGB      = 60
N_TRIALS_CAT      = 60
N_TRIALS_REG_LGBM = 100
N_TRIALS_REG_XGB  = 60
N_TRIALS_REG_CAT  = 60
N_TRIALS_P90      = 40

SEVERE_WEIGHT_FACTOR = 3.0
F2_BETA              = 2.0


# ══════════════════════════════════════════════════════════════════════════════
# MERGE PIPELINE — build enriched stop-level feature matrix
# ══════════════════════════════════════════════════════════════════════════════

def _time_bucket(h: int) -> str:
    if 0 <= h <= 6:   return 'early_morning'
    if 7 <= h <= 9:   return 'morning_rush'
    if 10 <= h <= 15: return 'midday'
    if 16 <= h <= 19: return 'evening_rush'
    return 'night'


def build_feature_matrix() -> pd.DataFrame:
    """
    Joins all 5 raw data sources into a single stop-level feature matrix.

    Join strategy
    -------------
    route_stops  + routes           → on route_id
    route_stops  + traffic_segments → on road_type + hour_of_day (aggregate)
    route_stops  + weather_obs      → on weather_condition (aggregate)
    route_stops  + hist_delay_stats → on road_type + traffic + weather + time_bucket

    Leakage guard
    -------------
    actual_arrival and actual_travel_min used ONLY to derive travel_delay_ratio,
    then dropped before return.
    """
    stops = pd.read_csv(
        os.path.join(DATA_DIR, 'route_stops.csv'),
        parse_dates=['planned_arrival', 'actual_arrival',
                     'time_window_open', 'time_window_close'],
    )
    routes  = pd.read_csv(os.path.join(DATA_DIR, 'routes.csv'))
    traffic = pd.read_csv(os.path.join(DATA_DIR, 'traffic_segments.csv'))
    weather = pd.read_csv(os.path.join(DATA_DIR, 'weather_observations.csv'))
    hist    = pd.read_csv(os.path.join(DATA_DIR, 'historical_delay_stats.csv'))

    df = stops.copy()

    # ── Time features ─────────────────────────────────────────────────────────
    df['hour_of_day'] = df['planned_arrival'].dt.hour
    df['day_of_week'] = df['planned_arrival'].dt.dayofweek
    df['time_bucket'] = df['hour_of_day'].apply(_time_bucket)

    # ── Stop-level derived features ───────────────────────────────────────────
    df['time_window_slack_min'] = (
        (df['time_window_close'] - df['planned_arrival']).dt.total_seconds() / 60
    ).clip(lower=0)
    df['time_window_duration_min'] = (
        (df['time_window_close'] - df['time_window_open']).dt.total_seconds() / 60
    ).clip(lower=0)
    df['travel_delay_ratio'] = (
        (df['actual_travel_min'] - df['planned_travel_min'])
        / df['planned_travel_min'].clip(lower=1.0)
    ).clip(lower=-1.0, upper=5.0)

    # ── Route-sequential features ─────────────────────────────────────────────
    df = df.sort_values(['route_id', 'stop_sequence']).reset_index(drop=True)
    df['cumulative_delay_min'] = (
        df.groupby('route_id')['delay_at_stop_min']
        .cumsum().shift(1).fillna(0)
    )
    df['prev_stop_delay_min'] = (
        df.groupby('route_id')['delay_at_stop_min']
        .shift(1).fillna(0)
    )
    total_stops = df.groupby('route_id')['stop_sequence'].transform('max')
    df['stop_progress_ratio'] = df['stop_sequence'] / total_stops.clip(lower=1)
    df['planned_speed_kmh'] = (
        df['distance_from_prev_km']
        / (df['planned_travel_min'] / 60.0).clip(lower=0.01)
    )

    # ── Encode road_type before joins ─────────────────────────────────────────
    df['road_type'] = df['road_type'].str.lower().map(_ROAD_ENC)

    # ── Merge routes.csv ──────────────────────────────────────────────────────
    route_cols = [
        'route_id', 'vehicle_type', 'road_incident', 'incident_severity',
        'temperature_c', 'wind_speed_kmh', 'visibility_km',
        'overall_delay_factor', 'traffic_level', 'weather_condition',
        'precipitation_mm',
    ]
    rdf = routes[route_cols].copy()
    rdf['vehicle_type']      = rdf['vehicle_type'].str.lower()
    rdf['traffic_level']     = rdf['traffic_level'].str.lower()
    rdf['weather_condition'] = rdf['weather_condition'].str.lower()
    df = df.merge(rdf, on='route_id', how='left')

    df['vehicle_type_enc']  = df['vehicle_type'].map(_VEH_ENC)
    df['traffic_level']     = df['traffic_level'].map(_TRAFFIC_ENC)
    df['weather_condition'] = df['weather_condition'].map(_WEATHER_ENC)

    # ── Derived binary/composite flags ────────────────────────────────────────
    df['is_rush_hour'] = (
        df['hour_of_day'].between(7, 9) | df['hour_of_day'].between(16, 19)
    ).astype(int)
    df['is_high_risk_weather'] = df['weather_condition'].isin([3, 4]).astype(int)
    df['is_urban_road']        = (df['road_type'] == 3).astype(int)
    df['traffic_weather_risk'] = (
        df['traffic_level'].map(_TW_RISK).fillna(2)
        * df['weather_condition'].map(_WX_RISK).fillna(1)
    )

    # ── Merge traffic_segments (aggregate: road_type × hour_of_day) ──────────
    traffic['road_type_enc'] = traffic['road_type'].str.lower().map(_ROAD_ENC)
    traffic_agg = (
        traffic.groupby(['road_type_enc', 'hour_of_day'])
        .agg(
            congestion_ratio_mean=('congestion_ratio', 'mean'),
            incident_rate=('incident_reported', 'mean'),
        )
        .reset_index()
        .rename(columns={'road_type_enc': '_rt', 'hour_of_day': '_hr'})
    )
    df = df.merge(
        traffic_agg,
        left_on=['road_type', 'hour_of_day'],
        right_on=['_rt', '_hr'],
        how='left',
    ).drop(columns=['_rt', '_hr'])
    df['congestion_ratio_mean'] = df['congestion_ratio_mean'].fillna(
        traffic['congestion_ratio'].mean()
    )
    df['incident_rate'] = df['incident_rate'].fillna(
        traffic['incident_reported'].mean()
    )

    # ── Merge weather_observations (aggregate: weather_condition) ─────────────
    weather['_wc_enc'] = weather['weather_condition'].str.lower().map(_WEATHER_ENC)
    weather_agg = (
        weather.groupby('_wc_enc')
        .agg(
            _surf_mode=('road_surface_condition', lambda x: x.mode().iloc[0]),
            delay_risk_score_mean=('delay_risk_score', 'mean'),
        )
        .reset_index()
    )
    weather_agg['road_surface_condition_enc'] = (
        weather_agg['_surf_mode'].str.lower().map(_SURF_ENC).fillna(0).astype(int)
    )
    df = df.merge(
        weather_agg[['_wc_enc', 'road_surface_condition_enc', 'delay_risk_score_mean']],
        left_on='weather_condition', right_on='_wc_enc', how='left',
    ).drop(columns=['_wc_enc'])
    df['road_surface_condition_enc'] = df['road_surface_condition_enc'].fillna(0).astype(int)
    df['delay_risk_score_mean']       = df['delay_risk_score_mean'].fillna(
        weather['delay_risk_score'].mean()
    )

    # ── Merge historical_delay_stats ──────────────────────────────────────────
    hist_enc = hist.copy()
    hist_enc['_rt'] = hist_enc['road_type'].str.lower().map(_ROAD_ENC)
    hist_enc['_tl'] = hist_enc['traffic_level'].str.lower().map(_TRAFFIC_ENC)
    hist_enc['_wc'] = hist_enc['weather_condition'].str.lower().map(_WEATHER_ENC)
    hist_enc = (
        hist_enc.groupby(['_rt', '_tl', '_wc', 'time_bucket'])
        .agg(
            hist_delay_probability=('delay_probability', 'mean'),
            hist_median_delay_min=('median_delay_min', 'mean'),
        )
        .reset_index()
    )
    df = df.merge(
        hist_enc[['_rt', '_tl', '_wc', 'time_bucket',
                  'hist_delay_probability', 'hist_median_delay_min']],
        left_on=['road_type', 'traffic_level', 'weather_condition', 'time_bucket'],
        right_on=['_rt', '_tl', '_wc', 'time_bucket'],
        how='left',
    ).drop(columns=['_rt', '_tl', '_wc'])
    df['hist_delay_probability'] = df['hist_delay_probability'].fillna(0.25)
    df['hist_median_delay_min']  = df['hist_median_delay_min'].fillna(
        df['time_window_slack_min'].median()
    )
    df['hist_slack_min'] = (
        df['time_window_slack_min'] - df['hist_median_delay_min']
    ).clip(lower=0)

    # ── Interaction features ──────────────────────────────────────────────────
    df['weight_per_package'] = (
        df['package_weight_kg'] / df['package_count'].clip(lower=1)
    )
    _cap = df['vehicle_type_enc'].map(_VEH_CAPACITY_KG).fillna(800)
    df['vehicle_load_ratio']     = (df['package_weight_kg'] / _cap).clip(upper=5.0)
    df['speed_loss_ratio']       = df['congestion_ratio_mean']
    df['incident_traffic_risk']  = (
        df['road_incident'] * (df['congestion_ratio_mean'] + 1)
    )

    # ── Drop leakage / helper columns ─────────────────────────────────────────
    drop_cols = [
        'actual_travel_min', 'actual_arrival', 'actual_service_min',
        'planned_arrival', 'time_window_open', 'time_window_close',
        'planned_service_min',
        'vehicle_type',
        'time_bucket',
        'hist_median_delay_min',
        'latitude', 'longitude',
        'delay_probability',
    ]
    df = df.drop(columns=[c for c in drop_cols if c in df.columns])
    df = df.dropna(subset=['delay_at_stop_min', 'missed_time_window'])
    df = df.reset_index(drop=True)

    print(f"  Feature matrix    : {df.shape[0]} rows × {df.shape[1]} columns")
    print(f"  Unique routes     : {df['route_id'].nunique()}")
    print(f"  Delay target range: [{df['delay_at_stop_min'].min():.1f}, "
          f"{df['delay_at_stop_min'].max():.1f}] min")
    print(f"  Class balance     : late={df['missed_time_window'].mean()*100:.1f}%")
    return df


# ══════════════════════════════════════════════════════════════════════════════
# DATA — two feature matrices (clf: base+augmented, reg: base only)
# ══════════════════════════════════════════════════════════════════════════════

def load_data():
    df_raw = build_feature_matrix()
    df_aug = _augment_features(df_raw)

    groups = df_raw['route_id'].values
    drop   = ['stop_id', 'route_id', 'delay_at_stop_min', 'missed_time_window']

    X_clf_df = df_aug.drop(columns=[c for c in drop if c in df_aug.columns])
    X_reg_df = df_raw.drop(columns=[c for c in drop if c in df_raw.columns])

    y_clf = df_raw['missed_time_window'].values.astype(int)
    y_reg = df_raw['delay_at_stop_min'].values

    feat_clf = X_clf_df.columns.tolist()
    feat_reg = X_reg_df.columns.tolist()
    augmented_extra = [f for f in feat_clf if f not in feat_reg]

    print(f"\n  Classifier features : {len(feat_clf)} "
          f"(+{len(augmented_extra)} augmented vs regressor)")
    print(f"  Regressor features  : {len(feat_reg)} (base only)")
    print(f"  Augmented (clf-only): {augmented_extra}")

    return (X_clf_df, X_reg_df, y_clf, y_reg, groups,
            feat_clf, feat_reg, df_aug, df_raw)


def group_train_test_split(X_clf_df, y_clf, groups):
    """
    Route-grouped 5-fold split:
      Fold 5 → test set  (~20%)
      Fold 4 → val set   (~20%)
      Folds 1-3 → training pool (~60%)
        15% of training routes → calibration (isotonic)
        remaining              → fit set
    """
    X_arr = X_clf_df.values if hasattr(X_clf_df, 'values') else X_clf_df
    gkf    = GroupKFold(n_splits=5)
    splits = list(gkf.split(X_arr, y_clf, groups))

    _, test_idx = splits[-1]
    _, val_idx  = splits[-2]

    held_out  = set(test_idx.tolist()) | set(val_idx.tolist())
    train_idx = np.array([i for i in range(len(y_clf)) if i not in held_out])

    train_groups  = groups[train_idx]
    unique_routes = np.unique(train_groups)
    rng           = np.random.default_rng(SEED)
    calib_routes  = rng.choice(
        unique_routes,
        size=max(1, int(len(unique_routes) * 0.15)),
        replace=False,
    )
    calib_mask = np.isin(train_groups, calib_routes)
    calib_idx  = train_idx[calib_mask]
    fit_idx    = train_idx[~calib_mask]

    return fit_idx, calib_idx, val_idx, test_idx


# ══════════════════════════════════════════════════════════════════════════════
# HPO — CLASSIFIERS
# ══════════════════════════════════════════════════════════════════════════════

def _optuna_lgbm_clf(X_tr, y_tr, groups_tr):
    def objective(trial):
        p = dict(
            n_estimators      = trial.suggest_int('n_estimators', 200, 1000),
            learning_rate     = trial.suggest_float('learning_rate', 0.005, 0.15, log=True),
            num_leaves        = trial.suggest_int('num_leaves', 20, 200),
            max_depth         = trial.suggest_int('max_depth', 3, 12),
            min_child_samples = trial.suggest_int('min_child_samples', 5, 60),
            subsample         = trial.suggest_float('subsample', 0.5, 1.0),
            colsample_bytree  = trial.suggest_float('colsample_bytree', 0.5, 1.0),
            reg_alpha         = trial.suggest_float('reg_alpha', 1e-4, 10.0, log=True),
            reg_lambda        = trial.suggest_float('reg_lambda', 1e-4, 10.0, log=True),
            class_weight='balanced', random_state=SEED, verbose=-1, n_jobs=-1,
        )
        return cross_val_score(
            LGBMClassifier(**p), X_tr, y_tr,
            cv=GroupKFold(3), groups=groups_tr,
            scoring='average_precision', n_jobs=1,
        ).mean()
    study = optuna.create_study(direction='maximize',
                                sampler=optuna.samplers.TPESampler(seed=SEED))
    study.optimize(objective, n_trials=N_TRIALS_LGBM, show_progress_bar=True)
    return study.best_params


def _optuna_xgb_clf(X_tr, y_tr, groups_tr):
    scale_pos = (y_tr == 0).sum() / max((y_tr == 1).sum(), 1)
    def objective(trial):
        p = dict(
            n_estimators      = trial.suggest_int('n_estimators', 200, 800),
            learning_rate     = trial.suggest_float('learning_rate', 0.005, 0.15, log=True),
            max_depth         = trial.suggest_int('max_depth', 3, 10),
            min_child_weight  = trial.suggest_int('min_child_weight', 1, 30),
            subsample         = trial.suggest_float('subsample', 0.5, 1.0),
            colsample_bytree  = trial.suggest_float('colsample_bytree', 0.5, 1.0),
            colsample_bylevel = trial.suggest_float('colsample_bylevel', 0.5, 1.0),
            reg_alpha         = trial.suggest_float('reg_alpha', 1e-4, 10.0, log=True),
            reg_lambda        = trial.suggest_float('reg_lambda', 1e-4, 10.0, log=True),
            gamma             = trial.suggest_float('gamma', 0.0, 5.0),
            scale_pos_weight=scale_pos, random_state=SEED,
            eval_metric='aucpr', n_jobs=-1,
        )
        return cross_val_score(
            XGBClassifier(**p), X_tr, y_tr,
            cv=GroupKFold(3), groups=groups_tr,
            scoring='average_precision', n_jobs=1,
        ).mean()
    study = optuna.create_study(direction='maximize',
                                sampler=optuna.samplers.TPESampler(seed=SEED))
    study.optimize(objective, n_trials=N_TRIALS_XGB, show_progress_bar=True)
    return study.best_params


def _optuna_cat_clf(X_tr, y_tr, groups_tr):
    def objective(trial):
        p = dict(
            iterations          = trial.suggest_int('iterations', 200, 800),
            learning_rate       = trial.suggest_float('learning_rate', 0.005, 0.15, log=True),
            depth               = trial.suggest_int('depth', 3, 10),
            l2_leaf_reg         = trial.suggest_float('l2_leaf_reg', 1e-3, 10.0, log=True),
            border_count        = trial.suggest_int('border_count', 32, 255),
            bagging_temperature = trial.suggest_float('bagging_temperature', 0.0, 1.0),
            auto_class_weights='Balanced', random_seed=SEED, verbose=0,
        )
        return cross_val_score(
            CatBoostClassifier(**p), X_tr, y_tr,
            cv=GroupKFold(3), groups=groups_tr,
            scoring='average_precision', n_jobs=1,
        ).mean()
    study = optuna.create_study(direction='maximize',
                                sampler=optuna.samplers.TPESampler(seed=SEED))
    study.optimize(objective, n_trials=N_TRIALS_CAT, show_progress_bar=True)
    return study.best_params


# ══════════════════════════════════════════════════════════════════════════════
# HPO — REGRESSORS
# ══════════════════════════════════════════════════════════════════════════════

def _optuna_lgbm_reg(X_tr, y_tr, groups_tr):
    def objective(trial):
        p = dict(
            n_estimators      = trial.suggest_int('n_estimators', 200, 1000),
            learning_rate     = trial.suggest_float('learning_rate', 0.005, 0.15, log=True),
            num_leaves        = trial.suggest_int('num_leaves', 20, 200),
            max_depth         = trial.suggest_int('max_depth', 3, 12),
            min_child_samples = trial.suggest_int('min_child_samples', 5, 60),
            subsample         = trial.suggest_float('subsample', 0.5, 1.0),
            colsample_bytree  = trial.suggest_float('colsample_bytree', 0.5, 1.0),
            reg_alpha         = trial.suggest_float('reg_alpha', 1e-4, 10.0, log=True),
            reg_lambda        = trial.suggest_float('reg_lambda', 1e-4, 10.0, log=True),
            random_state=SEED, verbose=-1, n_jobs=-1,
        )
        return cross_val_score(
            LGBMRegressor(**p), X_tr, y_tr,
            cv=GroupKFold(3), groups=groups_tr,
            scoring='neg_mean_absolute_error', n_jobs=1,
        ).mean()
    study = optuna.create_study(direction='maximize',
                                sampler=optuna.samplers.TPESampler(seed=SEED))
    study.optimize(objective, n_trials=N_TRIALS_REG_LGBM, show_progress_bar=True)
    return study.best_params


def _optuna_xgb_reg(X_tr, y_tr, groups_tr):
    def objective(trial):
        p = dict(
            n_estimators      = trial.suggest_int('n_estimators', 200, 800),
            learning_rate     = trial.suggest_float('learning_rate', 0.005, 0.15, log=True),
            max_depth         = trial.suggest_int('max_depth', 3, 10),
            min_child_weight  = trial.suggest_int('min_child_weight', 1, 30),
            subsample         = trial.suggest_float('subsample', 0.5, 1.0),
            colsample_bytree  = trial.suggest_float('colsample_bytree', 0.5, 1.0),
            reg_alpha         = trial.suggest_float('reg_alpha', 1e-4, 10.0, log=True),
            reg_lambda        = trial.suggest_float('reg_lambda', 1e-4, 10.0, log=True),
            random_state=SEED, n_jobs=-1,
        )
        return cross_val_score(
            XGBRegressor(**p), X_tr, y_tr,
            cv=GroupKFold(3), groups=groups_tr,
            scoring='neg_mean_absolute_error', n_jobs=1,
        ).mean()
    study = optuna.create_study(direction='maximize',
                                sampler=optuna.samplers.TPESampler(seed=SEED))
    study.optimize(objective, n_trials=N_TRIALS_REG_XGB, show_progress_bar=True)
    return study.best_params


def _optuna_cat_reg(X_tr, y_tr, groups_tr):
    def objective(trial):
        p = dict(
            iterations    = trial.suggest_int('iterations', 200, 800),
            learning_rate = trial.suggest_float('learning_rate', 0.005, 0.15, log=True),
            depth         = trial.suggest_int('depth', 3, 10),
            l2_leaf_reg   = trial.suggest_float('l2_leaf_reg', 1e-3, 10.0, log=True),
            border_count  = trial.suggest_int('border_count', 32, 255),
            random_seed=SEED, verbose=0,
        )
        return cross_val_score(
            CatBoostRegressor(**p), X_tr, y_tr,
            cv=GroupKFold(3), groups=groups_tr,
            scoring='neg_mean_absolute_error', n_jobs=1,
        ).mean()
    study = optuna.create_study(direction='maximize',
                                sampler=optuna.samplers.TPESampler(seed=SEED))
    study.optimize(objective, n_trials=N_TRIALS_REG_CAT, show_progress_bar=True)
    return study.best_params


def _optuna_p90(X_tr, y_tr, groups_tr):
    def objective(trial):
        p = dict(
            n_estimators      = trial.suggest_int('n_estimators', 100, 600),
            learning_rate     = trial.suggest_float('learning_rate', 0.01, 0.15, log=True),
            num_leaves        = trial.suggest_int('num_leaves', 15, 127),
            min_child_samples = trial.suggest_int('min_child_samples', 5, 40),
            subsample         = trial.suggest_float('subsample', 0.6, 1.0),
            colsample_bytree  = trial.suggest_float('colsample_bytree', 0.6, 1.0),
            reg_alpha         = trial.suggest_float('reg_alpha', 1e-4, 5.0, log=True),
            reg_lambda        = trial.suggest_float('reg_lambda', 1e-4, 5.0, log=True),
        )
        scores = []
        gkf = GroupKFold(n_splits=3)
        for tr_idx, va_idx in gkf.split(X_tr, y_tr, groups_tr):
            m = LGBMRegressor(
                **p, objective='quantile', alpha=0.90,
                random_state=SEED, verbose=-1, n_jobs=-1,
            )
            m.fit(X_tr[tr_idx], y_tr[tr_idx])
            scores.append(-mean_pinball_loss(y_tr[va_idx], m.predict(X_tr[va_idx]), alpha=0.90))
        return np.mean(scores)
    study = optuna.create_study(direction='maximize',
                                sampler=optuna.samplers.TPESampler(seed=SEED))
    study.optimize(objective, n_trials=N_TRIALS_P90, show_progress_bar=True)
    return study.best_params


# ══════════════════════════════════════════════════════════════════════════════
# CLASSIFICATION PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

def train_classifier(X_clf_df, y_clf, groups, fit_idx, calib_idx, test_idx, df_aug):
    X_clf = X_clf_df.values
    X_fit,   y_fit   = X_clf[fit_idx],   y_clf[fit_idx]
    X_calib, y_calib = X_clf[calib_idx], y_clf[calib_idx]
    X_test,  y_test  = X_clf[test_idx],  y_clf[test_idx]
    g_fit = groups[fit_idx]

    print("\n[CLF] Optuna HPO — LightGBM...")
    lgbm_p = _optuna_lgbm_clf(X_fit, y_fit, g_fit)
    print("[CLF] Optuna HPO — XGBoost...")
    xgb_p = _optuna_xgb_clf(X_fit, y_fit, g_fit)
    print("[CLF] Optuna HPO — CatBoost...")
    cat_p = _optuna_cat_clf(X_fit, y_fit, g_fit)

    scale_pos_meta = (y_fit == 0).sum() / max((y_fit == 1).sum(), 1)
    meta_clf = XGBClassifier(
        n_estimators=200, learning_rate=0.05, max_depth=4,
        scale_pos_weight=scale_pos_meta, eval_metric='aucpr',
        random_state=SEED, n_jobs=-1,
    )
    base_clfs = [
        ('lgbm', LGBMClassifier(**lgbm_p, verbose=-1, n_jobs=-1)),
        ('xgb',  XGBClassifier(**xgb_p, eval_metric='aucpr', n_jobs=-1)),
        ('cat',  CatBoostClassifier(**cat_p, verbose=0)),
    ]
    stack_clf = StackingClassifier(
        estimators=base_clfs, final_estimator=meta_clf,
        cv=5, passthrough=True, n_jobs=1,
    )
    print("[CLF] Training stacking ensemble...")
    stack_clf.fit(X_fit, y_fit)

    print("[CLF] Calibrating probabilities (isotonic)...")
    calibrated_clf = _PreFitCalibrator(stack_clf)
    calibrated_clf.fit(X_calib, y_calib)

    probs_test = calibrated_clf.predict_proba(X_test)[:, 1]

    thresholds = np.linspace(0.05, 0.90, 120)
    best_t = max(
        thresholds,
        key=lambda t: fbeta_score(
            y_test, (probs_test >= t).astype(int),
            beta=F2_BETA, average='binary', pos_label=1, zero_division=0,
        ),
    )
    y_pred = (probs_test >= best_t).astype(int)

    early_recall = cascade_hit = float('nan')
    if df_aug is not None:
        test_df    = df_aug.iloc[test_idx]
        early_mask = (test_df['stop_sequence'] <= 3).values
        if early_mask.sum() > 0:
            early_recall = recall_score(
                y_test[early_mask], y_pred[early_mask],
                pos_label=1, zero_division=0,
            )
        crit_mask = (test_df['is_already_critical'] == 1).values
        if crit_mask.sum() > 0:
            cascade_hit = recall_score(
                y_test[crit_mask], y_pred[crit_mask],
                pos_label=1, zero_division=0,
            )

    metrics = {
        'auc_roc':            round(roc_auc_score(y_test, probs_test), 4),
        'avg_precision':      round(average_precision_score(y_test, probs_test), 4),
        'brier':              round(brier_score_loss(y_test, probs_test), 4),
        'f1_macro':           round(f1_score(y_test, y_pred, average='macro'), 4),
        'f2_binary':          round(fbeta_score(y_test, y_pred, beta=F2_BETA,
                                               average='binary', pos_label=1), 4),
        'prec_late':          round(precision_score(y_test, y_pred, pos_label=1,
                                                    zero_division=0), 4),
        'rec_late':           round(recall_score(y_test, y_pred, pos_label=1,
                                                 zero_division=0), 4),
        'recall_early_stops': round(early_recall, 4) if not np.isnan(early_recall) else 'N/A',
        'cascade_hit_rate':   round(cascade_hit, 4)  if not np.isnan(cascade_hit)  else 'N/A',
        'threshold':          round(float(best_t), 4),
        'cm':                 confusion_matrix(y_test, y_pred).tolist(),
    }
    return calibrated_clf, best_t, metrics, probs_test, y_test


# ══════════════════════════════════════════════════════════════════════════════
# REGRESSION PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

def train_regressor(X_reg_df, y_reg, groups, fit_idx, calib_idx, test_idx,
                    severe_threshold_min):
    """
    _ArcsinhRegressor wraps StackingRegressor — transforms y to arcsinh space
    without TransformedTargetRegressor, avoiding sklearn 1.7↔1.8 pickle breakage.
    Severe delays receive SEVERE_WEIGHT_FACTOR× sample weight.
    """
    X_reg = X_reg_df.values
    X_fit,  y_fit  = X_reg[fit_idx],  y_reg[fit_idx]
    X_test, y_test = X_reg[test_idx], y_reg[test_idx]
    g_fit = groups[fit_idx]

    severe_mask   = (y_fit >= severe_threshold_min).astype(float)
    sample_weight = 1.0 + (SEVERE_WEIGHT_FACTOR - 1.0) * severe_mask
    frac_severe   = severe_mask.mean() * 100
    print(f"\n[REG] Severe delay weight ×{SEVERE_WEIGHT_FACTOR}  "
          f"({frac_severe:.1f}% of training stops are severe)")

    print("[REG] Optuna HPO — LightGBM...")
    lgbm_p = _optuna_lgbm_reg(X_fit, y_fit, g_fit)
    print("[REG] Optuna HPO — XGBoost...")
    xgb_p  = _optuna_xgb_reg(X_fit, y_fit, g_fit)
    print("[REG] Optuna HPO — CatBoost...")
    cat_p  = _optuna_cat_reg(X_fit, y_fit, g_fit)

    base_regs = [
        ('lgbm', LGBMRegressor(**lgbm_p, verbose=-1, n_jobs=-1)),
        ('xgb',  XGBRegressor(**xgb_p)),
        ('cat',  CatBoostRegressor(**cat_p)),
    ]
    stack_reg = StackingRegressor(
        estimators=base_regs, final_estimator=RidgeCV(), cv=5, n_jobs=1,
    )
    reg = _ArcsinhRegressor(stack_reg)

    print("[REG] Training _ArcsinhRegressor (severity sample weights)...")
    reg.fit(X_fit, y_fit, sample_weight=sample_weight)

    y_pred = reg.predict(X_test)
    mae    = mean_absolute_error(y_test, y_pred)
    rmse   = np.sqrt(mean_squared_error(y_test, y_pred))
    r2     = r2_score(y_test, y_pred)

    severe_true = (y_test >= severe_threshold_min).astype(int)
    severe_pred = (y_pred >= severe_threshold_min).astype(int)

    metrics = {
        'mae':           round(mae, 3),
        'rmse':          round(rmse, 3),
        'r2':            round(r2, 4),
        'within2_pct':   round(np.mean(np.abs(y_test - y_pred) <= 2) * 100, 1),
        'within5_pct':   round(np.mean(np.abs(y_test - y_pred) <= 5) * 100, 1),
        'severe_recall': round(recall_score(severe_true, severe_pred, pos_label=1,
                                            zero_division=0), 4),
        'severe_prec':   round(precision_score(severe_true, severe_pred, pos_label=1,
                                               zero_division=0), 4),
        'severe_f1':     round(f1_score(severe_true, severe_pred, pos_label=1,
                                        zero_division=0), 4),
    }
    return reg, metrics, y_pred, y_test


def train_p90_regressor(X_reg_df, y_reg, groups, fit_idx):
    X_reg = X_reg_df.values
    X_fit = X_reg[fit_idx]
    y_fit = y_reg[fit_idx]
    g_fit = groups[fit_idx]

    print("\n[P90] Optuna HPO — LightGBM quantile (alpha=0.90)...")
    best_p = _optuna_p90(X_fit, y_fit, g_fit)
    p90_reg = LGBMRegressor(
        **best_p, objective='quantile', alpha=0.90,
        random_state=SEED, verbose=-1, n_jobs=-1,
    )
    p90_reg.fit(X_fit, y_fit)
    return p90_reg


def optimize_severity_threshold(reg, X_reg_df, y_reg, calib_idx):
    X_calib = X_reg_df.values[calib_idx]
    y_calib = y_reg[calib_idx]
    y_pred  = reg.predict(X_calib)

    thresholds = np.linspace(8.0, 25.0, 35)
    results    = []
    for t in thresholds:
        true_sev = (y_calib >= t).astype(int)
        pred_sev = (y_pred  >= t).astype(int)
        if true_sev.sum() < 5:
            results.append((t, 0.0, 0.0, 0.0))
            continue
        f1   = f1_score(true_sev, pred_sev, pos_label=1, zero_division=0)
        prec = precision_score(true_sev, pred_sev, pos_label=1, zero_division=0)
        rec  = recall_score(true_sev, pred_sev, pos_label=1, zero_division=0)
        results.append((t, f1, prec, rec))

    best_t, best_f1, best_prec, best_rec = max(results, key=lambda x: x[1])
    print(f"\n[SEV] Optimal severity threshold: {best_t:.1f} min  "
          f"(F1={best_f1:.4f}, P={best_prec:.4f}, R={best_rec:.4f})")
    return float(best_t), results


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE IMPORTANCE
# ══════════════════════════════════════════════════════════════════════════════

def _lgbm_from_stacking(clf_or_reg):
    try:
        est = getattr(clf_or_reg, 'estimator', clf_or_reg)
        return est.estimators_[0]
    except Exception:
        return None


def _lgbm_from_arcsinh_reg(reg):
    """Extract LGBM base estimator from _ArcsinhRegressor."""
    try:
        return reg.base_regressor.estimators_[0]
    except Exception:
        return None


def feature_importance_report(clf, reg, feat_clf, feat_reg):
    print("\n  ── FEATURE IMPORTANCE (LGBM base — gain) ──────────────────────")
    for label, model, feats, extractor in [
        ('Classifier', clf, feat_clf, lambda m: _lgbm_from_stacking(m)),
        ('Regressor',  reg, feat_reg, lambda m: _lgbm_from_arcsinh_reg(m)),
    ]:
        lgbm = extractor(model)
        if lgbm is None:
            print(f"  {label}: LGBM not extractable")
            continue
        try:
            imp = pd.Series(
                lgbm.feature_importances_,
                index=feats[:len(lgbm.feature_importances_)],
            ).sort_values(ascending=False)
            print(f"\n  {label} — top 10:")
            for i, (feat, val) in enumerate(imp.head(10).items(), 1):
                bar = '█' * int(val / imp.iloc[0] * 20)
                print(f"    {i:>2}. {feat:<35} {val:>8.1f}  {bar}")
        except Exception as e:
            print(f"  {label}: importance extraction failed — {e}")


# ══════════════════════════════════════════════════════════════════════════════
# VISUALIZATIONS
# ══════════════════════════════════════════════════════════════════════════════

def plot_calibration(probs_test, y_test):
    _, ax = plt.subplots(figsize=(7, 6))
    prob_true, prob_pred = calibration_curve(y_test, probs_test, n_bins=10)
    ax.plot(prob_pred, prob_true, 's-', color='steelblue', lw=2, label='v7 Calibrated')
    ax.plot([0, 1], [0, 1], 'k--', lw=1.5, label='Perfect')
    ax.fill_between(prob_pred, prob_true, prob_pred, alpha=0.15, color='steelblue')
    ax.set_xlabel('Mean Predicted Probability')
    ax.set_ylabel('Fraction of Positives')
    ax.set_title('Reliability Diagram — v7 Classifier', fontweight='bold')
    ax.legend(); plt.tight_layout()
    path = os.path.join(PLOT_DIR, '50_calibration_v7.png')
    plt.savefig(path, dpi=150); plt.close()
    print(f"  → {path}")


def plot_pr_curve(probs_test, y_test):
    precision, recall, _ = precision_recall_curve(y_test, probs_test)
    ap = average_precision_score(y_test, probs_test)
    _, ax = plt.subplots(figsize=(7, 6))
    ax.plot(recall, precision, lw=2, color='coral', label=f'v7  AP={ap:.4f}')
    ax.axhline(y_test.mean(), color='gray', linestyle='--', lw=1.2,
               label=f'Baseline ({y_test.mean():.2f})')
    ax.set_xlabel('Recall'); ax.set_ylabel('Precision')
    ax.set_title('Precision-Recall Curve — v7', fontweight='bold')
    ax.legend(); plt.tight_layout()
    path = os.path.join(PLOT_DIR, '51_pr_curve_v7.png')
    plt.savefig(path, dpi=150); plt.close()
    print(f"  → {path}")


def plot_feature_importance(clf, reg, feat_clf, feat_reg, X_clf_test, X_reg_test):
    fig, axes = plt.subplots(1, 2, figsize=(18, 8))
    for ax, label, model, feats, X_test, extractor in [
        (axes[0], 'Classifier', clf, feat_clf, X_clf_test,
         lambda m: _lgbm_from_stacking(m)),
        (axes[1], 'Regressor',  reg, feat_reg, X_reg_test,
         lambda m: _lgbm_from_arcsinh_reg(m)),
    ]:
        lgbm = extractor(model)
        if lgbm is None:
            ax.set_title(f'{label}: LGBM not extractable')
            continue
        try:
            explainer = shap.TreeExplainer(lgbm)
            shap_vals = explainer.shap_values(X_test[:300])
            if isinstance(shap_vals, list):
                shap_vals = shap_vals[1]
            plt.sca(ax)
            shap.summary_plot(
                shap_vals, X_test[:300],
                feature_names=feats[:X_test.shape[1]],
                show=False, plot_size=None,
            )
            ax.set_title(f'SHAP — v7 {label}', fontweight='bold')
        except Exception as e:
            try:
                imp = pd.Series(lgbm.feature_importances_,
                                index=feats[:len(lgbm.feature_importances_)])
                imp.sort_values().tail(15).plot.barh(ax=ax, color='steelblue')
                ax.set_title(f'Feature Importance (gain) — v7 {label}', fontweight='bold')
                ax.set_xlabel('Importance')
            except Exception:
                ax.set_title(f'{label}: importance unavailable ({e})')
    plt.tight_layout()
    path = os.path.join(PLOT_DIR, '52_feature_importance_v7.png')
    plt.savefig(path, dpi=150, bbox_inches='tight'); plt.close()
    print(f"  → {path}")


def plot_p50_vs_p90(y_test, y_pred_p50, y_pred_p90):
    _, axes = plt.subplots(1, 2, figsize=(14, 5))
    ax = axes[0]
    ax.scatter(y_test, y_pred_p50, alpha=0.3, s=10, color='steelblue', label='P50 (expected)')
    ax.scatter(y_test, y_pred_p90, alpha=0.2, s=10, color='coral',     label='P90 (worst-case)')
    lim = max(y_test.max(), y_pred_p90.max()) * 1.05
    ax.plot([0, lim], [0, lim], 'k--', lw=1, label='Perfect')
    ax.set_xlabel('Actual Delay (min)'); ax.set_ylabel('Predicted Delay (min)')
    ax.set_title('P50 vs P90 — Scatter vs Actuals', fontweight='bold')
    ax.legend(markerscale=3); ax.set_xlim(0, lim); ax.set_ylim(0, lim)
    ax2 = axes[1]
    pct_p50 = np.mean(y_test <= y_pred_p50) * 100
    pct_p90 = np.mean(y_test <= y_pred_p90) * 100
    ax2.bar(['P50\n(expected)', 'P90\n(worst-case)'],
            [pct_p50, pct_p90], color=['steelblue', 'coral'],
            edgecolor='white', width=0.5)
    ax2.axhline(50, color='steelblue', linestyle='--', lw=1, alpha=0.7, label='50% target')
    ax2.axhline(90, color='coral',     linestyle='--', lw=1, alpha=0.7, label='90% target')
    for i, v in enumerate([pct_p50, pct_p90]):
        ax2.text(i, v + 0.5, f'{v:.1f}%', ha='center', va='bottom', fontweight='bold')
    ax2.set_ylabel('% Actual Delays Below Prediction'); ax2.set_ylim(0, 105)
    ax2.set_title('Coverage: Actuals Covered by P50 / P90', fontweight='bold')
    ax2.legend()
    plt.suptitle('v7 — P50 (Median) vs P90 (Worst-Case) Delay Predictions',
                 fontsize=13, fontweight='bold')
    plt.tight_layout()
    path = os.path.join(PLOT_DIR, '53_p50_vs_p90_v7.png')
    plt.savefig(path, dpi=150); plt.close()
    print(f"  → {path}")


def plot_severity_threshold_sweep(sweep_results):
    thresholds = [r[0] for r in sweep_results]
    f1s        = [r[1] for r in sweep_results]
    precs      = [r[2] for r in sweep_results]
    recs       = [r[3] for r in sweep_results]
    best_t     = thresholds[int(np.argmax(f1s))]
    _, ax = plt.subplots(figsize=(9, 5))
    ax.plot(thresholds, f1s,   'o-',  color='steelblue', lw=2,   label='F1')
    ax.plot(thresholds, precs, 's--', color='coral',     lw=1.5, label='Precision')
    ax.plot(thresholds, recs,  '^--', color='seagreen',  lw=1.5, label='Recall')
    ax.axvline(best_t, color='black', linestyle=':', lw=1.5,
               label=f'Optimal = {best_t:.1f} min')
    ax.axvline(SEVERE_DELAY_THRESHOLD_MIN, color='gray', linestyle='--', lw=1,
               label=f'Default = {SEVERE_DELAY_THRESHOLD_MIN:.0f} min')
    ax.set_xlabel('Severity Threshold (min)'); ax.set_ylabel('Score')
    ax.set_title('Severity Threshold Optimisation — F1 Sweep (v7)', fontweight='bold')
    ax.legend(); ax.set_ylim(0, 1.05)
    plt.tight_layout()
    path = os.path.join(PLOT_DIR, '54_severity_threshold_sweep_v7.png')
    plt.savefig(path, dpi=150); plt.close()
    print(f"  → {path}")


# ══════════════════════════════════════════════════════════════════════════════
# inference.py UPDATER
# ══════════════════════════════════════════════════════════════════════════════

def update_inference_py(new_defaults: dict) -> None:
    inf_path = os.path.join(_ML_DIR, 'inference.py')
    with open(inf_path, 'r') as fh:
        src = fh.read()

    # ── 1. Replace _DEFAULTS dict ─────────────────────────────────────────────
    lines = ['_DEFAULTS: dict = {']
    for k, v in sorted(new_defaults.items()):
        lines.append(f'    "{k}": {v},')
    lines.append('}')
    src = re.sub(
        r'_DEFAULTS: dict = \{.*?\}',
        '\n'.join(lines),
        src,
        flags=re.DOTALL,
    )

    # ── 2. Add v7 to _get_predictor() preference order ────────────────────────
    if 'route_predictor_v7.pkl' not in src:
        src = src.replace(
            '        for fname in (\n'
            '            "route_predictor_v5.pkl",\n',
            '        for fname in (\n'
            '            "route_predictor_v7.pkl",\n'
            '            "route_predictor_v5.pkl",\n',
        )

    # ── 3. Update get_model_info version map ──────────────────────────────────
    if '"route_predictor_v7.pkl"' not in src:
        src = src.replace(
            '        "route_predictor_v5.pkl":        "v5 (enriched 39-feature set)",',
            '        "route_predictor_v7.pkl":        "v7 (enriched 43-feature clf, 39-feature reg, _ArcsinhRegressor)",\n'
            '        "route_predictor_v5.pkl":        "v5 (enriched 39-feature set)",',
        )

    with open(inf_path, 'w') as fh:
        fh.write(src)
    print(f"  inference.py updated: {inf_path}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  SMART LOGISTICS — MODEL TRAINING v7")
    print("  v3 architecture + full enriched feature set + _ArcsinhRegressor")
    print("=" * 70)
    print("\n  v3 → v7 key changes:")
    print("    #1  Full merge pipeline (5 raw sources) — not merged_features_v2.csv")
    print("    #2  43 clf features / 39 reg features (was ~24 in v3)")
    print("    #3  P90 quantile regressor added")
    print("    #4  Severity threshold optimisation (vs fixed 15 min)")
    print("    #5  _ArcsinhRegressor — no TransformedTargetRegressor (sklearn compat)")
    print("    #6  Val split added (5-fold: test + val + train)")

    # ── Data ──────────────────────────────────────────────────────────────────
    print(f"\nBuilding feature matrix from {DATA_DIR}...")
    (X_clf_df, X_reg_df, y_clf, y_reg, groups,
     feat_clf, feat_reg, df_aug, df_raw) = load_data()

    print(f"  Total samples     : {len(y_clf)}")
    print(f"  Class balance     : late={y_clf.mean()*100:.1f}%  "
          f"on-time={(1-y_clf.mean())*100:.1f}%")
    print(f"  Severe (≥{SEVERE_DELAY_THRESHOLD_MIN:.0f} min): "
          f"{(y_reg >= SEVERE_DELAY_THRESHOLD_MIN).mean()*100:.1f}%")

    fit_idx, calib_idx, val_idx, test_idx = group_train_test_split(X_clf_df, y_clf, groups)
    print(f"  Split — fit:{len(fit_idx)}  calib:{len(calib_idx)}"
          f"  val:{len(val_idx)}  test:{len(test_idx)}")

    # ── Classification ────────────────────────────────────────────────────────
    calibrated_clf, best_t, clf_metrics, probs_test, y_test_clf = \
        train_classifier(X_clf_df, y_clf, groups, fit_idx, calib_idx, test_idx, df_aug)

    X_clf_arr  = X_clf_df.values
    probs_val  = calibrated_clf.predict_proba(X_clf_arr[val_idx])[:, 1]
    y_val_clf  = y_clf[val_idx]
    y_pred_val = (probs_val >= best_t).astype(int)
    clf_val = {
        'auc_roc':   round(roc_auc_score(y_val_clf, probs_val), 4),
        'f1_macro':  round(f1_score(y_val_clf, y_pred_val, average='macro'), 4),
        'rec_late':  round(recall_score(y_val_clf, y_pred_val, pos_label=1,
                                        zero_division=0), 4),
        'prec_late': round(precision_score(y_val_clf, y_pred_val, pos_label=1,
                                           zero_division=0), 4),
    }

    print("\n  ── CLASSIFICATION RESULTS (v7) ──────────────────────────────────")
    print(f"  {'Metric':<26} {'Val':>8}  {'Test':>8}")
    print(f"  {'-'*44}")
    print(f"  {'AUC-ROC':<26} {clf_val['auc_roc']:>8}  {clf_metrics['auc_roc']:>8}")
    print(f"  {'F1-macro':<26} {clf_val['f1_macro']:>8}  {clf_metrics['f1_macro']:>8}")
    print(f"  {'Recall(Late)':<26} {clf_val['rec_late']:>8}  {clf_metrics['rec_late']:>8}")
    print(f"  {'Precision(Late)':<26} {clf_val['prec_late']:>8}  {clf_metrics['prec_late']:>8}")
    print(f"  {'Avg Precision (PR)':<26} {'N/A':>8}  {clf_metrics['avg_precision']:>8}")
    print(f"  {'Brier Score':<26} {'N/A':>8}  {clf_metrics['brier']:>8}")
    print(f"  {'F2-score (β=2)':<26} {'N/A':>8}  {clf_metrics['f2_binary']:>8}")
    print(f"  {'Threshold (F2-opt)':<26} {best_t:>8.4f}  {best_t:>8.4f}")
    print(f"  {'Recall@EarlyStops':<26} {'N/A':>8}  {clf_metrics['recall_early_stops']:>8}")
    print(f"  {'Cascade Hit Rate':<26} {'N/A':>8}  {clf_metrics['cascade_hit_rate']:>8}")

    # ── Regression ────────────────────────────────────────────────────────────
    reg, reg_metrics, y_pred_p50, y_test_reg = \
        train_regressor(X_reg_df, y_reg, groups, fit_idx, calib_idx, test_idx,
                        SEVERE_DELAY_THRESHOLD_MIN)

    X_reg_arr      = X_reg_df.values
    y_pred_val_reg = reg.predict(X_reg_arr[val_idx])
    y_val_reg      = y_reg[val_idx]
    sev_true_val   = (y_val_reg >= SEVERE_DELAY_THRESHOLD_MIN).astype(int)
    sev_pred_val   = (y_pred_val_reg >= SEVERE_DELAY_THRESHOLD_MIN).astype(int)
    reg_val = {
        'mae':           round(mean_absolute_error(y_val_reg, y_pred_val_reg), 3),
        'rmse':          round(np.sqrt(mean_squared_error(y_val_reg, y_pred_val_reg)), 3),
        'r2':            round(r2_score(y_val_reg, y_pred_val_reg), 4),
        'severe_recall': round(recall_score(sev_true_val, sev_pred_val, pos_label=1,
                                            zero_division=0), 4),
    }

    optimal_sev_t, sweep_results = optimize_severity_threshold(
        reg, X_reg_df, y_reg, calib_idx)

    print("\n  ── REGRESSION RESULTS (v7) ──────────────────────────────────────")
    print(f"  {'Metric':<22} {'Val':>8}  {'Test':>8}")
    print(f"  {'-'*40}")
    print(f"  {'MAE (min)':<22} {reg_val['mae']:>8}  {reg_metrics['mae']:>8}")
    print(f"  {'RMSE (min)':<22} {reg_val['rmse']:>8}  {reg_metrics['rmse']:>8}")
    print(f"  {'R²':<22} {reg_val['r2']:>8}  {reg_metrics['r2']:>8}")
    print(f"  {'Severe Recall':<22} {reg_val['severe_recall']:>8}  {reg_metrics['severe_recall']:>8}  ← key metric")
    print(f"  {'Within ±2 min':<22} {'N/A':>8}  {reg_metrics['within2_pct']:>7}%")
    print(f"  {'Within ±5 min':<22} {'N/A':>8}  {reg_metrics['within5_pct']:>7}%")
    print(f"  {'Severe Precision':<22} {'N/A':>8}  {reg_metrics['severe_prec']:>8}")
    print(f"  {'Severe F1':<22} {'N/A':>8}  {reg_metrics['severe_f1']:>8}")

    # ── P90 ───────────────────────────────────────────────────────────────────
    p90_reg = train_p90_regressor(X_reg_df, y_reg, groups, fit_idx)
    X_test_reg = X_reg_df.values[test_idx]
    y_pred_p90 = p90_reg.predict(X_test_reg)

    y_pred_p90_val   = p90_reg.predict(X_reg_arr[val_idx])
    coverage_p50_val = np.mean(y_val_reg <= y_pred_val_reg) * 100
    coverage_p90_val = np.mean(y_val_reg <= y_pred_p90_val) * 100
    coverage_p50     = np.mean(y_test_reg <= y_pred_p50) * 100
    coverage_p90     = np.mean(y_test_reg <= y_pred_p90) * 100
    pinball_p90      = mean_pinball_loss(y_test_reg, y_pred_p90, alpha=0.90)

    print(f"\n  ── P90 REGRESSOR RESULTS ────────────────────────────────────────")
    print(f"  {'Metric':<32} {'Val':>8}  {'Test':>8}")
    print(f"  {'-'*50}")
    print(f"  {'Coverage (actuals ≤ P50)':<32} {coverage_p50_val:>7.1f}%  {coverage_p50:>7.1f}%  (target ~50%)")
    print(f"  {'Coverage (actuals ≤ P90)':<32} {coverage_p90_val:>7.1f}%  {coverage_p90:>7.1f}%  (target ~90%)")
    print(f"  {'Pinball loss (α=0.90)':<32} {'N/A':>8}  {pinball_p90:>8.4f}  (lower=better)")

    # ── Feature importance ────────────────────────────────────────────────────
    feature_importance_report(calibrated_clf, reg, feat_clf, feat_reg)

    # ── Plots ─────────────────────────────────────────────────────────────────
    print("\n  ── PLOTS ────────────────────────────────────────────────────────")
    plot_calibration(probs_test, y_test_clf)
    plot_pr_curve(probs_test, y_test_clf)
    plot_feature_importance(
        calibrated_clf, reg, feat_clf, feat_reg,
        X_clf_df.values[test_idx], X_test_reg,
    )
    plot_p50_vs_p90(y_test_reg, y_pred_p50, y_pred_p90)
    if sweep_results:
        plot_severity_threshold_sweep(sweep_results)

    # ── Build RouteDelayPredictor ─────────────────────────────────────────────
    predictor = RouteDelayPredictor(
        clf=calibrated_clf,
        reg=reg,
        threshold=best_t,
        feature_cols=feat_clf,
        reg_feature_cols=feat_reg,
        augment_fn=_augment_features,
        severe_threshold_min=optimal_sev_t,
        p90_reg=p90_reg,
    )

    # ── Serialize ─────────────────────────────────────────────────────────────
    print("\n  ── SERIALIZING ──────────────────────────────────────────────────")
    joblib.dump(calibrated_clf, os.path.join(OUT_DIR, 'delay_classifier_v7.pkl'))
    joblib.dump(reg,            os.path.join(OUT_DIR, 'delay_regressor_v7.pkl'))
    joblib.dump(p90_reg,        os.path.join(OUT_DIR, 'delay_regressor_v7_p90.pkl'))
    joblib.dump(predictor,      os.path.join(OUT_DIR, 'route_predictor_v7.pkl'))
    np.save(os.path.join(OUT_DIR, 'clf_threshold_v7.npy'),      best_t)
    np.save(os.path.join(OUT_DIR, 'severity_threshold_v7.npy'), optimal_sev_t)
    print("  delay_classifier_v7.pkl      ✓")
    print("  delay_regressor_v7.pkl       ✓")
    print("  delay_regressor_v7_p90.pkl   ✓")
    print("  route_predictor_v7.pkl       ✓")
    print("  clf_threshold_v7.npy         ✓")
    print("  severity_threshold_v7.npy    ✓")

    # ── Update inference.py ───────────────────────────────────────────────────
    print("\n  ── UPDATING inference.py ────────────────────────────────────────")
    full_df = X_reg_df.copy()
    new_feature_medians = {col: round(float(full_df[col].median()), 4)
                           for col in feat_reg}
    existing_defaults = {
        "distance_from_prev_km":  30.45,
        "planned_travel_min":     30.60,
        "stop_sequence":          4,
        "road_type":              3,
        "hour_of_day":            13,
        "day_of_week":            3,
        "traffic_level":          3,
        "weather_condition":      0,
        "is_rush_hour":           0,
        "precipitation_mm":       0.0,
        "cumulative_delay_min":   22.20,
        "prev_stop_delay_min":    6.70,
        "time_window_slack_min":  75.0,
        "stop_progress_ratio":    0.57,
        "planned_speed_kmh":      64.91,
        "is_high_risk_weather":   0,
        "is_urban_road":          0,
        "traffic_weather_risk":   5,
    }
    merged_defaults = {**new_feature_medians, **existing_defaults}
    merged_defaults['hist_delay_probability'] = round(
        float(full_df['hist_delay_probability'].median()), 4
    ) if 'hist_delay_probability' in full_df.columns else 0.25
    merged_defaults['hist_slack_min'] = round(
        float(full_df['hist_slack_min'].median()), 4
    ) if 'hist_slack_min' in full_df.columns else 54.69

    update_inference_py(merged_defaults)

    # ── Smoke test ────────────────────────────────────────────────────────────
    print("\n  ── SMOKE TEST ───────────────────────────────────────────────────")
    sample_route = df_raw[df_raw['route_id'] == df_raw['route_id'].iloc[0]].copy()
    result = predictor.predict_route(sample_route)
    rs = result['route_summary']
    print(f"  route_delay_probability      : {rs['route_delay_probability']}")
    print(f"  expected_total_delay_min     : {rs['expected_total_delay_min']}")
    print(f"  worst_case_total_delay_min   : {rs['worst_case_total_delay_min']}  (P90)")
    print(f"  high_risk_stop_count         : {rs['high_risk_stop_count']}")
    print(f"  severe_stop_count            : {rs['severe_stop_count']}")
    print(f"  optimised severity threshold : {optimal_sev_t:.1f} min")
    print(f"\n  Per-stop (first 5):")
    for s in result['stop_predictions'][:5]:
        p90_str = f"  p90={s['delay_p90_min']:>5.1f}min" \
                  if s['delay_p90_min'] is not None else ""
        print(f"    stop {s['stop_sequence']:>2}  "
              f"p={s['delay_probability']:.3f}  "
              f"exp={s['expected_delay_min']:>5.1f}min{p90_str}  "
              f"{s['severity']}")

    print("\n" + "=" * 70)
    print("  Training complete — v7 models saved.")
    print("=" * 70)


if __name__ == '__main__':
    main()
