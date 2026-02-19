# Task 7 — Operational Maturity: Ownership Boundaries + Release/Monitoring Workflow

This document describes **operational boundaries** and the **release + monitoring workflow** for this system, assuming a production environment with two owners:

- **Data Science (DS):** owns model logic, model parameters, and validation thresholds.
- **ML Ops (MLOps):** owns infrastructure, deployments, observability, and versioning operations.

The pipeline design in this repo supports that separation by making every compute run:
1) **explicitly versioned** (`logic_version`, `config_hash`),  
2) **immutably recorded** (append-only DB row + immutable manifest), and  
3) **gated** by **quality checks** and **monitoring thresholds** (exit codes + reports).


## What “production” means here

Even though this take-home runs locally with SQLite, the operational boundaries translate cleanly to production:

- SQLite → Postgres (or similar)
- local `out/` manifests → object storage (S3/GCS/Blob) with write-once policies
- local CLI invocations → scheduled jobs (Airflow/Cron/K8s Jobs)
- stdout JSON → structured logs + metrics + alerting


## Ownership boundaries (who owns what)

### DS owns
**Model logic**
- `src/grc_pipeline/logic/**` (e.g., `days_remaining.py` and future versions)

**Model parameters**
- “DS params” that directly affect the calculation (captured in `ds_params` → `config_hash`)
- Example currently: `min_days_remaining`, `max_days_remaining`

**Validation thresholds**
- Guardrails / sanity thresholds that define “acceptable output behavior”
- Examples currently:
  - `min_days_remaining`, `max_days_remaining` (used in `compute` DQ summary)
  - monitoring thresholds in `PipelineConfig` used by `monitor` (warn/crit thresholds)

> DS changes should always result in a **new logic version and/or config hash**, never a silent overwrite.

### MLOps owns
**Infra + runtime**
- where the DB lives, where manifests live, execution environment, secrets

**Deployment**
- building, packaging, and promoting the pipeline to staging/prod

**Monitoring + alert routing**
- scheduling `monitor`, collecting reports, routing alerts (PagerDuty/Slack/email)

**Versioning operations**
- enforcing version bump rules, immutability, and drift guards
- ensuring rollbacks are possible and auditable


## How the design supports separation cleanly

### 1) Explicit versioning at the API boundary
`compute` requires (or defaults) a `logic_version` and computes a deterministic `config_hash`.

- `logic_version`: “what logic produced this output” (owned by DS)
- `config_hash`: “what parameter set produced this output” (owned by DS; enforced by MLOps)
- Both are stored in the **primary key** used for idempotency/append-only history:
  `(boundary_id, herd_config_id, calculation_date, model_version, config_version)`

This makes it impossible to “accidentally overwrite history” without a version bump.

### 2) Immutable manifests for full reproducibility
Each compute run writes a manifest under:
`out/manifests/{boundary_id}/{as_of}_{snapshot_id}.json`

The manifest captures:
- code metadata (git commit, package version, python/platform)
- the idempotency key
- a full input snapshot (boundary hash, herd hash, features row, logic provenance)
- outputs and DQ summary

In production, MLOps would store these in object storage with write-once policies.

### 3) Drift guard prevents silent changes under the same version key
If inputs change but the version key does not, `compute` refuses to proceed:

- same `(boundary, herd, date, logic_version, config_hash)`
- different provenance payload
→ **hard error** requiring a version bump

This is the operational enforcement mechanism that keeps DS and MLOps aligned.


## Release workflow (where DS pushes updates, how they get deployed)

### DS workflow (authoritative source of model changes)
DS makes changes in a PR that includes:
1) **Logic change** in `src/grc_pipeline/logic/...`
2) **Version bump**:
   - bump `logic_version` (e.g., `days_remaining:v2`), OR
   - change DS parameters (which changes `config_hash`), OR both
