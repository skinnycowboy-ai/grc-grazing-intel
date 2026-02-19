# src/grc_pipeline/cli.py
from __future__ import annotations

import inspect
import json
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

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
    check_rap_freshness,
    check_weather_freshness,
    check_weather_response_complete,
)
from .quality.monitoring import run_output_monitoring
from .runtime import collect_code_metadata
from .store.db import (
    db_conn,
    exec_one,
    finalize_ingestion_run,
    insert_dq_check,
    insert_ingestion_run,
    upsert_geographic_boundary,
)
from .store.manifest import (
    RunManifest,
    read_manifest,
    sha256_text,
    stable_json_dumps,
    write_manifest_if_missing,
)
from .timeutil import parse_date, utc_now_iso

app = typer.Typer(add_completion=False)


def _unwrap_option(v: Any) -> Any:
    """
    When calling @app.command() functions directly (e.g., unit tests),
    typer.Option defaults may still be OptionInfo objects.
    """
    if isinstance(v, typer.models.OptionInfo):
        return v.default
    return v


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


def _load_boundary_with_optional_crs(
    boundary_geojson: str,
    *,
    boundary_id: str | None,
    boundary_name: str | None,
    boundary_crs: str,
):
    """Call load_boundary_geojson with whichever CRS kwarg exists (back-compat)."""
    sig = inspect.signature(load_boundary_geojson)
    kwargs: dict[str, Any] = {"boundary_id": boundary_id, "name": boundary_name}
    if "input_crs" in sig.parameters:
        kwargs["input_crs"] = boundary_crs
    elif "boundary_crs" in sig.parameters:
        kwargs["boundary_crs"] = boundary_crs
    return load_boundary_geojson(boundary_geojson, **kwargs)


