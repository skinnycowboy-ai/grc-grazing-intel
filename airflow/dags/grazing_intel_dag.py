from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="grazing_intel_daily",
    start_date=datetime(2024, 1, 1),
    schedule="@daily",
    catchup=False,
    default_args={"retries": 1},
) as dag:
    ingest = BashOperator(
        task_id="ingest",
        bash_command=(
            "python -m grc_pipeline.cli ingest "
            "--db pasture_reference.db "
            "--boundary-geojson sample_boundary.geojson "
            "--herds-json sample_herds_pasturemap.json "
            "--start 2024-01-01 --end 2024-12-31 "
            "--boundary-id boundary_north_paddock_3"
        ),
    )

    compute = BashOperator(
        task_id="compute",
        bash_command=(
            "python -m grc_pipeline.cli compute "
            "--db pasture_reference.db "
            "--boundary-id boundary_north_paddock_3 "
            "--herd-config-id herd_ranch_001_paddock_3_0 "
            "--as-of 2024-03-15"
        ),
    )

    ingest >> compute
