"""ODM raster access for the map-to-route prototype (docs/MAP-TO-ROUTE.md).

Contributed by Maya (AI agent, Sesame). Experimental: not yet run on a real
ODM project, only on the synthetic GeoTIFFs in tests/map_synth.py.

What this module does
---------------------
OpenDroneMap writes three rasters that matter for spot spraying:

    odm_orthophoto/odm_orthophoto.tif   colour picture of the ground, 4 bands
                                        (R, G, B, alpha), georeferenced
    odm_dem/dsm.tif                     "tops of everything" height (float32)
    odm_dem/dtm.tif                     "the ground" height (float32)

(DSM and DTM are only written when ODM runs with --dsm / --dtm.)

This module opens them, checks they agree on a metric map CRS, and gives the
rest of the pipeline four things:

  1. pixel <-> map CRS <-> WGS84 lat/lon conversions (`MapGeo`)
  2. point and box sampling of a DEM in map CRS metres (`DemSampler`)
  3. a canopy-height tile (DSM minus DTM) resampled onto the exact pixel grid
     of an orthophoto tile, so a 4th "height above ground" channel lines up
     pixel-for-pixel with R, G, B (`OdmMap.chm_window`)
  4. a single place where the on-disk paths and nodata rules live

Design rules (also in docs/MAP-TO-ROUTE.md):
  * Heights are only ever used as DIFFERENCES within one ODM project
    (target ground minus home ground, DSM minus DTM). That cancels the unknown
    vertical datum (ellipsoid vs geoid) and most of the absolute vertical
    error of a map made without GCPs or RTK. Never mix heights from two
    different ODM runs.
  * Map CRS must be projected in metres (ODM writes UTM). Geographic
    (degree) rasters are refused rather than silently mis-measured.
  * nodata comes back as NaN / None, never as a number that looks like a
    height.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window

# A DEM larger than this many pixels is sampled window-by-window instead of
# being loaded whole. 64 M px of float32 = 256 MB. A 5-acre paddock at ODM's
# default 5 cm/px DEM is ~2,850 x 2,850 px = 8 M px, so normally it all loads.
MAX_IN_MEMORY_PX = 64_000_000


def _apply(aff, x: float, y: float) -> Tuple[float, float]:
    """Apply an affine transform to one point. Written out by hand because
    the `*` operator is being deprecated in favour of `@` in newer `affine`
    releases and older ones lack `@`."""
    return (aff.a * x + aff.b * y + aff.c, aff.d * x + aff.e * y + aff.f)


class MapError(ValueError):
    """Raised for unusable inputs (wrong CRS, missing file, home off-map)."""


# =============================================================================
# Coordinate conversions
# =============================================================================

class MapGeo:
    """Map CRS (metres) <-> WGS84 lat/lon.

    The drone, MAVLink and the safety gate all speak WGS84 lat/lon (EPSG:4326).
    ODM rasters are in a projected CRS such as WGS 84 / UTM 55S (EPSG:32755)
    or GDA2020 / MGA 55 (EPSG:7855). `always_xy=True` keeps axis order as
    (x=east/lon, y=north/lat) everywhere, which avoids the classic EPSG:4326
    lat-first trap.

    Datum note: GDA2020 and WGS84 differ by well under a metre today, which
    is far below the 2-6 m absolute error of a map made without GCPs/RTK, but
    it is not zero. pyproj picks the transformation; we do not hand-roll one.
    """

    def __init__(self, crs):
        self.crs = CRS.from_user_input(crs)
        if not self.crs.is_projected:
            raise MapError(f"map CRS {self.crs.to_string()} is not projected; ODM "
                           "outputs should be UTM (metres). Reproject first.")
        unit = (self.crs.axis_info[0].unit_name or "").lower()
        if unit not in ("metre", "meter", "m"):
            raise MapError(f"map CRS units are '{unit}', need metres")
        wgs84 = CRS.from_epsg(4326)
        self._to_ll = Transformer.from_crs(self.crs, wgs84, always_xy=True)
        self._from_ll = Transformer.from_crs(wgs84, self.crs, always_xy=True)

    def to_latlon(self, x: float, y: float) -> Tuple[float, float]:
        lon, lat = self._to_ll.transform(x, y)
        return float(lat), float(lon)

    def from_latlon(self, lat: float, lon: float) -> Tuple[float, float]:
        x, y = self._from_ll.transform(lon, lat)
        return float(x), float(y)


# =============================================================================
# DEM sampling
# =============================================================================

class DemSampler:
    """Sample a DSM or DTM at map-CRS points.

    `elev(x, y)`       bilinear height at a point (pixel-centre convention),
                       None if any of the 4 neighbours is nodata or off-map.
    `max_in_box(...)`  highest cell in an axis-aligned square around a point:
                       used for obstacle clearance (a tree next to a weed,
                       a fence along a leg). Conservative on purpose.

    The DEM can be in a different CRS from the orthophoto in principle; in
    practice ODM writes all three in the same UTM zone and OdmMap checks it.
    """

    def __init__(self, ds: "rasterio.io.DatasetReader", band: int = 1,
                 max_in_memory_px: int = MAX_IN_MEMORY_PX):
        self.ds = ds
        self.band = band
        self.transform = ds.transform
        self.inv = ~ds.transform
        self.width, self.height = ds.width, ds.height
        self.res = (abs(ds.transform.a), abs(ds.transform.e))
        self.nodata = ds.nodata
        self._arr: Optional[np.ndarray] = None
        if self.width * self.height <= max_in_memory_px:
            self._arr = self._clean(ds.read(band).astype(np.float64))

    # -- helpers ------------------------------------------------------------
    def _clean(self, a: np.ndarray) -> np.ndarray:
        """nodata -> NaN. ODM DEMs use -9999 as nodata."""
        if self.nodata is not None and not (isinstance(self.nodata, float)
                                            and math.isnan(self.nodata)):
            a[a == self.nodata] = np.nan
        return a

    def _read(self, r0: int, c0: int, r1: int, c1: int) -> np.ndarray:
        """Rows r0..r1-1, cols c0..c1-1, clipped to the raster, NaN outside."""
        out = np.full((r1 - r0, c1 - c0), np.nan)
        rr0, cc0 = max(r0, 0), max(c0, 0)
        rr1, cc1 = min(r1, self.height), min(c1, self.width)
        if rr1 <= rr0 or cc1 <= cc0:
            return out
        if self._arr is not None:
            block = self._arr[rr0:rr1, cc0:cc1]
        else:
            block = self._clean(self.ds.read(
                self.band, window=Window(cc0, rr0, cc1 - cc0, rr1 - rr0)).astype(np.float64))
        out[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] = block
        return out

    # -- public -------------------------------------------------------------
    def elev(self, x: float, y: float) -> Optional[float]:
        col, row = _apply(self.inv, x, y)
        # Pixel centres sit at +0.5; shift so integer = centre for bilinear.
        fc, fr = col - 0.5, row - 0.5
        c0, r0 = int(math.floor(fc)), int(math.floor(fr))
        dc, dr = fc - c0, fr - r0
        blk = self._read(r0, c0, r0 + 2, c0 + 2)
        if np.isnan(blk).any():
            # At the very edge (or next to a hole) fall back to nearest cell
            # if it is valid, else give up.
            rn, cn = int(math.floor(row)), int(math.floor(col))
            v = self._read(rn, cn, rn + 1, cn + 1)[0, 0]
            return None if np.isnan(v) else float(v)
        top = blk[0, 0] * (1 - dc) + blk[0, 1] * dc
        bot = blk[1, 0] * (1 - dc) + blk[1, 1] * dc
        return float(top * (1 - dr) + bot * dr)

    def max_in_box(self, x: float, y: float, half_size_m: float) -> Optional[float]:
        """Highest valid cell whose centre lies within +/- half_size_m of (x, y).
        None only if the whole box is nodata/off-map."""
        c_lo, r_hi = _apply(self.inv, x - half_size_m, y - half_size_m)
        c_hi, r_lo = _apply(self.inv, x + half_size_m, y + half_size_m)
        if r_lo > r_hi:            # north-up rasters have negative e
            r_lo, r_hi = r_hi, r_lo
        if c_lo > c_hi:
            c_lo, c_hi = c_hi, c_lo
        r0, r1 = int(math.floor(r_lo)), int(math.floor(r_hi)) + 1
        c0, c1 = int(math.floor(c_lo)), int(math.floor(c_hi)) + 1
        blk = self._read(r0, c0, r1, c1)
        if np.isnan(blk).all():
            return None
        return float(np.nanmax(blk))


# =============================================================================
# The ODM project
# =============================================================================

@dataclass
class OdmPaths:
    ortho: Path
    dsm: Optional[Path] = None
    dtm: Optional[Path] = None

    @classmethod
    def from_project(cls, project_dir) -> "OdmPaths":
        """Standard ODM layout (docs.opendronemap.org/outputs)."""
        p = Path(project_dir)
        dsm, dtm = p / "odm_dem" / "dsm.tif", p / "odm_dem" / "dtm.tif"
        return cls(ortho=p / "odm_orthophoto" / "odm_orthophoto.tif",
                   dsm=dsm if dsm.exists() else None,
                   dtm=dtm if dtm.exists() else None)


class OdmMap:
    """Opened ODM orthophoto + optional DSM/DTM. Use as a context manager.

    The DTM is REQUIRED for route planning (terrain-relative heights). The
    DSM is strongly recommended (obstacle clearance and the canopy-height
    channel); without it the planner cannot see trees or sheds, and says so.
    """

    def __init__(self, paths: OdmPaths):
        self.paths = paths
        for name in ("ortho", "dsm", "dtm"):
            f = getattr(paths, name)
            if f is not None and not Path(f).is_file():
                raise MapError(f"{name} raster not found: {f}")
        self.ortho = rasterio.open(paths.ortho)
        self.dsm_ds = rasterio.open(paths.dsm) if paths.dsm else None
        self.dtm_ds = rasterio.open(paths.dtm) if paths.dtm else None
        self.geo = MapGeo(self.ortho.crs)
        for name, ds in (("dsm", self.dsm_ds), ("dtm", self.dtm_ds)):
            if ds is not None and CRS.from_user_input(ds.crs) != self.geo.crs:
                raise MapError(f"{name} CRS {ds.crs} differs from orthophoto CRS "
                               f"{self.ortho.crs}; reproject so all three match")
        self.dsm = DemSampler(self.dsm_ds) if self.dsm_ds else None
        self.dtm = DemSampler(self.dtm_ds) if self.dtm_ds else None
        # Ground size of one orthophoto pixel, m. ODM ortho pixels are square.
        self.ortho_res_m = (abs(self.ortho.transform.a), abs(self.ortho.transform.e))
        self._chm_vrts = None

    # -- context manager ----------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self._chm_vrts:
            for v in self._chm_vrts:
                v.close()
        for ds in (self.ortho, self.dsm_ds, self.dtm_ds):
            if ds is not None:
                ds.close()

    # -- orthophoto ---------------------------------------------------------
    def ortho_window(self, win: Window) -> Tuple[np.ndarray, np.ndarray]:
        """(rgb HxWx3 uint8, valid HxW bool) for one window.

        ODM orthophotos carry an alpha band (band 4): 0 = no data. If there is
        no alpha band we fall back to the dataset nodata mask.
        """
        bands = self.ortho.count
        rgb = self.ortho.read([1, 2, 3], window=win, boundless=True, fill_value=0)
        rgb = np.moveaxis(rgb, 0, -1)
        if rgb.dtype != np.uint8:
            # 16-bit or float orthos (multispectral rigs): scale to 0-255 for
            # the stand-in detector. A real model should be trained on the
            # same scaling, see docs/MAP-TO-ROUTE.md "Classifier".
            hi = float(np.nanpercentile(rgb, 99.5)) or 1.0
            rgb = np.clip(rgb / hi * 255.0, 0, 255).astype(np.uint8)
        if bands >= 4:
            alpha = self.ortho.read(4, window=win, boundless=True, fill_value=0)
            valid = alpha > 0
        else:
            m = self.ortho.read_masks(1, window=win, boundless=True)
            valid = m > 0
        return rgb, valid

    def pixel_to_map(self, col: float, row: float) -> Tuple[float, float]:
        """Orthophoto pixel coordinates (col, row; 0,0 = top-left CORNER of
        the top-left pixel) -> map CRS x, y."""
        x, y = _apply(self.ortho.transform, col, row)
        return float(x), float(y)

    # -- canopy height ------------------------------------------------------
    def chm_window(self, win: Window) -> Optional[np.ndarray]:
        """Canopy height (DSM - DTM, metres) on the orthophoto's pixel grid for
        `win`. NaN where either DEM has no data. None if DSM/DTM missing.

        Both DEMs are resampled onto the ortho grid with a WarpedVRT, so the
        result lines up pixel-for-pixel with the RGB tile even though ODM's
        DEM resolution (default 5 cm/px) usually differs from the ortho's.
        Bilinear resampling smooths edges: a 2 cm-wide fence wire will not
        survive, a 30 cm shrub will.
        """
        if self.dsm_ds is None or self.dtm_ds is None:
            return None
        if self._chm_vrts is None:
            kw = dict(crs=self.ortho.crs, transform=self.ortho.transform,
                      width=self.ortho.width, height=self.ortho.height,
                      resampling=Resampling.bilinear, src_nodata=None, nodata=np.nan,
                      dtype="float32")
            vrts = []
            for ds in (self.dsm_ds, self.dtm_ds):
                k = dict(kw)
                k["src_nodata"] = ds.nodata
                vrts.append(WarpedVRT(ds, **k))
            self._chm_vrts = vrts
        dsm_v, dtm_v = self._chm_vrts
        # Windows from map_tiles never hang off the raster, so a plain read is
        # enough (WarpedVRT does not allow boundless reads).
        a = dsm_v.read(1, window=win).astype(np.float32)
        b = dtm_v.read(1, window=win).astype(np.float32)
        chm = a - b
        # Photogrammetric DSMs dip slightly below the filtered DTM on bare
        # ground; a negative canopy height means "ground", not a hole.
        return np.where(np.isnan(chm), np.nan, np.maximum(chm, 0.0))

    # -- heights relative to home ------------------------------------------
    def ground_rel(self, x: float, y: float, ref_ground: float) -> Optional[float]:
        """DTM height at (x, y) minus a reference ground height (home)."""
        if self.dtm is None:
            raise MapError("DTM required (run ODM with --dtm)")
        z = self.dtm.elev(x, y)
        return None if z is None else z - ref_ground
