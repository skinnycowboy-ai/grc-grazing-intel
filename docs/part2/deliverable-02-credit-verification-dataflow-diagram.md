# Part 2 — Data Flow Diagram (Credit Verification)

> This is a **Mermaid** diagram to keep the repo self-contained. Render it in GitHub or any Mermaid viewer.

```mermaid
flowchart LR
  %% SOURCES
  subgraph Sources
    PM[PastureMap\n(paddocks, herd moves, notes, photos)]
    SF[Salesforce\n(enrollment, protocol, check-ins, case state)]
    LAB[Soil Lab APIs\n(results, chain-of-custody, sample points)]
    HW[Hardware Feeds\n(Ranchbot/Halter/etc)]
    DOCS[GMP / LSA PDFs\n(uploaded docs)]
  end

  %% INGESTION + SNAPSHOTS
  subgraph Ingestion[Connectors + Snapshotting (Ops-owned)]
    C1[Connector Jobs\n(auth, pull, paginate)]
    RAW[(Raw Snapshot Store\nobject storage)]
    META[(Snapshot Index DB\nmetadata + hashes)]
  end

  %% NORMALIZATION
  subgraph Normalize[Normalization + Claim Extraction]
    N1[Parse + Normalize\n(ids, timestamps, geometries)]
    EVID[(Evidence Items\n(doc/event/lab-result))]
    CLAIMS[(Claims\n(machine-checkable assertions))]
    RECON[Geo Reconciliation\nLSA parcels vs PM paddocks\n+ sampling points coverage]
  end

  %% VERIFICATION ENGINE
  subgraph Verify[Verification Engine (policy-as-code)]
    GATES[Completeness Gates\nNOT_READY / NEEDS_REVIEW]
    POLICY[Policy Eval\nGood/Better/Best\n(policy_version)]
    OUTREC[(Ranch Verification Record\nstatus + tier + metrics)]
    PACK[(Evidence Pack\nmanifest + report\nhashes + pointers)]
  end

  %% HUMAN REVIEW
  subgraph HITL[Human Review (MRV workbench)]
    UI[Reviewer UI\nqueue + case view]
    DEC[Approve / Request Info / Override\n(reason + notes + attachments)]
    AUDIT[(Immutable Audit Log\nappend-only)]
  end

  %% OUTPUTS
  subgraph Outputs
    ISS[Credit Issuance System\n(consumes APPROVED_LOCKED records)]
    API[Verification API\n/read-only queries]
  end

  %% FLOWS
  PM --> C1
  SF --> C1
  LAB --> C1
  HW --> C1
  DOCS --> C1

  C1 --> RAW
  C1 --> META

  RAW --> N1
  META --> N1

  N1 --> EVID
  N1 --> CLAIMS
  N1 --> RECON
  RECON --> CLAIMS

  CLAIMS --> GATES
  EVID --> GATES
  GATES --> POLICY
  POLICY --> OUTREC
  POLICY --> PACK

  OUTREC --> UI
  PACK --> UI
  UI --> DEC
  DEC --> AUDIT
  DEC --> OUTREC

  OUTREC --> API
  OUTREC --> ISS

  %% VERSIONING / OWNERSHIP ANNOTATIONS
  classDef ops fill:#f3f4f6,stroke:#111827,stroke-width:1px;
  classDef ds fill:#ecfeff,stroke:#0f766e,stroke-width:1px;
  classDef audit fill:#fff7ed,stroke:#9a3412,stroke-width:1px;

  class Ingestion,RAW,META,C1 ops;
  class Normalize,N1,EVID,CLAIMS,RECON ds;
  class Verify,GATES,POLICY,OUTREC,PACK ds;
  class HITL,UI,DEC audit;
```

## Versioning points (what gets pinned in every record)

- `policy_version` + policy hash
- per-source `snapshot_id` + snapshot hash
- connector build versions
- geo reconciliation algorithm version
- evidence pack manifest hash

These are what allow “replay the decision” under audit: load the same snapshots + the same policy.
