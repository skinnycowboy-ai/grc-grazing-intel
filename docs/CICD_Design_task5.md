# Task 5 — CI/CD (Design Only): Testing + Deployment Safety for Logic Updates

**Goal:** Automate testing and deployment of **logic updates** (e.g., `days_remaining` rules, thresholds/config)
to a production-like environment with strong **traceability**, **rollbacks**, and **guardrails**.

> No working code. This document is an implementation-grade CI/CD design spec.

---

## 1) What we are optimizing for (rubric alignment)

- **Reproducibility:** pinned deps, deterministic builds, immutable images, promotion without rebuild.
- **Idempotency/backfills:** deployment should not change run outputs unless logic/config changes; replayable by `run_id`.
- **DQ gates:** DQ failures block compute (or downgrade confidence) and surface clearly in monitoring.
- **Lineage/provenance:** every response ties back to `git_sha`, `logic_version`, `config_version`, input hashes, and source timestamps.
- **Monitoring/operability:** health + latency + error rate + business guardrails; fast rollback.
- **Cost awareness:** keep staging/prod-like lean; avoid high baseline infra unless needed.

---

## 2) Assumed high-level architecture

### Containerized API

- FastAPI service (read-only) that serves recommendations, e.g. `GET /v1/recommendations/{boundary_id}`.
- The service runs from a **Docker image** built in CI.
- Runtime configuration via env (thresholds, staleness windows), but **logic version** is code-controlled.

### Artifact provenance contract (must be emitted)

Every recommendation response and/or run manifest should include at least:

- `git_sha`
- `logic_version` (semver for rules)
- `config_version` (semver for thresholds)
- `generated_at_utc`
- `boundary_hash`, `herd_config_hash`
- source timestamps per upstream (`weather_fetched_at`, `rap_as_of_date`, etc.)

This makes rollbacks and A/B comparisons defensible.

---

## 3) CI/CD triggers

### PR (pull request) triggers

On PR open/update:

- lint + typecheck
- unit tests
- integration tests (DB join + compute)
- golden tests (logic regression)
- container build (optional, cached) to verify Dockerfile still builds

PR merges are blocked unless all checks pass.

### Main branch triggers

On merge to `main`:

- build immutable image tagged by git SHA
- generate SBOM
- push image to registry
- deploy to **staging**
- run staging smoke + canary dataset checks
- gate promotion to prod via approval (or auto-promotion if desired)

### Release tag triggers (optional)

On tag `vX.Y.Z`:

- publish immutable release image `:vX.Y.Z`
- promote that exact image to prod

---

## 4) Test coverage strategy (what runs where)

### 4.1 PR checks (fast + high-signal)

## **Static checks**

- format/lint (ruff/black)
- typecheck (mypy or pyright)
- dependency audit (pip-audit)
- basic security lint (bandit)

## **Unit tests (logic-focused)**

- `days_remaining = available_forage_kg / daily_consumption_kg`
- edge cases: zero/negative headcount, missing intake, divide-by-zero protection
- staleness classification behavior (fresh/stale/blocked)
- deterministic confidence scoring (if computed from data health)

## **Integration tests (pipeline semantics)**

- ingest -> materialize `boundary_daily_features` -> compute recommendation
- idempotency: reruns should not create duplicates; stable unique keys should hold
- DQ gates: missing weather day fails; RAP missing all-days fails; stale data warns/degrades as designed

## **Golden tests (MRV-grade regression)**

- 1–3 pinned scenarios with frozen inputs (boundary + herd config + timeframe)
- assert exact outputs for key fields and manifest metadata
- catch “logic drift” from refactors

## **API contract tests**

- OpenAPI schema snapshot (or simple contract assertions)
- required metadata fields present (run/provenance)

### 4.2 Staging checks (production-like)

After deploying the new image to staging:

- `GET /healthz` and `GET /readyz`
- smoke request for known boundary/herd_config/as_of
- “canary dataset” job:
  - run compute across ~10–50 fixture cases
  - assert outputs are within expected ranges and metadata is correct
