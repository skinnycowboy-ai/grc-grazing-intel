# src/grc_pipeline/ingest/herd.py
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..timeutil import utc_now_iso


def load_herd_configs(json_path: str, valid_from: str) -> list[dict[str, Any]]:
    """
    Parse sample PastureMap herd config JSON into rows matching `herd_configurations`.

    Returns a list of dicts with keys:
      id, ranch_id, pasture_id, boundary_id,
      animal_count, animal_type, daily_intake_kg_per_head, avg_daily_gain_kg,
      config_snapshot_json, valid_from, valid_to, created_at

    Observed structure (from your traceback):
      {
        "effective_date": "...",
        "herd": {
          "animal_count": 120,
          "animal_type": "...",
          "daily_intake_kg_per_head": 11.5,
          ...
        },
        "pasture_id": "paddock_3",
        "ranch_id": "ranch_001",
        ...
      }

    Notes:
    - boundary_id is often absent in PastureMap exports; we allow it to be NULL.
    - We always store full snapshot JSON for auditability.
    """
    raw = json.loads(Path(json_path).read_text(encoding="utf-8"))
    items = raw.get("herds") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("Unsupported herd JSON format; expected list or {herds:[...]}")

    now = utc_now_iso()
    out: list[dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        herd: dict[str, Any] = item.get("herd") or {}

        ranch_id = (
            item.get("ranch_id")
            or item.get("ranchId")
            or item.get("operation_id")
            or item.get("operationId")
            or "UNKNOWN_RANCH"
        )

        pasture_id = (
            item.get("pasture_id")
            or item.get("pastureId")
            or item.get("paddock_id")
            or item.get("paddockId")
            or item.get("pasture")
            or item.get("paddock")
            or None
        )

        boundary_id = item.get("boundary_id") or item.get("boundaryId")

        animal_count = herd.get("animal_count") or herd.get("count") or 0
        daily_intake = (
            herd.get("daily_intake_kg_per_head")
            or herd.get("dailyIntakeKgPerHead")
            or herd.get("daily_intake")
            or 0.0
        )

        animal_type = herd.get("animal_type") or herd.get("type")
        avg_daily_gain = herd.get("avg_daily_gain_kg") or herd.get("avgDailyGainKg")

        snapshot = json.dumps(item, sort_keys=True)
        src_id = item.get("id") or item.get("herd_id") or item.get("herdId")
        if not src_id:
            src_id = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()[:24]

        out.append(
            {
                "id": str(src_id),
                "ranch_id": str(ranch_id),
                "pasture_id": str(pasture_id) if pasture_id is not None else None,
                "boundary_id": str(boundary_id) if boundary_id else None,
                "animal_count": int(animal_count) if animal_count is not None else 0,
                "animal_type": str(animal_type) if animal_type is not None else None,
                "daily_intake_kg_per_head": float(daily_intake)
                if daily_intake is not None
                else 0.0,
                "avg_daily_gain_kg": float(avg_daily_gain) if avg_daily_gain is not None else None,
                "config_snapshot_json": snapshot,
                "valid_from": valid_from,
                "valid_to": None,
                "created_at": now,
            }
        )

    return out


def upsert_herd_configs(conn: sqlite3.Connection, herds: Iterable[dict[str, Any]]) -> int:
    """
    Insert/update herd configurations.

    Audit/quality stance:
    - NEVER persist invalid configs (animal_count<=0 or intake<=0). Prevents poisoning
      canonical store with incomplete PastureMap exports.
    - Do not wipe an existing boundary_id with NULL when the export omits it.
    """
    cur = conn.cursor()
    n = 0

    for h in herds:
        animal_count = int(h.get("animal_count") or 0)
        daily_intake = float(h.get("daily_intake_kg_per_head") or 0.0)

        # Hard gate: do not write unusable configs
        if animal_count <= 0 or daily_intake <= 0:
            continue

        cur.execute(
            """
            INSERT INTO herd_configurations (
              id, ranch_id, pasture_id, boundary_id,
              animal_count, animal_type, daily_intake_kg_per_head, avg_daily_gain_kg,
              config_snapshot_json, valid_from, valid_to, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              ranch_id=excluded.ranch_id,
              pasture_id=excluded.pasture_id,
              boundary_id=COALESCE(excluded.boundary_id, herd_configurations.boundary_id),
              animal_count=excluded.animal_count,
              animal_type=excluded.animal_type,
              daily_intake_kg_per_head=excluded.daily_intake_kg_per_head,
              avg_daily_gain_kg=excluded.avg_daily_gain_kg,
              config_snapshot_json=excluded.config_snapshot_json,
              valid_from=excluded.valid_from,
              valid_to=excluded.valid_to
            """,
            (
                h["id"],
                h["ranch_id"],
                h.get("pasture_id"),
                h.get("boundary_id"),
                animal_count,
                h.get("animal_type"),
                daily_intake,
                h.get("avg_daily_gain_kg"),
                h.get("config_snapshot_json"),
                h.get("valid_from"),
                h.get("valid_to"),
                h.get("created_at"),
            ),
        )
        n += 1

    conn.commit()
    return n
