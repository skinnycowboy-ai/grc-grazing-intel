# GRC Grazing Intelligence (Part 1 Take-Home)

Production-grade, reproducible *grazing intelligence* pipeline using **SQLite + Airflow patterns** (local CLI + minimal DAG skeleton).  
Focus: pipeline design, versioning, lineage, auditability, and operability — **not** ML modeling.

> Repo visibility note: This repo is a **fork** of `pasturemap/ml-test`. GitHub does **not** allow changing a fork from public → private, so it remains public for reviewer access.

---

## What this pipeline does

Given a **boundary GeoJSON** + **timeframe** (e.g. 2024 calendar year), the pipeline **ingests and joins**:

- **NRCS gSSURGO** soil attributes (static by boundary) — from the provided reference DB (`pasture_reference.db`)
- **RAP biomass** time series by boundary (sparse composites) — from the provided reference DB
- **Open-Meteo** daily weather — fetched live and stored (daily rows)
- **Herd config** from PastureMap JSON — parsed and stored in `herd_configurations`

It materializes a joined daily feature frame for the timeframe:

- `boundary_daily_features` = **(soil static) + (RAP as-of) + (weather daily)** per boundary per day

Then it computes a rules-based recommendation:

### Days of grazing remaining (rules-based)

```text
days_remaining = available_forage_kg / daily_herd_consumption_kg
recommended_move_date = calculation_date + floor(days_remaining)
```

Outputs:

- a row in `grazing_recommendations` (**idempotent by input key**)
- a JSON **manifest** under `out/manifests/...` (audit spine: hashes + source versions + parameters)

---

## Task 1 alignment: CRS + temporal joins + idempotent ingestion

### Coordinate system alignment (CRS)

- **Internal canonical CRS:** EPSG:4326 (WGS84 lon/lat)
- **Input GeoJSON default:** assumed EPSG:4326 **unless** you pass `--boundary-crs`
- If your GeoJSON coordinates are projected (UTM/etc), pass `--boundary-crs EPSG:xxxx` and the pipeline will transform to EPSG:4326 before storing.
- Geometry types supported: `Polygon` and `MultiPolygon`
- Validation: bounds are checked for EPSG:4326 plausibility; invalid geometries are repaired with `buffer(0)` where possible.

CLI flag:

```bash
--boundary-crs EPSG:4326
```

### Temporal joins (static + time-series)

Materialized table: `boundary_daily_features(boundary_id, feature_date, …)`

Join semantics for each `feature_date` in `[start, end]`:

- **Weather (Open-Meteo):** exact join on `forecast_date = feature_date`
  - Missing weather for any day is a **DQ failure**
- **RAP biomass:** **as-of join** using the latest composite where `composite_date <= feature_date`
  - No interpolation; the composite is treated as the “most recent known” value
  - If RAP is missing for **all** days in the timeframe, this is a **DQ failure**
- **Soil:** static summary by boundary (simple mean of selected attributes across rows)
  - Note: intentionally simplified for the take-home; production typically uses area-weighted SSURGO component aggregation.

### Idempotency & backfills (ingest)

The ingestion command is safe to rerun:

- `weather_forecasts` uses **partition replace** per `(boundary_id, source_version, [start,end])`
- `boundary_daily_features` uses **partition replace** per `(boundary_id, [start,end])`
- `herd_configurations` uses deterministic IDs (stable across reruns)

Backfills: run `ingest` for any other timeframe; relevant partitions rebuild deterministically.

---

## Task 2 alignment: deployed logic + idempotent compute

### Deployment pattern

The “Days of Grazing Remaining” calculator is deployed as:

- **CLI entrypoint:** `python -m grc_pipeline.cli compute ...`
- **API serving:** `/v1/recommendations/{boundary_id}?herd_config_id=...&as_of=...`
- **Versioned artifact:** `logic_version` recorded in `model_versions` and each `grazing_recommendations` row
- **MRV-grade provenance:** per-run manifest + hashes + source versions persisted alongside outputs

### Task 2 run contract

Inputs:

- `boundary_id`
- `herd_config_id`
- `as_of` (ISO date, `YYYY-MM-DD`)
- `logic_version` (default: `days_remaining:v1`)

Data dependency:

- Compute reads from **ingested** `boundary_daily_features` for `(boundary_id, as_of)`.
- If missing, compute fails fast with: “Run `ingest` for a timeframe that includes this as_of date.”

Output:

- `grazing_recommendations` row with:
  - `available_forage_kg` (kg) = `rap_biomass_kg_per_ha * area_ha`
  - `daily_consumption_kg` (kg/day) = `animal_count * daily_intake_kg_per_head`
  - `days_of_grazing_remaining`
  - `recommended_move_date`

### Idempotency & backfills (compute)

Compute is retry/backfill safe via an idempotency key:

```text
(boundary_id, herd_config_id, calculation_date, model_version, config_version)
```

Enforced by a unique index and an UPSERT:

- reruns update the same row (no duplicates)
- good fit for Airflow retries, daily schedules, and backfills

Note: manifests are written **per invocation** (new `snapshot_id`/file each run), but they can point to the same stable `recommendation_id` when inputs are unchanged.

---

## Repo layout

```text
.
├── src/grc_pipeline/            # library + CLI
│   ├── api/                     # FastAPI app
│   ├── ingest/                  # boundary/herd/weather loaders + feature join
│   ├── logic/                   # days remaining calculator
│   ├── quality/                 # DQ checks + helpers
│   ├── store/                   # sqlite helpers + run manifest
│   ├── cli.py                   # typer commands: ingest / compute / serve
│   └── config.py                # thresholds + versions
├── airflow/                     # minimal DAG skeleton (docs-first)
├── docs/                        # notes + diagrams
├── tests/                       # unit + small integration coverage
├── pasture_reference.db         # provided baseline DB (keep pristine)
├── schema.sql                   # canonical schema (docs + reviewer convenience)
├── sample_boundary*.geojson
└── sample_herds_pasturemap.json
```

---

## Requirements

- Python **3.12**
- Optional tools: `sqlite3`, `jq`, `curl`

---

## Install (recommended)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

---

## Run: end-to-end demo

### 0) Create a working DB (don’t mutate the baseline)

The assignment allows using the pre-built reference DB. To keep it clean, copy it:

```bash
mkdir -p out
cp pasture_reference.db out/pipeline.db
```

### 1) Ingest (Task 1: pulls + joins)

```bash
python -m grc_pipeline.cli ingest \
  --db out/pipeline.db \
  --boundary-geojson sample_boundary.geojson \
  --boundary-id boundary_north_paddock_3 \
  --boundary-crs EPSG:4326 \
  --herds-json sample_herds_pasturemap.json \
  --start 2024-01-01 \
  --end 2024-12-31
```

This will:

- upsert the boundary
- upsert herd configs for the pasture
- fetch + store daily Open-Meteo rows
- materialize `boundary_daily_features` (joined daily frame)
- record DQ checks and the `ingestion_runs` status

#### Validate the run + DQ

Latest ingestion run:

```bash
sqlite3 out/pipeline.db "
select run_id,status,started_at,completed_at,records_ingested
from ingestion_runs
order by started_at desc
limit 3;
"
```

DQ checks for the latest run:

```bash
sqlite3 out/pipeline.db "
select check_name, passed, details_json
from data_quality_checks
where run_id = (
  select run_id from ingestion_runs
  order by started_at desc
  limit 1
);
"
```

#### Validate the Task 1 join artifact

Row count should match number of days in timeframe  
(2024 is a leap year → **366**):

```bash
sqlite3 out/pipeline.db "
select count(*) as n
from boundary_daily_features
where boundary_id='boundary_north_paddock_3'
  and feature_date between '2024-01-01' and '2024-12-31';
"
```

---

### 2) Compute recommendation (Task 2: deployed logic)

Compute once:

```bash
python -m grc_pipeline.cli compute \
  --db out/pipeline.db \
  --boundary-id boundary_north_paddock_3 \
  --herd-config-id 6400725295db666946d63535 \
  --as-of 2024-12-18
```

Inspect the result:

```bash
sqlite3 out/pipeline.db "
select
  id,
  calculation_date,
  available_forage_kg,
  daily_consumption_kg,
  days_of_grazing_remaining,
  recommended_move_date,
  model_version,
  config_version
from grazing_recommendations
where boundary_id='boundary_north_paddock_3'
order by id desc
limit 1;
"
```

#### Prove idempotency (rerun-safe)

