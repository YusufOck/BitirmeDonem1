"""
schemas.py
----------
Pydantic v2 request / response models for the optimization API.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DelayFactor(BaseModel):
    """Human-readable factor explaining why a stop may be delayed."""

    label: str
    value: str
    impact: str
    severity: Literal["info", "warning", "danger"]


# ── Request models ────────────────────────────────────────────────────────────

class StopInput(BaseModel):
    """Single delivery stop features for ML inference + OR-Tools optimization."""

    # Required ML features
    stop_sequence: int = Field(..., ge=1, description="1-based planned position on the route")
    cumulative_delay_min: float = Field(default=0.0, ge=0.0, description="Total delay accumulated up to this stop (minutes)")
    prev_stop_delay_min: float = Field(default=0.0, ge=0.0, description="Delay at the immediately prior stop (minutes)")
    time_window_slack_min: float = Field(default=480.0, ge=0.0, description="Allowed slack before the delivery window closes (minutes). Default 480 (8h) = effectively unconstrained.")
    hist_slack_min: float = Field(default=54.69, ge=0.0, description="Historical average slack for this stop context (minutes)")
    hist_delay_probability: float = Field(default=0.25, ge=0.0, le=1.0, description="Historical miss-window probability (0-1)")

    # Optional ML features — None causes ml/inference.py to use dataset medians
    distance_from_prev_km: float | None = Field(default=None, ge=0.0)
    planned_travel_min: float | None = Field(default=None, ge=0.0, description="Planned travel time from previous stop (minutes). Also used as OR-Tools travel-time fallback.")
    road_type: Literal["highway", "urban", "rural", "mountain"] | None = None
    traffic_level: Literal["low", "moderate", "high", "congested"] | None = None
    weather_condition: Literal["clear", "cloudy", "wind", "fog", "rain", "snow"] | None = None
    hour_of_day: int | None = Field(default=None, ge=0, le=23)
    day_of_week: int | None = Field(default=None, ge=0, le=6, description="0=Monday … 6=Sunday")
    stop_progress_ratio: float | None = Field(default=None, ge=0.0, le=1.0, description="Computed automatically if None: (stop_index+1) / total_stops")
    congestion_ratio_mean: float | None = Field(default=None, ge=0.0, le=1.0)
    incident_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    road_surface_condition: Literal["dry", "wet", "icy", "snow_covered"] | None = None
    road_surface_condition_enc: int | None = Field(default=None, ge=0, le=3)
    delay_risk_score_mean: float | None = Field(default=None, ge=0.0, le=1.0)
    package_count: int | None = Field(default=None, ge=0)
    package_weight_kg: float | None = Field(default=None, ge=0.0)
    vehicle_type: Literal["car", "motorcycle", "truck", "van"] | None = None
    road_incident: int | None = Field(default=None, ge=0, le=1)
    incident_severity: float | None = Field(default=None, ge=0.0, le=1.0)
    temperature_c: float | None = None
    precipitation_mm: float | None = Field(default=None, ge=0.0)
    wind_speed_kmh: float | None = Field(default=None, ge=0.0)
    visibility_km: float | None = Field(default=None, ge=0.0)
    overall_delay_factor: float | None = Field(default=None, ge=0.0)
    time_window_duration_min: float | None = Field(default=None, ge=0.0)

    # Display-only fields — not used by ML, passed through to response
    stop_id: str | None = None
    stop_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    feature_source: str | None = None
    delay_factors: list[DelayFactor] = Field(default_factory=list)


class OptimizeRequest(BaseModel):
    """Full optimization request."""

    stops: list[StopInput] = Field(
        ...,
        min_length=2,
        description="Delivery stops (depot excluded). Minimum 2 stops required.",
    )
    use_p90: bool = Field(
        default=False,
        description="Use P90 (worst-case) delay estimates for conservative routing. Default: P50 (expected).",
    )
    num_vehicles: int = Field(default=1, ge=1, le=10, description="Number of delivery vehicles")
    time_limit_seconds: int = Field(
        default=30,
        ge=5,
        le=120,
        description="OR-Tools solver wall-clock budget (seconds)",
    )
    travel_time_matrix: list[list[float]] | None = Field(
        default=None,
        description=(
            "NxN matrix of leg durations (minutes) between delivery stops. "
            "Rows and columns are 0-based delivery-stop indices. "
            "If omitted and stop coordinates + depot are provided, Mapbox Matrix API is called automatically."
        ),
    )
    depot_latitude: float | None = Field(
        default=None,
        description="Depot latitude. Required for automatic Mapbox Matrix API integration.",
    )
    depot_longitude: float | None = Field(
        default=None,
        description="Depot longitude. Required for automatic Mapbox Matrix API integration.",
    )
    generate_explanation: bool = Field(
        default=False,
        description=(
            "If True, call the local Ollama model after optimization and include "
            "a dispatcher-friendly natural language explanation in the response. "
            "Requires Ollama to be running (see OLLAMA_BASE_URL in .env)."
        ),
    )
    route_id: int | None = Field(
        default=None,
        description="Optional DB route ID. When set, result is registered in fleet store and DB route is updated.",
    )


# ── Explanation models ────────────────────────────────────────────────────────

class StopAlert(BaseModel):
    """A single high-risk stop alert generated by the AI explainer."""
    stop_name: str
    message: str = Field(..., description="One-sentence plain-language alert for the dispatcher")


class ExplanationResult(BaseModel):
    """
    Natural language explanation of the optimization decision, produced by a
    local Ollama LLM.  All fields use plain language suitable for dispatchers
    with no ML background.
    """
    overall_assessment: str = Field(
        ...,
        description="2-3 sentence summary of route risk and main concern",
    )
    risk_factors: list[str] = Field(
        default_factory=list,
        description="Up to 3 top risk factors identified on this route",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Concrete dispatcher actions to reduce delay risk",
    )
    stop_alerts: list[StopAlert] = Field(
        default_factory=list,
        description="Per-stop alerts for high-risk or window-missing stops only",
    )
    reorder_rationale: list[str] = Field(
        default_factory=list,
        description="Plain-language explanation of why each moved stop was repositioned",
    )
    model_used: str = Field(..., description="Ollama model that generated this explanation")
    generation_time_ms: int = Field(..., description="Time taken to generate the explanation (ms)")
    parse_warning: str | None = Field(
        default=None,
        description="Set when the model output could not be parsed as JSON",
    )
    error: str | None = Field(
        default=None,
        description="Set when Ollama was unreachable or returned an error",
    )


# ── Response models ───────────────────────────────────────────────────────────

class OptimizedStop(BaseModel):
    """A single stop in the OR-Tools optimized sequence, enriched with ML predictions."""

    optimized_position: int = Field(..., description="Position in the OR-Tools optimized route (0-based, depot not counted)")
    vehicle_id: int = Field(..., description="Which vehicle serves this stop (0-based)")
    original_stop_index: int = Field(..., description="0-based index in the original stops list")
    stop_sequence: int | None = None
    stop_id: str | None = None
    stop_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    planned_travel_min: float | None = None
    time_window_slack_min: float
    feature_source: str | None = None
    road_type: Literal["highway", "urban", "rural", "mountain"] | None = None
    traffic_level: Literal["low", "moderate", "high", "congested"] | None = None
    weather_condition: Literal["clear", "cloudy", "wind", "fog", "rain", "snow"] | None = None
    congestion_ratio_mean: float | None = None
    road_incident: int | None = None
    incident_severity: float | None = None
    precipitation_mm: float | None = None
    wind_speed_kmh: float | None = None
    visibility_km: float | None = None
    package_count: int | None = None
    package_weight_kg: float | None = None
    delay_factors: list[DelayFactor] = Field(default_factory=list)

    # ML prediction fields
    delay_probability: float = Field(..., description="Cascade-adjusted delay probability (0-1)")
    expected_delay_min: float = Field(..., description="P50 expected delay (minutes)")
    delay_p90_min: float | None = Field(None, description="P90 worst-case delay (minutes)")
    will_miss_window: bool
    risk_level: Literal["low", "medium", "high"]
    severity: Literal["on-time", "delayed", "severe"]


class RouteSummary(BaseModel):
    """ML route-level metrics merged with VRP solver metadata."""

    # ML route-level metrics
    route_delay_probability: float
    expected_total_delay_min: float
    worst_case_total_delay_min: float | None = None
    high_risk_stop_count: int
    severe_stop_count: int
    overall_risk_score: float

    # Departure time recommendation
    recommended_departure_shift_min: float = Field(
        default=0.0,
        description=(
            "Recommended departure time shift (minutes). "
            "Depart this many minutes earlier than planned to absorb predicted delays. "
            "Equals expected_total_delay_min."
        ),
    )

    # VRP solver metadata
    vrp_status: str
    vrp_objective_value: int = Field(..., description="Total route cost in 0.01-minute units")
    vrp_solve_time_ms: int
    dropped_stop_indices: list[int] = Field(
        default_factory=list,
        description="0-based indices of stops that could not be served (empty = all served)",
    )
    cost_mode: Literal["expected", "p90"]


class OptimizeResponse(BaseModel):
    """Full optimization response."""

    optimized_route: list[OptimizedStop] = Field(
        ...,
        description="Delivery stops in OR-Tools optimized order, each enriched with ML predictions",
    )
    route_summary: RouteSummary
    ml_predictions: dict = Field(..., description="Raw predict_route() output for debugging / transparency")
    vrp_result: dict = Field(..., description="Raw solve_vrp() output for debugging")
    use_p90: bool
    num_vehicles: int
    explanation: ExplanationResult | None = Field(
        default=None,
        description="Dispatcher-friendly natural language explanation (set when generate_explanation=True)",
    )


class RouteGeometry(BaseModel):
    """GeoJSON geometry returned by Mapbox Directions API."""

    type: str
    coordinates: list


class VehicleRoute(BaseModel):
    """Per-vehicle route: geometry + delivery intelligence merged into one object."""

    vehicle_id: int
    geometry: RouteGeometry = Field(..., description="GeoJSON LineString for this vehicle — pass directly to your map layer")
    distance_m: float = Field(..., description="Total route distance for this vehicle (meters)")
    duration_s: float = Field(..., description="Total route duration for this vehicle (seconds)")
    stop_count: int
    stop_names: list[str | None] = Field(..., description="Stop names in optimized delivery order")
    total_planned_travel_min: float = Field(..., description="Sum of planned leg travel times (minutes)")
    total_expected_delay_min: float = Field(..., description="Sum of ML expected delays across all stops (minutes)")
    total_worst_case_delay_min: float | None = Field(None, description="Sum of P90 worst-case delays (minutes). None if model has no P90.")
    high_risk_stop_count: int = Field(..., description="Number of stops with risk_level == 'high'")
    severe_stop_count: int = Field(..., description="Number of stops with severity == 'severe'")


class VehicleSummary(BaseModel):
    """Per-courier/vehicle breakdown computed from optimized_route (used by /optimize)."""

    vehicle_id: int
    stop_count: int
    stop_names: list[str | None] = Field(..., description="Stop names in optimized delivery order")
    total_planned_travel_min: float = Field(..., description="Sum of planned leg travel times (minutes)")
    total_expected_delay_min: float = Field(..., description="Sum of ML expected delays across all stops (minutes)")
    total_worst_case_delay_min: float | None = Field(None, description="Sum of P90 worst-case delays (minutes). None if model has no P90.")
    high_risk_stop_count: int = Field(..., description="Number of stops with risk_level == 'high'")
    severe_stop_count: int = Field(..., description="Number of stops with severity == 'severe'")


class FullRouteResponse(BaseModel):
    """
    Single-call full pipeline response.

    Contains everything the frontend needs to render the map and
    display delivery intelligence — no second request required.

    Each entry in ``vehicle_routes`` carries both the GeoJSON geometry and the
    delivery intelligence for that vehicle, so the frontend can render each
    courier's path independently (different colours, separate panels, etc.).
    """

    vehicle_routes: list[VehicleRoute] = Field(
        ...,
        description="One entry per active vehicle — geometry + ML metrics combined",
    )
    optimized_route: list[OptimizedStop] = Field(
        ...,
        description="All stops in OR-Tools optimized order, each enriched with ML delay predictions",
    )
    route_summary: RouteSummary
    use_p90: bool
    num_vehicles: int
    explanation: ExplanationResult | None = Field(
        default=None,
        description="Dispatcher-friendly natural language explanation (set when generate_explanation=True)",
    )


class ExplainRequest(BaseModel):
    """
    Standalone explain request.

    Pass the ``route_summary`` and ``optimized_route`` fields from a previous
    /optimize or /full-route response to get a dispatcher-friendly explanation
    without re-running the optimization pipeline.
    """
    route_summary: RouteSummary
    optimized_route: list[OptimizedStop]
    use_p90: bool = False


# ── What-if scenario models ───────────────────────────────────────────────────

class StopModification(BaseModel):
    """Per-stop override within a what-if scenario."""

    stop_index: int = Field(..., ge=0, description="0-based index of the stop to modify")
    delay_added_min: float = Field(
        default=0.0,
        ge=0.0,
        description="Extra minutes of delay injected at this stop. Cascades forward into cumulative_delay_min of subsequent stops.",
    )
    traffic_level_override: Literal["low", "moderate", "high", "congested"] | None = None
    weather_condition_override: Literal["clear", "cloudy", "wind", "fog", "rain", "snow"] | None = None


class WhatIfModifications(BaseModel):
    """Scenario modifications to apply on top of the baseline route."""

    weather_override: Literal["clear", "cloudy", "wind", "fog", "rain", "snow"] | None = Field(
        default=None,
        description="Apply this weather condition to all stops (e.g. simulate sudden snowfall)",
    )
    traffic_override: Literal["low", "moderate", "high", "congested"] | None = Field(
        default=None,
        description="Apply this traffic level to all stops (e.g. simulate rush-hour congestion)",
    )
    stop_modifications: list[StopModification] = Field(
        default_factory=list,
        description="Per-stop overrides. Per-stop weather/traffic wins over global overrides.",
    )


class WhatIfRequest(BaseModel):
    """Scenario simulation request."""

    stops: list[StopInput] = Field(..., min_length=2)
    modifications: WhatIfModifications
    travel_time_matrix: list[list[float]] | None = Field(
        default=None,
        description="NxN travel time matrix (minutes) reused from the original optimize call. No Mapbox API call is made.",
    )
    use_p90: bool = False
    num_vehicles: int = Field(default=1, ge=1, le=10)
    time_limit_seconds: int = Field(default=15, ge=5, le=60)


class WhatIfDelta(BaseModel):
    """Quantified difference between the baseline and modified scenarios."""

    delta_risk_score: float = Field(
        ...,
        description="Modified minus baseline overall_risk_score. Positive = scenario is worse.",
    )
    delta_expected_delay_min: float = Field(
        ...,
        description="Change in total expected delay (minutes). Positive = more delay.",
    )
    delta_worst_case_delay_min: float | None = Field(
        None,
        description="Change in P90 worst-case delay. None if model has no P90.",
    )
    newly_at_risk_stop_count: int = Field(
        ...,
        description="Stops that moved to risk_level=high that were not high-risk in the baseline.",
    )
    newly_missing_window_count: int = Field(
        ...,
        description="Stops where will_miss_window flipped from False to True.",
    )
    recovered_stop_count: int = Field(
        ...,
        description="Stops that dropped from high risk — useful for positive what-if (e.g. weather improves).",
    )


class WhatIfResponse(BaseModel):
    """Full what-if comparison result."""

    baseline_summary: RouteSummary
    modified_summary: RouteSummary
    delta: WhatIfDelta
    baseline_route: list[OptimizedStop]
    modified_route: list[OptimizedStop]
    modifications_applied: dict = Field(
        ...,
        description="Echo of what was applied — for audit trail and frontend display.",
    )



# ── Fleet monitoring models ───────────────────────────────────────────────────

class ScenarioControls(BaseModel):
    """Continuous what-if controls adjusted by the dispatcher UI."""

    traffic_density: int = Field(default=45, ge=0, le=100, description="0=no traffic, 100=gridlock")
    accident_severity: int = Field(default=0, ge=0, le=100, description="0=no accident, 100=major incident")
    weather_condition: Literal["clear", "cloudy", "wind", "fog", "rain", "snow"] = "clear"
    weather_severity: int = Field(default=10, ge=0, le=100, description="Intensity of weather impact")
    road_disruption: int = Field(default=0, ge=0, le=100, description="Road work/closure pressure")
    package_load: int = Field(default=50, ge=0, le=100, description="Relative package load pressure")
    dispatch_hour: int = Field(default=9, ge=0, le=23)
    conservative_mode: bool = Field(
        default=False,
        description="When true, optimize with P90 delay estimates instead of expected delay.",
    )


class ScenarioRouteRequest(BaseModel):
    """Run a real ML + OR-Tools + Mapbox re-optimization for one route scenario."""

    depot_latitude: float
    depot_longitude: float
    stops: list[StopInput] = Field(..., min_length=2)
    controls: ScenarioControls = Field(default_factory=ScenarioControls)
    time_limit_seconds: int = Field(default=15, ge=5, le=60)


class ScenarioImpactFactor(BaseModel):
    label: str
    before: str
    after: str
    impact: str
    severity: Literal["info", "warning", "danger"]


class ScenarioReoptimizationResponse(BaseModel):
    """Scenario lab response with auditable route, model, and map evidence."""

    baseline_summary: RouteSummary
    scenario_summary: RouteSummary
    baseline_route: list[OptimizedStop]
    scenario_route: list[OptimizedStop]
    baseline_geometry: RouteGeometry
    scenario_geometry: RouteGeometry
    baseline_metrics: dict
    scenario_metrics: dict
    delta: dict
    factor_impacts: list[ScenarioImpactFactor]
    controls_applied: ScenarioControls
    order_changed: bool
    sequence_before: list[str | None]
    sequence_after: list[str | None]
    explanation: str
    mapbox_alternatives: list[dict] = Field(default_factory=list)


class FleetRouteSummary(BaseModel):
    """Per-route summary row in the fleet risk overview."""

    route_id: int
    status: Literal["active", "completed", "cancelled"]
    delay_status: Literal["green", "amber", "red"]
    num_vehicles: int
    stop_count: int
    high_risk_stop_count: int
    severe_stop_count: int
    expected_total_delay_min: float
    worst_case_total_delay_min: float | None = None
    dropped_stop_count: int = Field(
        ...,
        description="Number of stops that could not be served by OR-Tools",
    )
    updated_at: str


class FleetRiskSummaryResponse(BaseModel):
    """Fleet-level risk overview response."""

    timestamp: str
    total_active_routes: int
    at_risk_routes: int = Field(..., description="amber + red count")
    green_routes: int
    amber_routes: int
    red_routes: int
    routes: list[FleetRouteSummary]
