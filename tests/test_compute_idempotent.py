import json
import sqlite3
from pathlib import Path

from grc_pipeline.cli import compute


def _mk_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(
            """
            CREATE TABLE geographic_boundaries (
              boundary_id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              geometry_geojson TEXT NOT NULL,
              area_ha REAL,
              crs TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE herd_configurations (
              id TEXT PRIMARY KEY,
              ranch_id TEXT NOT NULL,
              pasture_id TEXT,
              boundary_id TEXT,
              animal_count INTEGER NOT NULL,
              daily_intake_kg_per_head REAL NOT NULL,
              config_snapshot_json TEXT,
              valid_from TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE boundary_daily_features (
              boundary_id TEXT NOT NULL,
              feature_date TEXT NOT NULL,
              rap_composite_date TEXT,
              rap_biomass_kg_per_ha REAL,
              rap_source_version TEXT,
              weather_source_version TEXT,
              soil_source_version TEXT,
              area_ha REAL,
              created_at TEXT NOT NULL,
              PRIMARY KEY (boundary_id, feature_date)
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
              config_version TEXT,
              input_data_versions_json TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE model_versions (
              version_id TEXT PRIMARY KEY,
              description TEXT,
              parameters_json TEXT,
              deployed_at TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )

        conn.execute(
            "INSERT INTO geographic_boundaries(boundary_id,name,geometry_geojson,area_ha,crs,created_at) VALUES (?,?,?,?,?,?)",
            ("b1", "B1", "{}", 10.0, "EPSG:4326", "2024-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO herd_configurations(id,ranch_id,pasture_id,boundary_id,animal_count,daily_intake_kg_per_head,config_snapshot_json,valid_from,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "h1",
                "r1",
                "p1",
                "b1",
                10,
                5.0,
                json.dumps({"effective_date": "2024-01-01"}),
                "2024-01-01",
                "2024-01-01T00:00:00Z",
            ),
        )
        conn.execute(
            "INSERT INTO boundary_daily_features(boundary_id,feature_date,rap_composite_date,rap_biomass_kg_per_ha,rap_source_version,weather_source_version,soil_source_version,area_ha,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "b1",
                "2024-01-01",
                "2024-01-01",
                100.0,  # kg/ha
                "rap:v1",
                "openmeteo:v1",
                "gssurgo:v1",
                10.0,  # ha
                "2024-01-01T00:00:00Z",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_compute_is_idempotent(tmp_path: Path):
    db = tmp_path / "t.db"
    _mk_db(db)

    out_dir = tmp_path / "manifests"

    # Run compute twice with identical inputs.
    compute(
        db=str(db),
        boundary_id="b1",
        herd_config_id="h1",
        as_of="2024-01-01",
        logic_version="days_remaining:v1",
        manifest_out=str(out_dir),
    )
    compute(
        db=str(db),
        boundary_id="b1",
        herd_config_id="h1",
        as_of="2024-01-01",
        logic_version="days_remaining:v1",
        manifest_out=str(out_dir),
    )

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM grazing_recommendations").fetchone()["n"]
        assert n == 1

        row = conn.execute("SELECT * FROM grazing_recommendations LIMIT 1").fetchone()
        assert row["recommended_move_date"] == "2024-01-21"  # 100*10 / (10*5) = 20 days
    finally:
        conn.close()
