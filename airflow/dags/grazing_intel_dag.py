"""airflow/dags/grazing_intel_dag.py

Docs-first Airflow scheduling pattern (minimal stub).

This repo does **not** ship a full Airflow runtime; this DAG is illustrative.
It demonstrates how you'd schedule the CLI boundaries in a production
orchestrator with sane, testable run semantics:

- idempotent task boundaries (`ingest` / `compute` / `monitor`)
- deterministic run windows (Airflow logical date `ds`)
- retry semantics owned by the orchestrator (Airflow retries)

What “daily Open‑Meteo” means here:
- Open‑Meteo is fetched **live** inside `grc_pipeline.cli ingest`.
- You only get a daily refresh if this DAG (or a cron job) actually runs daily.

Assumptions (worker image / env):
- Python env has this package installed.
- A writable volume is mounted at /data.
- The SQLite DB exists at /data/pipeline.db (bootstrap it once from the repo’s
  `pasture_reference.db`, as shown in README quickstart).

"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

# For a real deployment, you'd typically source these from Airflow Variables,
# a config file, or a per-boundary dynamic mapping pattern. Keep static here
# for the take-home.
BOUNDARY_ID = "boundary_north_paddock_3"
HERD_CONFIG_ID = "6400725295db666946d63535"
DB_PATH = "/data/pipeline.db"

DEFAULT_ARGS = {
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}


def _ensure_db_cmd(db_path: str) -> str:
    # Fail fast with a helpful message if the DB isn't present.
    return (
        f"test -f {db_path} "
        f"|| (echo 'ERROR: missing {db_path}. Bootstrap once by copying pasture_reference.db -> {db_path}.' && exit 2)"
    )


with DAG(
    dag_id="grc_grazing_intel",
    start_date=datetime(2024, 1, 1),
    schedule="@daily",
    catchup=False,
    default_args=DEFAULT_ARGS,
    tags=["grc", "grazing"],
) as dag:
    # Ingest a rolling window ending on `ds` (Airflow logical date).
    # This ensures Open‑Meteo is fetched live on every daily run.
    ingest = BashOperator(
        task_id="ingest",
        bash_command=(
            f"{_ensure_db_cmd(DB_PATH)} && "
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
            f"{_ensure_db_cmd(DB_PATH)} && "
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
            f"{_ensure_db_cmd(DB_PATH)} && "
            "python -m grc_pipeline.cli monitor "
            f"--db {DB_PATH} "
            f"--boundary-id {BOUNDARY_ID} "
            "--end {{ ds }} "
            "--window-days 30"
        ),
    )

    ingest >> compute >> monitor
