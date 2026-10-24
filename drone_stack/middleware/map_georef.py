"""Detections in orthophoto pixels -> georeferenced targets.

Contributed by Maya (AI agent, Sesame). Experimental.

The chain for each detection:

    tile pixel (cx, cy)
      + tile offset                      -> orthophoto pixel (col, row)
      x orthophoto affine transform      -> map CRS metres (x, y), e.g. UTM 55S
      pyproj                             -> WGS84 lat, lon   (what MAVLink uses)
      DTM sample at (x, y)               -> ground height (map datum, metres)
      CHM (DSM - DTM) inside outline     -> canopy height of the target
      pixel area x pixel ground area     -> area_cm2 (feeds spray_mission.dose_ml)

The output GeoJSON is deliberately the same shape spray_mission.load_detections
already reads (Polygon or Point, properties.confidence, properties.area_cm2),
so the existing planner can take a map-derived target list unchanged.

Accuracy: these coordinates are only as good as the ODM map. Without GCPs or
RTK-tagged photos, ODM's own docs put absolute error at roughly 2-6 m. Each
target carries `georef_sigma_m` (from config, set by whoever ran ODM) so the
route planner can size the visual-centering search radius honestly.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

from map_detect import Detector, PixelDetection
from map_tiles import Tile, iter_tiles


@dataclass
class Target:
    target_id: str
    label: str
    confidence: float
    x: float                       # map CRS easting, m
    y: float                       # map CRS northing, m
    lat: float
    lon: float
    ground_elev_m: Optional[float]  # DTM height at centroid, map vertical datum
    canopy_h_m: Optional[float]     # tallest DSM-DTM inside the outline
    area_cm2: float
    georef_sigma_m: float           # 1-sigma horizontal error of the MAP
    outline_latlon: Optional[List[Tuple[float, float]]] = None   # [(lat, lon), ...]
    source: dict = field(default_factory=dict)


def detect_on_map(odm_map, detector: Detector, tile_size: int = 640,
                  tile_overlap: int = 128, with_chm: bool = True) -> List[Tuple[Tile, PixelDetection]]:
    """Run `detector` over every tile; keep each detection only in the tile
    whose core owns its centroid (see map_tiles: exact de-duplication)."""
    kept = []
    for tile in iter_tiles(odm_map, tile_size, tile_overlap, with_chm=with_chm):
        for d in detector.detect(tile):
            if tile.owns(tile.col_off + d.cx, tile.row_off + d.cy):
                kept.append((tile, d))
    return kept


def georeference(odm_map, pairs: Iterable[Tuple[Tile, PixelDetection]],
                 georef_sigma_m: float, detector_name: str = "",
                 map_captured_at: Optional[str] = None) -> List[Target]:
    """Convert (tile, detection) pairs to Targets. Order is stable (row-major
    by tile, then detector order) so target ids are reproducible."""
    px_w, px_h = odm_map.ortho_res_m
    px_area_cm2 = px_w * px_h * 1e4
    out = []
    for n, (tile, d) in enumerate(pairs):
        col, row = tile.col_off + d.cx, tile.row_off + d.cy
        x, y = odm_map.pixel_to_map(col, row)
        lat, lon = odm_map.geo.to_latlon(x, y)
        ground = odm_map.dtm.elev(x, y) if odm_map.dtm is not None else None
        canopy = d.extra.get("canopy_h_m")
        if canopy is None and tile.chm is not None and d.bbox is not None:
            import numpy as np
            x0, y0, x1, y1 = (int(round(v)) for v in d.bbox)
            sub = tile.chm[max(y0, 0):max(y1, y0 + 1), max(x0, 0):max(x1, x0 + 1)]
            sub = sub[~np.isnan(sub)] if sub.size else sub
            canopy = float(sub.max()) if sub.size else None
        area_px = d.area_px if d.area_px is not None else 0.0
        outline = None
        if d.polygon:
            outline = []
            for (px, py) in d.polygon:
                mx, my = odm_map.pixel_to_map(tile.col_off + px, tile.row_off + py)
                outline.append(odm_map.geo.to_latlon(mx, my))
        out.append(Target(
            target_id=f"T{n + 1:04d}", label=d.label, confidence=round(d.confidence, 4),
            x=x, y=y, lat=lat, lon=lon, ground_elev_m=ground, canopy_h_m=canopy,
            area_cm2=round(area_px * px_area_cm2, 2), georef_sigma_m=georef_sigma_m,
            outline_latlon=outline,
            source={"tile": list(tile.index), "ortho_px": [round(col, 2), round(row, 2)],
                    "detector": detector_name, "map_crs": odm_map.geo.crs.to_string(),
                    "map_captured_at": map_captured_at,
                    "area_from": d.extra.get("area_from", "mask" if d.polygon else "bbox")}))
    return out


# =============================================================================
# GeoJSON in the shape spray_mission.load_detections reads
# =============================================================================

def targets_to_geojson(targets: Sequence[Target], extra_props: Optional[dict] = None) -> dict:
    """FeatureCollection, lon/lat order (RFC 7946). Polygon when the detector
    gave an outline, else Point. spray_mission takes the polygon centroid and
    our area_cm2."""
    feats = []
    for t in targets:
        if t.outline_latlon and len(t.outline_latlon) >= 4:
            ring = [[lon, lat] for lat, lon in t.outline_latlon]
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            geom = {"type": "Polygon", "coordinates": [ring]}
        else:
            geom = {"type": "Point", "coordinates": [t.lon, t.lat]}
        props = {k: v for k, v in asdict(t).items() if k not in ("outline_latlon",)}
        props.update(extra_props or {})
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    return {"type": "FeatureCollection", "features": feats}


def write_geojson(path, targets: Sequence[Target], extra_props: Optional[dict] = None) -> None:
    with open(path, "w") as f:
        json.dump(targets_to_geojson(targets, extra_props), f, indent=1)
