# Part 2 — Credit Verification Pipeline Design (Good / Better / Best)

## Goal and constraints

GRC needs an automated, audit-ready way to verify that a ranch has met *protocol practice requirements* before issuing carbon credits. Verification must:

- Pull evidence from multiple systems (PastureMap, Salesforce, soil lab APIs, third‑party hardware feeds).
- Assign **Good / Better / Best** compliance tiers with **data completeness checks**.
- Support **human-in-the-loop** (MRV team) for final approval with an immutable audit trail.
- Survive regulatory scrutiny (e.g., CAR‑SEP / TRS-style expectations): reproducible decisions, lineage, and “show your work” evidence packs.

The GMP and LSA reference documents illustrate two core evidentiary anchors:

- **GMP**: the ranch’s committed practice change plan (baseline vs project, prescriptions, sampling plan, rotation guidance, etc.). It includes explicit expectations around documenting progress and a recommended soil test array cadence (e.g., Haney / PLFA / TND).  
- **LSA**: the legal enrollment boundary / parcel definitions and creditable acreage, which must reconcile with operational activity (PastureMap logs) and sampling locations.

## Compliance tiers (policy)

**Good (minimum):**

- Final soil sample map complete
- GMP on file
- Soil sampling done

**Better (active engagement):**

- Good + PastureMap activity logging herd movements following GMP guidelines
- Quarterly check-ins completed

**Best (full digital integration):**

- Better + third-party hardware (Ranchbot / Halter / similar) providing automated grazing records

Key design choice: treat tiers as **policy bundles** composed of **verifiable claims**. A tier is “earned” when all required claims for that tier are satisfied, *and* all lower tiers are satisfied.

## Architecture overview

### High-level components

1) **Connectors / Ingestion**

- Pull from:
  - PastureMap: paddocks, boundaries, herd moves, notes, photo evidence, activity timeline
  - Salesforce: enrollment + contract + protocol metadata, quarterly check-ins, MRV case state
  - Soil labs: sample kits, chain-of-custody, results, geolocated sampling points
  - Hardware feeds: Ranchbot / Halter events (water station visits, GPS herd location, virtual fence moves, etc.)
- Produce **raw immutable snapshots** (JSON/CSV/PDF binaries) + metadata.

1) **Evidence Normalization**

- Transform raw snapshots into a normalized set of:
  - **Evidence items** (documents, events, lab results) with stable IDs, hashes, timestamps, and source pointers
  - **Claims** (machine-checkable assertions) derived from evidence items:
    - `gmp_on_file`, `lsa_on_file`, `soil_sampling_complete`, `soil_map_complete`
    - `pasturemap_moves_present`, `moves_align_to_gmp_guidelines`
    - `quarterly_checkins_complete`
    - `sensor_coverage_sufficient`, `automated_grazing_records_present`

1) **Verification Engine**

- Runs as an idempotent batch job per ranch + reporting period.
- Evaluates:
  - Data completeness gates
  - Claim satisfaction
  - Tier assignment
  - Conflicts / ambiguity flags
- Emits a **Ranch Verification Record** + an **Evidence Pack**.

1) **Human Review UI + Workflow**

- A queue of ranch-period verification cases with:
  - tier recommendation
  - missing evidence / conflict flags
  - one-click open of the evidence pack
- MRV reviewers can:
  - approve
  - request-more-info
  - override tier / decision (with mandatory reason & attachment)
- All actions append to an immutable audit log.

1) **Regulatory-grade storage**

- **Relational DB** for canonical records + audit log (e.g., Postgres).
- **Object storage** for raw evidence + generated evidence packs (S3/GCS/Azure Blob).
- Optional: warehouse/lakehouse for analytics (Iceberg/Delta/Snowflake/BigQuery) later.

### Why this split works

- **Binary evidence** (PDFs, maps, images) belongs in object storage with hashes and retention policy.
- **Decision state** (what tier, what claims passed, who approved) belongs in a transactional DB.
- **Snapshots** + **versioned policy** guarantee reproducibility (“why was this approved?”).

## Data model and reproducibility strategy

### Core identifiers

- `ranch_id`: stable internal ID (links to Salesforce account / PastureMap ranch)
- `period_id`: e.g., `2026-Q1` or `[2026-01-01, 2026-03-31]`
- `verification_run_id`: immutable ID for an engine execution
- `policy_version`: version of tier requirements + thresholds
- `connector_versions`: per-source connector build + schema version
- `snapshot_ids`: per-source immutable snapshot references (and hashes)

### “Evidence Pack” (immutable audit artifact)

For each `verification_run_id`, build a pack containing:

- pointers + hashes for all raw evidence used
- derived claim evaluations (inputs, thresholds, outputs)
- reconciliation summaries (LSA parcels vs PastureMap boundaries, sampling points coverage, etc.)
- a human-readable report (PDF/HTML) and machine-readable manifest (JSON)

This mirrors Part 1’s “run manifest” pattern: **promote without rebuild** and reproduce decisions by reloading the same snapshots + policy version.

## Data completeness checks and tier assignment

### Completeness gates (examples)

These run before tier logic; failures create “NOT_READY” or “NEEDS_REVIEW” states.

- **Enrollment completeness**
  - LSA present and parsable
  - parcel list + creditable acreage non-empty
  - boundary geometry valid
- **GMP completeness**
  - GMP document present
  - (optional best-effort parsing) structured extraction of:
    - practice start date, rotation guidance, monitoring requirements, soil sampling plan
- **Soil sampling completeness**
  - sample event(s) exist for period or protocol-required cadence
  - chain-of-custody present
  - sample points fall within enrolled parcels/boundary
  - “final soil sample map” generated and stored
