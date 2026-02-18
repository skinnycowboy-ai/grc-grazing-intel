from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pyproj import Geod
from shapely.geometry import shape

WGS84 = Geod(ellps="WGS84")


@dataclass(frozen=True)
class Boundary:
    boundary_id: str
    name: str
    geometry_geojson: str
    crs: str
    area_ha: float
    centroid_lat: float
    centroid_lon: float


def load_boundary_geojson(
    path: str | Path, *, boundary_id: str | None = None, name: str | None = None
) -> Boundary:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))

    if data.get("type") == "FeatureCollection":
        feat = data["features"][0]
        geom = feat["geometry"]
        props = feat.get("properties") or {}
    elif data.get("type") == "Feature":
        geom = data["geometry"]
        props = data.get("properties") or {}
    else:
        geom = data
        props = {}

    shp = shape(geom)

    lon, lat = shp.exterior.coords.xy
    area_m2, _ = WGS84.polygon_area_perimeter(lon, lat)
    area_ha = abs(area_m2) / 10_000.0

    c = shp.centroid
    centroid_lon, centroid_lat = float(c.x), float(c.y)

    bid = boundary_id or props.get("boundary_id") or props.get("id") or p.stem
    nm = name or props.get("name") or props.get("pasture") or props.get("label") or str(bid)

    return Boundary(
        boundary_id=str(bid),
        name=str(nm),
        geometry_geojson=json.dumps(geom, separators=(",", ":"), sort_keys=True),
        crs="EPSG:4326",
        area_ha=float(area_ha),
        centroid_lat=centroid_lat,
        centroid_lon=centroid_lon,
    )
