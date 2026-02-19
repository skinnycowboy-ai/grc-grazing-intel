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
    problems: list[str] = []
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


def check_rap_freshness(
    conn, *, boundary_id: str, timeframe_end: str, cfg: PipelineConfig
) -> CheckResult:
    """Fail if RAP composites are too stale relative to timeframe_end."""
    end = parse_date(timeframe_end)
    row = exec_one(
        conn,
        "SELECT MAX(composite_date) AS max_date, COUNT(*) AS n FROM rap_biomass WHERE boundary_id=?",
        (boundary_id,),
    )
    max_date = row["max_date"] if row else None
    n = int(row["n"]) if row else 0

    if not max_date:
        return CheckResult(
            "rap_fresh_enough",
            "freshness",
            False,
            {
                "max_composite_date": None,
                "staleness_days": None,
                "max_allowed_days": cfg.rap_stale_days,
                "count": n,
            },
        )

    stale_days = (end - parse_date(max_date)).days
    passed = (n > 0) and (stale_days <= cfg.rap_stale_days)

    return CheckResult(
        "rap_fresh_enough",
        "freshness",
        passed,
        {
            "max_composite_date": max_date,
            "staleness_days": stale_days,
            "max_allowed_days": cfg.rap_stale_days,
            "count": n,
        },
    )


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


def check_weather_response_complete(
    conn,
    *,
    boundary_id: str,
    start: str | None = None,
    end: str | None = None,
    source_version: str | None = None,
) -> CheckResult:
    """
    Back-compat for existing tests:
    Verifies Open-Meteo ingestion returned a complete daily response for the requested window.

    If start/end provided:
      - expects COUNT(DISTINCT forecast_date) == number of days in [start,end]
      - expects min_date==start and max_date==end

    If start/end not provided:
      - falls back to “at least one row exists” for boundary_id (and optional source_version).
    """
    where = "boundary_id=?"
    params: list[object] = [boundary_id]

    if source_version:
        where += " AND source_version=?"
        params.append(source_version)

    if start and end:
        s = parse_date(start)
        e = parse_date(end)
        expected = (e - s).days + 1

        where_window = where + " AND forecast_date BETWEEN ? AND ?"
        params_window = params + [start, end]

        row = exec_one(
            conn,
            f"""
            SELECT
              COUNT(DISTINCT forecast_date) AS n,
              MIN(forecast_date) AS min_date,
              MAX(forecast_date) AS max_date
            FROM weather_forecasts
            WHERE {where_window}
            """,
            tuple(params_window),
        )
        n = int(row["n"]) if row and row["n"] is not None else 0
        min_date = row["min_date"] if row else None
        max_date = row["max_date"] if row else None

        passed = (n == expected) and (min_date == start) and (max_date == end)
        return CheckResult(
            "weather_response_complete",
            "completeness",
            passed,
            {
                "expected_days": expected,
                "rows_distinct_days": n,
                "min_date": min_date,
                "max_date": max_date,
                "start": start,
                "end": end,
                "source_version": source_version,
            },
        )

    row = exec_one(
        conn,
        f"""
        SELECT
          COUNT(*) AS n,
          MIN(forecast_date) AS min_date,
          MAX(forecast_date) AS max_date
        FROM weather_forecasts
        WHERE {where}
        """,
        tuple(params),
    )
    n = int(row["n"]) if row and row["n"] is not None else 0
    return CheckResult(
        "weather_response_complete",
        "completeness",
        n > 0,
        {
            "rows": n,
            "min_date": row["min_date"] if row else None,
            "max_date": row["max_date"] if row else None,
            "source_version": source_version,
        },
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
    weather_missing = (
        int(row["weather_missing"]) if row and row["weather_missing"] is not None else 0
    )

    # Pass criteria:
    # - complete daily frame (n == expected)
    # - weather present for all days (weather_missing == 0)
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
