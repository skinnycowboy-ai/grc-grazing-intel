# GRC Grazing Intelligence (Part 1 Take‑Home)

Production‑grade, reproducible *grazing intelligence* pipeline using **SQLite + Airflow patterns** (local CLI + minimal DAG skeleton).  
Focus: pipeline design, versioning, lineage, auditability, and operability — **not** ML modeling.

> Repo visibility note: This repo is a **fork** of `pasturemap/ml-test`. GitHub does **not** allow changing a fork from public → private, so it remains public for reviewer access.

---

## What this pipeline does

Given a **boundary GeoJSON** + **timeframe** (e.g. 2024 calendar year), the pipeline **ingests and joins**:

- **NRCS gSSURGO** soil attributes (static by boundary) — from the provided reference DB (`pasture_reference.db`)
- **RAP biomass** time series by boundary (sparse composites) — from the provided reference DB
- **Open‑Meteo** daily weather — fetched live and stored (daily rows)
- **Herd config** from PastureMap JSON — parsed and stored in `herd_configurations`

It materializes a joined daily feature frame for the timeframe:

- `boundary_daily_features` = **(soil static) + (RAP as‑of) + (weather daily)** per boundary per day

Then it computes a rules‑based recommendation:

### Days of grazing remaining (rules-based)

```text
days_remaining = available_forage_kg / daily_herd_consumption_kg
```

Outputs:

- a row in `grazing_recommendations`
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

- **Weather (Open‑Meteo):** exact join on `forecast_date = feature_date`
  - Missing weather for any day is a **DQ failure**
- **RAP biomass:** **as‑of join** using the latest composite where `composite_date <= feature_date`
  - No interpolation; the composite is treated as the “most recent known” value
  - If RAP is missing for **all** days in the timeframe, this is a **DQ failure**
- **Soil:** static summary by boundary (simple mean of selected attributes across rows)
  - Note: this is intentionally simplified for the take‑home; production would typically use area‑weighted SSURGO component aggregation.

### Idempotency & backfills

The ingestion command is safe to rerun:

- `weather_forecasts` uses **partition replace** per `(boundary_id, source_version, [start,end])`
- `boundary_daily_features` uses **partition replace** per `(boundary_id, [start,end])`
- `herd_configurations` uses deterministic IDs (stable across reruns)

Backfills: run `ingest` for any other timeframe; the relevant partitions are rebuilt deterministically.

---

## Task 2 alignment: deployed “Days Remaining” logic (idempotent compute)

- Logic lives in `src/grc_pipeline/logic/days_remaining.py`
- Deployment pattern:
  - exposed via CLI command `compute`
  - served via API route `/v1/recommendations/{boundary_id}`
- Compute idempotency key:
  - `(boundary_id, herd_config_id, calculation_date, logic_version, config_hash)`
  - enforced with a **unique index** and `INSERT ... ON CONFLICT DO UPDATE`

---

## Task 3 alignment: validation + monitoring (no labels required)

### Ingestion data quality (implemented)

Checks are recorded per ingestion run in `data_quality_checks` and summarized in `ingestion_runs.status`:

- `herd_config_valid` — animal_count > 0 and daily_intake_kg_per_head > 0
- `rap_present` — RAP rows exist for boundary
- `rap_fresh_enough` — latest RAP composite is within `cfg.rap_stale_days` of `timeframe_end` (warning if violated)
- `soil_present` — soil rows exist for boundary
- `weather_fresh_enough` — weather covers at least `timeframe_end - cfg.weather_stale_days`
- `daily_features_complete` — materialized join has:
  - expected number of days
  - no missing weather days
  - RAP not missing for all days

This covers:

- **missing API responses**: weather gaps show up as `daily_features_complete = failed`
- **stale data**: `weather_fresh_enough` and `rap_fresh_enough`
- **invalid configs**: `herd_config_valid`

### Output monitoring over time (implemented)

Without labels, monitor the **shape** and **guardrails** of outputs over a rolling window:

- % of recommendations with `days_remaining <= 0` (indicates likely data/config issues)
- % of recommendations with `days_remaining > cfg.max_days_remaining` (outliers)
- p95 RAP staleness (calculation_date − as_of_composite_date)

Command (writes a JSON report under `out/monitoring/...` and sets exit codes):

```bash
python -m grc_pipeline.cli monitor   --db out/pipeline.db   --boundary-id boundary_north_paddock_3   --end 2024-12-31   --window-days 30
```

Exit codes (escalation logic):

- `0` = OK
- `1` = WARN (page a human in business hours)
- `2` = CRIT (page immediately / stop the line)

This makes the monitor runnable via Airflow/cron and easy to wire into alerting.

---

## Repo layout

