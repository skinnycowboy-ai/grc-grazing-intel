from __future__ import annotations

import json
import time
from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Query, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel

from ..store.db import db_conn, exec_one

# Keep label cardinality bounded:
# - route is the *template* (e.g. /v1/recommendations/{boundary_id}), not the raw path
# - method is bounded
# - status is bounded
HTTP_REQUESTS_TOTAL = Counter(
    "grc_http_requests_total",
    "Total HTTP requests",
    labelnames=("method", "route", "status"),
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "grc_http_request_duration_seconds",
    "HTTP request duration in seconds",
    labelnames=("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)


class RecommendationResponse(BaseModel):
    boundary_id: str
    herd_config_id: str
    calculation_date: str
    recommended_move_date: str | None
    days_of_grazing_remaining: float | None
    available_forage_kg: float | None
    daily_consumption_kg: float | None
    model_version: str
    config_version: str | None
    input_data_versions: dict


def _route_template(request: Request) -> str:
    """Return the route template (bounded cardinality) for metrics labels."""
    try:
        route = request.scope.get("route")
        path_format = getattr(route, "path", None) or getattr(route, "path_format", None)
        return str(path_format) if path_format else str(request.url.path)
    except Exception:
        return str(request.url.path)


def create_app(db_path: str) -> FastAPI:
    app = FastAPI(title="GRC Grazing Intelligence API", version="0.1.0")

    @app.middleware("http")
    async def prom_metrics_middleware(request: Request, call_next: Callable):
        route = _route_template(request)
        method = request.method
        start = time.perf_counter()

        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        finally:
            dur = time.perf_counter() - start
            status = str(getattr(response, "status_code", 500))
            HTTP_REQUESTS_TOTAL.labels(method=method, route=route, status=status).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(method=method, route=route).observe(dur)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/metrics")
    def metrics():
        payload = generate_latest()
        return Response(content=payload, media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/recommendations/{boundary_id}", response_model=RecommendationResponse)
    def get_recommendation(
        boundary_id: str,
        herd_config_id: str = Query(...),
        as_of: str = Query(..., description="YYYY-MM-DD"),
    ):
        with db_conn(db_path) as conn:
            row = exec_one(
                conn,
                """
                SELECT boundary_id, herd_config_id, calculation_date,
                       available_forage_kg, daily_consumption_kg,
                       days_of_grazing_remaining, recommended_move_date,
                       model_version, config_version, input_data_versions_json
                FROM grazing_recommendations
                WHERE boundary_id=? AND herd_config_id=? AND calculation_date=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (boundary_id, herd_config_id, as_of),
            )
            if not row:
                raise HTTPException(status_code=404, detail="recommendation_not_found")

            try:
                versions = json.loads(row["input_data_versions_json"] or "{}")
            except Exception:
                versions = {"parse_error": True}

            return RecommendationResponse(
                boundary_id=row["boundary_id"],
                herd_config_id=row["herd_config_id"],
                calculation_date=row["calculation_date"],
                available_forage_kg=row["available_forage_kg"],
                daily_consumption_kg=row["daily_consumption_kg"],
                days_of_grazing_remaining=row["days_of_grazing_remaining"],
                recommended_move_date=row["recommended_move_date"],
                model_version=row["model_version"],
                config_version=row["config_version"],
                input_data_versions=versions,
            )

    return app
