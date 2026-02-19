# Task 4 — Grazing Intelligence Visualization (Design Only)

**Goal:** Present grazing intelligence recommendations to a rancher in PastureMap with clear **actionability**, **confidence**, and **data health** (stale/incomplete handling), while remaining **MRV/audit-ready** (provenance, reproducibility).

> **No working code**. This is a design spec + wireframes suitable to check into the repo.

---

## 1) Product intent

### Primary rancher question

### **“When should I move, and why?”**

### Design principles

- **Decision-first:** Show the next move recommendation prominently; details are one tap away.
- **Honest uncertainty:** Confidence must be visible at a glance and explainable.
- **Fail soft, not silent:** If data is stale or incomplete, degrade confidence or block recommendations explicitly.
- **Audit-ready:** Every recommendation view exposes `run_id`, `logic_version`, and input timestamps.

---

## 2) Where it lives in PastureMap

1. **Ranch Dashboard (Overview)**
   - “Grazing Intelligence” card
   - “Next move” module + list of upcoming moves

2. **Map View**
   - Optional “Forage (RAP)” heatmap overlay
   - Pasture chips for “days remaining + move window + confidence”

3. **Pasture Detail**
   - Deep view: forage balance, trends, drivers, assumptions, confidence breakdown, data health/provenance

4. **Notifications**
   - “Move window opens”
   - “Data stale” / “Confidence dropped” alerts

---

## 3) Wireframes / mockups (ASCII)

### 3.1 Ranch Dashboard (action-first)

```yaml
┌──────────────────────── Ranch: Mimms Unit ────────────────────────┐
│ Grazing Intelligence                                              │
│ Last updated: Feb 19, 2026 06:15  •  Run: 20260219T121500Z         │
│ Logic v1.3  •  Inputs: Herd=Stockers(400), Pasture Set=44          │
├───────────────────────────────────────────────────────────────────┤
│ NEXT MOVE                                                         │
│ Pasture: North Flat (P-12)                                        │
│ Days remaining: 6.2 days                                          │
│ Move window: Feb 25–26                                            │
│ Confidence:  High  ████████░░  (82)                               │
│ Key drivers: RAP biomass ↓ • No rain next 7d • High intake demand  │
│ [View pasture]  [Explain]  [Set reminder]                          │
├───────────────────────────────────────────────────────────────────┤
│ UPCOMING MOVES (next 14 days)                                     │
│ 1) P-12  Feb 25–26   6.2d   High (82)   last RAP: Feb 10           │
│ 2) P-08  Mar 03–05   12.4d  Med  (61)   missing: 1 temp fence log  │
│ 3) P-21  Mar 08–10   17.9d  Low  (38)   RAP stale > 21d            │
│                                                                   │
│ Data Health:  RAP ✓  Weather ✓  Herd ✓  Boundary ✓                 │
└───────────────────────────────────────────────────────────────────┘
```

## **Notes (Ranch Dashboard)**

- **Move window** is a range (more honest than a single day).
- Confidence is **label + bar + numeric score**.
- Each upcoming move row includes the most important “health hint” (e.g., last RAP date).

---

### 3.2 Map View (spatial intuition)

```less
┌──────────────────────────── Map ────────────────────────────┐
│ Layers:  [ ] Fences  [x] Forage (RAP)  [ ] Soil  [ ] Weather │
│ Legend:  Low forage ░░░░░░░░ High forage ████████            │
│                                                             │
│  (Pastures shaded by biomass; outlines remain clickable)     │
│                                                             │
│  P-12 chip:  6.2d  •  Feb 25–26  •  High (82)                │
│                                                             │
│ Tap pasture → opens pasture detail sheet                     │
└─────────────────────────────────────────────────────────────┘
```

## **Notes (Map View)**

- Map answers “where is forage?” quickly.
- Chips show the **three essentials** without opening details.

---

### 3.3 Pasture Detail (trust + explainability)