```text
.
├── src/grc_pipeline/            # library + CLI
│   ├── api/                     # FastAPI app
│   ├── ingest/                  # boundary/herd/weather loaders + feature join
│   ├── logic/                   # days remaining calculator
│   ├── quality/                 # DQ checks + monitoring
│   ├── store/                   # sqlite helpers + run manifest
│   ├── cli.py                   # typer commands: ingest / compute / monitor / serve
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

```bash
mkdir -p out
cp pasture_reference.db out/pipeline.db
```

### 1) Ingest (Task 1: pulls + joins)

```bash
python -m grc_pipeline.cli ingest   --db out/pipeline.db   --boundary-geojson sample_boundary.geojson   --boundary-id boundary_north_paddock_3   --boundary-crs EPSG:4326   --herds-json sample_herds_pasturemap.json   --start 2024-01-01   --end 2024-12-31
```

Validate run:

```bash
sqlite3 out/pipeline.db "
select run_id,status,started_at,completed_at,records_ingested
from ingestion_runs
order by started_at desc
limit 1;
"
```

DQ checks for latest run:

```bash
sqlite3 out/pipeline.db "
select check_name, passed, details_json
from data_quality_checks
where run_id = (
  select run_id from ingestion_runs
  order by started_at desc
  limit 1
)
order by check_name;
"
```

Validate join artifact row count (2024 = 366 days):

```bash
sqlite3 out/pipeline.db "
select count(*) as n
from boundary_daily_features
where boundary_id='boundary_north_paddock_3'
  and feature_date between '2024-01-01' and '2024-12-31';
"
```

---

### 2) Compute recommendation (Task 2)

```bash
python -m grc_pipeline.cli compute   --db out/pipeline.db   --boundary-id boundary_north_paddock_3   --herd-config-id 6400725295db666946d63535   --as-of 2024-12-18
```

Inspect:

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

---

### 3) Monitor output quality (Task 3)

```bash
python -m grc_pipeline.cli monitor   --db out/pipeline.db   --boundary-id boundary_north_paddock_3   --end 2024-12-31   --window-days 30
```

---

### 4) Serve API

```bash
python -m grc_pipeline.cli serve --db out/pipeline.db --host 127.0.0.1 --port 8001
```

Smoke test:

```bash
curl -s "http://127.0.0.1:8001/healthz" | jq

curl -s   "http://127.0.0.1:8001/v1/recommendations/boundary_north_paddock_3?herd_config_id=6400725295db666946d63535&as_of=2024-12-18"   | jq
```

---

## Airflow scheduling (pattern)

A minimal DAG stub lives in `airflow/dags/grazing_intel_dag.py` and demonstrates how to schedule:

- `ingest(boundary, [ds-30, ds])` as a parameterized task
- `compute(boundary, herd_config_id, ds)` downstream
- `monitor(boundary, ds-30..ds)` downstream (alerts on WARN/CRIT via exit codes)

This repo does not ship a full Airflow runtime; the DAG is intentionally docs-first to show schedulability and idempotent task boundaries.

---

## Git hygiene

Example `.gitignore` snippet:

```gitignore
out/
*.db
!pasture_reference.db
```

**Important:** do not commit `out/pipeline.db` (derived artifact).

---

## AI Tools Used

- **Tool:** OpenAI ChatGPT / Codex and Anthropic Claude Code.

  **Purpose:**

  - Used as an accelerator for design review, documentation drafting, and code/architecture sanity checks (not a substitute for implementation judgment).
  - Reviewed repo patterns and proposed an immutable versioning + manifest strategy; produced `compute`/`explain` design and drafted this reviewer doc.
  - Design review and articulation of CI/CD safety patterns (tests, rollout/rollback, provenance).
  - Drafted the visualization design spec and ASCII wireframes.

  **What I refined (MTI):**
  - Reworked AI-drafted sections to match the assignment rubric (reproducibility, idempotency/backfills, DQ gates, lineage/provenance, operability).
  - Replaced generic phrasing with concrete run contracts, keys, and failure modes that are testable and auditable.
  - Tightened the UX/design write-up to be decision-first for ranchers while still exposing provenance (run ids, timestamps, logic versions).
  - adjusted idempotency semantics to eliminate overwrite, added deterministic snapshot identity, and clarified the “why” query interface.
  - Anchored the design to MRV-grade traceability (immutable artifacts, promotion without rebuild, provenance fields).
  - Chose pragmatic runtime options (ECS default + ROSA as OpenShift-aligned alternative) with explicit cost/safety tradeoffs.
  - Defined concrete rollback triggers including business guardrails, not just infrastructure metrics.
  - Adjusted thresholds / copy tone / terminology to match PastureMap patterns.  
  - Verified the UX states map cleanly to pipeline freshness/completeness outputs.

  **What I verified manually (MTI):**
  - Ran the pipeline end-to-end locally and validated expected tables/row counts for the demo timeframe.
  - Re-ran ingestion/compute to confirm idempotency semantics (no duplicate rows; partitions replaced as expected).
  - Validated monitoring exit codes and that DQ failures surface deterministically.
  - Ran markdownlint over repo docs and corrected lint failures to keep the repo reviewer-friendly.
  - pytest`; two consecutive`compute` runs produce one DB row + one manifest; `explain` prints formula + provenance and references the manifest.
  - Confirmed the proposed test layers map to actual repo primitives (unit/integration/golden, DB join semantics, API contracts).
  - Ensured the deployment design supports deterministic replay and audit narratives via `run_id` and version metadata.
  - Screens match PastureMap navigation expectations.
  - Confidence logic and stale/blocked thresholds align with data availability realities.
