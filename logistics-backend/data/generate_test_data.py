"""
data/generate_test_data.py
--------------------------
V7 merge pipeline'ına uygun sentetik test verisi üretir.

5 CSV:
  route_stops.csv         — stop-level ham veri
  routes.csv              — route-level veri (weather, vehicle, incident)
  traffic_segments.csv    — trafik segmentleri (road_type × hour aggregate için)
  weather_observations.csv— hava gözlemleri (weather_condition aggregate için)
  historical_delay_stats.csv — geçmiş gecikme istatistikleri (lookup tablosu)

Kullanım:
    python data/generate_test_data.py
"""

import os
import random
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

random.seed(42)
np.random.seed(42)

OUT_DIR  = os.path.join(os.path.dirname(__file__), "synthetic")
LAT_MIN, LAT_MAX = 39.20, 39.90
LON_MIN, LON_MAX = 36.80, 37.50

ROAD_TYPES     = ["highway", "urban", "rural", "mountain"]
TRAFFIC_LEVELS = ["low", "moderate", "high", "congested"]
WEATHER_CONDS  = ["clear", "cloudy", "fog", "rain", "snow", "wind"]
VEHICLE_TYPES  = ["van", "car", "truck", "motorcycle"]
ROAD_SURFACES  = ["dry", "wet", "icy", "snow_covered"]
TIME_BUCKETS   = ["morning_rush", "midday", "evening_rush", "night"]

def rand_lat(): return round(random.uniform(LAT_MIN, LAT_MAX), 6)
def rand_lon(): return round(random.uniform(LON_MIN, LON_MAX), 6)

def hour_to_bucket(hour):
    if 7 <= hour <= 9:            return "morning_rush"
    if 16 <= hour <= 19:          return "evening_rush"
    if hour >= 22 or hour <= 5:   return "night"
    return "midday"


# ── 1. routes.csv ─────────────────────────────────────────────────────────────

