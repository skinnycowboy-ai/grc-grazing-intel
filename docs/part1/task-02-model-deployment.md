# Task 02 — Model/Logic Deployment (“Days Remaining” compute)

This task deploys a rules-based “Days of Grazing Remaining” calculator and exposes it via CLI and API.

## Logic

Rules-based calculation (intentionally simple):

```text
days_remaining = available_forage_kg / daily_consumption_kg
recommended_move_date = as_of + floor(days_remaining)
```

Where:

- `daily_consumption_kg = animal_count * daily_intake_kg_per_head`
- `available_forage_kg` comes from the RAP biomass estimate and boundary area used at compute time.

Implementation:

- `src/grc_pipeline/logic/days_remaining.py`

## Deployment surface

### CLI (primary deployment boundary)

- `python -m grc_pipeline.cli compute ...`

The CLI is the “deployment artifact” for this take-home:

- deterministic inputs
- deterministic outputs
- schedulable
- observable (writes manifest + DB row)

### API (serving)

- FastAPI app: `src/grc_pipeline/api/`
- Example route: `/v1/recommendations/{boundary_id}`

The API reads from the same persisted recommendation/manifest artifacts the CLI produces.

## Versioning & immutability

A compute run is versioned along three axes:

1) **Logic version** (`logic_version`)

- example: `days_remaining:v1`
- owned by **Data Science** in production

1) **Config hash** (`config_hash`)

- hash of parameters that influence compute (e.g., guardrail thresholds used by logic)
- owned by **Data Science** in production

1) **Data snapshot pointers**

- source versions + as-of dates for RAP/soil/weather
- owned by **ML Ops** in production (data plumbing + provenance capture)

Artifacts:

- DB row in `grazing_recommendations` stores a *thin provenance pointer*
- Manifest JSON stores the *full input snapshot* + outputs

Manifest location:

- `out/manifests/{boundary_id}/{as_of}_{snapshot_id}.json`

## Idempotency and “no overwrite” semantics

Compute key:

- `(boundary_id, herd_config_id, calculation_date, model_version, config_version)`

Behavior:

- Insert is **append-only** by version key:
  - if the exact key already exists, compute returns the existing row id
- Drift protection:
  - if the same version key is present but provenance differs, compute raises and refuses to overwrite history.

This prevents silent history rewrites if upstream data or configs change without a version bump.

## Smoke test (local)

```bash
DB="out/pipeline_smoke.db"
BID="boundary_north_paddock_3"
ASOF="2024-12-18"

HID="$(sqlite3 "$DB" "select id from herd_configurations where boundary_id='$BID' order by created_at desc limit 1;")"

python -m grc_pipeline.cli compute \
  --db "$DB" \
  --boundary-id "$BID" \
  --herd-config-id "$HID" \
  --as-of "$ASOF" | tee out/compute_1.json

python -m grc_pipeline.cli compute \
  --db "$DB" \
  --boundary-id "$BID" \
  --herd-config-id "$HID" \
  --as-of "$ASOF" | tee out/compute_2.json
```

Expected:

- `recommendation_id` is identical between runs
- `manifest_path` is identical
- manifest sha256 is identical (immutable, idempotent write)

See Task 06 for a full reproducibility and drift-guard smoke test.
