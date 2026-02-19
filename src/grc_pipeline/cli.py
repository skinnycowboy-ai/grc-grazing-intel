# src/grc_pipeline/cli.py
from __future__ import annotations

import json
import uuid
from pathlib import Path

import typer

from .config import PipelineConfig
from .ingest.boundary import load_boundary_geojson
from .ingest.features import materialize_boundary_daily_features
from .ingest.herd import load_herd_configs, upsert_herd_configs
from .ingest.openmeteo import fetch_openmeteo_daily, upsert_weather_forecasts
from .logic.days_remaining import compute_grazing_recommendation
from .quality.checks import (
    check_daily_features_complete,
    check_has_rap_for_boundary,
    check_has_soil_for_boundary,
    check_herd_config_valid,
    check_weather_freshness,
)
from .store.db import (
    db_conn,
    exec_one,
    finalize_ingestion_run,
    insert_dq_check,
    insert_ingestion_run,
    upsert_geographic_boundary,
)
from .store.manifest import RunManifest, sha256_text, stable_json_dumps, write_manifest
from .timeutil import parse_date, utc_now_iso

app = typer.Typer(add_completion=False)


def _infer_pasture_id_from_boundary_id(boundary_id: str) -> str | None:
    """Heuristic fallback only. Prefer DB mapping if present."""
    if not boundary_id:
        return None
    parts = boundary_id.split("_")
    if len(parts) >= 3:
        return "_".join(parts[2:])
    return None


def _stable_herd_id(boundary_id: str, herd_row: dict) -> str:
    """Deterministic herd config ID so ingest is idempotent across reruns."""
    src_id = herd_row.get("id")
    if src_id and isinstance(src_id, str) and src_id.strip():
        return src_id.strip()

    effective_date = ""
    try:
        snap = json.loads(herd_row.get("config_snapshot_json") or "{}")
        effective_date = str(snap.get("effective_date") or snap.get("effectiveDate") or "")
    except Exception:
        effective_date = ""

    key = {
        "boundary_id": boundary_id,
        "ranch_id": herd_row.get("ranch_id"),
        "pasture_id": herd_row.get("pasture_id"),
        "effective_date": effective_date,
        "animal_count": herd_row.get("animal_count"),
        "daily_intake_kg_per_head": herd_row.get("daily_intake_kg_per_head"),
        "animal_type": herd_row.get("animal_type"),
    }
    return sha256_text(stable_json_dumps(key))[:24]


