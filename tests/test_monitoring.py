import json
import sqlite3

from grc_pipeline.config import PipelineConfig
from grc_pipeline.quality.monitoring import run_output_monitoring


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE grazing_recommendations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          boundary_id TEXT NOT NULL,
          herd_config_id TEXT NOT NULL,
          calculation_date TEXT NOT NULL,
          days_of_grazing_remaining REAL,
          recommended_move_date TEXT,
          input_data_versions_json TEXT
        );
        """
    )
    return conn


def test_monitor_ok_window():
    conn = _conn()
    cfg = PipelineConfig()

    # 3 recos, all sane
    for d in ["2024-12-01", "2024-12-02", "2024-12-03"]:
        payload = {"data_snapshot": {"rap": {"as_of_composite_date": "2024-11-15"}}}
        conn.execute(
            """
            INSERT INTO grazing_recommendations(
              boundary_id, herd_config_id, calculation_date,
              days_of_grazing_remaining, recommended_move_date, input_data_versions_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("b1", "h1", d, 10.0, d, json.dumps(payload)),
        )

    report = run_output_monitoring(
        conn,
        boundary_id="b1",
        start="2024-12-01",
        end="2024-12-31",
        cfg=cfg,
    )
    assert report["status"] in ("ok", "warn")  # depending on RAP thresholds
    assert report["metrics"]["n_recommendations"] == 3


def test_monitor_crit_when_no_recos():
    conn = _conn()
    cfg = PipelineConfig()

    report = run_output_monitoring(
        conn,
        boundary_id="b1",
        start="2024-12-01",
        end="2024-12-31",
        cfg=cfg,
    )
    assert report["status"] == "crit"
