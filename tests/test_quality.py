import sqlite3

from grc_pipeline.config import PipelineConfig
from grc_pipeline.quality.checks import (
    check_rap_freshness,
    check_weather_response_complete,
)


def test_check_weather_response_complete_detects_missing_days():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE weather_forecasts(
          boundary_id TEXT,
          forecast_date TEXT,
          precipitation_mm REAL,
          temp_max_c REAL,
          temp_min_c REAL,
          wind_speed_kmh REAL,
          source_version TEXT
        )
        """
    )

    # 3-day range, but only 2 days present
    conn.execute(
        "INSERT INTO weather_forecasts VALUES (?,?,?,?,?,?,?)",
        ("b1", "2024-01-01", 0.0, 10.0, 2.0, 5.0, "openmeteo:v1"),
    )
    conn.execute(
        "INSERT INTO weather_forecasts VALUES (?,?,?,?,?,?,?)",
        ("b1", "2024-01-02", 1.0, 11.0, 3.0, 6.0, "openmeteo:v1"),
    )

    res = check_weather_response_complete(
        conn,
        boundary_id="b1",
        start="2024-01-01",
        end="2024-01-03",
        source_version="openmeteo:v1",
    )
    assert res.passed is False
    assert res.name == "weather_response_complete"


def test_check_rap_freshness_warns_on_stale_composite():
    cfg = PipelineConfig(rap_stale_days=60)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE rap_biomass(
          boundary_id TEXT,
          composite_date TEXT
        )
        """
    )

    conn.execute("INSERT INTO rap_biomass VALUES (?,?)", ("b1", "2024-01-01"))

    res = check_rap_freshness(conn, boundary_id="b1", timeframe_end="2024-12-31", cfg=cfg)
    assert res.name == "rap_fresh_enough"
    assert res.passed is False
