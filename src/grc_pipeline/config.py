from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class PipelineConfig:
    weather_stale_days: int = 7
    max_days_remaining: float = 365.0
    min_days_remaining: float = 0.0
    openmeteo_source_version: str = "openmeteo:v1"

    @property
    def weather_stale_delta(self) -> timedelta:
        return timedelta(days=self.weather_stale_days)