Run `compute` twice with identical inputs:

```bash
python -m grc_pipeline.cli compute \
  --db out/pipeline.db \
  --boundary-id boundary_north_paddock_3 \
  --herd-config-id 6400725295db666946d63535 \
  --as-of 2024-12-18

python -m grc_pipeline.cli compute \
  --db out/pipeline.db \
  --boundary-id boundary_north_paddock_3 \
  --herd-config-id 6400725295db666946d63535 \
  --as-of 2024-12-18
```

You should still have exactly one output row for that key:

```bash
sqlite3 out/pipeline.db "
select count(*) from grazing_recommendations
where boundary_id='boundary_north_paddock_3'
  and herd_config_id='6400725295db666946d63535'
  and calculation_date='2024-12-18';
"
```

Expected: `1`

#### Inspect provenance captured on the recommendation row

```bash
sqlite3 out/pipeline.db "
select input_data_versions_json
from grazing_recommendations
where boundary_id='boundary_north_paddock_3'
order by id desc
limit 1;
" | jq
```

Manifest (audit spine):

```bash
ls -la out/manifests/boundary_north_paddock_3/
cat out/manifests/boundary_north_paddock_3/2024-12-18_*.json | jq
```

---

### 3) Serve API

If port 8000 is already in use, use 8001:

```bash
python -m grc_pipeline.cli serve --db out/pipeline.db --host 127.0.0.1 --port 8001
```

Smoke test:

```bash
curl -s "http://127.0.0.1:8001/healthz" | jq

curl -s \
  "http://127.0.0.1:8001/v1/recommendations/boundary_north_paddock_3?herd_config_id=6400725295db666946d63535&as_of=2024-12-18" \
  | jq
```

---

## Run via Docker (same API)

Build:

```bash
docker build -t grc-grazing-intel:dev .
```

Run (maps container port 8000 to host 8002, mounts `./out` to `/data`, and runs as your user):

```bash
docker run --rm \
  -u "$(id -u):$(id -g)" \
  -p 8002:8000 \
  -v "$PWD/out:/data" \
  grc-grazing-intel:dev
```

Smoke test:

```bash
curl -s "http://127.0.0.1:8002/healthz" | jq
```

---

## Data quality strategy (MRV-friendly)

Defensive checks are recorded per ingestion run in `data_quality_checks`, and summarized via `ingestion_runs.status`:

- `herd_config_valid` — animal_count > 0 and daily_intake_kg_per_head > 0
- `rap_present` — RAP rows exist for boundary
- `soil_present` — soil rows exist for boundary
- `weather_fresh_enough` — weather covers at least `timeframe_end - cfg.weather_stale_days`
- `daily_features_complete` — materialized join has:
  - expected number of days
  - no missing weather days
  - RAP not missing for all days

---

## Provenance & auditability

Each recommendation can be explained by:

1. `grazing_recommendations.input_data_versions_json`
   - RAP/soil/weather source versions
   - hashes of boundary geojson + herd snapshot
   - logic version + DS params
   - idempotency key
2. Manifest JSON file under `out/manifests/...`
   - stable snapshot identity (`snapshot_id`)
   - output row IDs (including idempotency key)
   - guardrail flags

---

## Airflow scheduling (pattern)

A minimal DAG stub lives in `airflow/dags/grazing_intel_dag.py` and demonstrates how to schedule:

- `ingest(boundary, timeframe)` as a parameterized task
- `compute(boundary, herd_config_id, as_of)` downstream

This repo does not ship a full Airflow runtime; the DAG is intentionally docs-first to show schedulability and idempotent task boundaries.

---

## Git hygiene

Goal:

- keep `pasture_reference.db` in repo (baseline reference)
- ignore generated DBs and outputs under `out/`

Example `.gitignore` snippet:

```gitignore
out/
*.db
!pasture_reference.db
```

**Important:** do not commit `out/pipeline.db` (derived artifact).

---

## Screenshots (optional)

If you include screenshots for reviewers, the highest signal ones are:

1) `boundary_daily_features` row count = 366 for 2024  
2) `data_quality_checks` showing `daily_features_complete` passed  
3) idempotent compute proof: `count(*) = 1` after two `compute` runs  
4) manifest JSON showing hashes + source versions + idempotency key
