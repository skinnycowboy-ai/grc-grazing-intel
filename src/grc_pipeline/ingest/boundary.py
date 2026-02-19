# src/grc_pipeline/ingest/boundary.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyproj import CRS, Geod, Transformer
from shapely.geometry import shape
from shapely.ops import transform as shp_transform

WGS84 = Geod(ellps="WGS84")
EPSG4326 = CRS.from_epsg(4326)


@dataclass(frozen=True)
class Boundary:
    boundary_id: str
    name: str
    geometry_geojson: str
    crs: str
    area_ha: float
    centroid_lat: float
    centroid_lon: float


def _parse_geojson(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))

    if data.get("type") == "FeatureCollection":
        feats = data.get("features") or []
        if not feats:
            raise ValueError("FeatureCollection has no features")
        # Take first feature as canonical for this take-home (repo samples are single-feature).
        feat = feats[0]
        geom = feat.get("geometry")
        props = feat.get("properties") or {}
        if not geom:
            raise ValueError("Feature missing geometry")
        return geom, props

    if data.get("type") == "Feature":
        geom = data.get("geometry")
        props = data.get("properties") or {}
        if not geom:
            raise ValueError("Feature missing geometry")
        return geom, props

    # Raw geometry object
    return data, {}


def _maybe_transform_to_epsg4326(geom_obj: dict[str, Any], input_crs: str) -> Any:
    shp = shape(geom_obj)

    src = CRS.from_user_input(input_crs)
    if src == EPSG4326:
        return shp

    transformer = Transformer.from_crs(src, EPSG4326, always_xy=True)

    def _f(x: float, y: float, z: float | None = None):
        return transformer.transform(x, y)

    return shp_transform(_f, shp)


def _validate_epsg4326_bounds(shp) -> None:
    minx, miny, maxx, maxy = shp.bounds
    if minx < -180.0 or maxx > 180.0 or miny < -90.0 or maxy > 90.0:
        raise ValueError(
            "Boundary coordinates appear out of EPSG:4326 bounds. "
            "If your GeoJSON is projected (UTM/etc), pass --boundary-crs accordingly."
        )


def _geodetic_area_ha(shp) -> float:
    if shp.is_empty:
        return 0.0

    # Fix common self-intersection issues (keeps the take-home robust)
    if not shp.is_valid:
        shp = shp.buffer(0)
    if not shp.is_valid:
        raise ValueError(
            "Boundary geometry is invalid and could not be repaired (buffer(0) failed)"
        )

    geom_type = shp.geom_type
    if geom_type == "Polygon":
        # Exterior
        lon, lat = shp.exterior.coords.xy
        area_m2, _ = WGS84.polygon_area_perimeter(lon, lat)
        area = abs(area_m2)
        # Holes
        for ring in shp.interiors:
            lon_i, lat_i = ring.coords.xy
            hole_m2, _ = WGS84.polygon_area_perimeter(lon_i, lat_i)
            area -= abs(hole_m2)
        return area / 10_000.0

    if geom_type == "MultiPolygon":
        return sum(_geodetic_area_ha(g) for g in shp.geoms)

    raise ValueError(f"Unsupported geometry type for boundary: {geom_type}")


def load_boundary_geojson(
    path: str | Path,
    *,
    boundary_id: str | None = None,
    name: str | None = None,
    input_crs: str = "EPSG:4326",
) -> Boundary:
    p = Path(path)
    geom_obj, props = _parse_geojson(p)

    shp = _maybe_transform_to_epsg4326(geom_obj, input_crs=input_crs)
    _validate_epsg4326_bounds(shp)

    area_ha = float(_geodetic_area_ha(shp))
    c = shp.centroid
    centroid_lon, centroid_lat = float(c.x), float(c.y)

    bid = boundary_id or props.get("boundary_id") or props.get("id") or p.stem
    nm = name or props.get("name") or props.get("pasture") or props.get("label") or str(bid)

    geom_4326_obj = shp.__geo_interface__
    geometry_geojson = json.dumps(geom_4326_obj, separators=(",", ":"), sort_keys=True)

    return Boundary(
        boundary_id=str(bid),
        name=str(nm),
        geometry_geojson=geometry_geojson,
        crs="EPSG:4326",
        area_ha=area_ha,
        centroid_lat=centroid_lat,
        centroid_lon=centroid_lon,
    )
