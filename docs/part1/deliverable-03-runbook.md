# Operational Runbook (Task 7) — 1 page

Scope: **deploying a model update**, **investigating a data quality alert**, **reproducing a historical prediction**.
Assumes the system is deployed as a container (or Python package) that runs `grc_pipeline.cli` and serves via `grc_pipeline.cli serve`.

---

## 1) Deploy a model update (DS → ML Ops)

### DS responsibilities (PR content)

1. Update **logic** (`src/grc_pipeline/logic/*`) and/or **parameters + validation thresholds** (`PipelineConfig`).
2. Bump **logic_version** for any behavior-changing update:
   - `days_remaining:v1` → `days_remaining:v2`
3. Update docs (Task 6/7) and add/extend tests.

### ML Ops deployment steps

1. **CI gates** (must pass):

   ```bash
   python -m ruff format --check .
   python -m ruff check .
   pytest -q
   ```

2. Build/release artifact (choose one):
   - **Container image**: tag with git SHA + semver (e.g. `grc-pipeline:<gitsha>`).
   - **Python package**: build wheel, publish to internal index.
3. Deploy the new artifact to the runtime environment (Airflow job / cron / K8s job).
4. Run **smoke test** against a staging DB:
   - `ingest` → `compute` (twice) → manifest hash stable → `explain` has populated derived_from.
5. Promote to prod.

## **Rollback rule**

- If monitoring/DQ alerts trip after deploy, roll back deployment artifact to last known good version.
- DS publishes a follow-up fix with a new `logic_version` if behavior changed.

---

## 2) Investigate a data quality alert

### First questions (triage)

- Is this an **ingestion** issue (missing/partial data)?
- Is this a **freshness** issue (weather/RAP stale)?
- Is this an **output monitoring** issue (days remaining outside thresholds)?

### Quick checks (SQLite)

```bash
DB="path/to/prod.db"
BID="boundary_north_paddock_3"
END="2024-12-31"
START="2024-12-01"

# ingestion status + counts
sqlite3 "$DB" "select started_at,status,records_ingested from ingestion_runs order by started_at desc limit 5;"

# DQ check failures
sqlite3 "$DB" "
select run_id,check_name,passed,details_json,checked_at
from dq_checks
where passed=0
order by checked_at desc
limit 20;
"

# features availability in the window
sqlite3 "$DB" "
select count(*) from boundary_daily_features
where boundary_id='$BID'
  and feature_date between '$START' and '$END';
"
```

### Likely fixes

- **Weather missing / incomplete**: re-run `ingest` for the timeframe; confirm Open‑Meteo response completeness checks.
- **RAP stale/missing**: confirm reference DB has RAP rows for boundary/time; re-materialize features.
- **Soil missing**: confirm boundary mapping aligns to reference soil tables.
- **Output monitoring triggered**:
  - If it’s a true issue: DS revisits thresholds/logic and bumps `logic_version`.
  - If it’s data anomaly: ML Ops flags data issue and re-ingests or quarantines the day/boundary.

---

## 3) Reproduce a historical prediction (audit / incident / customer question)

### Preferred: reproduce by recommendation id

```bash
DB="path/to/prod.db"
RID=4
python -m grc_pipeline.cli explain --db "$DB" --recommendation-id "$RID" > out/explain.json
```

This returns provenance including:

- `logic_version`
- `config_hash`
- `data_snapshot_versions`
- `manifest.path` + `manifest.snapshot_id`
- `code_version` (git commit + package version)

### Verify manifest immutability

```bash
python - <<'PY'
import json, hashlib, pathlib
d=json.load(open("out/explain.json"))
p=pathlib.Path(d["provenance"]["manifest"]["path"])
b=p.read_bytes()
print("sha256=", hashlib.sha256(b).hexdigest())
print("manifest_exists=", p.exists())
PY
```

### Optional: recompute (should be idempotent / drift-guarded)

Use the same boundary/herd/as_of and the recorded `logic_version`.
If underlying inputs changed but version key didn’t, compute will **refuse** (drift guard), which is the correct behavior for auditability.

---

## When a model update breaks a monitoring threshold (expected behavior)

1. **Monitoring triggers** (WARN/CRIT) based on DS-defined thresholds.
2. **ML Ops**:
   - treats it as an incident: freeze rollout / rollback deployment artifact if needed.
   - captures evidence: affected boundaries, dates, run manifests, monitoring report artifact.
3. **DS**:
   - determines if the alert indicates a real regression vs expected behavior change.
   - ships a fix or revised thresholds with **new `logic_version`**.
4. Re-deploy through the same gates and re-run smoke tests before promotion.
