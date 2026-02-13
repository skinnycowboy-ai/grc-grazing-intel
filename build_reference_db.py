#!/usr/bin/env python3
import sqlite3
import json
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path(__file__).resolve().parent / "pasture_reference.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

BOUNDARY_FILES = [
    ("sample_boundary.geojson", "boundary_north_paddock_3"),
    ("sample_boundary_south.geojson", "boundary_south_paddock_1"),
    ("sample_boundary_east.geojson", "boundary_east_paddock_2"),
]

BOUNDARY_CENTROIDS = {
    "boundary_north_paddock_3": (40.586, -105.08),
    "boundary_south_paddock_1": (40.575, -105.082),
    "boundary_east_paddock_2": (40.5845, -105.069),
}

PASTURE_TO_BOUNDARY = {
    "paddock_3": "boundary_north_paddock_3",
    "paddock_south_1": "boundary_south_paddock_1",
    "paddock_east_2": "boundary_east_paddock_2",
}


def load_geojson(geojson_path: Path):
    with open(geojson_path) as f:
        return json.load(f)


def _nrcs_rows(boundary_id: str, now: str, variant: str):
    if variant == "north":
        return [
            (boundary_id, "12345", "Loam", 0.72, "II", "B", 18.5, "gSSURGO_2024", now),
            (boundary_id, "12346", "Silt loam", 0.68, "II", "B", 20.2, "gSSURGO_2024", now),
        ]
    if variant == "south":
        return [
            (boundary_id, "22301", "Clay loam", 0.65, "III", "C", 22.0, "gSSURGO_2024", now),
            (boundary_id, "22302", "Sandy loam", 0.58, "III", "B", 14.5, "gSSURGO_2024", now),
            (boundary_id, "22303", "Loam", 0.70, "II", "B", 19.0, "gSSURGO_2024", now),
        ]
    return [
        (boundary_id, "32310", "Silt loam", 0.74, "II", "B", 21.0, "gSSURGO_2024", now),
        (boundary_id, "32311", "Loam", 0.69, "II", "B", 17.8, "gSSURGO_2024", now),
    ]


def _rap_rows(boundary_id: str, now: str, area_ha: float):
    base = datetime(2024, 1, 1)
    rows = []
    for i in range(23):
        d = (base + timedelta(days=i * 16)).strftime("%Y-%m-%d")
        month = int(d[5:7])
        biomass = 800 + 400 * (month - 1) if month <= 6 else 2800 - 200 * (month - 6)
        biomass = max(400, min(2800, biomass + (i % 5 - 2) * 50))
        biomass = biomass * (0.92 + (hash(boundary_id) % 10) / 100.0)
        cover = min(95, 15 + month * 4 + (i % 3) * 2)
        ndvi = 0.3 + (month / 12) * 0.5 + (i % 4) * 0.02
        ndvi = min(0.85, ndvi)
        rows.append((boundary_id, d, round(biomass, 1), round(cover, 1), round(ndvi, 3), "RAP_2024", now))
    return rows


