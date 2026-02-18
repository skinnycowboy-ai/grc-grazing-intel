# Runbook (1 page)

## Deploy a logic update (DS-owned change)

1) DS modifies rule logic and/or config thresholds.
2) CI runs: ruff (lint + format check), pytest, container build.
3) Deploy image to **staging**.
4) Run a smoke compute for a known boundary/herd/date and confirm:
   - outputs are non-null and within reasonable bounds
   - DQ results remain stable (no new failures/warnings)
5) Promote to prod.
6) Keep the prior image available for rollback and long-term reproducibility.

## Investigate a DQ alert

1) Query `ingestion_runs` for latest run and inspect:
   - `status`, `error_message`, timestamps, `records_ingested`
2) Query `data_quality_checks` by `run_id` to find failing checks.
3) Common remediation:
   - Open‑Meteo outage/timeouts → retry ingest with backoff
   - stale weather coverage → widen timeframe or adjust stale threshold (DS decision)
   - missing RAP/soil rows → boundary_id mismatch or reference DB issue

## Reproduce a historical recommendation (audit request)

1) Query `grazing_recommendations` for `(boundary_id, herd_config_id, as_of)`.
2) Capture:
   - `model_version`, `config_version`
   - `input_data_versions_json` (hashes + upstream versions)
3) Locate manifest file:
   - `out/manifests/{boundary_id}/{as_of}_{snapshot_id}.json`
4) Re-run compute using the same logic/config version and the same selection rule:
   - “latest RAP composite_date <= as_of”
5) Compare:
   - recommendation row + manifest hashes should match (or explain divergence).
