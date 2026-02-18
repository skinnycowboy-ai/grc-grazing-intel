# Architecture

## Overview

This repo implements a small, production-patterned “grazing intelligence” service:

- **Ingestion** is idempotent and records a `run_id` plus DQ results.
- **Computation** is deterministic for a `(boundary_id, herd_config_id, as_of)` triple.
- **Serving** reads from SQLite and exposes:
  - `/v1/recommendations/{boundary_id}` (data product)
  - `/healthz` (liveness)
  - `/metrics` (Prometheus)

For this take-home, **RAP + soil** are read from the provided `pasture_reference.db`.

## Data flow

1) Load boundary GeoJSON (assume EPSG:4326): compute **area_ha** and **centroid (lat/lon)**.
2) Load herd configs from PastureMap JSON and store as a **hashable snapshot**.
3) Fetch Open‑Meteo daily weather for centroid + timeframe; store into `weather_daily`.
4) Read RAP biomass + NRCS soil for the boundary from reference DB; copy/select into working tables.
5) Record `ingestion_runs` and `data_quality_checks` (failures/warnings are persisted, not just logged).
6) Compute recommendation for `as_of`:
   - `available_forage_kg = rap_kg_per_ha(latest composite_date <= as_of) * boundary_area_ha`
   - `daily_consumption_kg = animal_count * daily_intake_kg_per_head`
   - `days_remaining = available_forage_kg / daily_consumption_kg`
   - `move_date = as_of + floor(days_remaining)`
7) Persist recommendation + provenance and write an immutable manifest:
   - row in `grazing_recommendations`
   - file in `out/manifests/{boundary_id}/{as_of}_{snapshot_id}.json`

## Versioning (answering “why did we recommend X?”)

**Logic version**:

- `grazing_recommendations.model_version` (e.g., `days_remaining:v1`)
- In a real deployment this maps to a git SHA / container digest.

**Config version** (DS parameters / thresholds)

- `grazing_recommendations.config_version` (hash of thresholds/guardrails)

**Input snapshot identity**:

- `grazing_recommendations.input_data_versions_json`
  - boundary geojson hash, herd snapshot hash
  - upstream source versions and selection rules
- manifest `snapshot_id`
  - stable hash over inputs + config + outputs (audit spine)

## Ownership boundaries

**Data Science owns**:

- rule logic and parameters (intake rates, stale thresholds, confidence rules)
- DQ policy (what constitutes warning vs failure)

**ML Ops owns**:

- orchestration and storage
- manifests/versioning and reproducibility
- monitoring/alerting and API deployment/rollbacks

This repo supports separation by treating **logic/config** as versioned inputs and persisting **run + DQ + provenance** for every result.
