# src/grc_pipeline/ingest/features.py
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..store.db import exec_one
from ..timeutil import utc_now_iso


FEATURES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS boundary_daily_features (
  boundary_id TEXT NOT NULL,
  feature_date TEXT NOT NULL,

  -- RAP (as-of composite <= feature_date)
  rap_composite_date TEXT,
  rap_biomass_kg_per_ha REAL,
  rap_source_version TEXT,

  -- Open-Meteo daily (exact date join)
  weather_precipitation_mm REAL,
  weather_temp_max_c REAL,
  weather_temp_min_c REAL,
  weather_wind_speed_kmh REAL,
  weather_source_version TEXT,

  -- Soil (static summary join)
  soil_productivity_index_mean REAL,
  soil_available_water_capacity_mean REAL,
  soil_source_version TEXT,

  -- Boundary static
  area_ha REAL,

  created_at TEXT NOT NULL,

  PRIMARY KEY (boundary_id, feature_date)
);

CREATE INDEX IF NOT EXISTS idx_features_boundary_date
  ON boundary_daily_features(boundary_id, feature_date);
"""


@dataclass(frozen=True)
class MaterializeResult:
    inserted: int
    missing_weather_days: int
    missing_rap_days: int


def materialize_boundary_daily_features(
    conn: sqlite3.Connection,
    *,
    boundary_id: str,
    start: str,
    end: str,
    weather_source_version: str,
    created_at: str | None = None,
) -> MaterializeResult:
    """
    Materialize a daily joined feature table for a boundary + timeframe.

    Idempotency/backfill strategy:
      - partition replace: delete the (boundary_id, date-range) slice, then insert rebuilt rows.
    """
    if created_at is None:
        created_at = utc_now_iso()

    conn.executescript(FEATURES_SCHEMA_SQL)

    b = exec_one(conn, "SELECT area_ha FROM geographic_boundaries WHERE boundary_id=?", (boundary_id,))
    if not b:
        raise ValueError(f"Unknown boundary_id: {boundary_id}")
    area_ha = float(b["area_ha"] or 0.0)

    soil_stats = exec_one(
        conn,
        """
        SELECT
          AVG(productivity_index) AS pi_mean,
          AVG(available_water_capacity) AS awc_mean
        FROM nrcs_soil_data
        WHERE boundary_id=?
        """,
        (boundary_id,),
    )
    soil_pi_mean = float(soil_stats["pi_mean"]) if soil_stats and soil_stats["pi_mean"] is not None else None
    soil_awc_mean = float(soil_stats["awc_mean"]) if soil_stats and soil_stats["awc_mean"] is not None else None

    soil_ver = exec_one(
        conn,
        """
        SELECT source_version
        FROM nrcs_soil_data
        WHERE boundary_id=?
        ORDER BY ingested_at DESC
        LIMIT 1
        """,
        (boundary_id,),
    )
    soil_source_version = soil_ver["source_version"] if soil_ver else None

    # Partition replace (idempotent)
    conn.execute(
        """
        DELETE FROM boundary_daily_features
        WHERE boundary_id=? AND feature_date BETWEEN ? AND ?
        """,
        (boundary_id, start, end),
    )

    cur = conn.execute(
        """
        WITH RECURSIVE dates(d) AS (
          SELECT date(?)
          UNION ALL
          SELECT date(d, '+1 day') FROM dates WHERE d < date(?)
        )
        SELECT
          d AS feature_date,

          -- RAP (as-of)
          (
            SELECT composite_date
            FROM rap_biomass
            WHERE boundary_id=? AND composite_date <= d
            ORDER BY composite_date DESC
            LIMIT 1
          ) AS rap_composite_date,
          (
            SELECT biomass_kg_per_ha
            FROM rap_biomass
            WHERE boundary_id=? AND composite_date <= d
            ORDER BY composite_date DESC
            LIMIT 1
          ) AS rap_biomass_kg_per_ha,
          (
            SELECT source_version
            FROM rap_biomass
            WHERE boundary_id=? AND composite_date <= d
            ORDER BY composite_date DESC
            LIMIT 1
          ) AS rap_source_version,

          -- Weather (exact day)
          (
            SELECT precipitation_mm
            FROM weather_forecasts
            WHERE boundary_id=? AND source_version=? AND forecast_date=d
            LIMIT 1
          ) AS weather_precipitation_mm,
          (
            SELECT temp_max_c
            FROM weather_forecasts
            WHERE boundary_id=? AND source_version=? AND forecast_date=d
            LIMIT 1
          ) AS weather_temp_max_c,
          (
            SELECT temp_min_c
            FROM weather_forecasts
            WHERE boundary_id=? AND source_version=? AND forecast_date=d
            LIMIT 1
          ) AS weather_temp_min_c,
          (
            SELECT wind_speed_kmh
            FROM weather_forecasts
            WHERE boundary_id=? AND source_version=? AND forecast_date=d
            LIMIT 1
          ) AS weather_wind_speed_kmh

        FROM dates
        """,
        (
            start,
            end,
            boundary_id,
            boundary_id,
            boundary_id,
            boundary_id,
            weather_source_version,
            boundary_id,
            weather_source_version,
            boundary_id,
            weather_source_version,
            boundary_id,
            weather_source_version,
        ),
    )

    inserted = 0
    missing_weather = 0
    missing_rap = 0

    for r in cur.fetchall():
        rap_missing = r["rap_biomass_kg_per_ha"] is None
        weather_missing = (
            r["weather_precipitation_mm"] is None
            and r["weather_temp_max_c"] is None
            and r["weather_temp_min_c"] is None
            and r["weather_wind_speed_kmh"] is None
        )

        if rap_missing:
            missing_rap += 1
        if weather_missing:
            missing_weather += 1

        conn.execute(
            """
            INSERT INTO boundary_daily_features(
              boundary_id, feature_date,
              rap_composite_date, rap_biomass_kg_per_ha, rap_source_version,
              weather_precipitation_mm, weather_temp_max_c, weather_temp_min_c, weather_wind_speed_kmh, weather_source_version,
              soil_productivity_index_mean, soil_available_water_capacity_mean, soil_source_version,
              area_ha, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                boundary_id,
                r["feature_date"],
                r["rap_composite_date"],
                r["rap_biomass_kg_per_ha"],
                r["rap_source_version"],
                r["weather_precipitation_mm"],
                r["weather_temp_max_c"],
                r["weather_temp_min_c"],
                r["weather_wind_speed_kmh"],
                weather_source_version,
                soil_pi_mean,
                soil_awc_mean,
                soil_source_version,
                area_ha,
                created_at,
            ),
        )
        inserted += 1

    return MaterializeResult(inserted=inserted, missing_weather_days=missing_weather, missing_rap_days=missing_rap)
