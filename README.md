# GRC Grazing Intelligence (Part 1 Take‑Home)

Production‑grade, reproducible *grazing intelligence* pipeline using **SQLite + Airflow patterns** (local CLI + minimal DAG skeleton).  
Focus: pipeline design, versioning, lineage, auditability, and operability — **not** ML modeling.

> Repo visibility note: This repo is a **fork** of `pasturemap/ml-test`. GitHub does **not** allow changing a fork from public → private, so it remains public for reviewer access.

---

## What this pipeline does

Given a **boundary GeoJSON** + **timeframe** (e.g. 2024), the pipeline ingests/joins:

- **NRCS gSSURGO** soil attributes (static by boundary) — from the provided reference DB
- **RAP biomass** time series by boundary — from the provided reference DB
- **Open‑Meteo** daily weather — fetched live and stored
- **Herd config** from PastureMap JSON — parsed and stored in `herd_configurations`

Then it computes a rules‑based recommendation:

### Days of grazing remaining

```text
days_remaining = available_forage_kg / daily_herd_consumption_kg
```

and stores:

- a DB row in `grazing_recommendations`
- a JSON **manifest** under `out/manifests/...` (audit spine: hashes + source versions)

---

## Repo layout

```text
.
├── src/grc_pipeline/            # library + CLI
│   ├── api/                     # FastAPI app
│   ├── ingest/                  # boundary/herd/weather loaders
│   ├── logic/                   # days remaining calculator
│   ├── quality/                 # DQ checks + helpers
│   ├── store/                   # sqlite helpers + run manifest
│   ├── cli.py                   # typer commands: ingest / compute / serve
│   └── config.py                # thresholds + versions
├── airflow/                     # minimal DAG skeleton (docs-first)
├── docs/                        # notes + diagrams
├── tests/                       # unit + small integration coverage
├── pasture_reference.db         # provided baseline DB (keep pristine)
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

**Common gotcha:** if you run `python3 -m grc_pipeline.cli ...` outside the venv you may see:
`ModuleNotFoundError: No module named 'grc_pipeline'`

Fix: `source .venv/bin/activate` then use `python -m ...`.

---

## Run: end-to-end demo

### 0) Create a working DB (don’t mutate the baseline)

The assignment allows using the pre-built reference DB. To keep it clean, copy it:

```bash
mkdir -p out
cp pasture_reference.db out/pipeline.db
```

### 1) Ingest

```bash
python -m grc_pipeline.cli ingest   --db out/pipeline.db   --boundary-geojson sample_boundary.geojson   --herds-json sample_herds_pasturemap.json   --start 2024-01-01   --end 2024-12-31   --boundary-id boundary_north_paddock_3
```

Validate the run + DQ:

```bash
sqlite3 out/pipeline.db "
select run_id,status,started_at,completed_at,records_ingested
from ingestion_runs
order by started_at desc
limit 3;
"

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

Expected (example):

- `status=succeeded`
- DQ checks all `passed=1` (herd, rap, soil, weather)

### 2) Compute recommendation

Pick a date that has RAP data. For the sample boundary:

```bash
sqlite3 out/pipeline.db "
select max(composite_date)
from rap_biomass
where boundary_id='boundary_north_paddock_3';
"
```

Then compute:

```bash
python -m grc_pipeline.cli compute   --db out/pipeline.db   --boundary-id boundary_north_paddock_3   --herd-config-id 6400725295db666946d63535   --as-of 2024-12-18
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

Inspect provenance:

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

### 3) Serve API

If port 8000 is already in use, use 8001:

```bash
python -m grc_pipeline.cli serve --db out/pipeline.db --host 127.0.0.1 --port 8001
```

Smoke test:

```bash
curl -s "http://127.0.0.1:8001/healthz" | jq

curl -s   "http://127.0.0.1:8001/v1/recommendations/boundary_north_paddock_3?herd_config_id=6400725295db666946d63535&as_of=2024-12-18"   | jq
```

---

## Run via Docker (same API)

Useful for a clean, reproducible runtime. When you mount `out/` into the container, run the container **as your host UID/GID** so anything written under `/data` is owned by you (not root), and you avoid permission issues.

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

curl -s \
  "http://127.0.0.1:8002/v1/recommendations/boundary_north_paddock_3?herd_config_id=herd_ranch_001_paddock_3_0&as_of=2024-03-15" \
  | jq
```

If you see `address already in use`, pick a different host port (e.g. `-p 8003:8000`).

## Data quality strategy

No labels required — just defensive checks recorded per ingestion run:

- `herd_config_valid`: animal_count > 0, daily_intake_kg_per_head > 0
- `rap_present`: RAP rows exist for boundary
- `soil_present`: soil rows exist for boundary
- `weather_fresh_enough`: weather covers at least `timeframe_end - cfg.weather_stale_days`

Results are persisted in `data_quality_checks` and reflected in `ingestion_runs.status`:

- `succeeded` vs `succeeded_with_warnings` vs `failed`

---

## Provenance & “Why?” (auditability)

Each recommendation can be explained by:

1. `grazing_recommendations.input_data_versions_json`:
   - RAP/soil/weather source versions
   - hashes of boundary geojson + herd snapshot
   - logic version + DS params
2. Manifest JSON file under `out/manifests/...`:
   - stable snapshot identity (`snapshot_id`)
   - output row IDs
   - guardrail flags

---

## Git hygiene (.gitignore)

Goal:

- keep `pasture_reference.db` in repo (baseline reference)
- ignore generated DBs and outputs under `out/`

Example:

```gitignore
out/
*.db
!pasture_reference.db
```

**Important:** do not commit `out/pipeline.db` (it’s a derived artifact).