def _materialize_features_with_compat_kwargs(
    conn,
    *,
    boundary_id: str,
    start: str,
    end: str,
    weather_source_version: str,
    created_at: str,
):
    sig = inspect.signature(materialize_boundary_daily_features)
    kwargs: dict[str, Any] = {
        "conn": conn,
        "boundary_id": boundary_id,
        "start": start,
        "end": end,
        "created_at": created_at,
    }
    if "weather_source_version" in sig.parameters:
        kwargs["weather_source_version"] = weather_source_version
    elif "source_version" in sig.parameters:
        kwargs["source_version"] = weather_source_version
    elif "weather_version" in sig.parameters:
        kwargs["weather_version"] = weather_source_version
    return materialize_boundary_daily_features(**kwargs)


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

    boundary = _load_boundary_with_optional_crs(
        boundary_geojson,
        boundary_id=boundary_id,
        boundary_name=boundary_name,
        boundary_crs=boundary_crs,
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

            # Herd ingest: filter to pasture for THIS boundary run, attach boundary_id, stable IDs.
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

            feat_res = _materialize_features_with_compat_kwargs(
                conn,
                boundary_id=boundary.boundary_id,
                start=start,
                end=end,
                weather_source_version=cfg.openmeteo_source_version,
                created_at=utc_now_iso(),
            )
            features_n = int(getattr(feat_res, "inserted", 0))

            first = herds[0] if herds else {}
            herd_for_check = {
                "animal_count": int(first.get("animal_count") or 0),
                "daily_intake_kg_per_head": float(first.get("daily_intake_kg_per_head") or 0.0),
            }

            checks = [
                check_herd_config_valid(herd_for_check),
                check_has_rap_for_boundary(conn, boundary_id=boundary.boundary_id),
                check_rap_freshness(
                    conn, boundary_id=boundary.boundary_id, timeframe_end=end, cfg=cfg
                ),
                check_has_soil_for_boundary(conn, boundary_id=boundary.boundary_id),
                check_weather_freshness(
                    conn, boundary_id=boundary.boundary_id, timeframe_end=end, cfg=cfg
                ),
                check_weather_response_complete(
                    conn,
                    boundary_id=boundary.boundary_id,
                    start=start,
                    end=end,
                    source_version=cfg.openmeteo_source_version,
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
    """
    Task 6: immutable, reproducible recommendation compute.

    Versioning:
      - logic_version: explicit model/logic version (e.g. days_remaining:v1)
      - config_hash: hash of config parameters that affect computation
      - data snapshot: captured in a manifest with stable snapshot_id

    Idempotency/backfill:
      - Primary key is (boundary_id, herd_config_id, calculation_date, model_version, config_version)
      - On conflict: DO NOTHING (never overwrite history)
      - If an existing record has different provenance: error and require a version bump
    """
    # Unwrap Typer defaults when called directly in tests.
    logic_version = str(_unwrap_option(logic_version))
    manifest_out = str(_unwrap_option(manifest_out))

    now = utc_now_iso()
    cfg = PipelineConfig()
    code_meta = collect_code_metadata()

    ds_params = {
        "max_days_remaining": cfg.max_days_remaining,
        "min_days_remaining": cfg.min_days_remaining,
    }
    config_hash = sha256_text(stable_json_dumps(ds_params))

    idempotency_key = {
        "boundary_id": boundary_id,
        "herd_config_id": herd_config_id,
        "as_of": as_of,
        "logic_version": logic_version,
        "config_hash": config_hash,
    }
    run_id = sha256_text(stable_json_dumps(idempotency_key))[:32]  # stable across retries

    with db_conn(db) as conn:
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_grazing_reco_idempotent
            ON grazing_recommendations(boundary_id, herd_config_id, calculation_date, model_version, config_version)
            """
        )

        conn.execute(
            """
            INSERT INTO model_versions(version_id, description, parameters_json, deployed_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(version_id) DO NOTHING
            """,
            (
                logic_version,
                "Rules-based days remaining calculator",
                stable_json_dumps(ds_params),
                now,
                now,
            ),
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
            "SELECT config_snapshot_json, animal_count, daily_intake_kg_per_head "
            "FROM herd_configurations WHERE id=?",
            (herd_config_id,),
        )
        if not h:
            raise typer.BadParameter(f"Unknown herd_config_id: {herd_config_id}")
        herd_snapshot = h["config_snapshot_json"] or "{}"
        herd_hash = sha256_text(herd_snapshot)

        feat = exec_one(
            conn,
            """
            SELECT *
            FROM boundary_daily_features
            WHERE boundary_id=? AND feature_date=?
            LIMIT 1
            """,
            (boundary_id, as_of),
        )
        if not feat:
            raise typer.BadParameter(
                "Missing boundary_daily_features for boundary/as_of. "
                "Run `ingest` for a timeframe that includes this as_of date."
            )

        calc, prov = compute_grazing_recommendation(
            conn,
            boundary_id=boundary_id,
            herd_config_id=herd_config_id,
            calculation_date=as_of,
        )

        # Thin, indexed provenance (store in DB)
        input_versions = {
            "rap": {
                "source_version": feat["rap_source_version"],
                "as_of_composite_date": feat["rap_composite_date"],
            },
            "soil": {"source_version": feat["soil_source_version"]},
            "weather": {"source_version": feat["weather_source_version"]},
        }

        # Full input snapshot (store in manifest file)
        feat_dict = {k: feat[k] for k in feat.keys()}
        inputs_snapshot = {
            "boundary": {"boundary_id": boundary_id, "boundary_geojson_hash": boundary_hash},
            "herd": {
                "herd_config_id": herd_config_id,
                "herd_snapshot_hash": herd_hash,
                "animal_count": int(h["animal_count"] or 0),
                "daily_intake_kg_per_head": float(h["daily_intake_kg_per_head"] or 0.0),
            },
            "features_row": feat_dict,
            "logic_provenance": prov,  # includes RAP composite + biomass + area_ha used
            "data_snapshot_versions": input_versions,
            "config": {"ds_params": ds_params, "config_hash": config_hash},
        }

        dq = {
            "guardrails": {
                "days_remaining_in_range": cfg.min_days_remaining
                <= calc.days_remaining
                <= cfg.max_days_remaining
            },
            "has_features_row": True,
            "has_rap": bool((prov or {}).get("inputs", {}).get("rap")),
        }

        outputs = {
            "available_forage_kg": calc.available_forage_kg,
            "daily_consumption_kg": calc.daily_consumption_kg,
            "days_of_grazing_remaining": calc.days_remaining,
            "recommended_move_date": calc.recommended_move_date,
        }

        # Build manifest + stable snapshot id/path
        manifest = RunManifest(
            schema_version=1,
            run_type="compute_recommendation",
            run_id=run_id,
            created_at=now,
            code=code_meta,
            idempotency_key=idempotency_key,
            inputs=inputs_snapshot,
            dq_summary=dq,
            outputs=outputs,
        )
        snap_id = manifest.snapshot_id()
        out_path = Path(manifest_out) / boundary_id / f"{as_of}_{snap_id}.json"

        # Store a minimal pointer + hashes in DB (no big blobs)
        payload = stable_json_dumps(
            {
                "schema_version": 1,
                "manifest": {"snapshot_id": snap_id, "path": str(out_path)},
                "data_snapshot": input_versions,
                "boundary_geojson_hash": boundary_hash,
                "herd_snapshot_hash": herd_hash,
                "logic_version": logic_version,
                "ds_params": ds_params,
                "config_hash": config_hash,
                "idempotency_key": idempotency_key,
                "code_version": {
                    "git_commit": code_meta.get("git_commit", "unknown"),
                    "package_version": code_meta.get("package_version", "unknown"),
                },
                "inputs_snapshot_hash": sha256_text(stable_json_dumps(inputs_snapshot)),
            }
        )

        # Append-only insert (never overwrite history)
        conn.execute(
            """
            INSERT INTO grazing_recommendations(
              boundary_id, herd_config_id, calculation_date,
              available_forage_kg, daily_consumption_kg, days_of_grazing_remaining, recommended_move_date,
              model_version, config_version, input_data_versions_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(boundary_id, herd_config_id, calculation_date, model_version, config_version)
            DO NOTHING
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
                payload,
                now,
            ),
        )

        rec = exec_one(
            conn,
            """
            SELECT id, input_data_versions_json
            FROM grazing_recommendations
            WHERE boundary_id=? AND herd_config_id=? AND calculation_date=? AND model_version=? AND config_version=?
            """,
            (boundary_id, herd_config_id, as_of, logic_version, config_hash),
        )
        if not rec:
            raise RuntimeError(
                "Failed to read back grazing_recommendations row after insert/do-nothing."
            )

        existing_payload = rec["input_data_versions_json"] or ""
        if existing_payload and existing_payload != payload:
            # This prevents silent drift if underlying inputs changed but the version key didn’t.
            raise RuntimeError(
                "Existing recommendation already present with DIFFERENT provenance under the same "
                "(boundary, herd, date, logic_version, config_hash). "
                "Refusing to overwrite history. Bump logic_version (e.g. days_remaining:v2) "
                "or change config params to create a new config_hash."
            )

        rec_id = int(rec["id"])

        # Write manifest (immutable + idempotent)
        write_manifest_if_missing(out_path, manifest)

    typer.echo(
        json.dumps(
            {
                "recommendation_id": rec_id,
                "snapshot_id": snap_id,
                "manifest_path": str(out_path),
                "logic_version": logic_version,
                "config_hash": config_hash,
            },
            indent=2,
        )
    )


@app.command()
def explain(
    db: str = typer.Option(...),
    recommendation_id: int | None = typer.Option(
        None, help="Primary key id in grazing_recommendations."
    ),
    boundary_id: str | None = typer.Option(None),
    herd_config_id: str | None = typer.Option(None),
    as_of: str | None = typer.Option(None, help="YYYY-MM-DD"),
):
    """
    Task 6: Answer “Why did we recommend moving?” months later.

    Preferred usage:
      explain --recommendation-id <id>
    Fallback:
      explain --boundary-id ... --herd-config-id ... --as-of ...
    """
    # Unwrap Typer defaults when called directly in tests.
    db = str(_unwrap_option(db))

    if recommendation_id is None:
        if not (boundary_id and herd_config_id and as_of):
            raise typer.BadParameter(
                "Provide --recommendation-id OR (--boundary-id, --herd-config-id, --as-of)."
            )

    with db_conn(db) as conn:
        if recommendation_id is not None:
            row = exec_one(
                conn,
                """
                SELECT *
                FROM grazing_recommendations
                WHERE id=?
                """,
                (recommendation_id,),
            )
        else:
            row = exec_one(
                conn,
                """
                SELECT *
                FROM grazing_recommendations
                WHERE boundary_id=? AND herd_config_id=? AND calculation_date=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (boundary_id, herd_config_id, as_of),
            )

    if not row:
        raise typer.BadParameter("recommendation_not_found")

    rec = {k: row[k] for k in row.keys()}
    try:
        prov = json.loads(rec.get("input_data_versions_json") or "{}")
    except Exception:
        prov = {"parse_error": True}

    manifest_path = ((prov.get("manifest") or {}) if isinstance(prov, dict) else {}).get("path")
    manifest: dict[str, Any] | None = None
    if manifest_path:
        p = Path(manifest_path)
        if p.exists():
            manifest = read_manifest(p)

    # Prefer values from the manifest snapshot if available (strongest “why?”)
    inputs = (manifest or {}).get("inputs") if isinstance(manifest, dict) else None
    outputs = (manifest or {}).get("outputs") if isinstance(manifest, dict) else None
    logic_prov = (inputs or {}).get("logic_provenance") or {} if isinstance(inputs, dict) else {}

    # Build explanation (formula + substitutions)
    available = (outputs or {}).get("available_forage_kg", rec.get("available_forage_kg"))
    daily = (outputs or {}).get("daily_consumption_kg", rec.get("daily_consumption_kg"))
    days = (outputs or {}).get("days_of_grazing_remaining", rec.get("days_of_grazing_remaining"))
    move = (outputs or {}).get("recommended_move_date", rec.get("recommended_move_date"))

    herd = (inputs or {}).get("herd") if isinstance(inputs, dict) else None
    rap = (logic_prov.get("inputs") or {}).get("rap") if isinstance(logic_prov, dict) else None
    boundary = (
        (logic_prov.get("inputs") or {}).get("boundary") if isinstance(logic_prov, dict) else None
    )

    explanation = {
        "question": "Why did the system recommend moving cattle?",
        "recommendation": {
            "id": rec.get("id"),
            "boundary_id": rec.get("boundary_id"),
            "herd_config_id": rec.get("herd_config_id"),
            "calculation_date": rec.get("calculation_date"),
            "recommended_move_date": move,
            "days_of_grazing_remaining": days,
            "model_version": rec.get("model_version"),
            "config_version": rec.get("config_version"),
        },
        "because": {
            "formula": "days_remaining = available_forage_kg / daily_consumption_kg; "
            "recommended_move_date = as_of + floor(days_remaining)",
            "available_forage_kg": {
                "value": available,
                "derived_from": {
                    "rap": rap,
                    "boundary": boundary,
                },
            },
            "daily_consumption_kg": {
                "value": daily,
                "derived_from": herd,
            },
            "days_remaining": {"value": days},
        },
        "provenance": {
            "logic_version": (prov.get("logic_version") if isinstance(prov, dict) else None),
            "config_hash": (prov.get("config_hash") if isinstance(prov, dict) else None),
            "data_snapshot_versions": (
                prov.get("data_snapshot") if isinstance(prov, dict) else None
            ),
            "manifest": prov.get("manifest") if isinstance(prov, dict) else None,
            "code_version": prov.get("code_version") if isinstance(prov, dict) else None,
        },
    }

    typer.echo(json.dumps(explanation, indent=2))


@app.command()
def monitor(
    db: str = typer.Option(...),
    boundary_id: str = typer.Option(...),
    end: str = typer.Option(..., help="Inclusive window end date (YYYY-MM-DD)."),
    window_days: int = typer.Option(30, help="Number of days in rolling window."),
    out_dir: str = typer.Option("out/monitoring"),
    fail_on_warn: bool = typer.Option(True, help="If true, WARN causes exit code 1."),
):
    """Task 3: label-free output monitoring over time."""
    cfg = PipelineConfig()
    d_end = parse_date(end)
    d_start = (d_end - timedelta(days=max(1, window_days) - 1)).isoformat()

    with db_conn(db) as conn:
        report = run_output_monitoring(
            conn, boundary_id=boundary_id, start=d_start, end=end, cfg=cfg
        )

    created_at = utc_now_iso()
    report_with_meta = {
        "created_at": created_at,
        "thresholds": {
            "monitor_zero_days_warn_pct": cfg.monitor_zero_days_warn_pct,
            "monitor_zero_days_crit_pct": cfg.monitor_zero_days_crit_pct,
            "monitor_over_max_warn_pct": cfg.monitor_over_max_warn_pct,
            "monitor_over_max_crit_pct": cfg.monitor_over_max_crit_pct,
            "monitor_rap_p95_stale_warn_days": cfg.monitor_rap_p95_stale_warn_days,
            "monitor_rap_p95_stale_crit_days": cfg.monitor_rap_p95_stale_crit_days,
        },
        **report,
    }
    snap = sha256_text(stable_json_dumps(report_with_meta))
    out_path = Path(out_dir) / boundary_id / f"{end}_{snap[:16]}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(stable_json_dumps(report_with_meta), encoding="utf-8")

    typer.echo(json.dumps({**report_with_meta, "report_path": str(out_path)}, indent=2))

    status = str(report.get("status") or "ok")
    if status == "crit":
        raise typer.Exit(code=2)
    if status == "warn" and fail_on_warn:
        raise typer.Exit(code=1)


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
