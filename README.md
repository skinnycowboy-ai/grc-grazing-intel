# GRC Grazing Intelligence (Take‑Home: Part 1 + Part 2)

Production‑grade, reproducible *grazing intelligence* pipeline using **SQLite + Airflow patterns** (local CLI + minimal DAG skeleton).  
Focus: **pipeline design, versioning, lineage, auditability, and operability** — *not* ML sophistication.

> Repo visibility note: This repo is a **fork** of `pasturemap/ml-test`. GitHub does **not** allow changing a fork from public → private, so it remains public for reviewer access.

---

## Where to start (reviewer guide)

### Part 1 — Grazing intelligence pipeline (Tasks 1–7)

- Task 01: `docs/part1/task-01-data-ingestion.md`
- Task 02: `docs/part1/task-02-model-deployment.md`
- Task 03: `docs/part1/task-03-validation-monitoring.md`
- Task 04: `docs/part1/task-04-grazing-intel-visualization-design.md`
- Task 05: `docs/part1/task-05-ci-cd-design.md`
- Task 06: `docs/part1/task-06-versioning.md`
- Task 07: `docs/part1/task-07-operational-maturity.md`

Part 1 “deliverables” (reviewer-friendly artifacts):
- Reviewer copy of README: `docs/part1/deliverable-01-README.md` (optional convenience)
- Architecture diagram: `docs/part1/deliverable-02-architecture-diagram.md`
- Runbook: `docs/part1/deliverable-03-runbook.md`

### Part 2 — Credit verification (deliverables)

- Design doc: `docs/part2/deliverable-01-credit-verification-design.md`
- Dataflow diagram: `docs/part2/deliverable-02-credit-verification-dataflow-diagram.md`
- Verification record schema: `docs/part2/deliverable-03-ranch-verification-record.schema.yaml`

---

## Architecture at a glance

## **Inputs**

- Boundary GeoJSON (sample polygons in repo)
- PastureMap-style herd config JSON (`sample_herds_pasturemap.json`)
- Reference DB (`pasture_reference.db`) containing:
  - NRCS gSSURGO soil attributes (static)
  - RAP biomass composites (sparse time series)
- Live weather from Open‑Meteo (**fetched at ingest time; “daily” when scheduled**)

## **Core artifacts**

- SQLite tables (canonical schema in `schema.sql`)
- `boundary_daily_features`: joined daily feature frame per boundary per day
- `grazing_recommendations`: computed outputs (rules-based)
- `out/manifests/.../*.json`: immutable run manifest (hashes + versions + params)

## **Pipeline boundaries (schedulable, idempotent)**

1. `ingest` → fetch/parse/load + materialize daily features for a timeframe
2. `compute` → generate (or reuse) a recommendation + write a manifest
3. `monitor` → evaluate DQ + output guardrails over a rolling window (exit codes)
4. `serve` → FastAPI endpoint over the stored recommendations

### Scheduling note (what “daily Open‑Meteo” means)

- The repo includes a **minimal Airflow DAG skeleton** at `airflow/dags/grazing_intel_dag.py` scheduled `@daily`.
- That DAG runs `ingest` for a **rolling 30‑day window ending on Airflow `ds`** (`--end {{ ds }}`), which triggers a **live Open‑Meteo HTTP fetch** during each run.
- This repo does **not** deploy Airflow; “daily” happens only if you run the DAG (or cron the CLI).

#### Quick verification (prove weather was refreshed)

```bash
DB="out/pipeline_smoke.db"
BID="boundary_north_paddock_3"
sqlite3 "$DB" "
  SELECT MAX(ingested_at) AS last_ingested_at,
         MAX(forecast_date) AS max_forecast_date
  FROM weather_forecasts
  WHERE boundary_id='$BID';
"
```

---

## Task 1–3 mapping (Part 1)

Tasks 1–3 are summarized in this README for fast review, and expanded in dedicated docs:

- Task 01: `docs/part1/task-01-data-ingestion.md`
- Task 02: `docs/part1/task-02-model-deployment.md`
- Task 03: `docs/part1/task-03-validation-monitoring.md`

---

## Quickstart

### Requirements

- Python **3.12**
- Optional tools: `sqlite3`, `jq`, `curl`, `docker`

### Install (recommended)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

### Smoke test (end-to-end, reproducible)

```bash
rm -rf out && mkdir -p out
cp pasture_reference.db out/pipeline_smoke.db

DB="out/pipeline_smoke.db"
BID="boundary_north_paddock_3"
START="2024-01-01"
END="2024-12-31"
ASOF="2024-12-18"

python -m grc_pipeline.cli ingest   --db "$DB"   --boundary-geojson sample_boundary.geojson   --boundary-id "$BID"   --boundary-crs EPSG:4326   --herds-json sample_herds_pasturemap.json   --start "$START"   --end "$END"

HID="$(sqlite3 "$DB" "select id from herd_configurations where boundary_id='$BID' order by created_at desc limit 1;")"

python -m grc_pipeline.cli compute   --db "$DB" --boundary-id "$BID" --herd-config-id "$HID" --as-of "$ASOF"

python -m grc_pipeline.cli explain --db "$DB" --recommendation-id 1 | jq

python -m grc_pipeline.cli monitor   --db "$DB" --boundary-id "$BID" --end "$END" --window-days 30
```

