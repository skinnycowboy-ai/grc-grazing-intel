from __future__ import annotations

from dataclasses import dataclass

from ..config import PipelineConfig
from ..store.db import exec_one
from ..timeutil import parse_date


@dataclass(frozen=True)
class CheckResult:
    name: str
    check_type: str
    passed: bool
    details: dict


def check_herd_config_valid(herd: dict) -> CheckResult:
    problems = []
    if int(herd.get("animal_count", 0)) <= 0:
        problems.append("animal_count must be > 0")
    if float(herd.get("daily_intake_kg_per_head", 0.0)) <= 0.0:
        problems.append("daily_intake_kg_per_head must be > 0")
    return CheckResult("herd_config_valid", "config", len(problems) == 0, {"problems": problems})


def check_has_rap_for_boundary(conn, *, boundary_id: str) -> CheckResult:
    row = exec_one(
        conn, "SELECT COUNT(*) AS n FROM rap_biomass WHERE boundary_id=?", (boundary_id,)
    )
    n = int(row["n"]) if row else 0
    return CheckResult("rap_present", "completeness", n > 0, {"count": n})


def check_has_soil_for_boundary(conn, *, boundary_id: str) -> CheckResult:
    row = exec_one(
        conn, "SELECT COUNT(*) AS n FROM nrcs_soil_data WHERE boundary_id=?", (boundary_id,)
    )
    n = int(row["n"]) if row else 0
    return CheckResult("soil_present", "completeness", n > 0, {"count": n})


def check_weather_freshness(
    conn, *, boundary_id: str, timeframe_end: str, cfg: PipelineConfig
) -> CheckResult:
    end = parse_date(timeframe_end)
    min_expected = (end - cfg.weather_stale_delta).isoformat()

    row = exec_one(
        conn,
        "SELECT MAX(forecast_date) AS max_date, COUNT(*) AS n FROM weather_forecasts WHERE boundary_id=?",
        (boundary_id,),
    )
    max_date = row["max_date"] if row else None
    n = int(row["n"]) if row else 0
    passed = (max_date is not None) and (max_date >= min_expected) and (n > 0)
    return CheckResult(
        "weather_fresh_enough",
        "freshness",
        passed,
        {"max_forecast_date": max_date, "min_expected": min_expected, "count": n},
    )


def summarize_checks(results: list[CheckResult]) -> dict:
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": [r.name for r in results if not r.passed],
    }