def gen_routes(n_routes=80):
    rows = []
    base = datetime(2025, 6, 1, 7, 0)
    for i in range(n_routes):
        weather  = random.choice(WEATHER_CONDS)
        traffic  = random.choice(TRAFFIC_LEVELS)
        vehicle  = random.choice(VEHICLE_TYPES)
        incident = int(random.random() < 0.08)
        temp     = round(random.gauss(15, 8), 1)
        precip   = round(random.uniform(0.5, 10), 1) if weather in ("rain","snow") else 0.0
        dep_plan = base + timedelta(days=i // 5, hours=random.randint(0, 3))
        dep_act  = dep_plan + timedelta(minutes=random.randint(-5, 20))
        n_stops  = random.randint(4, 12)
        dist     = round(random.uniform(20, 120), 1)
        plan_dur = round(dist / 50 * 60 + random.gauss(0, 10), 1)
        delay_f  = round(random.uniform(0.9, 1.6), 2) if traffic in ("high","congested") else 1.0
        act_dur  = round(plan_dur * delay_f, 1)

        rows.append({
            "route_id":             f"RT-{i+1:04d}",
            "vehicle_id":           i % 4,
            "vehicle_type":         vehicle,
            "driver_id":            f"DRV-{(i % 10)+1:03d}",
            "num_stops":            n_stops,
            "departure_planned":    dep_plan.strftime("%Y-%m-%d %H:%M:%S"),
            "departure_actual":     dep_act.strftime("%Y-%m-%d %H:%M:%S"),
            "total_distance_km":    dist,
            "planned_duration_min": plan_dur,
            "actual_duration_min":  act_dur,
            "total_delay_min":      round(act_dur - plan_dur, 1),
            "on_time_delivery_rate":round(random.uniform(0.6, 1.0), 2),
            "weather_condition":    weather,
            "temperature_c":        temp,
            "precipitation_mm":     precip,
            "wind_speed_kmh":       round(random.uniform(0, 55), 1),
            "humidity_pct":         round(random.uniform(30, 95), 1),
            "visibility_km":        round(random.uniform(1, 20), 1),
            "traffic_level":        traffic,
            "road_incident":        incident,
            "incident_severity":    round(random.uniform(0.3, 1.0), 2) if incident else 0.0,
            "overall_delay_factor": delay_f,
        })
    return pd.DataFrame(rows)


# ── 2. route_stops.csv ────────────────────────────────────────────────────────

def gen_route_stops(routes_df):
    rows = []
    stop_counter = 1

    for _, route in routes_df.iterrows():
        n    = route["num_stops"]
        dep  = datetime.strptime(route["departure_planned"], "%Y-%m-%d %H:%M:%S")
        road = random.choice(ROAD_TYPES)
        cumulative_delay = 0.0

        for seq in range(1, n + 1):
            dist_prev    = round(random.uniform(3, 25), 2)
            plan_travel  = round(dist_prev / 50 * 60 + random.gauss(0, 5), 1)
            plan_service = round(random.uniform(5, 20), 1)
            pkg_count    = random.randint(1, 15)
            pkg_weight   = round(random.uniform(5, 200), 1)

            plan_arr  = dep + timedelta(minutes=plan_travel)
            win_open  = plan_arr - timedelta(minutes=random.randint(10, 30))
            win_close = plan_arr + timedelta(minutes=random.randint(30, 120))
            slack_min = (win_close - plan_arr).total_seconds() / 60

            delay_at_stop = round(np.clip(
                random.gauss(cumulative_delay * 0.3, 5), -5, 60), 1)
            cumulative_delay += max(delay_at_stop, 0)

            act_travel = round(plan_travel * route["overall_delay_factor"]
                               + random.gauss(0, 2), 1)
            act_arr    = plan_arr + timedelta(minutes=delay_at_stop)
            missed     = int(act_arr > win_close)

            rows.append({
                "route_id":           route["route_id"],
                "stop_sequence":      seq,
                "stop_id":            f"STP-{stop_counter:05d}",
                "latitude":           rand_lat(),
                "longitude":          rand_lon(),
                "distance_from_prev_km": dist_prev,
                "road_type":          road,
                "planned_arrival":    plan_arr.strftime("%Y-%m-%d %H:%M:%S"),
                "actual_arrival":     act_arr.strftime("%Y-%m-%d %H:%M:%S"),
                "time_window_open":   win_open.strftime("%Y-%m-%d %H:%M:%S"),
                "time_window_close":  win_close.strftime("%Y-%m-%d %H:%M:%S"),
                "planned_service_min":plan_service,
                "actual_service_min": round(plan_service + random.gauss(0, 2), 1),
                "planned_travel_min": plan_travel,
                "actual_travel_min":  act_travel,
                "delay_at_stop_min":  delay_at_stop,
                "missed_time_window": missed,
                "delay_probability":  round(np.clip(missed * 0.6 + random.uniform(0, 0.4), 0, 1), 3),
                "package_count":      pkg_count,
                "package_weight_kg":  pkg_weight,
            })
            dep = plan_arr + timedelta(minutes=plan_service)
            stop_counter += 1

    return pd.DataFrame(rows)


# ── 3. traffic_segments.csv ───────────────────────────────────────────────────

def gen_traffic_segments(n=600):
    rows = []
    base = datetime(2025, 5, 1)
    for i in range(n):
        road  = random.choice(ROAD_TYPES)
        hour  = random.randint(0, 23)
        dow   = random.randint(0, 6)
        rush  = int(7 <= hour <= 9 or 16 <= hour <= 19)

        free_flow = {"highway": 90, "urban": 50, "rural": 70, "mountain": 40}[road]
        cong = round(np.clip(random.betavariate(2, 5) + (0.25 if rush else 0), 0, 1), 3)
        cur_speed = round(free_flow * (1 - cong * 0.6), 1)

        if cong > 0.7:   traffic = "congested"
        elif cong > 0.4: traffic = "high"
        elif cong > 0.2: traffic = "moderate"
        else:            traffic = "low"

        rows.append({
            "segment_id":               f"SEG-{10000+i}",
            "center_lat":               rand_lat(),
            "center_lon":               rand_lon(),
            "road_type":                road,
            "hour_of_day":              hour,
            "day_of_week":              dow,
            "is_rush_hour":             rush,
            "is_weekend":               int(dow >= 5),
            "free_flow_speed_kmh":      round(free_flow + random.gauss(0,5), 1),
            "current_speed_kmh":        cur_speed,
            "congestion_ratio":         cong,
            "traffic_level":            traffic,
            "incident_reported":        int(random.random() < 0.05),
            "avg_vehicle_count_per_min":round(random.uniform(5, 60), 1),
            "timestamp":                (base + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return pd.DataFrame(rows)


# ── 4. weather_observations.csv ──────────────────────────────────────────────

def gen_weather_observations(n=500):
    rows = []
    base = datetime(2025, 4, 1)
    for i in range(n):
        cond = random.choice(WEATHER_CONDS)
        temp = round(random.gauss(15, 8), 1)
        prec = round(random.uniform(0.5, 15), 1) if cond in ("rain","snow") else 0.0
        wind = round(random.uniform(0, 60), 1)
        vis  = round(random.uniform(1, 5), 1) if cond == "fog" else round(random.uniform(5, 20), 1)

        if cond == "snow" and temp < -2:  surface = "snow_covered"
        elif cond == "snow":              surface = "icy"
        elif cond == "rain" and temp < 0: surface = "icy"
        elif cond == "rain":              surface = "wet"
        else:                             surface = "dry"

        risk = round(np.clip(
            {"clear":0.1,"cloudy":0.2,"wind":0.25,"fog":0.4,"rain":0.55,"snow":0.75}[cond]
            + random.gauss(0, 0.05), 0, 1), 3)

        rows.append({
            "obs_id":                  f"WOB-{20000+i}",
            "timestamp":               (base + timedelta(hours=i*3)).strftime("%Y-%m-%d %H:%M:%S"),
            "latitude":                rand_lat(),
            "longitude":               rand_lon(),
            "weather_condition":       cond,
            "temperature_c":           temp,
            "feels_like_c":            round(temp - wind * 0.05, 1),
            "precipitation_mm":        prec,
            "precipitation_type":      "snow" if cond=="snow" else ("rain" if prec>0 else "none"),
            "wind_speed_kmh":          wind,
            "wind_direction_deg":      random.randint(0, 359),
            "humidity_pct":            round(random.uniform(30, 95), 1),
            "pressure_hpa":            round(random.uniform(980, 1030), 1),
            "visibility_km":           vis,
            "cloud_cover_pct":         round(random.uniform(0, 100), 1),
            "uv_index":                round(random.uniform(0, 8), 1),
            "road_surface_condition":  surface,
            "delay_risk_score":        risk,
        })
    return pd.DataFrame(rows)


# ── 5. historical_delay_stats.csv ─────────────────────────────────────────────

def gen_historical_delay_stats():
    rows = []
    for road in ROAD_TYPES:
        for traffic in TRAFFIC_LEVELS:
            for weather in WEATHER_CONDS:
                for bucket in TIME_BUCKETS:
                    base = {"highway":3,"urban":12,"rural":6,"mountain":15}[road]
                    tm   = {"low":1.0,"moderate":1.4,"high":2.0,"congested":3.0}[traffic]
                    wm   = {"clear":1.0,"cloudy":1.1,"wind":1.2,"fog":1.5,"rain":1.8,"snow":2.5}[weather]
                    rm   = 1.3 if "rush" in bucket else (0.8 if bucket=="night" else 1.0)

                    mean_d = round(base * tm * wm * rm, 2)
                    std_d  = round(mean_d * 0.4, 2)
                    rows.append({
                        "road_type":          road,
                        "traffic_level":      traffic,
                        "weather_condition":  weather,
                        "time_bucket":        bucket,
                        "sample_count":       random.randint(20, 200),
                        "mean_delay_min":     mean_d,
                        "median_delay_min":   round(mean_d * 0.85, 2),
                        "p90_delay_min":      round(mean_d + 1.28 * std_d, 2),
                        "p95_delay_min":      round(mean_d + 1.65 * std_d, 2),
                        "std_delay_min":      std_d,
                        "on_time_rate":       round(np.clip(1 - mean_d/30, 0.02, 0.98), 3),
                        "delay_probability":  round(np.clip(mean_d/30, 0.02, 0.98), 3),
                    })
    return pd.DataFrame(rows)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)

    print("Generating routes.csv ...")
    routes_df = gen_routes(80)
    routes_df.to_csv(f"{OUT_DIR}/routes.csv", index=False)

    print("Generating route_stops.csv ...")
    gen_route_stops(routes_df).to_csv(f"{OUT_DIR}/route_stops.csv", index=False)

    print("Generating traffic_segments.csv ...")
    gen_traffic_segments(600).to_csv(f"{OUT_DIR}/traffic_segments.csv", index=False)

    print("Generating weather_observations.csv ...")
    gen_weather_observations(500).to_csv(f"{OUT_DIR}/weather_observations.csv", index=False)

    print("Generating historical_delay_stats.csv ...")
    gen_historical_delay_stats().to_csv(f"{OUT_DIR}/historical_delay_stats.csv", index=False)

    print(f"\nDone. Files written to {OUT_DIR}/")
    print("V7 modeli yeniden train etmek için: python ml/train_model_v7.py")