---

## Key architecture decisions

### 1) SQLite-first + deterministic partitions (reproducible + reviewable)

- SQLite keeps the take-home **fully local** and easy to review (schema + data + outputs).
- “Derived” tables are rebuilt via **partition replace** (boundary + date range), making `ingest` safe to rerun and schedulable.
- The baseline `pasture_reference.db` is kept pristine; smoke tests copy it into `out/`.

### 2) Explicit join semantics (static + time series)

For each `feature_date` in `[start, end]`:

- **Weather**: exact join on date (missing days are a DQ failure)
- **RAP**: **as-of join** to the latest composite where `composite_date <= feature_date`
- **Soil**: static boundary summary (intentionally simplified for this assignment)

This makes the feature frame stable and auditable — you can point at exactly which composite fed each day.

### 3) Versioned provenance (manifest as the “audit spine”)

Every compute run emits a manifest under `out/manifests/...` containing:

- input snapshot identifiers (hashes)
- `source_version` for RAP/soil/weather
- `logic_version` and `config_hash`
- code metadata (git commit + package version)

This enables **replay**, “why” explanations, and immutable evidence trails.

### 4) Drift guard over “silent overwrite”

`compute` is **idempotent** under an explicit key:
`(boundary_id, herd_config_id, calculation_date, logic_version, config_hash)`.

If the same key is requested but the underlying input snapshot differs, the pipeline **refuses to overwrite history** and requires a version bump (`logic_version` or config change → new `config_hash`). This prevents silent “same ID, different answer”.

### 5) Monitoring without labels (shape + guardrails)

Because we don’t have ground truth labels, output monitoring checks:

- distribution/guardrails of `days_remaining`
- staleness of key inputs (e.g., RAP composite age)
- completeness of daily features

`monitor` returns exit codes (OK/WARN/CRIT) to make it schedulable and easy to wire into alerting.

---

## Assumptions (explicit)

- The provided reference DB (`pasture_reference.db`) is treated as the **authoritative** RAP + soil source for the take-home.
- Soil aggregation is simplified to a boundary-level summary (production would likely do area-weighted SSURGO component aggregation).
- RAP composites are treated as “last known value” until a newer composite exists (no interpolation).
- Weather is fetched from Open‑Meteo and stored daily; missing weather days are treated as ingestion failures for the affected partition.
- The “Days Remaining” logic is intentionally rules-based to emphasize deployment + operability patterns.

---

## What I’d improve with more time

- **Schema migrations** (Alembic-like pattern) and stronger backwards compatibility guarantees for manifests.
- **More realistic geospatial handling**: area-weighted soil aggregation, explicit CRS validation for more edge cases, boundary simplification options.
- **Operational instrumentation**: structured logs + metrics export (Prometheus) + trace IDs tied to run/manifest IDs.
- **Golden test fixtures**: deterministic weather fixture for CI to avoid live API dependence in pipelines beyond smoke tests.
- **Promotion workflow**: explicit “promote manifest” mechanics to move runs between environments without recomputation.
- **Multi-tenant boundaries**: boundary registry, RBAC concepts, and API auth (kept out of scope for the take-home).

---

## AI Tools Used (transparency)

- **Tools:** OpenAI ChatGPT / Codex and Anthropic Claude Code.

## **How they were used**

- Architecture review + tradeoffs (idempotency, versioning, manifest/provenance patterns)
- Drafting and tightening documentation (turning design intent into reviewer-friendly specs)
- Fast iteration on CLI ergonomics and edge cases (e.g., drift guard error messages)

## **My judgment and refinement**

- Rewrote AI-drafted sections to match the rubric (reproducibility, idempotency/backfills, DQ gates, lineage/provenance, operability).
- Replaced generic wording with concrete keys, failure modes, and smoke tests that are auditable.
- Tightened “why/explain” semantics to reference immutable manifests and explicit source versions.
- Chose pragmatic rollout/rollback triggers based on business guardrails (not just infra health).

## **What I verified manually**

- `ruff format/check`, `pytest`, and repeated end-to-end smoke tests (ingest → compute → explain → monitor).
- Verified compute idempotency (two identical computes produce the same recommendation + manifest).
- Verified drift guard behavior (mutating inputs under the same key triggers a hard failure requiring version bump).
- Verified container build path expectations (Dockerfile copies root `README.md`).
