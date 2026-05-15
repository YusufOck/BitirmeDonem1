"""
model_classes.py
================
Shared class and function definitions for Smart Logistics ML models.

Keeping these in a separate importable module ensures that joblib-pickled
objects remain loadable from any script (backend service, notebooks, tests)
without having to run as the training script itself.

Used by: train_model_v3.py, train_model_v4.py, backend services, inference scripts.

v4 additions to RouteDelayPredictor:
  - reg_feature_cols : separate feature subset for regressor (hybrid / v4 mode)
  - p90_reg          : quantile P90 regressor — adds delay_p90_min to every prediction
"""

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

SEVERE_DELAY_THRESHOLD_MIN = 15.0


def _clip01(value):
    try:
        if pd.isna(value):
            return 0.0
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _num(value, default=0.0) -> float:
    try:
        if pd.isna(value):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _condition_floor(row) -> tuple[float, float, list[str]]:
    """Return a conservative delay/probability floor from runtime road signals.

    The trained regressor can be over-confident on out-of-distribution scenario
    inputs, especially synthetic combinations such as "snow + accident +
    gridlock". This floor is not a replacement model; it is a deterministic
    safety calibration based only on features that are available at inference
    time and are also used by the optimizer.
    """
    planned = max(_num(row.get("planned_travel_min", 0.0), 0.0), 0.0)
    if planned <= 0:
        planned = 5.0

    traffic_value = row.get("traffic_level", 3)
    if isinstance(traffic_value, str):
        traffic_code = {"heavy": 0, "congested": 0, "moderate": 1, "normal": 2, "low": 3}.get(
            traffic_value.strip().lower(),
            3,
        )
    else:
        traffic_code = int(_num(traffic_value, 3))

    weather_value = row.get("weather_condition", 0)
    if isinstance(weather_value, str):
        weather_code = {"clear": 0, "cloudy": 1, "fog": 2, "rain": 3, "snow": 4, "wind": 5}.get(
            weather_value.strip().lower(),
            0,
        )
    else:
        weather_code = int(_num(weather_value, 0))

    traffic_pressure = {0: 1.0, 1: 0.55, 2: 0.0, 3: 0.0}.get(traffic_code, 0.0)
    congestion_ratio = row.get("congestion_ratio_mean")
    if congestion_ratio is not None and not pd.isna(congestion_ratio):
        traffic_pressure = max(
            traffic_pressure,
            _clip01((0.75 - _num(congestion_ratio, 0.75)) / 0.75),
        )

    weather_pressure = {0: 0.0, 1: 0.0, 2: 0.55, 3: 0.35, 4: 0.85, 5: 0.45}.get(weather_code, 0.0)
    wind_speed = _num(row.get("wind_speed_kmh", 0.0), 0.0)
    if wind_speed >= 45.0:
        weather_pressure = max(weather_pressure, 0.50)
    incident_pressure = max(
        _clip01(row.get("road_incident", 0)),
        _clip01(row.get("incident_severity", 0.0)),
        _clip01(_num(row.get("incident_rate", 0.0), 0.0) * 1.8),
    )
    delay_factor_pressure = _clip01((_num(row.get("overall_delay_factor", 1.0), 1.0) - 1.0) / 2.0)
    slack = _num(row.get("time_window_slack_min", 480.0), 480.0)
    slack_pressure = _clip01((30.0 - slack) / 30.0)
    load_pressure = _clip01(_num(row.get("vehicle_load_ratio", 0.0), 0.0) / 1.2)

    combined = (
        0.45 * traffic_pressure
        + 0.35 * weather_pressure
        + 0.60 * incident_pressure
        + 0.25 * delay_factor_pressure
        + 0.18 * slack_pressure
        + 0.10 * load_pressure
    )
    if combined < 0.12:
        return 0.0, 0.0, []

    delay_floor = planned * min(2.5, 0.08 + combined)
    probability_floor = min(0.98, 0.08 + combined * 0.72)

    reasons = []
    if traffic_pressure >= 0.3:
        reasons.append("traffic pressure")
    if weather_pressure >= 0.3:
        reasons.append("weather pressure")
    if incident_pressure >= 0.3:
        reasons.append("incident pressure")
    if delay_factor_pressure >= 0.3:
        reasons.append("scenario delay factor")
    if slack_pressure >= 0.3:
        reasons.append("tight time window")

    return float(delay_floor), float(probability_floor), reasons