@app.command()
def ingest(
    db: str = typer.Option(...),
    boundary_geojson: str = typer.Option(...),
    herds_json: str = typer.Option(...),
    start: str = typer.Option(...),
    end: str = typer.Option(...),
    boundary_id: str = typer.Option(
        None, help="Override boundary_id to align with reference DB (recommended)."
    ),
    boundary_name: str = typer.Option(None),
    boundary_crs: str = typer.Option(
        "EPSG:4326",
        help="CRS of the input GeoJSON coordinates (used to transform to EPSG:4326).",
    ),
):
    cfg = PipelineConfig()
    run_id = str(uuid.uuid4())
    started_at = utc_now_iso()

    boundary = load_boundary_geojson(
        boundary_geojson,
        boundary_id=boundary_id,
        name=boundary_name,
        input_crs=boundary_crs,
    )

    sources = [
        "reference_db:nrcs_soil_data",
        "reference_db:rap_biomass",
        cfg.openmeteo_source_version,
        "pasturemap:herd_config",
        "derived:boundary_daily_features",
    ]

    with db_conn(db) as conn:
        insert_ingestion_run(
            conn,
            run_id=run_id,
            boundary_id=boundary.boundary_id,
            timeframe_start=start,
            timeframe_end=end,
            sources_included=",".join(sources),
            status="running",
            started_at=started_at,
        )

        try:
            # Prefer existing boundary metadata from DB (ranch_id/pasture_id), so we don't overwrite
            # canonical reference DB fields with NULLs.
            existing = exec_one(
                conn,
                "SELECT ranch_id, pasture_id FROM geographic_boundaries WHERE boundary_id=?",
                (boundary.boundary_id,),
            )
            boundary_ranch_id = existing["ranch_id"] if existing and existing["ranch_id"] else None
            boundary_pasture_id = (
                existing["pasture_id"] if existing and existing["pasture_id"] else None
            )

            if not boundary_pasture_id:
                boundary_pasture_id = _infer_pasture_id_from_boundary_id(boundary.boundary_id)

            upsert_geographic_boundary(
                conn,
                boundary_id=boundary.boundary_id,
                name=boundary.name,
                ranch_id=boundary_ranch_id,
                pasture_id=boundary_pasture_id,
                geometry_geojson=boundary.geometry_geojson,
                area_ha=boundary.area_ha,
                crs=boundary.crs,
                created_at=started_at,
                source_file=str(Path(boundary_geojson).name),
            )

            # Herd ingest:
            # - Filter herds to the pasture for THIS boundary run.
            # - Only attach boundary_id to those matching herds.
            # - Use deterministic IDs to avoid duplicates.
            all_herds = load_herd_configs(herds_json, valid_from=start)
            herds: list[dict] = []
            for h in all_herds:
                if (
                    boundary_pasture_id
                    and h.get("pasture_id")
                    and h.get("pasture_id") != boundary_pasture_id
                ):
                    continue
                if not h.get("boundary_id"):
                    h["boundary_id"] = boundary.boundary_id
                h["id"] = _stable_herd_id(boundary.boundary_id, h)
                herds.append(h)

            herd_count = upsert_herd_configs(conn, herds)

            rows = fetch_openmeteo_daily(
                lat=boundary.centroid_lat,
                lon=boundary.centroid_lon,
                start=parse_date(start),
                end=parse_date(end),
            )
            weather_n = upsert_weather_forecasts(
                conn,
                boundary_id=boundary.boundary_id,
                rows=rows,
                source_version=cfg.openmeteo_source_version,
            )

            # Materialize static+time-series join for the timeframe (Task 1 “joins”)
            feat_res = materialize_boundary_daily_features(
                conn,
                boundary_id=boundary.boundary_id,
                start=start,
                end=end,
                weather_source_version=cfg.openmeteo_source_version,
                created_at=utc_now_iso(),
            )
            features_n = feat_res.inserted

            # DQ checks (recorded) — use the first filtered herd (if any)
            first = herds[0] if herds else {}
            herd_for_check = {
                "animal_count": int(first.get("animal_count") or 0),
                "daily_intake_kg_per_head": float(first.get("daily_intake_kg_per_head") or 0.0),
            }

            checks = [
                check_herd_config_valid(herd_for_check),
                check_has_rap_for_boundary(conn, boundary_id=boundary.boundary_id),
                check_has_soil_for_boundary(conn, boundary_id=boundary.boundary_id),
                check_weather_freshness(
                    conn, boundary_id=boundary.boundary_id, timeframe_end=end, cfg=cfg
                ),
                check_daily_features_complete(
                    conn, boundary_id=boundary.boundary_id, start=start, end=end
                ),
            ]

            for c in checks:
                insert_dq_check(
                    conn,
                    run_id=run_id,
                    check_name=c.name,
                    check_type=c.check_type,
                    passed=c.passed,
                    details_json=stable_json_dumps(c.details),
                    checked_at=utc_now_iso(),
                )

            status = "succeeded" if all(c.passed for c in checks) else "succeeded_with_warnings"
            finalize_ingestion_run(
                conn,
                run_id=run_id,
                status=status,
                completed_at=utc_now_iso(),
                records_ingested=int(herd_count + weather_n + features_n + 1),
                error_message=None,
            )

        except Exception as e:
            finalize_ingestion_run(
                conn,
                run_id=run_id,
                status="failed",
                completed_at=utc_now_iso(),
                records_ingested=0,
                error_message=str(e),
            )
            raise

    typer.echo(json.dumps({"run_id": run_id, "boundary_id": boundary.boundary_id}, indent=2))


