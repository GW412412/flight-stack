"""GeoJSON weed detections -> spray waypoints.

Stage 1 (now): centroid-per-detection, boustrophedon ordering, zone bounds
from the detection extent. Stage 2 (later): terrain raster offset via
rasterio, OR-Tools route optimisation, spray-footprint polygons instead of
points.
"""
import json

from shapely.geometry import shape


def load_detections(geojson_path):
    with open(geojson_path) as f:
        data = json.load(f)
    geoms = [shape(feat["geometry"]) for feat in data.get("features", [])]
    if not geoms:
        raise ValueError("no features in detections file")
    return geoms


def generate_spray_plan(geojson_path, terrain_tiff=None, tank_litres=5.0,
                        spray_alt_m=10.0, spacing_m=2.0, spacing_deg=None):
    """Returns (waypoints, zone_bounds).
    waypoints: list of (lat, lon, alt_m). zone_bounds: (minlat, minlon, maxlat, maxlon)."""
    geoms = load_detections(geojson_path)
    pts = []
    for g in geoms:
        c = g.centroid
        pts.append((c.y, c.x))
    if not pts:
        raise ValueError("no detections")

    est = 0.5 * len(pts)
    if est > tank_litres:
        raise ValueError(f"mission needs ~{est:.1f}L > tank {tank_litres}L - split mission")

    if spacing_deg is None:
        spacing_deg = spacing_m / 111320.0
    pts.sort(key=lambda p: (round(p[0] / spacing_deg), p[1]))
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
    waypoints = [(lat, lon, spray_alt_m) for lat, lon in ordered]
    return waypoints, bounds


if __name__ == "__main__":
    import sys
    wps, bounds = generate_spray_plan(sys.argv[1] if len(sys.argv) > 1
                                      else "../sample/detections.geojson")
    print(f"{len(wps)} waypoints, bounds={bounds}")
