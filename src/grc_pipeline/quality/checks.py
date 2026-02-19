# src/grc_pipeline/quality/checks.py
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
    row = exec_one(conn, "SELECT COUNT(*) AS n FROM rap_biomass WHERE boundary_id=?", (boundary_id,))
    n = int(row["n"]) if row else 0
    return CheckResult("rap_present", "completeness", n > 0, {"count": n})


def check_has_soil_for_boundary(conn, *, boundary_id: str) -> CheckResult:
    row = exec_one(conn, "SELECT COUNT(*) AS n FROM nrcs_soil_data WHERE boundary_id=?", (boundary_id,))
    n = int(row["n"]) if row else 0
    return CheckResult("soil_present", "completeness", n > 0, {"count": n})


def check_weather_freshness(conn, *, boundary_id: str, timeframe_end: str, cfg: PipelineConfig) -> CheckResult:
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


def check_daily_features_complete(conn, *, boundary_id: str, start: str, end: str) -> CheckResult:
    s = parse_date(start)
    e = parse_date(end)
    expected = (e - s).days + 1

    row = exec_one(
        conn,
        """
        SELECT
          COUNT(*) AS n,
          SUM(CASE WHEN rap_biomass_kg_per_ha IS NULL THEN 1 ELSE 0 END) AS rap_missing,
          SUM(
            CASE
              WHEN weather_precipitation_mm IS NULL
               AND weather_temp_max_c IS NULL
               AND weather_temp_min_c IS NULL
               AND weather_wind_speed_kmh IS NULL
              THEN 1 ELSE 0
            END
          ) AS weather_missing
        FROM boundary_daily_features
        WHERE boundary_id=? AND feature_date BETWEEN ? AND ?
        """,
        (boundary_id, start, end),
    )

    n = int(row["n"]) if row and row["n"] is not None else 0
    rap_missing = int(row["rap_missing"]) if row and row["rap_missing"] is not None else 0
    weather_missing = int(row["weather_missing"]) if row and row["weather_missing"] is not None else 0

    # Pass criteria:
    # - we produced a complete daily frame (n == expected)
    # - weather is present for all days (weather_missing == 0)
    # - RAP isn't totally absent for the entire frame (rap_missing < expected)
    passed = (n == expected) and (weather_missing == 0) and (rap_missing < expected)

    return CheckResult(
        "daily_features_complete",
        "join_completeness",
        passed,
        {
            "expected_days": expected,
            "rows_materialized": n,
            "rap_missing_days": rap_missing,
            "weather_missing_days": weather_missing,
        },
    )


def summarize_checks(results: list[CheckResult]) -> dict:
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": [r.name for r in results if not r.passed],
    }
