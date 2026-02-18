from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import httpx

from ..timeutil import date_iso, utc_now_iso


@dataclass(frozen=True)
class WeatherRow:
    forecast_date: str
    latitude: float
    longitude: float
    precipitation_mm: float | None
    temp_max_c: float | None
    temp_min_c: float | None
    wind_speed_kmh: float | None


def fetch_openmeteo_daily(
    *, lat: float, lon: float, start: date, end: date, timeout_s: float = 30.0
) -> list[WeatherRow]:
    """
    Use Open-Meteo archive for historical ranges, forecast for near-future.

    Why: /v1/forecast has a limited horizon and will 400 on long historical ranges.
    """
    start_s = date_iso(start)
    end_s = date_iso(end)

    # Archive endpoint supports historical ranges.
    # Keep this simple for the take-home: always use archive when end < today.
    from datetime import date as _date

    today = _date.today()

    if end < today:
        url = "https://archive-api.open-meteo.com/v1/archive"
        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": start_s,
            "end_date": end_s,
            "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min,windspeed_10m_max",
            "timezone": "UTC",
        }
    else:
        # Forecast horizon clamp (defensive). Keep end to <= today+16 days.
        url = "https://api.open-meteo.com/v1/forecast"
        max_end = today.fromordinal(today.toordinal() + 16)
        if end > max_end:
            end_s = date_iso(max_end)
        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": start_s,
            "end_date": end_s,
            "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min,windspeed_10m_max",
            "timezone": "UTC",
        }

    with httpx.Client(timeout=timeout_s) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        payload = r.json()

    daily = payload.get("daily") or {}
    times = daily.get("time") or []
    precip = daily.get("precipitation_sum") or []
    tmax = daily.get("temperature_2m_max") or []
    tmin = daily.get("temperature_2m_min") or []
    wind = daily.get("windspeed_10m_max") or []

    out: list[WeatherRow] = []
    for i, t in enumerate(times):
        out.append(
            WeatherRow(
                forecast_date=str(t),
                latitude=float(lat),
                longitude=float(lon),
                precipitation_mm=float(precip[i])
                if i < len(precip) and precip[i] is not None
                else None,
                temp_max_c=float(tmax[i]) if i < len(tmax) and tmax[i] is not None else None,
                temp_min_c=float(tmin[i]) if i < len(tmin) and tmin[i] is not None else None,
                wind_speed_kmh=float(wind[i]) if i < len(wind) and wind[i] is not None else None,
            )
        )
    return out


def upsert_weather_forecasts(
    conn,
    *,
    boundary_id: str,
    rows: list[WeatherRow],
    source_version: str,
    ingested_at: str | None = None,
) -> int:
    if ingested_at is None:
        ingested_at = utc_now_iso()

    # idempotent partition replace
    if rows:
        start = rows[0].forecast_date
        end = rows[-1].forecast_date
        conn.execute(
            """
            DELETE FROM weather_forecasts
            WHERE boundary_id=? AND source_version=? AND forecast_date BETWEEN ? AND ?
            """,
            (boundary_id, source_version, start, end),
        )

    n = 0
    for r in rows:
        conn.execute(
            """
            INSERT INTO weather_forecasts(
              boundary_id, forecast_date, latitude, longitude,
              precipitation_mm, temp_max_c, temp_min_c, wind_speed_kmh,
              source_version, ingested_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                boundary_id,
                r.forecast_date,
                r.latitude,
                r.longitude,
                r.precipitation_mm,
                r.temp_max_c,
                r.temp_min_c,
                r.wind_speed_kmh,
                source_version,
                ingested_at,
            ),
        )
        n += 1
    return n
