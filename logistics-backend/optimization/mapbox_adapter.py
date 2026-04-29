"""
mapbox_adapter.py
-----------------
Mapbox Matrix API çıktısını SBTU Logistics pipeline girdisine dönüştürür.

Mapbox get_weight_matrices() şunu döndürür:
    duration_matrix  : (N+1)×(N+1)  saniye    — index 0 = depot
    distance_matrix  : (N+1)×(N+1)  metre     — index 0 = depot

Pipeline şunu bekliyor:
    stops_input           : her stop'ta planned_travel_min (dk) ve
                            distance_from_prev_km (km) — önceki durağa göre
    travel_time_matrix    : N×N dakika — sadece delivery durakları arası

Kullanım:
    duration_sec, distance_m = get_weight_matrices()
    stops_enriched, tt_matrix = mapbox_to_pipeline(stops_raw, duration_sec, distance_m)
    result = optimizer.optimize(stops_enriched, travel_time_matrix=tt_matrix)
"""

from __future__ import annotations


def mapbox_to_pipeline(
    stops_raw: list[dict],
    duration_matrix_sec: list[list[float]],
    distance_matrix_m: list[list[float]],
) -> tuple[list[dict], list[list[float]]]:
    """
    Mapbox matrislerini pipeline girdisine dönüştür.

    Parameters
    ----------
    stops_raw : list[dict]
        N delivery stop dicts — Mapbox'tan önce oluşturduğun ham stop listesi.
        stop_sequence, cumulative_delay_min, vs. gibi ML alanlarını içermeli.
        planned_travel_min ve distance_from_prev_km bu fonksiyon tarafından
        doldurulur/üzerine yazılır.

    duration_matrix_sec : list[list[float]]
        (N+1)×(N+1) saniye cinsinden süre matrisi.
        Satır/sütun 0 = depot.  Mapbox get_weight_matrices()[0].

    distance_matrix_m : list[list[float]]
        (N+1)×(N+1) metre cinsinden mesafe matrisi.
        Satır/sütun 0 = depot.  Mapbox get_weight_matrices()[1].

    Returns
    -------
    stops_enriched : list[dict]
        Her stop'a planned_travel_min ve distance_from_prev_km eklendi.
        Planned sıra: depot→stop[0]→stop[1]→…→stop[N-1]

    travel_time_matrix : list[list[float]]
        N×N dakika cinsinden süre matrisi — sadece delivery durakları.
        RouteOptimizer.optimize(travel_time_matrix=...) parametresine doğrudan ver.
    """
    n = len(stops_raw)
    expected_size = n + 1   # depot + N delivery stop

    if len(duration_matrix_sec) != expected_size:
        raise ValueError(
            f"duration_matrix boyutu {len(duration_matrix_sec)}×? bekleniyordu "
            f"{expected_size}×{expected_size}  (depot + {n} durak)"
        )
    if len(distance_matrix_m) != expected_size:
        raise ValueError(
            f"distance_matrix boyutu {len(distance_matrix_m)}×? bekleniyordu "
            f"{expected_size}×{expected_size}  (depot + {n} durak)"
        )

    # ── 1. Per-stop planned_travel_min ve distance_from_prev_km ──────────────
    # Planned sıra: depot (idx 0) → stop0 (idx 1) → stop1 (idx 2) → …
    # Her stop için "önceki düğüm" planned sıradaki bir önceki indekstir.
    #
    #   stop[i]  →  Mapbox satır: i,  sütun: i+1
    #   prev_node = i   (depot=0, stop0=1, stop1=2, …)
    #   curr_node = i+1

    stops_enriched: list[dict] = []
    for i, stop in enumerate(stops_raw):
        prev_node = i          # depot=0 için i=0, stop1 için i=1, ...
        curr_node = i + 1      # delivery stop'un Mapbox indeksi

        travel_sec  = duration_matrix_sec[prev_node][curr_node]
        dist_m      = distance_matrix_m[prev_node][curr_node]

        enriched = dict(stop)
        enriched["planned_travel_min"]   = travel_sec / 60.0
        enriched["distance_from_prev_km"] = dist_m / 1000.0
        stops_enriched.append(enriched)

    # ── 2. N×N travel_time_matrix (dakika) — OR-Tools için ───────────────────
    # Mapbox matrisinin satır/sütun 1..N kısmını al, saniyeyi dakikaya çevir.
    # Bu tam NxN gerçek seyahat süreleri — OR-Tools tüm geçişler için bunu kullanır.
    travel_time_matrix: list[list[float]] = []
    for row_mapbox in range(1, expected_size):          # 1..N (delivery stops)
        row: list[float] = []
        for col_mapbox in range(1, expected_size):      # 1..N (delivery stops)
            row.append(duration_matrix_sec[row_mapbox][col_mapbox] / 60.0)
        travel_time_matrix.append(row)

    return stops_enriched, travel_time_matrix
