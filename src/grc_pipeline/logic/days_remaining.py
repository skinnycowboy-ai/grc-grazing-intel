from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from ..store.db import exec_one
from ..timeutil import parse_date


@dataclass(frozen=True)
class GrazingCalc:
    available_forage_kg: float
    daily_consumption_kg: float
    days_remaining: float
    recommended_move_date: str


def daily_consumption_kg(animal_count: int, daily_intake_kg_per_head: float) -> float:
    return float(animal_count) * float(daily_intake_kg_per_head)


def compute_available_forage_kg(conn, *, boundary_id: str, as_of: str) -> tuple[float, dict]:
    """
    Compute available forage based on *ingested* daily features.

    Source of truth for Task 2: boundary_daily_features for (boundary_id, as_of).

    Units:
      - rap_biomass_kg_per_ha: kg/ha
      - area_ha: ha
      - available_forage_kg: kg  (kg/ha * ha)
    """
    feat = exec_one(
        conn,
        """
        SELECT
          rap_composite_date,
          rap_biomass_kg_per_ha,
          rap_source_version,
          soil_source_version,
          weather_source_version,
          area_ha
        FROM boundary_daily_features
        WHERE boundary_id=? AND feature_date=?
        LIMIT 1
        """,
        (boundary_id, as_of),
    )
    if not feat:
        raise ValueError(
            "Missing boundary_daily_features for boundary/date. "
            "Run `ingest` for a timeframe that includes this as_of date."
        )

    biomass_kg_per_ha = float(feat["rap_biomass_kg_per_ha"] or 0.0)
    area_ha = float(feat["area_ha"] or 0.0)
    available = biomass_kg_per_ha * area_ha

    prov = {
        "features_row": {
            "feature_date": as_of,
            "rap_composite_date": feat["rap_composite_date"],
            "rap_biomass_kg_per_ha": biomass_kg_per_ha,
            "area_ha": area_ha,
            "rap_source_version": feat["rap_source_version"],
            "soil_source_version": feat["soil_source_version"],
            "weather_source_version": feat["weather_source_version"],
        }
    }
    return float(available), prov


def compute_days_remaining(*, available_forage_kg: float, daily_consumption_kg: float) -> float:
    if daily_consumption_kg <= 0:
        return 0.0
    return float(available_forage_kg) / float(daily_consumption_kg)


def recommend_move_date(calc_date: str, days_remaining: float) -> str:
    d = parse_date(calc_date)
    delta_days = int(max(0.0, days_remaining))  # deterministic floor
    return (d + timedelta(days=delta_days)).isoformat()


def compute_grazing_recommendation(
    conn, *, boundary_id: str, herd_config_id: str, calculation_date: str
) -> tuple[GrazingCalc, dict]:
    herd = exec_one(
        conn,
        "SELECT animal_count, daily_intake_kg_per_head FROM herd_configurations WHERE id=?",
        (herd_config_id,),
    )
    if not herd:
        raise ValueError(f"Unknown herd_config_id: {herd_config_id}")

    available, prov_avail = compute_available_forage_kg(
        conn, boundary_id=boundary_id, as_of=calculation_date
    )
    daily = daily_consumption_kg(int(herd["animal_count"]), float(herd["daily_intake_kg_per_head"]))
    days = compute_days_remaining(available_forage_kg=available, daily_consumption_kg=daily)
    move = recommend_move_date(calculation_date, days)

    calc = GrazingCalc(float(available), float(daily), float(days), move)
    prov = {
        "inputs": prov_avail,
        "herd": {"id": herd_config_id},
        "calculation_date": calculation_date,
    }
    return calc, prov