```yaml
┌──────────────────────────── Pasture P-12 ─────────────────────────┐
│ Status: Grazing now  •  Herd: Stockers (400)                      │
│ Recommendation: Move Feb 25–26  •  Days remaining: 6.2            │
│ Confidence: High (82)   [Explain confidence]                      │
├───────────────────────────────────────────────────────────────────┤
│ FORAGE BALANCE                                                    │
│ Available forage (DM):   18,400 lb (±2,900)                        │
│ Daily herd demand (DM):   2,950 lb/day                             │
│ Utilization target:       50% residual (GMP)                        │
│ Days remaining:           6.2                                      │
│                                                                   │
│ Trend (last 60d):  biomass ↓  | Forecast (next 14d): growth flat  │
│  ────────╲___                                                    │
├───────────────────────────────────────────────────────────────────┤
│ WHAT’S DRIVING THIS                                               │
│ • RAP biomass dropped 12% since last cycle                         │
│ • Weather: low precip probability next 7 days                       │
│ • Demand high: intake assumptions for 500–600lb yearlings           │
│                                                                   │
│ [Adjust assumptions]  (intake%, utilization%, herd count)           │
├───────────────────────────────────────────────────────────────────┤
│ DATA HEALTH & PROVENANCE                                           │
│ RAP biomass:     Fresh ✓  (as-of Feb 10, 2026)  coverage 98%        │
│ Weather:         Fresh ✓  (fetched Feb 19, 2026) horizon 14d        │
│ Soil baseline:   Static ✓ (gSSURGO cached)                          │
│ Herd config:     Fresh ✓  (updated Feb 18, 2026)                    │
│ Boundary:        Verified ✓ (polygon v2)                            │
│                                                                   │
│ Run ID: 20260219T121500Z  •  Logic: days-of-grazing@v1.3            │
│ [View run details]  [Download inputs]                               │
└───────────────────────────────────────────────────────────────────┘
```

## **Notes (Pasture Detail**

- The trust anchor is explicit: **DM supply / DM demand → days remaining**.
- Provenance is first-class.

---

## 4) Confidence model (how we communicate it)

### 4.1 Primary representation (always visible)

- **Pill label:** High / Medium / Low
- **Bar:** 10 segments
- **Score:** 0–100

Example: `High ████████░░ (82)`

### 4.2 Confidence explanation (one tap)

A simple points breakdown tied to **data quality + completeness**, not a black-box ML probability.

### **Example drawer**

- RAP recency (0–30 pts): 28 (fresh: 9 days old)
- RAP coverage/nodata (0–20 pts): 18 (2% nodata)
- Weather horizon + availability (0–15 pts): 15
- Herd config completeness (0–20 pts): 20
- Boundary integrity / area sanity checks (0–15 pts): 13  
**Total: 82**

### 4.3 Confidence buckets

- **High:** 75–100 → show move window + standard drivers
- **Medium:** 50–74 → show move window + “confidence reasons” banner
- **Low:** < 50 → show move window only if not blocked; emphasize assumptions + refresh CTA

---

## 5) Stale / incomplete data handling (UX states)

The UI must distinguish **degraded** vs **blocked**.

### 5.1 State definitions

| State | Meaning | Recommendation shown? | Confidence | CTA |
| --- | --- | ---: | --- | --- |
| Fresh | All required inputs present; source recency within thresholds | Yes | Normal | None |
| Stale (Degraded) | Required inputs present but one or more sources are stale | Yes | Reduced | Refresh / See what’s stale |
| Incomplete (Blocked) | Missing required inputs OR coverage too low to trust | No move date | N/A | Fix missing input / Manual estimate |

### 5.2 Example banners

### **Stale (degraded)**

- “RAP biomass is **23 days old** (expected ≤ 16). Recommendation confidence reduced.”
- Buttons: **[Refresh now] [See what’s stale]**

## **Incomplete (blocked)**

- “Can’t compute days remaining: **missing herd headcount**.”
- Buttons: **[Update herd] [Enter temporary estimate]**

### **Coverage issue (treated as incomplete if severe)**