- **PastureMap completeness (Better+)**
  - herd move events exist for period
  - move events reference paddocks within enrolled boundary
  - event density meets minimum (e.g., % days with events above threshold OR minimum moves per month)
- **Sensor completeness (Best)**
  - feed connected and authenticated
  - coverage above threshold (e.g., ≥X% of days with location pings)
  - data freshness (no gaps > N days)

### Tier logic (policy-as-code)

Define tier requirements in a versioned YAML policy file:

- Tier Good requires: `gmp_on_file`, `soil_sampling_complete`, `soil_map_complete`
- Tier Better adds: `pasturemap_moves_present`, `moves_align_to_gmp_guidelines`, `quarterly_checkins_complete`
- Tier Best adds: `sensor_coverage_sufficient`, `automated_grazing_records_present`

Policy versions are deployed like “model versions”:

- DS/Policy owners change **policy files** and thresholds.
- Ops owns deployment and rollback.
- Each verification run records the exact `policy_version` and policy hash.

## Ownership boundaries (DS vs Ops)

Even though Part 2 is “no code,” the system should reflect clean ownership boundaries:

## **Data Science / MRV policy owners**

- Tier policy and thresholds (what constitutes “moves align to GMP,” sensor coverage thresholds, etc.)
- Claim definitions and mapping to evidence
- Validation logic semantics (what is a “conflict,” “ambiguous,” “insufficient”)

## **ML Ops / Platform owners**

- Ingestion infrastructure (connectors, credentials, queues)
- Snapshotting, storage, retention, access control
- Orchestration (schedules), monitoring, alerting, and backfills
- Deployment pipelines for policy updates (tests + canary runs)

Interface contract between them:

- Policy files + test fixtures (golden cases) live in a versioned repo.
- CI runs verification engine against fixture snapshots to ensure policy changes behave as expected.
- Promotion: staged environments + canary ranches before broad rollout.

## Edge cases (special handling)

1) **Partial data availability**

- Example: soil lab API outage during sampling season → soil results delayed.
- Handling:
  - completeness gate marks `NOT_READY`
  - case stays open with SLA timer
  - MRV can approve “conditional” only if protocol permits (otherwise block)

1) **Conflicting boundaries / records**

- Example: LSA parcel geometry differs from PastureMap paddock boundary export.
- Handling:
  - geometry reconciliation produces an overlap score + conflict flag
  - require MRV adjudication; store corrected mapping as an override artifact
  - never silently “pick one”; the decision must be explicit and audited

1) **Tier transitions mid-period**

- Example: Ranch starts Q1 at Good but adds sensors in Feb → becomes Best.
- Handling:
  - tier assigned per *period* with evidence windows
  - support “tier_effective_date” and “partial-period” labeling in the record
  - credits issuance logic can choose the appropriate tier for the crediting window

1) **Retroactive documentation**

- Example: GMP signed late but claims start date earlier.
- Handling:
  - enforce “document created_at” vs “claimed effective_date”
  - allow override with reason + supporting evidence (email, signed PDF) and flag as “retroactive”

1) **Identity / entity resolution**

- Example: Salesforce ranch name differs from PastureMap ranch name.
- Handling:
  - deterministic mapping table + manual resolution UI
  - record the mapping decision in audit log

## Human-in-the-loop review interface concept

### Review queue (MRV workbench)

Columns:

- Ranch, Period, Recommended Tier, Confidence, Flags, Missing Evidence, Assigned Reviewer, SLA clock

### Case detail view

Tabs:

1) **Summary**
   - recommended tier + score breakdown
   - completeness gate results
   - conflicts/ambiguity flags
2) **Enrollment (LSA)**
   - parcel list, acreage, boundary map overlay
   - reconciliation report vs PastureMap paddocks
3) **GMP**
   - GMP PDF viewer + extracted structured fields
   - policy checklist (expected practices and monitoring cadence)
4) **Soil Sampling**
   - sampling points map overlay vs enrolled area
   - lab results + chain-of-custody
5) **PastureMap Activity**
   - move timeline, density stats, adherence-to-guidelines checks
6) **Sensors (Best)**
   - coverage plots, gap report, device registry
7) **Audit Trail**
   - immutable log of evaluations and reviewer actions

### Overrides and approvals

- Overrides require:
  - selecting a reason code
  - free-text note
  - optional attachment
- Final approval produces:
  - reviewer signature, timestamp
  - record state transitions to `APPROVED_LOCKED`
  - evidence pack pointer and hash are sealed

## What I’d improve with more time

- GMP/LSA **structured extraction**: robust parsing + human validation UI to turn PDFs into strongly typed facts.
- “Move alignment to GMP” model: more nuanced rules (seasonality, drought exceptions) and stronger explainability.
- Stronger geospatial reconciliation: automated snapping/matching for paddocks to parcels, and robust handling of multipolygons.
- Protocol-aware engine: explicit CAR‑SEP / TRS rule modules and audit report templates per protocol.
- A formal “evidence confidence” scoring framework and calibrated reviewer workload routing.

## Assumptions

- LSA is the legal source of truth for enrolled boundary/acreage; PastureMap boundaries are operational and may drift.
- A verification case is evaluated per ranch per reporting period (monthly or quarterly), and the system can backfill.
- Third-party sensor feeds can be normalized into a common “grazing event” schema with coverage metrics.
- Protocol rules permit human override with documented rationale (and require it to be auditable).

## AI tools used

- OpenAI ChatGPT / Codex and Anthropic Claude Code were used for outlining and wording refinement of this design doc,
  and to sanity-check the architecture for auditability and operability. Final decisions, tradeoffs, and structure were
  reviewed and edited manually.
