# UI Wireframe (Design)

## Pasture screen – “Grazing Intelligence”

### Primary (top card)

- **Recommended Move Date**
- **Days of Grazing Remaining**
- **Confidence** (High / Medium / Low)

### Detail (expand/collapsible)

**Inputs used**:

- Available forage (kg) and RAP composite date selected
- Herd daily consumption (kg/day)
  - animal_count
  - daily_intake_kg_per_head
- Weather summary (precip/temp/wind) for next 7–10 days

**Why / provenance**:

- `model_version`
- `config_version` (hash)
- `snapshot_id`
- last updated timestamp
- DQ warnings (if any)

### Stale / incomplete behaviors

- If critical RAP or soil data is missing:
  - show **“Insufficient data to recommend”**
  - remediation: “check pasture boundary mapping / try again later”
- If weather is stale or incomplete:
  - show a warning banner
  - degrade confidence and explain why (“weather coverage ends on …”)

### Confidence rule (example)

- **High**: RAP present, soil present, weather fresh (<= 7 days stale), herd config valid
- **Medium**: RAP + soil present but weather stale OR minor DQ warning
- **Low**: any critical input missing OR multiple warnings; still show inputs but suppress move date
