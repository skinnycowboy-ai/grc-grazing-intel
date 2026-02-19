# airflow/dags/grazing_intel_dag.py
"""Docs-first Airflow scheduling pattern (minimal stub).

This repo does not ship a full Airflow runtime; this DAG is illustrative:
- idempotent task boundaries (ingest / compute / monitor)
- explicit parameters (boundary_id, herd_config_id)
- exit codes from `monitor` map cleanly to alerting.

Assume the Airflow worker has access to:
- a Python environment with this package installed
- a writable volume mounted at /data containing pipeline.db
"""

from __future__ import annotations

from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator

BOUNDARY_ID = "boundary_north_paddock_3"
HERD_CONFIG_ID = "6400725295db666946d63535"
DB_PATH = "/data/pipeline.db"

with DAG(
    dag_id="grc_grazing_intel",
    start_date=datetime(2024, 1, 1),
    schedule="@daily",
    catchup=False,
    tags=["grc", "grazing"],
) as dag:
    ingest = BashOperator(
        task_id="ingest",
        bash_command=(
            "python -m grc_pipeline.cli ingest "
            f"--db {DB_PATH} "
            "--boundary-geojson sample_boundary.geojson "
            f"--boundary-id {BOUNDARY_ID} "
            "--boundary-crs EPSG:4326 "
            "--herds-json sample_herds_pasturemap.json "
            "--start {{ macros.ds_add(ds, -30) }} "
            "--end {{ ds }}"
        ),
    )

    compute = BashOperator(
        task_id="compute",
        bash_command=(
            "python -m grc_pipeline.cli compute "
            f"--db {DB_PATH} "
            f"--boundary-id {BOUNDARY_ID} "
            f"--herd-config-id {HERD_CONFIG_ID} "
            "--as-of {{ ds }}"
        ),
    )

    monitor = BashOperator(
        task_id="monitor",
        bash_command=(
            "python -m grc_pipeline.cli monitor "
            f"--db {DB_PATH} "
            f"--boundary-id {BOUNDARY_ID} "
            "--end {{ ds }} "
            "--window-days 30"
        ),
    )

    ingest >> compute >> monitor
