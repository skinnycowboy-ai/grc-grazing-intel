# Task 6 — Versioning + Reproducibility (Immutable Manifests)

This document captures the **Task 6** implementation contract and the **green, end-to-end smoke test** that validates:

- **Idempotent compute**: repeated `compute` with the same version key returns the same DB row + same manifest.
- **Immutable manifests**: manifests are written once and never mutated.
- **Explainability**: `explain` reconstructs the “why” from the manifest snapshot and includes **RAP + boundary `derived_from`**.
- **Drift guard**: if underlying inputs drift while the version key stays the same, the pipeline **refuses to overwrite history**.

---

## What “versioned + reproducible” means here

A grazing recommendation is uniquely defined by the following **version key**:

- `boundary_id`
- `herd_config_id`
- `calculation_date` (aka `as_of`)
- `logic_version` (stored as `model_version`, e.g. `days_remaining:v1`)
- `config_hash` (stored as `config_version`)

### Immutable outputs

When `compute` runs it creates:

1. A row in `grazing_recommendations` (append-only semantics; never overwritten).
2. A content-addressed manifest snapshot on disk:

```text
out/manifests/{boundary_id}/{as_of}_{snapshot_id}.json
```

1. A thin provenance pointer stored in `grazing_recommendations.input_data_versions_json` that includes:

- `manifest.path` + `manifest.snapshot_id`
- data snapshot versions (RAP/soil/weather)
- boundary + herd snapshot hashes
- `logic_version`, `config_hash`
- code version (`git_commit`, `package_version`)

### Drift guard

If a recommendation already exists under the same:

```text
(boundary_id, herd_config_id, as_of, logic_version, config_hash)
```

…but the newly computed provenance payload differs, `compute` exits non-zero with:

> Existing recommendation already present with DIFFERENT provenance … Refusing to overwrite history.

This is deliberate: it prevents “same version key, different reality” bugs.

---

## Requirements

- Run from repo root.
- `sqlite3` CLI installed.
- A Python venv with dependencies installed (see `README.md`).
- Input fixtures available:
  - `pasture_reference.db`
  - `sample_boundary.geojson`
  - `sample_herds_pasturemap.json`

---

## Preflight sanity checks

```bash
source .venv/bin/activate

python -m ruff format --check .
python -m ruff check .
pytest -q
```

---

## ✅ Green end-to-end smoke test

> This is the “reviewer-ready” reproducibility proof.
It validates:

- ingestion produces the expected features rows
- compute is idempotent (DB row + manifest path/hash stable)
- explain is backed by the manifest and includes RAP + boundary provenance
- drift guard trips on silent input mutation

```bash
set -euo pipefail

rm -rf out
mkdir -p out
cp pasture_reference.db out/pipeline_smoke.db

DB="out/pipeline_smoke.db"
BID="boundary_north_paddock_3"
START="2024-01-01"
END="2024-12-31"
ASOF="2024-12-18"

python -m grc_pipeline.cli ingest   --db "$DB"   --boundary-geojson sample_boundary.geojson   --boundary-id "$BID"   --boundary-crs EPSG:4326   --herds-json sample_herds_pasturemap.json   --start "$START"   --end "$END"

sqlite3 "$DB" "select status,records_ingested from ingestion_runs order by started_at desc limit 1;"
sqlite3 "$DB" "select count(*) from boundary_daily_features where boundary_id='$BID' and feature_date between '$START' and '$END';"

# Note: herd_configurations uses created_at (NOT ingested_at).
HID="$(sqlite3 "$DB" "select id from herd_configurations where boundary_id='$BID' order by created_at desc limit 1;")"
echo "HID=$HID"

python -m grc_pipeline.cli compute   --db "$DB"   --boundary-id "$BID"   --herd-config-id "$HID"   --as-of "$ASOF" | tee out/compute_1.json

RID1="$(python -c 'import json;print(json.load(open("out/compute_1.json"))["recommendation_id"])')"
MP1="$(python -c 'import json;print(json.load(open("out/compute_1.json"))["manifest_path"])')"
echo "RID1=$RID1"
test -f "$MP1" && echo "✅ manifest exists"
sha256sum "$MP1" | tee out/manifest_1.sha256

# Idempotent rerun should be identical (same recommendation_id, same manifest hash)
python -m grc_pipeline.cli compute   --db "$DB"   --boundary-id "$BID"   --herd-config-id "$HID"   --as-of "$ASOF" | tee out/compute_2.json

RID2="$(python -c 'import json;print(json.load(open("out/compute_2.json"))["recommendation_id"])')"
MP2="$(python -c 'import json;print(json.load(open("out/compute_2.json"))["manifest_path"])')"
test "$RID1" = "$RID2" && echo "✅ same recommendation_id"
test "$MP1" = "$MP2" && echo "✅ same manifest path"
sha256sum "$MP1" | tee out/manifest_2.sha256
diff -u out/manifest_1.sha256 out/manifest_2.sha256 && echo "✅ manifest unchanged"

# Explain should include non-null derived_from.rap and derived_from.boundary
python -m grc_pipeline.cli explain --db "$DB" --recommendation-id "$RID1" | tee out/explain.json

python - <<'PY'
import json
d=json.load(open("out/explain.json"))
rap = d["because"]["available_forage_kg"]["derived_from"].get("rap")
bnd = d["because"]["available_forage_kg"]["derived_from"].get("boundary")
assert rap is not None, "expected derived_from.rap to be populated"
assert bnd is not None, "expected derived_from.boundary to be populated"
assert rap.get("source_version"), "expected rap.source_version"
assert rap.get("composite_date"), "expected rap.composite_date"
assert rap.get("biomass_kg_per_ha") is not None, "expected rap.biomass_kg_per_ha"
assert bnd.get("boundary_id"), "expected boundary.boundary_id"
print("✅ explain derived_from populated")
PY

# Drift guard: mutate herd snapshot in-place => compute must refuse overwrite with exit code != 0
sqlite3 "$DB" "update herd_configurations set config_snapshot_json=json_set(coalesce(config_snapshot_json,'{}'),'$.note','DRIFT_TEST') where id='$HID';"

set +e
python -m grc_pipeline.cli compute --db "$DB" --boundary-id "$BID" --herd-config-id "$HID" --as-of "$ASOF"
RC=$?
set -e
test "$RC" -ne 0 && echo "✅ drift guard triggered (exit=$RC)"
```

### Expected “green” signals

- `ingestion_runs` ends in `succeeded|…`
- `boundary_daily_features` count is `366` for the 2024 date window (2024 is leap year).
- `compute` rerun prints:
  - `✅ same recommendation_id`
  - `✅ manifest unchanged`
- `explain` assertion prints:
  - `✅ explain derived_from populated`
- drift guard prints:
  - `✅ drift guard triggered (exit=1)`

---

## Notes for reviewers

- The **manifest** is the durable “evidence pack” for a recommendation:
  - full input snapshot (boundary hash, herd snapshot hash, features row, and logic provenance),
  - output values,
  - data snapshot versions (RAP/soil/weather),
  - code version (`git_commit`, `package_version`).

- The **DB row** stores minimal pointers/hashes only (no large blobs).
- Any meaningful change to logic requires bumping `logic_version` (e.g. `days_remaining:v2`).
- Any meaningful change to config parameters yields a different `config_hash` automatically.

---

## Optional cleanup

```bash
rm -rf out
```
