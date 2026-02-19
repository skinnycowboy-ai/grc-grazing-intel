# tests/test_features.py
import sqlite3

from grc_pipeline.ingest.features import materialize_boundary_daily_features


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(
        """
        CREATE TABLE geographic_boundaries (
          boundary_id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          ranch_id TEXT,
          pasture_id TEXT,
          geometry_geojson TEXT NOT NULL,
          area_ha REAL,
          crs TEXT,
          created_at TEXT NOT NULL,
          source_file TEXT
        );

        CREATE TABLE nrcs_soil_data (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          boundary_id TEXT NOT NULL,
          productivity_index REAL,
          available_water_capacity REAL,
          source_version TEXT,
          ingested_at TEXT NOT NULL
        );

        CREATE TABLE rap_biomass (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          boundary_id TEXT NOT NULL,
          composite_date TEXT NOT NULL,
          biomass_kg_per_ha REAL,
          source_version TEXT,
          ingested_at TEXT NOT NULL
        );

        CREATE TABLE weather_forecasts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          boundary_id TEXT NOT NULL,
          forecast_date TEXT NOT NULL,
          precipitation_mm REAL,
          temp_max_c REAL,
          temp_min_c REAL,
          wind_speed_kmh REAL,
          source_version TEXT,
          ingested_at TEXT NOT NULL
        );
        """
    )
    return conn


def test_materialize_boundary_daily_features_partition_replace():
    conn = _conn()
    boundary_id = "b1"

    conn.execute(
        """
        INSERT INTO geographic_boundaries(boundary_id, name, geometry_geojson, area_ha, crs, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (boundary_id, "B1", "{}", 12.0, "EPSG:4326", "2024-01-01T00:00:00Z"),
    )

    conn.execute(
        """
        INSERT INTO nrcs_soil_data(boundary_id, productivity_index, available_water_capacity, source_version, ingested_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (boundary_id, 50.0, 0.15, "nrcs:v1", "2024-01-01T00:00:00Z"),
    )

    conn.execute(
        """
        INSERT INTO rap_biomass(boundary_id, composite_date, biomass_kg_per_ha, source_version, ingested_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (boundary_id, "2023-12-15", 100.0, "rap:v1", "2024-01-01T00:00:00Z"),
    )

    for d in ["2024-01-01", "2024-01-02", "2024-01-03"]:
        conn.execute(
            """
            INSERT INTO weather_forecasts(
              boundary_id, forecast_date, precipitation_mm, temp_max_c, temp_min_c, wind_speed_kmh, source_version, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (boundary_id, d, 1.0, 20.0, 10.0, 5.0, "openmeteo:v1", "2024-01-01T00:00:00Z"),
        )

    conn.commit()

    res = materialize_boundary_daily_features(
        conn,
        boundary_id=boundary_id,
        start="2024-01-01",
        end="2024-01-03",
        weather_source_version="openmeteo:v1",
        created_at="2024-01-01T00:00:00Z",
    )

    assert res.inserted == 3
    assert res.missing_weather_days == 0
    assert res.missing_rap_days == 0

    row = conn.execute(
        "SELECT * FROM boundary_daily_features WHERE boundary_id=? AND feature_date=?",
        (boundary_id, "2024-01-02"),
    ).fetchone()

    assert row is not None
    assert row["rap_biomass_kg_per_ha"] == 100.0
    assert row["soil_productivity_index_mean"] == 50.0
    assert row["area_ha"] == 12.0

    # Re-run same materialization to ensure partition replace is stable (no duplicates)
    res2 = materialize_boundary_daily_features(
        conn,
        boundary_id=boundary_id,
        start="2024-01-01",
        end="2024-01-03",
        weather_source_version="openmeteo:v1",
        created_at="2024-01-01T00:00:01Z",
    )
    assert res2.inserted == 3
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM boundary_daily_features WHERE boundary_id=?",
        (boundary_id,),
    ).fetchone()["n"]
    assert n == 3
