# CI/CD (Design)

## Triggers

- **Pull request**: validate code quality and run tests.
- **Push to main**: same validations + build container artifact (Option A in this repo).

## Pipeline steps

1) Checkout
2) Setup Python 3.12
3) Install: `pip install -e ".[dev]"`
4) Lint: `ruff check .`
5) Format check: `ruff format --check .`
6) Unit tests: `pytest -q`
7) Container build (Option A):
   - `docker build -t grc-grazing-intel:${GITHUB_SHA} .`

## Production-style extension (not implemented here)

If deployed to a managed environment:

- Build image tagged by git SHA and push to a registry
- Deploy to **staging**
- Run a smoke job:
  - ingest + compute on a known fixture boundary/herd/date
  - assert DQ and guardrails are within thresholds
- Promote to prod via immutable image digest
- Rollback by redeploying the previous digest

## Deployment safety notes

- Avoid heavy compute directly on Airflow workers; have the DAG trigger an external runner (Batch/ECS/EKS).
- Keep metrics label cardinality bounded:
  - label on **route template** (not raw path) and method/status only
  - do **not** label by `boundary_id` (high-cardinality).