- optional: run a brief load test (e.g., 100–500 requests) and assert p95 latency under threshold

---

## 5) Deployment options (runtime) — recommended + alternatives

### Option A (recommended default): AWS ECS Fargate (simple, fast, rollback-friendly)

**Fit:** best for take-home / lean production-like environment.

- Registry: Amazon ECR
- Deploy: ECS service with an ALB
- Rollback: redeploy previous task definition revision (one command)
- Safety: ALB health checks gate rollout; canary via two services or CodeDeploy blue/green

## **Pros**

- low operational overhead
- simple rollback mechanics
- production-like enough for API workloads

## **Risks**

- fewer native progressive delivery features than K8s/OpenShift unless you add CodeDeploy patterns
- if your target platform is OpenShift, ECS is “adjacent,” not identical

### Option B (strong alternative): ROSA (Red Hat OpenShift on AWS)

**Fit:** excellent if you want production-like behavior matching OpenShift/K8s customers.

- Registry: ECR (fastest) or Quay (OpenShift-native supply chain)
- Deploy: OpenShift `Deployment` + `Service` + `Route`, plus `ConfigMap`/`Secret`, HPA
- Rollback:
  - GitOps: revert commit (best audit story)
  - or `oc rollout undo` (direct deploy)

## **Recommended ROSA deploy pattern: GitOps**

- Use OpenShift GitOps (Argo CD) to sync manifests from a `deploy/` folder or a separate GitOps repo.
- CI updates the image tag in Git (or creates a PR); Argo CD applies.
- Rollback is a git revert; promotion is deterministic.

## **Progressive delivery (optional, best practice)**

- Use Argo Rollouts for canary/blue-green (traffic shifting) with automated abort based on metrics.

## **Pros (ROSA)**

- strongest deployment safety + policy (RBAC, NetworkPolicy, quotas)
- best fit for K8s/OpenShift production environments
- GitOps is highly auditable

## **Risks (with mitigations)**

- **Higher baseline cost than ECS** (cluster always-on), *mitigated by* AWS commercial alignment: ROSA is billed via AWS Marketplace and supports 1/3-year ROSA service fee contracts/discounts where applicable.
- **More platform surface area than ECS** (Routes, operators, quotas, RBAC), *mitigated by* ROSA being a managed service where Red Hat SRE handles much of cluster lifecycle and upgrade operations—though app-level ops remain your responsibility.
- Need deliberate registry auth/IAM setup for ECR pulls (standard ROSA/Kubernetes wiring; solvable but must be designed explicitly).

### Option C: EKS + Argo CD/Rollouts

## **Pros (EKS/Argo)**

- portable Kubernetes patterns, advanced rollout controls

## **Risks (EKS/Argo)**

- more ops than ECS for limited incremental value in a take-home

## **Recommendation**

- Default to **ECS Fargate** for cost/simplicity.
- Offer **ROSA** as the “enterprise-realistic” target when OpenShift alignment matters.

---

## 6) Rollout + rollback strategy (deployment safety)

### 6.1 Immutable artifacts + promotion without rebuild

- Build once on merge to `main`
- Tag image as `sha-<GIT_SHA>`
- Deploy the same image to staging, then promote the same image to prod

This prevents “works in staging, different in prod” drift.

### 6.2 Health checks and readiness gates

- `/healthz`: process up
- `/readyz`: dependencies ready (DB reachable, migrations compatible, configs loaded)
- readiness must fail fast if required config missing

### 6.3 Progressive delivery (preferred)

- **ECS:** use blue/green via two target groups (or CodeDeploy) and shift traffic gradually.
- **ROSA:** use Argo Rollouts canary steps (5% → 25% → 50% → 100%) with analysis gates.

### 6.4 Automated rollback triggers

Rollback the deployment automatically if any of these cross thresholds:

- **availability:** readiness/health check failures
- **errors:** 5xx rate > X% over Y minutes
- **latency:** p95 > threshold
- **business guardrails (logic sanity):**
  - spike in `days_remaining <= 0` over baseline
  - spike in `days_remaining > cfg.max_days_remaining`
  - sharp increase in “blocked” due to staleness (could indicate upstream outage)

### 6.5 Database/schema evolution

If schema changes are needed (ideally rare for logic-only changes):

- expand/contract migrations
- deploy code that can read both versions first
- migrate data
- remove old fields in a later release

This avoids “new code can’t read old DB” failures.

---

## 7) CI/CD pipeline (written description)

### PR pipeline

Trigger: `pull_request`

1) Checkout + install deps (pinned)
2) Lint/format/typecheck
3) Unit tests
4) Integration tests (fixture DB + ingest/join/compute)
5) Golden tests (pinned scenarios)
6) (Optional) build image to ensure Dockerfile remains valid

### Main pipeline

Trigger: push to `main`

1) Build image `:sha-<GIT_SHA>`
2) Generate SBOM and attach artifact
3) Push image to registry
4) Deploy to staging (ECS or ROSA)
5) Run staging smoke + canary dataset validation
6) Gate promotion to prod
7) Promote same image to prod
8) Observe rollout metrics and guardrails; rollback automatically if triggered

---

## 8) CI/CD diagram (ECS + ROSA paths)

```mermaid
flowchart TD
  PR[Pull Request] --> CI[CI: lint/typecheck/unit/integration/golden]
  CI -->|pass| PRok[PR green]
  CI -->|fail| PRfail[Block merge]

  main[Merge to main] --> build[Build image + SBOM; tag :sha]
  build --> push[Push to registry (ECR/Quay)]

  push --> deployStg[Deploy to Staging]
  deployStg --> stgChecks[Smoke + canary dataset + contract checks]
  stgChecks -->|fail| stgRollback[Auto rollback + alert]

  stgChecks -->|pass| approve[Approval gate / promote]
  approve --> promote[Promote same image to Prod]

  promote --> path{Runtime target}
  path --> ECS[ECS service rollout]
  path --> ROSA[ROSA rollout (GitOps/Argo CD)]

  ECS --> obs[Monitor: health, 5xx, p95, guardrails]
  ROSA --> obs

  obs -->|good| done[Rollout complete]
  obs -->|bad| rollback[Auto rollback to prior revision]
```

---

## 9) Cost notes (production-like but lean)

- ECS staging can be very low cost (small desired count; minimal infra overhead).
- ROSA has a higher floor cost due to the cluster; consider:
  - a single small staging cluster, or
  - using ROSA only for prod-like validation when OpenShift alignment is critical.

---

## 10) Defendability (how to justify in interview)

- “Logic changes are gated by **golden tests** and **canary dataset checks**, not just unit tests.”
- “We promote **immutable images** by git SHA from staging to prod; no rebuild drift.”
- “Rollback is **one command** and can be automated based on health + guardrails.”
- “Every output is traceable to **git_sha + logic/config versions + input hashes + source timestamps**.”

---

## 11) AI Tools Used (for consistency with repo transparency)

- **Tool:** OpenAI ChatGPT/Codex, Anthropic Claude Code

  **Purpose:** Design review and articulation of CI/CD safety patterns (tests, rollout/rollback, provenance).

  **What I refined (MTI):**
  - Anchored the design to MRV-grade traceability (immutable artifacts, promotion without rebuild, provenance fields).
  - Chose pragmatic runtime options (ECS default + ROSA as OpenShift-aligned alternative) with explicit cost/safety tradeoffs.
  - Defined concrete rollback triggers including business guardrails, not just infrastructure metrics.

  **What I verified manually (MTI):**
  - Confirmed the proposed test layers map to actual repo primitives (unit/integration/golden, DB join semantics, API contracts).
  - Ensured the deployment design supports deterministic replay and audit narratives via `run_id` and version metadata.
