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
    rap = exec_one(
        conn,
        """
        SELECT composite_date, biomass_kg_per_ha, source_version
        FROM rap_biomass
        WHERE boundary_id=? AND composite_date <= ?
        ORDER BY composite_date DESC
        LIMIT 1
        """,
        (boundary_id, as_of),
    )
    if not rap:
        return 0.0, {"rap": None}

    b = exec_one(
        conn, "SELECT area_ha FROM geographic_boundaries WHERE boundary_id=?", (boundary_id,)
    )
    area_ha = float(b["area_ha"]) if b and b["area_ha"] is not None else 0.0

    biomass_kg_per_ha = float(rap["biomass_kg_per_ha"] or 0.0)
    available = biomass_kg_per_ha * area_ha

    prov = {
        "rap": {
            "composite_date": rap["composite_date"],
            "biomass_kg_per_ha": biomass_kg_per_ha,
            "source_version": rap["source_version"],
        },
        "boundary": {"area_ha": area_ha},
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
