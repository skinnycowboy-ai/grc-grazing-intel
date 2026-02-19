from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class PipelineConfig:
    # Ingestion freshness gates
    weather_stale_days: int = 7
    rap_stale_days: int = 120  # RAP composites are sparse; treat as warning gate by default

    # Compute guardrails
    max_days_remaining: float = 365.0
    min_days_remaining: float = 0.0

    # Source versions (recorded as provenance)
    openmeteo_source_version: str = "openmeteo:v1"

    # Output monitoring thresholds (fractions in [0,1])
    monitor_zero_days_warn_pct: float = 0.02
    monitor_zero_days_crit_pct: float = 0.10

    monitor_over_max_warn_pct: float = 0.01
    monitor_over_max_crit_pct: float = 0.05

    # RAP staleness monitoring (p95 days between calc date and RAP composite date)
    monitor_rap_p95_stale_warn_days: int = 150
    monitor_rap_p95_stale_crit_days: int = 240

    @property
    def weather_stale_delta(self) -> timedelta:
        return timedelta(days=self.weather_stale_days)

    @property
    def rap_stale_delta(self) -> timedelta:
        return timedelta(days=self.rap_stale_days)
