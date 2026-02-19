from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..config import PipelineConfig
from ..timeutil import parse_date


@dataclass(frozen=True)
class MonitorAlert:
    name: str
    severity: str  # "warn" | "crit"
    passed: bool
    details: dict[str, Any]


def _pct(n: int, d: int) -> float:
    if d <= 0:
        return 0.0
    return float(n) / float(d)


def _pctl(values: list[int], p: float) -> int | None:
    if not values:
        return None
    xs = sorted(values)
    idx = int(round((len(xs) - 1) * p))
    idx = max(0, min(idx, len(xs) - 1))
    return int(xs[idx])


def run_output_monitoring(
    conn,
    *,
    boundary_id: str,
    start: str,
    end: str,
    cfg: PipelineConfig,
) -> dict[str, Any]:
    """Compute label-free output monitoring metrics over a window."""

    # Basic distribution stats for days_remaining
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS n,
          SUM(CASE WHEN days_of_grazing_remaining <= 0 THEN 1 ELSE 0 END) AS n_zero,
          SUM(CASE WHEN days_of_grazing_remaining > ? THEN 1 ELSE 0 END) AS n_over_max,
          MIN(days_of_grazing_remaining) AS min_days,
          MAX(days_of_grazing_remaining) AS max_days,
          AVG(days_of_grazing_remaining) AS avg_days
        FROM grazing_recommendations
        WHERE boundary_id=?
          AND calculation_date BETWEEN ? AND ?
        """,
        (cfg.max_days_remaining, boundary_id, start, end),
    ).fetchone()

    n = int(row[0] or 0)
    n_zero = int(row[1] or 0)
    n_over = int(row[2] or 0)

    # RAP staleness (p95) from recorded provenance.
    # We parse grazing_recommendations.input_data_versions_json and compute:
    #   (calculation_date - rap.as_of_composite_date) in days.
    rap_stale_days: list[int] = []
    for calc_date, payload in conn.execute(
        """
        SELECT calculation_date, input_data_versions_json
        FROM grazing_recommendations
        WHERE boundary_id=?
          AND calculation_date BETWEEN ? AND ?
        """,
        (boundary_id, start, end),
    ).fetchall():
        try:
            obj = json.loads(payload or "{}")
            rap_date = obj.get("data_snapshot", {}).get("rap", {}).get("as_of_composite_date")
            if not rap_date:
                continue
            d_calc = parse_date(str(calc_date))
            d_rap = parse_date(str(rap_date))
            rap_stale_days.append(int((d_calc - d_rap).days))
        except Exception:
            continue

    rap_p95 = _pctl(rap_stale_days, 0.95)

    metrics = {
        "n_recommendations": n,
        "pct_zero_or_negative_days_remaining": _pct(n_zero, n),
        "pct_over_max_days_remaining": _pct(n_over, n),
        "rap_p95_staleness_days": rap_p95,
        "window": {"start": start, "end": end},
    }

    alerts: list[MonitorAlert] = []

    def add_alert(
        *,
        name: str,
        severity: str,
        passed: bool,
        details: dict[str, Any],
    ) -> None:
        alerts.append(MonitorAlert(name=name, severity=severity, passed=passed, details=details))

    if n == 0:
        add_alert(
            name="no_recommendations_in_window",
            severity="crit",
            passed=False,
            details={"boundary_id": boundary_id, "start": start, "end": end},
        )
    else:
        # days_remaining <= 0
        zero_pct = metrics["pct_zero_or_negative_days_remaining"]
        if zero_pct > cfg.monitor_zero_days_crit_pct:
            add_alert(
                name="too_many_zero_days_remaining",
                severity="crit",
                passed=False,
                details={
                    "pct": zero_pct,
                    "crit": cfg.monitor_zero_days_crit_pct,
                    "warn": cfg.monitor_zero_days_warn_pct,
                },
            )
        elif zero_pct > cfg.monitor_zero_days_warn_pct:
            add_alert(
                name="too_many_zero_days_remaining",
                severity="warn",
                passed=False,
                details={
                    "pct": zero_pct,
                    "crit": cfg.monitor_zero_days_crit_pct,
                    "warn": cfg.monitor_zero_days_warn_pct,
                },
            )
        else:
            add_alert(
                name="too_many_zero_days_remaining",
                severity="warn",
                passed=True,
                details={"pct": zero_pct, "warn": cfg.monitor_zero_days_warn_pct},
            )

        # days_remaining > max
        over_pct = metrics["pct_over_max_days_remaining"]
        if over_pct > cfg.monitor_over_max_crit_pct:
            add_alert(
                name="too_many_over_max_days_remaining",
                severity="crit",
                passed=False,
                details={
                    "pct": over_pct,
                    "crit": cfg.monitor_over_max_crit_pct,
                    "warn": cfg.monitor_over_max_warn_pct,
                },
            )
        elif over_pct > cfg.monitor_over_max_warn_pct:
            add_alert(
                name="too_many_over_max_days_remaining",
                severity="warn",
                passed=False,
                details={
                    "pct": over_pct,
                    "crit": cfg.monitor_over_max_crit_pct,
                    "warn": cfg.monitor_over_max_warn_pct,
                },
            )
        else:
            add_alert(
                name="too_many_over_max_days_remaining",
                severity="warn",
                passed=True,
                details={"pct": over_pct, "warn": cfg.monitor_over_max_warn_pct},
            )

        # RAP staleness p95
        if rap_p95 is None:
            add_alert(
                name="missing_rap_staleness_metrics",
                severity="warn",
                passed=False,
                details={"reason": "no parsable rap composite dates in provenance"},
            )
        else:
            if rap_p95 > cfg.monitor_rap_p95_stale_crit_days:
                add_alert(
                    name="rap_p95_staleness_too_high",
                    severity="crit",
                    passed=False,
                    details={
                        "p95_days": rap_p95,
                        "crit_days": cfg.monitor_rap_p95_stale_crit_days,
                        "warn_days": cfg.monitor_rap_p95_stale_warn_days,
                    },
                )
            elif rap_p95 > cfg.monitor_rap_p95_stale_warn_days:
                add_alert(
                    name="rap_p95_staleness_too_high",
                    severity="warn",
                    passed=False,
                    details={
                        "p95_days": rap_p95,
                        "crit_days": cfg.monitor_rap_p95_stale_crit_days,
                        "warn_days": cfg.monitor_rap_p95_stale_warn_days,
                    },
                )
            else:
                add_alert(
                    name="rap_p95_staleness_too_high",
                    severity="warn",
                    passed=True,
                    details={"p95_days": rap_p95, "warn_days": cfg.monitor_rap_p95_stale_warn_days},
                )

    status = "ok"
    if any(a.severity == "crit" and not a.passed for a in alerts):
        status = "crit"
    elif any(a.severity == "warn" and not a.passed for a in alerts):
        status = "warn"

    return {
        "boundary_id": boundary_id,
        "status": status,
        "metrics": metrics,
        "alerts": [
            {"name": a.name, "severity": a.severity, "passed": a.passed, "details": a.details}
            for a in alerts
        ],
    }