- “Coverage issue: **18%** of pasture has no valid biomass pixels (cloud/nodata).”
- Buttons: **[Refresh] [Use manual forage estimate]**

- RAP biomass: **≤ 16 days** = fresh, 17–30 = stale, >30 = blocked (default)
- Weather: **≤ 24 hours** = fresh, 1–3 days stale, >3 days blocked
- Herd config: **must exist**; if older than 90 days → degrade with warning
- Boundary: must pass basic integrity checks (area > 0, no self-intersections); failures block

---

## 6) What information we surface (and why)

### 6.1 Dashboard summary fields

- Next pasture
- Days remaining (primary)
- Move window (range)
- Confidence (label + score)
- Key drivers (3 bullets)
- Last updated + run id + logic version (audit hook)
- Data health summary (RAP/Weather/Herd/Boundary)

### 6.2 Pasture detail fields

- Forage balance (DM supply / demand)
- Trend chart (60 days) + short-term forecast summary (14 days)
- Drivers list (RAP change, weather stress, demand assumptions)
- Assumptions controls (intake %, utilization %, headcount)
- Confidence breakdown
- Data health & provenance table
- “Download inputs / run details” link for auditability

---

## 7) Operability / MRV-grade traceability (design contract)

Even though this is a UI doc, the design assumes a stable “run contract” so the UI can:

- show exact timestamps,
- explain confidence deterministically,
- support backfills and replay.

### 7.1 Minimum metadata the UI expects per recommendation

- `run_id` (immutable)
- `logic_version` (semver)
- `generated_at` (UTC)
- `pasture_id`, `boundary_version`
- `inputs` summary: `herd_id`, `headcount`, `intake_assumptions`, `utilization_target`
- `sources` health: for each source `{as_of, fetched_at, freshness_state, coverage_pct, notes}`
- `outputs`: `days_remaining`, `move_window_start/end`, `confidence_score`, `confidence_reasons[]`

### 7.2 Run manifest access patterns

- “View run details” → shows the run manifest (JSON) in a read-only viewer.
- “Download inputs” → boundary + assumptions snapshot used for the run.

---

## 8) Monitoring hooks (lightweight, UI-consumable)

### **Signals**

- Number of pastures in `fresh/stale/blocked`
- Count of confidence bucket changes over time (per ranch)
- Data source fetch failures and age distribution

### **Metrics cardinality warning**

- Do **not** label metrics by `pasture_id` at global scope (high-cardinality).
- Prefer aggregation by `ranch_id` and sampling/trace logs keyed by `run_id`.

---

## 9) What we intentionally did not build (time-boxed)

- No ML model uncertainty intervals beyond simple `±` display derived from coverage/recency.
- No sophisticated growth model; “forecast” is a simple summarization of weather + recent biomass trend.
- No full design system or pixel-perfect UI spec; this is information architecture + interaction.

### **Next**

- Convert ASCII wireframes to Figma frames.
- Validate copy/tone with 2–3 ranchers (especially “stale/blocked” messaging).
- Add an “Assumption sensitivity” mini-panel (±10% intake/utilization impacts) to reduce anxiety.

---

## 10) Defendability (how to justify choices)

- Ranchers need a **move decision** more than charts; we put action first, explanation second.
- Confidence is **data-quality-driven and explainable** (recency/coverage/completeness), which is more defensible than opaque probabilities.
- Explicit stale/incomplete states prevent **false precision** (critical for both operator trust and MRV defensibility).
- Provenance visible at the point of decision supports **replay/backfill** and audit narratives.

---

## 11) AI Tools Used (fill-in for submission)

- **Tool:** ChatGPT  
  **Purpose:** Drafted the visualization design spec and ASCII wireframes.  
  **What I refined:** (you)  
  - Adjusted thresholds / copy tone / terminology to match PastureMap patterns  
  - Verified the UX states map cleanly to pipeline freshness/completeness outputs  
  **What I verified manually:** (you)  
  - Screens match PastureMap navigation expectations  
  - Confidence logic and stale/blocked thresholds align with data availability realities
