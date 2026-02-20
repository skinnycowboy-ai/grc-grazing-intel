# Task 01 — Data Ingestion (Reproducible, idempotent, schedulable)

This task demonstrates a reproducible ingestion + join process for a **boundary GeoJSON** and a **timeframe**.

## Inputs

- Boundary geometry: `sample_boundary*.geojson` (Polygon / MultiPolygon)
- Timeframe: `--start YYYY-MM-DD --end YYYY-MM-DD` (inclusive calendar range)
- Reference datasets (bundled): `pasture_reference.db`
  - NRCS gSSURGO soil rows (static by boundary)
  - RAP biomass composites (sparse time-series by boundary)
- Live dataset:
  - Open‑Meteo daily forecast/historical weather (daily rows)

## CRS alignment (coordinate system handling)

Canonical internal CRS: **EPSG:4326**.

`ingest` accepts an input CRS and normalizes geometry to EPSG:4326 before persisting.

- Default assumes EPSG:4326.
- For projected GeoJSON (UTM/etc), pass `--boundary-crs EPSG:xxxx`.

Implementation notes (where):

- Boundary loader: `src/grc_pipeline/ingest/boundary.py`
- CLI entry: `python -m grc_pipeline.cli ingest ... --boundary-crs EPSG:4326`
- Validations:
  - geometry type checks (Polygon/MultiPolygon)
  - bounds plausibility for WGS84
  - best-effort repair: `buffer(0)` for invalid rings where possible

## Temporal joins (static + time-series)

Materialized artifact:

- `boundary_daily_features(boundary_id, feature_date, ...)`

Join semantics for each day `feature_date` in `[start, end]`:

1) **Weather (Open‑Meteo)**

- Exact join: `weather_forecasts.forecast_date == feature_date`
- Missing any day → DQ failure (`daily_features_complete`)

1) **RAP biomass (composites)**

- **As-of join**: pick latest composite where `composite_date <= feature_date`
- No interpolation; composites are treated as “most recent known” state.
- If RAP is missing for **all** days in the timeframe → DQ failure (`rap_present` / `daily_features_complete`)

1) **Soil (gSSURGO)**

- Static summary by boundary (simple mean across relevant rows/fields)
- Note: simplified for take-home; production would be area-weighted aggregation by component.

Implementation notes (where):

- Feature materialization: `src/grc_pipeline/ingest/features.py`
- CLI calls feature materialization after weather + herd ingestion.

## Idempotency & schedulability

The ingestion boundary is designed to be safe to rerun for the same boundary/timeframe.

### Partition-replace behavior

- Weather: partition replace per `(boundary_id, source_version, [start,end])`
- Daily features: partition replace per `(boundary_id, [start,end])`

This guarantees:

- Rerunning `ingest` produces the same joined features for the same inputs.
- Backfills are deterministic: run `ingest` for a different timeframe; only those partitions are rebuilt.

### Herd configuration idempotency

Herd configs are upserted with stable/deterministic IDs so the same JSON input produces the same herd identity across reruns.

Where:

- Stable herd id: `src/grc_pipeline/cli.py` (`_stable_herd_id(...)`)
- Storage: `herd_configurations`

### Schedulability

The ingestion boundary maps cleanly to a scheduler task:

- Inputs: `{boundary_geojson, boundary_id, boundary_crs, start, end, herds_json}`
- Output: populated partitions + recorded `ingestion_runs` row + `data_quality_checks` rows
- Failure is explicit: `ingestion_runs.status = failed` and the CLI exits non-zero.

Airflow pattern:

- See `airflow/dags/grazing_intel_dag.py` for a docs-first DAG showing `ingest` as a parameterized task.

## Smoke test (local)

```bash
rm -rf out && mkdir -p out
cp pasture_reference.db out/pipeline_smoke.db

DB="out/pipeline_smoke.db"
BID="boundary_north_paddock_3"
START="2024-01-01"
END="2024-12-31"

python -m grc_pipeline.cli ingest \
  --db "$DB" \
  --boundary-geojson sample_boundary.geojson \
  --boundary-id "$BID" \
  --boundary-crs EPSG:4326 \
  --herds-json sample_herds_pasturemap.json \
  --start "$START" \
  --end "$END"

sqlite3 "$DB" "select status,records_ingested from ingestion_runs order by started_at desc limit 1;"
sqlite3 "$DB" "select count(*) from boundary_daily_features where boundary_id='$BID' and feature_date between '$START' and '$END';"
```

Expected:

- ingestion status `succeeded` or `succeeded_with_warnings`
- row count `366` for 2024 (leap year)
