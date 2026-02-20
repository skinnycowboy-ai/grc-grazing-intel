# Task 03 — Validation & Monitoring (no labels required)

This task implements:

1) ingestion-time data quality checks (hard fails + warnings)
2) label-free monitoring of output quality over time
3) explicit escalation logic via exit codes (scheduler-friendly)

## Ingestion data quality checks

Checks run during `ingest` and are recorded in:

- `data_quality_checks` (per check, per run)
- summarized in `ingestion_runs.status`

Implemented checks (examples):

- `herd_config_valid`
  - `animal_count > 0`
  - `daily_intake_kg_per_head > 0`
- `rap_present`
  - RAP rows exist for this boundary
- `rap_fresh_enough`
  - latest RAP composite is within `cfg.rap_stale_days` of timeframe end (WARN if violated)
- `soil_present`
  - soil rows exist for this boundary
- `weather_fresh_enough`
  - weather covers at least `timeframe_end - cfg.weather_stale_days`
- `weather_response_complete`
  - Open‑Meteo response includes expected fields / day coverage
- `daily_features_complete`
  - joined artifact has expected day count
  - no missing weather days
  - RAP not missing for all days

Where:

- checks: `src/grc_pipeline/quality/checks.py`
- recording: `src/grc_pipeline/store/db.py` helpers used by CLI

Failure mode:

- hard failures cause a non-green `ingestion_runs.status` and scheduler-visible failure.

## Output monitoring (label-free)

Without ground truth labels, we monitor the **shape** and **guardrails** of outputs over a rolling window.

Signals:

- `% days_remaining <= 0`
  - indicates likely data/config issues (bad herd config, missing forage estimate, etc.)
- `% days_remaining > cfg.max_days_remaining`
  - outliers / unit mismatch / stale input effect
- RAP staleness (p95)
  - `(calculation_date - rap_composite_date)` in days

Command:

- `python -m grc_pipeline.cli monitor ...`

Output:

- a JSON report under `out/monitoring/{boundary_id}/{end}_{snapshot}.json`
- an exit code that can be wired to Airflow/cron alerting

### Escalation via exit codes

- `0` = OK
- `1` = WARN (page a human in business hours)
- `2` = CRIT (page immediately / stop the line)

This is intentionally “dumb but reliable”: the scheduler doesn’t need to parse JSON.

Where:

- monitor logic: `src/grc_pipeline/quality/monitoring.py`
- CLI wrapper: `src/grc_pipeline/cli.py` (`monitor` command)

## Smoke test (local)

```bash
python -m grc_pipeline.cli monitor \
  --db out/pipeline_smoke.db \
  --boundary-id boundary_north_paddock_3 \
  --end 2024-12-31 \
  --window-days 30
echo "exit=$?"
```

Expected:

- exit `0` for a healthy window on the sample dataset
- report JSON written to `out/monitoring/...`

## What happens when monitoring fails?

In production (and aligned with Task 07 operational boundaries):

- WARN → notify DS + MLOps with the report + links to the run manifests
- CRIT → block promotion, roll back to last known-good model bundle, and open an incident

See:

- Task 07: `docs/part1/task-07-operational-maturity.md`
- Runbook: `docs/part1/deliverable-03-runbook.md`
