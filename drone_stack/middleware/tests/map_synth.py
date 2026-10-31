"""Synthetic ODM-like project for the map-to-route tests.

Contributed by Maya (AI agent, Sesame).

Builds, in a temp dir, the three files ODM would write:

    odm_orthophoto/odm_orthophoto.tif  RGBA uint8, 5 cm/px, WGS 84 / UTM 55S
    odm_dem/dsm.tif                    float32, 10 cm/px, nodata -9999
    odm_dem/dtm.tif                    float32, 10 cm/px, nodata -9999

The scene is a 50 m x 50 m paddock at a dummy location (beside ArduPilot's
default SITL home, CMAC, Canberra; not a real paddock) that rises
towards the east, with known weeds, a shrub, a tree, and a strip the
orthophoto did not cover. Everything is analytic, so tests can compare the
pipeline's answers with the truth.

Local scene coordinates (e, n) are metres east/north of the SW corner.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

EPSG = 32755                       # WGS 84 / UTM zone 55S
ORIGIN_LATLON = (-35.3630, 149.1650)   # dummy: next to ArduPilot's default SITL home (CMAC)
SIZE_M = 50.0
ORTHO_RES = 0.05
DEM_RES = 0.10
BASE_ELEV = 210.0                  # metres, arbitrary datum (cancels out)
NODATA = -9999.0

PASTURE = (125, 105, 75)           # ExG ~ 0.03: below the stand-in threshold
WEED = (40, 160, 40)               # ExG ~ 0.8
TREE = (70, 72, 60)                # dark canopy, low ExG


@dataclass
class Scene:
    root: Path
    x0: float                      # UTM easting of SW corner
    y0: float                      # UTM northing of SW corner
    slope: float                   # m rise per m east
    weeds: Dict[str, Tuple[float, float, float]]   # name -> (e, n, radius_m)
    shrub: Tuple[float, float, float, float]       # e, n, radius, height
    tree: Tuple[float, float, float, float, float] # e0, n0, e1, n1, height
    home_en: Tuple[float, float]
    nodata_strip_e: float          # ortho alpha = 0 for e >= this
    paths: Dict[str, Path] = field(default_factory=dict)

    def utm(self, e, n):
        return self.x0 + e, self.y0 + n

    def latlon(self, e, n):
        t = Transformer.from_crs(EPSG, 4326, always_xy=True)
        lon, lat = t.transform(*self.utm(e, n))
        return lat, lon

    def ground(self, e, n):
        return BASE_ELEV + self.slope * e

    @property
    def home_latlon(self):
        return self.latlon(*self.home_en)


DEFAULT_WEEDS = {
    "w1": (8.0, 6.0, 0.15),
    "w2": (14.0, 20.0, 0.15),
    "w3": (20.0, 9.0, 0.12),
    "w4": (30.0, 30.0, 0.15),        # behind the tree as seen from w5
    "w5": (30.0, 18.0, 0.15),
    "w6": (40.0, 12.0, 0.18),
    "w7": (12.8, 12.8, 0.15),        # sits in a tile overlap (dedup test)
    "w8": (46.0, 47.0, 0.15),        # ~63 m from home: beyond the 60 m geofence
}


def build_scene(root, slope: float = 0.15, weeds=None, tree_height: float = 8.0,
                with_dsm: bool = True, seed: int = 7) -> Scene:
    root = Path(root)
    t = Transformer.from_crs(4326, EPSG, always_xy=True)
    x0, y0 = t.transform(ORIGIN_LATLON[1], ORIGIN_LATLON[0])
    x0, y0 = math.floor(x0), math.floor(y0)
    sc = Scene(root=root, x0=x0, y0=y0, slope=slope, weeds=dict(weeds or DEFAULT_WEEDS),
               shrub=(24.0, 40.0, 0.8, 1.5), tree=(29.0, 23.0, 31.0, 25.0, tree_height),
               home_en=(2.0, 2.0), nodata_strip_e=48.5)

    # ---------------- orthophoto ------------------------------------------
    n_o = int(round(SIZE_M / ORTHO_RES))
    # pixel centre coordinates, row 0 = north edge
    e_o = (np.arange(n_o) + 0.5) * ORTHO_RES
    n_ocol = SIZE_M - (np.arange(n_o) + 0.5) * ORTHO_RES
    E, N = np.meshgrid(e_o, n_ocol)
    rng = np.random.default_rng(seed)
    img = np.empty((n_o, n_o, 3), dtype=np.float32)
    img[...] = PASTURE
    img += rng.normal(0, 4, img.shape)               # mild texture
    se, sn, sr, _sh = sc.shrub
    img[(E - se) ** 2 + (N - sn) ** 2 <= sr ** 2] = WEED      # shrub looks green
    te0, tn0, te1, tn1, _th = sc.tree
    img[(E >= te0) & (E <= te1) & (N >= tn0) & (N <= tn1)] = TREE
    for we, wn, wr in sc.weeds.values():
        img[(E - we) ** 2 + (N - wn) ** 2 <= wr ** 2] = WEED
    rgb = np.clip(img, 0, 255).astype(np.uint8)
    alpha = np.where(E >= sc.nodata_strip_e, 0, 255).astype(np.uint8)

    (root / "odm_orthophoto").mkdir(parents=True, exist_ok=True)
    op = root / "odm_orthophoto" / "odm_orthophoto.tif"
    with rasterio.open(op, "w", driver="GTiff", width=n_o, height=n_o, count=4,
                       dtype="uint8", crs=f"EPSG:{EPSG}", compress="deflate",
                       transform=from_origin(x0, y0 + SIZE_M, ORTHO_RES, ORTHO_RES)) as ds:
        for b in range(3):
            ds.write(rgb[..., b], b + 1)
        ds.write(alpha, 4)
    sc.paths["ortho"] = op

    # ---------------- DEMs ------------------------------------------------
    n_d = int(round(SIZE_M / DEM_RES))
    e_d = (np.arange(n_d) + 0.5) * DEM_RES
    n_d_ = SIZE_M - (np.arange(n_d) + 0.5) * DEM_RES
    Ed, Nd = np.meshgrid(e_d, n_d_)
    dtm = (BASE_ELEV + slope * Ed).astype(np.float32)
    dsm = dtm.copy()
    dsm[(Ed - se) ** 2 + (Nd - sn) ** 2 <= sr ** 2] += sc.shrub[3]
    dsm[(Ed >= te0) & (Ed <= te1) & (Nd >= tn0) & (Nd <= tn1)] += sc.tree[4]
    for we, wn, wr in sc.weeds.values():
        dsm[(Ed - we) ** 2 + (Nd - wn) ** 2 <= wr ** 2] += 0.05   # flat rosettes
    # a small hole in both DEMs (ODM leaves these over water / shadow)
    hole = (Ed >= 1.0) & (Ed <= 1.5) & (Nd >= 45.0) & (Nd <= 45.5)
    dtm[hole] = NODATA
    dsm[hole] = NODATA

    (root / "odm_dem").mkdir(parents=True, exist_ok=True)
    for name, arr in (("dtm", dtm), ("dsm", dsm)):
        if name == "dsm" and not with_dsm:
            continue
        p = root / "odm_dem" / f"{name}.tif"
        with rasterio.open(p, "w", driver="GTiff", width=n_d, height=n_d, count=1,
                           dtype="float32", crs=f"EPSG:{EPSG}", nodata=NODATA,
                           compress="deflate",
                           transform=from_origin(x0, y0 + SIZE_M, DEM_RES, DEM_RES)) as ds:
            ds.write(arr, 1)
        sc.paths[name] = p
    return sc


def stack_config_path() -> Path:
    """The real Pixhawk profile (20 m / 60 m / 3 m/s)."""
    return Path(__file__).resolve().parents[3] / "config" / "llm_stack.pixhawk.toml"


def base_cfg(scene: Scene, **route) -> dict:
    """A map_to_route config dict pointing at the scene."""
    r = {"camera_hfov_deg": 90.0}
    r.update(route)
    return {
        "map": {"ortho": str(scene.paths["ortho"]), "dsm": str(scene.paths.get("dsm", "")),
                "dtm": str(scene.paths["dtm"]), "horizontal_sigma_m": 3.0,
                "captured_at": "2026-10-20"},
        "tiles": {"size_px": 256, "overlap_px": 64},
        "detector": {"kind": "colour_blob", "max_canopy_h_m": 0.6, "min_area_px": 8},
        "limits": {"stack_config": str(stack_config_path())},
        "route": r,
    }
