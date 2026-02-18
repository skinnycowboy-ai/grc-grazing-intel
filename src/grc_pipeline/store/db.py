from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def connect_sqlite(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


@contextmanager
def db_conn(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    conn = connect_sqlite(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def exec_one(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
    cur = conn.execute(sql, params)
    return cur.fetchone()


def upsert_geographic_boundary(
    conn: sqlite3.Connection,
    *,
    boundary_id: str,
    name: str,
    ranch_id: str | None,
    pasture_id: str | None,
    geometry_geojson: str,
    area_ha: float | None,
    crs: str,
    created_at: str,
    source_file: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO geographic_boundaries(
          boundary_id, name, ranch_id, pasture_id, geometry_geojson, area_ha, crs, created_at, source_file
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(boundary_id) DO UPDATE SET
          name=excluded.name,
          ranch_id=excluded.ranch_id,
          pasture_id=excluded.pasture_id,
          geometry_geojson=excluded.geometry_geojson,
          area_ha=excluded.area_ha,
          crs=excluded.crs,
          source_file=excluded.source_file
        """,
        (
            boundary_id,
            name,
            ranch_id,
            pasture_id,
            geometry_geojson,
            area_ha,
            crs,
            created_at,
            source_file,
        ),
    )


def insert_ingestion_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    boundary_id: str | None,
    timeframe_start: str,
    timeframe_end: str,
    sources_included: str,
    status: str,
    started_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO ingestion_runs(
          run_id, boundary_id, timeframe_start, timeframe_end, sources_included, status, started_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, boundary_id, timeframe_start, timeframe_end, sources_included, status, started_at),
    )


def finalize_ingestion_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    status: str,
    completed_at: str,
    records_ingested: int,
    error_message: str | None,
) -> None:
    conn.execute(
        """
        UPDATE ingestion_runs
        SET status=?, completed_at=?, records_ingested=?, error_message=?
        WHERE run_id=?
        """,
        (status, completed_at, records_ingested, error_message, run_id),
    )


def insert_dq_check(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    check_name: str,
    check_type: str,
    passed: bool,
    details_json: str,
    checked_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO data_quality_checks(run_id, check_name, check_type, passed, details_json, checked_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_id, check_name, check_type, 1 if passed else 0, details_json, checked_at),
    )