3) **Threshold updates** (if needed) in `PipelineConfig` defaults
4) **Evidence**:
   - updated smoke test outputs (documented in the task docs)
   - any new unit tests

DS does **not** change infra wiring, deployment manifests, or alert routing.

### MLOps workflow (promotion + controls)
MLOps merges the DS PR and runs a release pipeline:
1) build (pinned deps, reproducible build)
2) unit tests + formatting/lint
3) integration smoke test (ingest → compute → explain → drift guard)
4) publish artifact (container or wheel)
5) deploy to staging
6) run canary + monitoring gates
7) promote to prod

**Key point:** DS can propose logic/threshold changes, but MLOps owns promotion and rollback.


## What happens when a model update breaks a monitoring threshold?

There are two layers of detection:

### A) Online “compute-time” guardrails (hard constraints)
During `compute`, we summarize guardrails (e.g., days remaining must be within bounds).
If guardrails fail, MLOps can choose to:
- fail the job (block downstream use), or
- write the output but mark DQ as failed and alert

Current implementation records `dq_summary` in the manifest and can be extended to fail compute.

### B) Rolling-window monitoring (warn/crit + exit codes)
`monitor` aggregates recent outputs for a boundary over a window and produces a status:
- `ok` → exit 0
- `warn` → exit 1 (when `fail_on_warn=true`)
- `crit` → exit 2

This is designed to be used as a **deployment gate** and/or an **SLO monitor**.

#### Recommended operational behavior
- **Staging:** block promotion on `warn` or `crit`
- **Prod:** alert on `warn`, page on `crit` (but do not auto-rollback unless required)

#### On threshold break (example runbook)
1) **Freeze rollout**
   - stop promoting the new artifact / set traffic back to prior release
2) **Identify the exact version**
   - from the failing report/manifest, capture:
     - `logic_version`
     - `config_hash`
     - `snapshot_id` + manifest path
3) **Triage: DS vs data issue**
   - DS investigates logic/parameters/thresholds
   - MLOps verifies data freshness + ingestion correctness (RAP/weather/soil)
4) **Choose remediation**
   - rollback to prior release, OR
   - adjust DS thresholds (with explicit version bump), OR
   - fix logic bug (new `logic_version`) and redeploy
5) **Post-incident**
   - record incident + link to manifest(s) for audit trail
   - add test/regression case so it can’t recur silently

The system already supports step (2) because all provenance and versions are persisted.


## Where to put things (repo boundaries that map to ownership)

Suggested (and already mostly true in this repo):

- DS-owned:
  - `src/grc_pipeline/logic/**`
  - DS parameters and validation thresholds in `src/grc_pipeline/config.py`
- MLOps-owned:
  - CI/CD files (GitHub Actions)
  - runtime execution wrappers (Airflow/job specs)
  - infra IaC and secret management
  - monitoring integrations (Prometheus exporters, alert rules)

**Rule of thumb:** if it changes *what the number should be*, DS owns it.  
If it changes *how/where it runs and how we observe it*, MLOps owns it.


## How to enforce this separation in practice (recommended controls)

### Code owners / reviews
- CODEOWNERS: require DS approval for `src/grc_pipeline/logic/**` and threshold changes
- require MLOps approval for deployment/CI/infrastructure changes

### Release contract
- DS changes must update `logic_version` or change `config_hash`
- no “silent” output shifts under the same version key (already enforced by drift guard)

### Promotion gates
- staging gate: run `monitor` over recent outputs and block on warn/crit
- prod gate: alerting and incident response

### Rollback strategy
- keep N prior release artifacts
- allow selection of active default `logic_version` via config
- manifests guarantee you can always explain and reproduce prior outputs


## Smoke test pointer

The canonical “Task 6” smoke test (ingest → compute idempotency → explain provenance → drift guard) is documented in:
- `docs/versioning-task-06.md`

Task 7 builds on that by defining **who changes what** and **what happens operationally** when quality gates trigger.
