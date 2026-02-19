# Runbook

This runbook documents how to operate the pipeline locally.

## Quick start

```bash
# 1) Start from the provided reference DB
cp pasture_reference.db out/pipeline.db

# 2) Ingest boundary + weather + features
python -m grc_pipeline.cli ingest \
  --boundary-geojson data/boundary.geojson \
  --boundary-id pasture_demo \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --herds-json data/herds.json \
  --db out/pipeline.db

# 3) Compute a recommendation (idempotent upsert)
python -m grc_pipeline.cli compute \
  --db out/pipeline.db \
  --boundary-id pasture_demo \
  --herd-config-id herd_default \
  --as-of 2024-12-15

# 4) Monitor output quality (label-free)
python -m grc_pipeline.cli monitor \
  --db out/pipeline.db \
  --boundary-id pasture_demo \
  --window-end 2024-12-31 \
  --lookback-days 30 \
  --fail-severity warn
```

## Day-2 operations

### Ingestion failures

If `ingest` exits non-zero, inspect the ingestion run + DQ checks.

```sql
SELECT run_id, status, created_at, finished_at, error_message
FROM ingestion_runs
ORDER BY created_at DESC
LIMIT 5;

SELECT run_id, check_name, check_type, passed, details_json, created_at
FROM data_quality_checks
WHERE run_id = '<RUN_ID>'
ORDER BY id ASC;
```

Hard-fail checks (exit code 2):
- `herd_config_valid`
- `rap_present`
- `soil_present`
- `weather_response_complete`
- `daily_features_complete`

Warnings (ingestion status = `succeeded_with_warnings`):
- `weather_fresh_enough`
- `rap_fresh_enough`

### Output monitoring alerts

`monitor` evaluates the trailing window of recommendations and records:
- aggregate metrics
- per-alert rows with severity
- a JSON manifest under `out/monitoring/<boundary>/<window_end>_<sha>.json`

Inspect the latest monitoring run:

```sql
SELECT run_id, status, window_start, window_end, created_at
FROM monitoring_runs
WHERE boundary_id='pasture_demo'
ORDER BY created_at DESC
LIMIT 5;

SELECT severity, alert_name, message, details_json
FROM monitoring_alerts
WHERE run_id = '<RUN_ID>'
ORDER BY id ASC;
```

Common alerts:
- `no_recommendations` (CRIT): nothing computed in the window.
- `elevated_zero_days_remaining` / `too_many_zero_days_remaining` (WARN/CRIT): outputs collapsing to 0.
- `some_out_of_range` / `too_many_out_of_range` (WARN/CRIT): guardrail violations.
- `rap_stale_p95` / `rap_too_stale_p95` (WARN/CRIT): staleness proxy from provenance.
- `output_drift_warning` / `output_drift_critical` (WARN/CRIT): mean output drift vs prior window.

### Escalation policy (suggested)

This repo does not ship paging/notifications. The operational intent is:

- `monitor` exits `0` for OK.
- `monitor` exits `1` for WARN if `--fail-severity warn`.
- `monitor` exits `2` for CRIT.

In Airflow:
- WARN: alert the on-call (Slack/email) and create a ticket.
- CRIT: page, and block downstream steps that consume the outputs.

## Data retention / lineage

- Raw reference data (RAP + soil) lives in `pasture_reference.db` (provided)
- Live fetched data (Open-Meteo) is persisted in `out/pipeline.db`
- Compute artifacts write a deterministic manifest to `out/manifests/<boundary>/...`
- Monitoring artifacts write a deterministic manifest to `out/monitoring/<boundary>/...`