def _weather_rows(boundary_id: str, now: str, start_date: datetime, num_days: int = 14):
    lat, lon = BOUNDARY_CENTROIDS.get(boundary_id, (40.58, -105.08))
    rows = []
    for i in range(num_days):
        d = (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
        precip = 0 if i % 4 else round(5 + (i % 3) * 8, 1)
        temp_max = 12 + (i % 7) + (i // 7) * 2
        temp_min = temp_max - 8
        wind = 10 + (i % 5) * 2
        rows.append((boundary_id, d, lat, lon, precip, temp_max, temp_min, wind, "OpenMeteo_v1", now))
    return rows


def main():
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())

    ref_dir = Path(__file__).resolve().parent
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    conn.executemany(
        """INSERT INTO model_versions (version_id, description, parameters_json, deployed_at, deprecated_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            ("days_remaining_v1", "Available forage / daily consumption", '{"utilization_pct": 50}', "2024-01-15T00:00:00Z", None, "2024-01-10T00:00:00Z"),
            ("days_remaining_v2", "Same with weather stress factor", '{"utilization_pct": 50, "weather_stress_factor": 0.95}', "2024-06-01T00:00:00Z", None, "2024-05-20T00:00:00Z"),
            ("config_2024q1", "Q1 2024 herd and boundary config", "{}", "2024-01-01T00:00:00Z", None, "2023-12-15T00:00:00Z"),
        ],
    )

    variants = ["north", "south", "east"]
    for (filename, boundary_id), variant in zip(BOUNDARY_FILES, variants):
        geo_path = ref_dir / filename
        if not geo_path.exists():
            continue
        geo = load_geojson(geo_path)
        conn.execute(
            """INSERT INTO geographic_boundaries
               (boundary_id, name, ranch_id, pasture_id, geometry_geojson, area_ha, crs, created_at, source_file)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                boundary_id,
                geo["properties"]["name"],
                geo["properties"]["ranch_id"],
                geo["properties"]["pasture_id"],
                json.dumps(geo["geometry"]),
                geo["properties"]["area_ha"],
                "EPSG:4326",
                now,
                filename,
            ),
        )

        nrcs = _nrcs_rows(boundary_id, now, variant)
        conn.executemany(
            """INSERT INTO nrcs_soil_data
               (boundary_id, mukey, component_name, productivity_index, land_capability_class, hydrologic_group, available_water_capacity, source_version, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            nrcs,
        )

        area_ha = geo["properties"]["area_ha"]
        rap_rows = _rap_rows(boundary_id, now, area_ha)
        conn.executemany(
            """INSERT INTO rap_biomass
               (boundary_id, composite_date, biomass_kg_per_ha, annual_herbaceous_cover_pct, ndvi, source_version, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rap_rows,
        )

        for start in (datetime(2024, 3, 1), datetime(2024, 6, 1)):
            weather_rows = _weather_rows(boundary_id, now, start, 14)
            conn.executemany(
                """INSERT INTO weather_forecasts
                   (boundary_id, forecast_date, latitude, longitude, precipitation_mm, temp_max_c, temp_min_c, wind_speed_kmh, source_version, ingested_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                weather_rows,
            )

    with open(ref_dir / "sample_herds_pasturemap.json") as f:
        herds_list = json.load(f)

    herd_config_ids = []
    for idx, herd in enumerate(herds_list):
        h = herd["herd"]
        pasture_id = herd["pasture_id"]
        boundary_id = PASTURE_TO_BOUNDARY.get(pasture_id)
        if not boundary_id:
            continue
        herd_config_id = f"herd_{herd['operation_id']}_{pasture_id}_{idx}"
        herd_config_ids.append((herd_config_id, boundary_id))
        conn.execute(
            """INSERT INTO herd_configurations
               (id, ranch_id, pasture_id, boundary_id, animal_count, animal_type, daily_intake_kg_per_head, avg_daily_gain_kg, config_snapshot_json, valid_from, valid_to, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                herd_config_id,
                herd["operation_id"],
                pasture_id,
                boundary_id,
                h["animal_count"],
                h["animal_type"],
                h["daily_intake_kg_per_head"],
                h.get("average_daily_gain_kg"),
                json.dumps(herd),
                herd["effective_date"],
                None,
                now,
            ),
        )

    runs = [
        ("run_20240301_001", "boundary_north_paddock_3", "2024-01-01", "2024-12-31", "nrcs,rap,openmeteo,herd", "completed", now, now, 42, None),
        ("run_20240301_002", "boundary_south_paddock_1", "2024-01-01", "2024-12-31", "nrcs,rap,openmeteo,herd", "completed", now, now, 58, None),
        ("run_20240301_003", "boundary_east_paddock_2", "2024-01-01", "2024-12-31", "nrcs,rap,openmeteo,herd", "completed", now, now, 51, None),
        ("run_20240615_001", None, "2024-06-01", "2024-08-31", "rap,openmeteo", "failed", now, None, None, "OpenMeteo rate limit exceeded"),
    ]
    for r in runs:
        conn.execute(
            """INSERT INTO ingestion_runs
               (run_id, boundary_id, timeframe_start, timeframe_end, sources_included, status, started_at, completed_at, records_ingested, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            r,
        )

    input_versions = json.dumps({"rap": "RAP_2024", "nrcs": "gSSURGO_2024", "weather": "OpenMeteo_v1"})
    boundary_id = "boundary_north_paddock_3"
    herd_config_id = next((hcid for hcid, bid in herd_config_ids if bid == boundary_id), herd_config_ids[0][0])
    area_ha = 45.2
    available_forage_kg = area_ha * 1200
    daily_consumption = 120 * 11.5
    days_remaining = available_forage_kg / daily_consumption
    move_date = (datetime(2024, 3, 15) + timedelta(days=int(days_remaining))).strftime("%Y-%m-%d")
    conn.execute(
        """INSERT INTO grazing_recommendations
           (boundary_id, herd_config_id, calculation_date, available_forage_kg, daily_consumption_kg, days_of_grazing_remaining, recommended_move_date, model_version, config_version, input_data_versions_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (boundary_id, herd_config_id, "2024-03-15", round(available_forage_kg, 1), daily_consumption, round(days_remaining, 1), move_date, "days_remaining_v1", "config_2024q1", input_versions, now),
    )
    boundary_id = "boundary_south_paddock_1"
    herd_config_id = next((hcid for hcid, bid in herd_config_ids if bid == boundary_id), None)
    if herd_config_id:
        area_ha = 62.8
        available_forage_kg = area_ha * 1100
        daily_consumption = 85 * 14.0
        days_remaining = available_forage_kg / daily_consumption
        move_date = (datetime(2024, 5, 20) + timedelta(days=int(days_remaining))).strftime("%Y-%m-%d")
        conn.execute(
            """INSERT INTO grazing_recommendations
               (boundary_id, herd_config_id, calculation_date, available_forage_kg, daily_consumption_kg, days_of_grazing_remaining, recommended_move_date, model_version, config_version, input_data_versions_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (boundary_id, herd_config_id, "2024-05-20", round(available_forage_kg, 1), daily_consumption, round(days_remaining, 1), move_date, "days_remaining_v1", "config_2024q1", input_versions, now),
        )
    boundary_id = "boundary_east_paddock_2"
    herd_config_id = next((hcid for hcid, bid in herd_config_ids if bid == boundary_id), None)
    if herd_config_id:
        area_ha = 38.1
        available_forage_kg = area_ha * 1300
        daily_consumption = 45 * 13.0
        days_remaining = available_forage_kg / daily_consumption
        move_date = (datetime(2024, 4, 10) + timedelta(days=int(days_remaining))).strftime("%Y-%m-%d")
        conn.execute(
            """INSERT INTO grazing_recommendations
               (boundary_id, herd_config_id, calculation_date, available_forage_kg, daily_consumption_kg, days_of_grazing_remaining, recommended_move_date, model_version, config_version, input_data_versions_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (boundary_id, herd_config_id, "2024-04-10", round(available_forage_kg, 1), daily_consumption, round(days_remaining, 1), move_date, "days_remaining_v2", "config_2024q1", input_versions, now),
        )

    dq_rows = [
        ("run_20240301_001", "nrcs_response_complete", "ingestion", 1, '{"records": 2}', now),
        ("run_20240301_001", "rap_no_stale", "freshness", 1, '{"latest_date": "2024-09-17"}', now),
        ("run_20240301_001", "herd_config_valid", "validation", 1, '{"animal_count": 120}', now),
        ("run_20240301_002", "nrcs_response_complete", "ingestion", 1, '{"records": 3}', now),
        ("run_20240301_002", "rap_no_stale", "freshness", 1, '{"latest_date": "2024-09-17"}', now),
        ("run_20240301_002", "herd_config_valid", "validation", 1, '{"animal_count": 85}', now),
        ("run_20240301_003", "nrcs_response_complete", "ingestion", 1, '{"records": 2}', now),
        ("run_20240301_003", "rap_no_stale", "freshness", 1, '{"latest_date": "2024-09-17"}', now),
        ("run_20240301_003", "herd_config_valid", "validation", 1, '{"animal_count": 45}', now),
        ("run_20240615_001", "openmeteo_available", "ingestion", 0, '{"error": "rate limit exceeded"}', now),
        ("run_20240615_001", "rap_no_stale", "freshness", 1, '{"latest_date": "2024-08-01"}', now),
    ]
    conn.executemany(
        """INSERT INTO data_quality_checks (run_id, check_name, check_type, passed, details_json, checked_at) VALUES (?, ?, ?, ?, ?, ?)""",
        dq_rows,
    )

    conn.commit()
    conn.close()

    boundaries_used = [bid for _, bid in BOUNDARY_FILES]
    print(f"Created {DB_PATH} with reference data.")
    print(f"  Boundaries: {len(boundaries_used)} ({', '.join(boundaries_used)})")
    print(f"  Herd configs: {len(herd_config_ids)}")
    print("Use this DB as the target shape for your pipeline and for reproducing recommendations.")


if __name__ == "__main__":
    main()
