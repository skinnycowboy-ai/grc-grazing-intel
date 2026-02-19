# tests/test_task6_versioning.py
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from grc_pipeline.cli import compute
from grc_pipeline.store.manifest import read_manifest

MIN_SCHEMA = """
CREATE TABLE geographic_boundaries (
  boundary_id TEXT PRIMARY KEY,
  name TEXT,
  ranch_id TEXT,
  pasture_id TEXT,
  geometry_geojson TEXT NOT NULL,
  area_ha REAL,
  crs TEXT,
  created_at TEXT,
  source_file TEXT
);

CREATE TABLE herd_configurations (
  id TEXT PRIMARY KEY,
  boundary_id TEXT,
  ranch_id TEXT,
  pasture_id TEXT,
  animal_type TEXT,
  animal_count INTEGER,
  daily_intake_kg_per_head REAL,
  config_snapshot_json TEXT NOT NULL,
  source_file TEXT,
  ingested_at TEXT
);

CREATE TABLE rap_biomass (
  boundary_id TEXT NOT NULL,
  composite_date TEXT NOT NULL,
  biomass_kg_per_ha REAL,
  source_version TEXT,
  ingested_at TEXT,
  PRIMARY KEY (boundary_id, composite_date)
);

CREATE TABLE boundary_daily_features (
  boundary_id TEXT NOT NULL,
  feature_date TEXT NOT NULL,
  rap_composite_date TEXT,
  rap_biomass_kg_per_ha REAL,
  rap_source_version TEXT,
  weather_precipitation_mm REAL,
  weather_temp_max_c REAL,
  weather_temp_min_c REAL,
  weather_wind_speed_kmh REAL,
  weather_source_version TEXT,
  soil_productivity_index_mean REAL,
  soil_available_water_capacity_mean REAL,
  soil_source_version TEXT,
  area_ha REAL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (boundary_id, feature_date)
);

CREATE TABLE model_versions (
  version_id TEXT PRIMARY KEY,
  description TEXT,
  parameters_json TEXT,
  deployed_at TEXT,
  created_at TEXT
);

CREATE TABLE grazing_recommendations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  boundary_id TEXT NOT NULL,
  herd_config_id TEXT NOT NULL,
  calculation_date TEXT NOT NULL,
  available_forage_kg REAL,
  daily_consumption_kg REAL,
  days_of_grazing_remaining REAL,
  recommended_move_date TEXT,
  model_version TEXT NOT NULL,
  config_version TEXT NOT NULL,
  input_data_versions_json TEXT,
  created_at TEXT
);
"""


def _exec(db: Path, sql: str, params=()):
    with sqlite3.connect(str(db)) as conn:
        conn.execute(sql, params)
        conn.commit()


def _one(db: Path, sql: str, params=()):
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, params)
        return cur.fetchone()


def test_task6_compute_is_immutable_and_manifest_is_stable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    db = tmp_path / "pipeline.db"

    with sqlite3.connect(str(db)) as conn:
        conn.executescript(MIN_SCHEMA)
        conn.commit()

    boundary_id = "boundary_north_paddock_3"
    herd_id = "herd_1"
    as_of = "2024-03-15"

    _exec(
        db,
        "INSERT INTO geographic_boundaries(boundary_id,name,ranch_id,pasture_id,geometry_geojson,area_ha,crs,created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            boundary_id,
            "North Paddock 3",
            "Ranch X",
            "paddock_3",
            "{}",
            10.0,
            "EPSG:4326",
            "2026-02-19T00:00:00Z",
        ),
    )

    herd_snapshot = {"animal_count": 50, "daily_intake_kg_per_head": 10.0}
    _exec(
        db,
        "INSERT INTO herd_configurations(id,boundary_id,ranch_id,pasture_id,animal_type,animal_count,daily_intake_kg_per_head,config_snapshot_json,ingested_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            herd_id,
            boundary_id,
            "Ranch X",
            "paddock_3",
            "cattle",
            50,
            10.0,
            json.dumps(herd_snapshot),
            "2026-02-19T00:00:00Z",
        ),
    )

    _exec(
        db,
        "INSERT INTO rap_biomass(boundary_id,composite_date,biomass_kg_per_ha,source_version,ingested_at) "
        "VALUES (?,?,?,?,?)",
        (boundary_id, "2024-03-10", 1000.0, "rap:v1", "2026-02-19T00:00:00Z"),
    )

    _exec(
        db,
        "INSERT INTO boundary_daily_features(boundary_id,feature_date,rap_composite_date,rap_biomass_kg_per_ha,rap_source_version,weather_source_version,soil_source_version,area_ha,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            boundary_id,
            as_of,
            "2024-03-10",
            1000.0,
            "rap:v1",
            "openmeteo:v1",
            "soil:v1",
            10.0,
            "2026-02-19T00:00:00Z",
        ),
    )

    manifest_dir = tmp_path / "manifests"

    # First compute
    compute(
        db=str(db),
        boundary_id=boundary_id,
        herd_config_id=herd_id,
        as_of=as_of,
        manifest_out=str(manifest_dir),
    )
    out1 = capsys.readouterr().out
    j1 = json.loads(out1)

    # Second compute should be idempotent: same snapshot, no overwrite
    compute(
        db=str(db),
        boundary_id=boundary_id,
        herd_config_id=herd_id,
        as_of=as_of,
        manifest_out=str(manifest_dir),
    )
    out2 = capsys.readouterr().out
    j2 = json.loads(out2)

    assert j1["recommendation_id"] == j2["recommendation_id"]
    assert j1["snapshot_id"] == j2["snapshot_id"]
    assert j1["manifest_path"] == j2["manifest_path"]

    # Only one DB row for this version key
    r = _one(
        db,
        "SELECT COUNT(*) AS n FROM grazing_recommendations WHERE boundary_id=? AND herd_config_id=? AND calculation_date=? AND model_version=?",
        (boundary_id, herd_id, as_of, "days_remaining:v1"),
    )
    assert int(r["n"]) == 1

    # Manifest exists and is parseable
    mp = Path(j1["manifest_path"])
    assert mp.exists()
    m = read_manifest(mp)
    assert m["run_type"] == "compute_recommendation"
    assert m["idempotency_key"]["as_of"] == as_of