@app.command()
def compute(
    db: str = typer.Option(...),
    boundary_id: str = typer.Option(...),
    herd_config_id: str = typer.Option(...),
    as_of: str = typer.Option(...),
    logic_version: str = typer.Option("days_remaining:v1"),
    manifest_out: str = typer.Option("out/manifests"),
):
    now = utc_now_iso()
    cfg = PipelineConfig()
    ds_params = {
        "max_days_remaining": cfg.max_days_remaining,
        "min_days_remaining": cfg.min_days_remaining,
    }
    config_hash = sha256_text(stable_json_dumps(ds_params))

    with db_conn(db) as conn:
        conn.execute(
            """
            INSERT INTO model_versions(version_id, description, parameters_json, deployed_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(version_id) DO NOTHING
            """,
            (logic_version, "Rules-based days remaining calculator", "{}", now, now),
        )

        b = exec_one(
            conn,
            "SELECT geometry_geojson FROM geographic_boundaries WHERE boundary_id=?",
            (boundary_id,),
        )
        if not b:
            raise typer.BadParameter(f"Unknown boundary_id: {boundary_id}")
        boundary_geojson = b["geometry_geojson"] or ""
        boundary_hash = sha256_text(boundary_geojson)

        h = exec_one(
            conn,
            "SELECT config_snapshot_json FROM herd_configurations WHERE id=?",
            (herd_config_id,),
        )
        if not h:
            raise typer.BadParameter(f"Unknown herd_config_id: {herd_config_id}")
        herd_snapshot = h["config_snapshot_json"] or "{}"
        herd_hash = sha256_text(herd_snapshot)

        calc, _prov = compute_grazing_recommendation(
            conn,
            boundary_id=boundary_id,
            herd_config_id=herd_config_id,
            calculation_date=as_of,
        )

        rap = exec_one(
            conn,
            """
            SELECT composite_date, source_version
            FROM rap_biomass
            WHERE boundary_id=? AND composite_date <= ?
            ORDER BY composite_date DESC
            LIMIT 1
            """,
            (boundary_id, as_of),
        )
        soil = exec_one(
            conn,
            "SELECT source_version FROM nrcs_soil_data WHERE boundary_id=? LIMIT 1",
            (boundary_id,),
        )

        input_versions = {
            "rap": {
                "source_version": rap["source_version"] if rap else None,
                "as_of_composite_date": rap["composite_date"] if rap else None,
            },
            "soil": {"source_version": soil["source_version"] if soil else None},
            "weather": {"source_version": cfg.openmeteo_source_version},
        }

        cur = conn.execute(
            """
            INSERT INTO grazing_recommendations(
              boundary_id, herd_config_id, calculation_date,
              available_forage_kg, daily_consumption_kg, days_of_grazing_remaining, recommended_move_date,
              model_version, config_version, input_data_versions_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                boundary_id,
                herd_config_id,
                as_of,
                calc.available_forage_kg,
                calc.daily_consumption_kg,
                calc.days_remaining,
                calc.recommended_move_date,
                logic_version,
                config_hash,
                stable_json_dumps(
                    {
                        "data_snapshot": input_versions,
                        "boundary_geojson_hash": boundary_hash,
                        "herd_snapshot_hash": herd_hash,
                        "logic_version": logic_version,
                        "ds_params": ds_params,
                    }
                ),
                now,
            ),
        )
        rec_id = int(cur.lastrowid)

        dq = {
            "guardrails": {
                "days_remaining_in_range": (
                    cfg.min_days_remaining <= calc.days_remaining <= cfg.max_days_remaining
                )
            }
        }
        manifest = RunManifest(
            run_id=str(uuid.uuid4()),
            boundary_id=boundary_id,
            timeframe_start=as_of,
            timeframe_end=as_of,
            logic_version=logic_version,
            config_hash=config_hash,
            boundary_geojson_hash=boundary_hash,
            herd_snapshot_hash=herd_hash,
            input_data_versions=input_versions,
            dq_summary=dq,
            outputs={"grazing_recommendation_id": rec_id},
            created_at=now,
        )
        snap_id = manifest.snapshot_id()
        out_path = Path(manifest_out) / boundary_id / f"{as_of}_{snap_id}.json"
        write_manifest(out_path, manifest)

        typer.echo(
            json.dumps(
                {
                    "recommendation_id": rec_id,
                    "snapshot_id": snap_id,
                    "manifest_path": str(out_path),
                },
                indent=2,
            )
        )


@app.command()
def serve(
    db: str = typer.Option(...),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
):
    import uvicorn

    from .api.app import create_app

    uvicorn.run(create_app(db), host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
