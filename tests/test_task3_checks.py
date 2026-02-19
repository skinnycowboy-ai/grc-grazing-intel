from __future__ import annotations

import sqlite3

from grc_pipeline.config import PipelineConfig
from grc_pipeline.quality.checks import check_rap_freshness


def test_check_rap_freshness_pass():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE rap_biomass (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          boundary_id TEXT NOT NULL,
          composite_date TEXT NOT NULL,
          biomass_kg_per_ha REAL,
          source_version TEXT,
          ingested_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO rap_biomass(boundary_id, composite_date, biomass_kg_per_ha) VALUES (?,?,?)",
        ("b1", "2024-12-01", 100.0),
    )
    cfg = PipelineConfig(rap_stale_days=60)
    res = check_rap_freshness(conn, boundary_id="b1", timeframe_end="2024-12-31", cfg=cfg)
    assert res.passed is True


def test_check_rap_freshness_fail_when_stale():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE rap_biomass (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          boundary_id TEXT NOT NULL,
          composite_date TEXT NOT NULL,
          biomass_kg_per_ha REAL,
          source_version TEXT,
          ingested_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO rap_biomass(boundary_id, composite_date, biomass_kg_per_ha) VALUES (?,?,?)",
        ("b1", "2024-01-01", 100.0),
    )
    cfg = PipelineConfig(rap_stale_days=30)
    res = check_rap_freshness(conn, boundary_id="b1", timeframe_end="2024-12-31", cfg=cfg)
    assert res.passed is False
