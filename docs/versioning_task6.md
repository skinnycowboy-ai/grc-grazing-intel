# Task 6 — Versioning & Reproducibility

Goal: answer **“Why did the system recommend moving cattle on Ranch X, paddock 3 on March 15th?”** six months later, using immutable provenance (logic + config + data snapshot) that can be replayed without calling live APIs.

---

## What we version

### 1) Model/Logic Version (explicit)

Every recommendation is tagged with a `model_version` (CLI flag `--logic-version`, default `days_remaining:v1`).

**Rule:** any behavioral change requires a version bump (e.g. `days_remaining:v2`).

### 2) Configuration Version (hashed)

Config parameters that affect computation are canonicalized and hashed into `config_hash`, stored as `config_version`.

Example params (from `PipelineConfig`):

- `min_days_remaining`
- `max_days_remaining`

This creates a stable, content-addressed identifier for the parameter set.

### 3) Data Snapshot (manifest + pointer)

For each compute run, the pipeline writes an **immutable** per-run JSON manifest under:

`out/manifests/{boundary_id}/{as_of}_{snapshot_id}.json`

The DB row stores a pointer + hashes in `grazing_recommendations.input_data_versions_json`, including:

- `manifest.path` + `manifest.snapshot_id`
- `boundary_geojson_hash` + `herd_snapshot_hash`
- `data_snapshot_versions` (RAP/soil/weather source versions + RAP composite date)
- `code_version` (git commit + package version)
- `inputs_snapshot_hash` (hash of the full inputs snapshot stored in the manifest)

The manifest captures the **exact inputs used**, including:

- RAP composite date and biomass value used
- boundary area (ha) used
- herd parameters used (animal_count, daily_intake_kg_per_head)
- intermediate “logic provenance” (values substituted into formulas)
- outputs and guardrail flags

**Key point:** this supports “Why?” even if upstream sources change, disappear, or forecasts are non-replayable.

---

## Immutability and idempotency (backfills/retries)

### Append-only recommendations

`compute` is **append-only** for a given idempotency key:

`(boundary_id, herd_config_id, calculation_date, model_version, config_version)`

On conflict it does **DO NOTHING**, then reads back the existing record.

### Drift protection

If an existing record is found under the same key but its provenance differs, `compute` **fails** and forces a version bump:

- bump `logic_version` (e.g. `days_remaining:v2`) **or**
- change config params (new `config_hash`)

### Stable snapshot identity

The manifest uses:

- a deterministic `run_id` derived from the idempotency key, and
- a stable `snapshot_id` derived from canonical JSON of meaningful fields (excluding volatile timestamps)

Result: retries/backfills produce the **same** manifest identity and do not create duplicate “decisions”.

---

## How to answer “Why did it recommend March 15?” later

### 1) Locate the recommendation (SQL)

Example: Ranch X / paddock_3 / move date 2024-03-15

```sql
SELECT gr.id
FROM grazing_recommendations gr
JOIN geographic_boundaries gb ON gb.boundary_id = gr.boundary_id
WHERE gb.ranch_id = 'Ranch X'
  AND gb.pasture_id = 'paddock_3'
  AND gr.calculation_date = '2024-03-15'
ORDER BY gr.id DESC
LIMIT 1;
```

### 2) Explain (CLI)

```bash
python -m grc_pipeline.cli explain --db out/pipeline.db --recommendation-id <ID>
```

The command prints:

- the formula and substituted values (e.g., `days_remaining = available / daily_consumption`)
- the exact RAP + area + herd values used
- `logic_version`, `config_hash`, and code version
- the immutable manifest pointer and snapshot id

### 3) Replay (optional)

A replay step can recompute from the manifest snapshot and assert it matches the stored recommendation. (Not required for Task 6, but recommended in production to detect non-determinism.)

---

## Why this meets Task 6 (MRV-grade traceability)

- **Reproducibility:** can reconstruct the decision from the manifest + DB row without contacting live systems.
- **Idempotency/backfills:** deterministic keys and append-only inserts prevent accidental mutation of history.
- **Lineage/provenance:** every recommendation links to:
  - logic version (explicit)
  - config hash (content addressed)
  - data snapshot versions + full inputs snapshot (manifest)
  - code version (git commit / package version)
- **Operability:** “explain” is a direct, reviewer-friendly interface to answer the audit question.

---

## Defendability

- **Why not overwrite?** Auditability requires historical decisions remain inspectable; recomputation produces a *new* versioned record.
- **Why stable snapshot ids?** Retries/backfills must not create new identities for the same decision; stable ids make provenance dedupable and externally referenceable.
- **Why manifest + DB pointer?** Keeps DB queryable and small while preserving a complete immutable decision packet (easy to ship to S3/Object Lock later).

---

## AI Tools Used

- **OpenAI/Claude Code**: reviewed repo patterns and proposed an immutable versioning + manifest strategy; produced `compute`/`explain` design and drafted this reviewer doc.
- **What I changed/refined**: adjusted idempotency semantics to eliminate overwrite, added deterministic snapshot identity, and clarified the “why” query interface.
- **What I verified manually**: `pytest`; two consecutive `compute` runs produce one DB row + one manifest; `explain` prints formula + provenance and references the manifest.