class _Log1pRegressor:
    """Trains on log1p(y); predict returns expm1(pred) — always non-negative."""

    def __init__(self, base_regressor):
        self.base_regressor = base_regressor

    def predict(self, X):
        import numpy as np
        return np.expm1(self.base_regressor.predict(X))


class _DirectStackRegressor:
    def __init__(self, base_estimators, meta_estimator):
        self.base_estimators = base_estimators
        self.meta_estimator  = meta_estimator

    def predict(self, X):
        meta_input = np.column_stack(
            [np.sinh(est.predict(X)) for est in self.base_estimators]
        )
        return self.meta_estimator.predict(meta_input)


class _ArcsinhRegressor:
    def __init__(self, base_regressor):
        self.base_regressor = base_regressor

    def fit(self, X, y, **fit_params):
        self.base_regressor.fit(X, np.arcsinh(y), **fit_params)
        return self

    def predict(self, X):
        return np.sinh(self.base_regressor.predict(X))


# ─── Feature augmentation (also stored in RouteDelayPredictor.augment_fn) ────

def _augment_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Derive 4 high-signal interaction features from the v2 feature set.
    All computable at inference time from existing v2 features — no leakage.

    New features:
      delay_to_slack_ratio  : cumulative_delay / time_window_slack  (r=0.74 with target)
      remaining_slack_net   : hist_slack_min − cumulative_delay      (r=−0.76)
      is_already_critical   : cumulative_delay > hist_slack_min       (r=0.87, strongest)
      stop_delay_momentum   : prev_stop_delay − rolling_avg_delay    (trend signal)

    NOTE: These 4 features are classification-domain signals. They are
    intentionally excluded from the v4 regressor feature set (see train_model_v4.py).
    """
    df = df.copy()

    # 1. delay_to_slack_ratio — how much of the window is eaten by accumulated delay?
    df['delay_to_slack_ratio'] = (
        df['cumulative_delay_min'] /
        df['time_window_slack_min'].clip(lower=1.0)
    )

    # 2. remaining_slack_net — true remaining buffer after current state
    df['remaining_slack_net'] = df['hist_slack_min'] - df['cumulative_delay_min']

    # 3. is_already_critical — stop is in the domino zone
    df['is_already_critical'] = (
        df['cumulative_delay_min'] > df['hist_slack_min']
    ).astype(int)

    # 4. stop_delay_momentum — delay acceleration (positive = worsening)
    rolling_avg = df['cumulative_delay_min'] / (df['stop_sequence'] - 1).clip(lower=1)
    df['stop_delay_momentum'] = df['prev_stop_delay_min'] - rolling_avg

    return df


class _PreFitCalibrator:
    """
    Isotonic calibration wrapper for an already-fitted classifier.

    Drop-in replacement for CalibratedClassifierCV(est, cv='prefit'),
    which was removed in sklearn 1.6.
    """
    def __init__(self, estimator):
        self.estimator = estimator

    def fit(self, X, y):
        raw = self.estimator.predict_proba(X)[:, 1]
        self._iso = IsotonicRegression(out_of_bounds='clip')
        self._iso.fit(raw, y)
        self.classes_ = self.estimator.classes_
        return self

    def predict_proba(self, X):
        raw = self.estimator.predict_proba(X)[:, 1]
        cal = self._iso.predict(raw)
        return np.column_stack([1 - cal, cal])

    def predict(self, X):
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)


class RouteDelayPredictor:
    """
    Route-level inference wrapper — exposes the competition output format.

    v3 additions:
      - cascade_factor  : amplifies risk of later stops when earlier stops are
                          high-risk (models the domino effect, Pearson r=0.688)
      - severity tier   : on-time / delayed / severe (≥severe_threshold_min expected)
      - augment_fn      : optional feature augmentation applied at inference time

    v4 additions:
      - reg_feature_cols: regressor may use a different (smaller) feature subset
                          than the classifier. Defaults to feature_cols so all
                          prior pickles remain fully backward-compatible.
      - p90_reg         : optional quantile P90 regressor. When present, every
                          prediction gains delay_p90_min — the 90th-percentile
                          worst-case delay estimate. Useful for dispatcher
                          decisions: "expected 8 min; worst-case (P90): 22 min."
    """
    CASCADE_DECAY     = 0.30   # how much prior high-risk stop bleeds into next
    CASCADE_THRESHOLD = 0.55   # probability above which a stop is a domino source

    def __init__(self, clf, reg, threshold, feature_cols,
                 augment_fn=None,
                 severe_threshold_min=SEVERE_DELAY_THRESHOLD_MIN,
                 reg_feature_cols=None,
                 p90_reg=None,
                 p90_offset=0.0):
        self.clf              = clf
        self.reg              = reg
        self.threshold        = threshold
        self.feature_cols     = feature_cols
        self.augment_fn       = augment_fn
        self.severe_threshold = severe_threshold_min
        # reg_feature_cols: feature subset for regressor (may differ from clf's
        # feature_cols in hybrid / v4 mode). Defaults to feature_cols so existing
        # pickles are fully backward-compatible.
        self.reg_feature_cols = reg_feature_cols if reg_feature_cols is not None \
                                else feature_cols
        # p90_reg: optional quantile P90 regressor trained with alpha=0.90.
        # Uses the same reg_feature_cols as the main regressor.
        self.p90_reg          = p90_reg
        self.p90_offset       = p90_offset

    def _severity(self, prob: float, delay: float) -> str:
        if delay >= self.severe_threshold:
            return 'severe'
        if prob >= self.threshold:
            return 'delayed'
        return 'on-time'

    def predict_route(self, stop_features_df: pd.DataFrame) -> dict:
        """
        Parameters
        ----------
        stop_features_df : pd.DataFrame
            One row per stop. Columns must include all feature_cols.
            If stop_sequence is present, stops are sorted by it.
            If augmented features (delay_to_slack_ratio, etc.) are absent
            and augment_fn is set, they are computed automatically.

        Returns
        -------
        dict with keys:
          stop_predictions  : list[dict] — per-stop risk details
            Each dict contains:
              stop_sequence           int
              delay_probability       float  (cascade-adjusted, calibrated)
              delay_probability_raw   float  (before cascade adjustment)
              expected_delay_min      float  (P50 — median estimate)
              delay_p90_min           float | None  (P90 worst-case; None if no p90_reg)
              will_miss_window        bool
              risk_level              str    "low" | "medium" | "high"
              severity                str    "on-time" | "delayed" | "severe"

          route_summary     : dict — aggregated route-level metrics
            route_delay_probability   float
            expected_total_delay_min  float  (P50 sum)
            worst_case_total_delay_min float | None  (P90 sum)
            high_risk_stop_count      int
            severe_stop_count         int
            overall_risk_score        float
        """
        df = stop_features_df.copy()

        # Apply feature augmentation if needed
        if self.augment_fn is not None and 'delay_to_slack_ratio' not in df.columns:
            df = self.augment_fn(df)

        if 'stop_sequence' in df.columns:
            df = df.sort_values('stop_sequence').reset_index(drop=True)

        # Pass as DataFrame to preserve feature names and silence sklearn warnings.
        # In hybrid / v4 mode clf and reg use different feature subsets.
        X_clf = df[self.feature_cols]
        X_reg = df[self.reg_feature_cols]

        probs  = self.clf.predict_proba(X_clf)[:, 1]
        delays = self.reg.predict(X_reg)
        condition_calibrations = [
            _condition_floor(row)
            for _, row in df.iterrows()
        ]
        delay_floors = np.array([item[0] for item in condition_calibrations], dtype=float)
        probability_floors = np.array([item[1] for item in condition_calibrations], dtype=float)

        # Runtime safety calibration: if explicit road-condition inputs imply
        # a higher minimum delay/risk than the model predicts, use that floor.
        # This prevents scenario testing from showing "0 min delay" under
        # gridlock/accident/snow combinations while still allowing the model to
        # dominate normal in-distribution cases.
        delays = np.maximum(delays, delay_floors)
        probs = np.maximum(probs, probability_floors)
        # getattr: pickles saved before p90_reg was added have no attribute → treat as None
        p90_reg = getattr(self, 'p90_reg', None)
        p90s    = p90_reg.predict(X_reg) if p90_reg is not None else None

        # Cascade propagation: high-risk stops amplify subsequent stop risk
        cascade_risk   = 0.0
        adjusted_probs = []
        for p in probs:
            p_adj = min(1.0, p + cascade_risk)
            adjusted_probs.append(p_adj)
            if p >= self.CASCADE_THRESHOLD:
                cascade_risk = self.CASCADE_DECAY * p
            else:
                cascade_risk *= 0.5   # decay when current stop is safe

        adjusted_probs = np.array(adjusted_probs)

        stop_preds = []
        for i, (prob_raw, prob_adj, delay) in enumerate(
                zip(probs, adjusted_probs, delays)):
            seq = int(df['stop_sequence'].iloc[i]) \
                  if 'stop_sequence' in df.columns else i + 1
            risk = ('high'   if prob_adj >= 0.60 else
                    'medium' if prob_adj >= 0.35 else 'low')

            effective_delay = max(float(delay), 0.0)
            # Only scale delay by cascade when the stop itself is intrinsically
            # risky (prob_raw >= threshold). If prob_raw is near-zero the stop is
            # genuinely safe; cascade already elevated prob_adj — don't also
            # multiply a small regressor baseline by 5× and create a false severe.
            if float(prob_raw) >= self.threshold:
                cascade_scale = min(prob_adj / max(float(prob_raw), 1e-6), 5.0)
            else:
                cascade_scale = 1.0
            scaled_delay    = round(effective_delay * cascade_scale, 1)

            p90_offset = float(getattr(self, "p90_offset", 0.0) or 0.0)
            p90_raw = max(float(p90s[i]) + p90_offset, 0.0) if p90s is not None else None
            if p90_raw is not None:
                p90_raw = max(p90_raw, effective_delay * 1.35)
            p90 = round(p90_raw * cascade_scale, 1) if p90_raw is not None else None

            _, _, calibration_reasons = condition_calibrations[i]
            stop_preds.append({
                'stop_sequence':         seq,
                'delay_probability':     round(float(prob_adj), 4),
                'delay_probability_raw': round(float(prob_raw), 4),
                'expected_delay_min':    scaled_delay,
                'delay_p90_min':         p90,
                'will_miss_window':      bool(prob_adj >= self.threshold),
                'risk_level':            risk,
                'severity':              self._severity(prob_adj, scaled_delay),
                'calibration_applied':   bool(delay_floors[i] > 0 or probability_floors[i] > 0),
                'calibration_reasons':   calibration_reasons,
            })

        # Route-level aggregations — use cascade-scaled values from stop_preds
        # so summary metrics stay consistent with what cost_matrix sees.
        route_delay_prob   = float(1 - np.prod(1 - adjusted_probs))
        total_delay        = sum(max(s['expected_delay_min'], 0.0) for s in stop_preds)
        total_p90_delay    = sum(s['delay_p90_min'] for s in stop_preds
                                 if s['delay_p90_min'] is not None) or None
        severe_count       = sum(1 for s in stop_preds if s['severity'] == 'severe')
        high_risk_count    = sum(1 for s in stop_preds if s['risk_level'] == 'high')
        # Position-weighted risk score (later stops weighted higher)
        overall_risk_score = float(
            np.average(adjusted_probs, weights=np.arange(1, len(adjusted_probs) + 1))
        )

        return {
            'stop_predictions': stop_preds,
            'route_summary': {
                'route_delay_probability':    round(route_delay_prob, 4),
                'expected_total_delay_min':   round(total_delay, 1),
                'worst_case_total_delay_min': round(total_p90_delay, 1)
                                              if total_p90_delay is not None else None,
                'high_risk_stop_count':       high_risk_count,
                'severe_stop_count':          severe_count,
                'overall_risk_score':         round(overall_risk_score, 4),
            },
        }

    def predict_stop(self, stop_features: dict) -> dict:
        """Single-stop convenience wrapper."""
        df     = pd.DataFrame([stop_features])
        result = self.predict_route(df)
        s      = result['stop_predictions'][0]
        return {k: s[k] for k in (
            'delay_probability', 'expected_delay_min', 'delay_p90_min',
            'will_miss_window', 'risk_level', 'severity',
            'calibration_applied', 'calibration_reasons')}
