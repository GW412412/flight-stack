"""GeoJSON weed detections -> spray waypoints.

Aligned to LIVE-PIPELINE.md:
  - Live / plan gate: conf >= 0.50
  - Tank: 0.5 L concentrate (carrier half; AI-equivalent)
  - Dose: V_mL = clamp(0.25 + 1.25 * A_cm2, 0.1, 2.5); skip if A < 0.5 cm2
  - Stage 1 planner: centroid + boustrophedon; terrain/OR-Tools later
"""
from __future__ import annotations

import json
import math
from typing import Any, Optional

from shapely.geometry import shape

CONF_MIN = 0.50
TANK_LITRES_DEFAULT = 0.5
V0_ML = 0.25
K_ML_PER_CM2 = 1.25
V_MIN_ML = 0.1
V_MAX_ML = 2.5
A_MIN_CM2 = 0.5
# Offline Point features without area_cm2: conservative planning default (cm2)
POINT_AREA_DEFAULT_CM2 = 1.0


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def dose_ml(area_cm2: float) -> Optional[float]:
    """Locked LIVE-PIPELINE dose. None => skip (too small)."""
    if area_cm2 < A_MIN_CM2:
        return None
    return clamp(V0_ML + K_ML_PER_CM2 * area_cm2, V_MIN_ML, V_MAX_ML)


def _polygon_area_cm2(geom, m_per_deg_lat: float, m_per_deg_lon: float) -> float:
    def ring_m2(coords):
        xs, ys = [], []
        for lon, lat_ in coords:
            xs.append(lon * m_per_deg_lon)
            ys.append(lat_ * m_per_deg_lat)
        a = 0.0
        for i in range(len(xs) - 1):
            a += xs[i] * ys[i + 1] - xs[i + 1] * ys[i]
        return abs(a) / 2.0

    m2 = ring_m2(list(geom.exterior.coords))
    for hole in geom.interiors:
        m2 -= ring_m2(list(hole.coords))
    return max(m2 * 1e4, 0.0)


def _approx_area_cm2(geom) -> float:
    """Rough ground area from WGS84 polygon (planar mid-lat). Points use default."""
    if geom.geom_type == "Point":
        return POINT_AREA_DEFAULT_CM2
    c = geom.centroid
    lat = c.y
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(lat))
    if geom.geom_type == "Polygon":
        return _polygon_area_cm2(geom, m_per_deg_lat, m_per_deg_lon)
    if geom.geom_type == "MultiPolygon":
        return sum(_polygon_area_cm2(g, m_per_deg_lat, m_per_deg_lon) for g in geom.geoms)
    return POINT_AREA_DEFAULT_CM2


def load_detections(geojson_path: str, conf_min: float = CONF_MIN):
    """Return list of (geom, props) with conf >= conf_min."""
    with open(geojson_path) as f:
        data = json.load(f)
    out = []
    for feat in data.get("features", []):
        props = feat.get("properties") or {}
        conf = float(props.get("confidence", props.get("conf", 0.0)))
        if conf < conf_min:
            continue
        geom = shape(feat["geometry"])
        out.append((geom, props, conf))
    if not out:
        raise ValueError(f"no features with conf >= {conf_min}")
    return out


def generate_spray_plan(geojson_path, terrain_tiff=None, tank_litres=TANK_LITRES_DEFAULT,
                        spray_alt_m=10.0, spacing_m=2.0, spacing_deg=None,
                        conf_min: float = CONF_MIN):
    """Returns (waypoints, zone_bounds, meta).

    waypoints: list of (lat, lon, alt_m).
    zone_bounds: (minlat, minlon, maxlat, maxlon) from accepted targets.
    meta: doses, skipped, tank usage (for tools / logging).
    """
    dets = load_detections(geojson_path, conf_min=conf_min)
    targets = []  # (lat, lon, dose_ml, conf, area_cm2)
    skipped = []
    for geom, props, conf in dets:
        if "area_cm2" in props:
            area = float(props["area_cm2"])
        else:
            area = _approx_area_cm2(geom)
        v = dose_ml(area)
        if v is None:
            skipped.append({"reason": "area_below_min", "conf": conf, "area_cm2": area})
            continue
        c = geom.centroid
        targets.append((c.y, c.x, v, conf, area))

    if not targets:
        raise ValueError("no sprayable targets after conf/area filters")

    total_ml = sum(t[2] for t in targets)
    tank_ml = float(tank_litres) * 1000.0
    if total_ml > tank_ml:
        raise ValueError(
            f"mission needs ~{total_ml:.1f} mL > tank {tank_ml:.0f} mL "
            f"({tank_litres} L) — split mission"
        )

    if spacing_deg is None:
        spacing_deg = spacing_m / 111320.0
    # boustrophedon by lon within lat bands
    pts = sorted(targets, key=lambda p: (round(p[0] / spacing_deg), p[1]))
    band = None
    ordered = []
    for p in pts:
        b = round(p[0] / spacing_deg)
        if band is not None and band != b:
            ordered.reverse()
        band = b
        ordered.append(p)
    if ordered:
        ordered.reverse()

    lats = [p[0] for p in ordered]
    lons = [p[1] for p in ordered]
    bounds = (min(lats) - 1e-4, min(lons) - 1e-4, max(lats) + 1e-4, max(lons) + 1e-4)
    waypoints = [(lat, lon, spray_alt_m) for lat, lon, _v, _c, _a in ordered]
    meta = {
        "waypoint_count": len(waypoints),
        "doses_ml": [round(p[2], 3) for p in ordered],
        "total_dose_ml": round(total_ml, 2),
        "tank_litres": tank_litres,
        "conf_min": conf_min,
        "skipped": skipped,
        "dose_formula": "clamp(0.25 + 1.25*A_cm2, 0.1, 2.5) mL",
    }
    return waypoints, bounds, meta


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "../sample/detections.geojson"
    wps, bounds, meta = generate_spray_plan(path)
    print(f"{len(wps)} waypoints, bounds={bounds}")
    print(json.dumps(meta, indent=2))
