"""Cut an ODM orthophoto into overlapping model-sized tiles.

Contributed by Maya (AI agent, Sesame). Experimental.

Why tiles: a farm orthophoto is tens of thousands of pixels across. Shrinking
it to a model's 640 px input would turn a 15 cm rosette into less than one
pixel. So we slide a window the size of the model input across the full-
resolution mosaic (the WeedMap pattern cited in docs/WEED-ID.md).

Why overlap: a weed cut in half by a tile edge is easy to miss. With overlap
every weed smaller than the overlap appears whole in at least one tile.

Why "core" regions: with overlap the same weed is found twice. Each tile owns
only its core (the boundary is the middle of each overlap strip), and a
detection is kept only by the tile whose core contains its centroid. That
de-duplicates without any distance threshold and is exact: the cores tile the
image with no gaps and no double cover. Rule for users: overlap_px must be
larger than the biggest target you expect, in pixels.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

import numpy as np
from rasterio.windows import Window


@dataclass
class Tile:
    index: Tuple[int, int]          # (row index, col index) in the tile grid
    window: Window                  # full tile, in orthophoto pixel coords
    core: Tuple[float, float, float, float]   # (col0, row0, col1, row1) owned region
    rgb: Optional[np.ndarray] = None          # HxWx3 uint8
    valid: Optional[np.ndarray] = None        # HxW bool (orthophoto alpha)
    chm: Optional[np.ndarray] = None          # HxW float32 metres, NaN = no data

    @property
    def col_off(self) -> int:
        return int(self.window.col_off)

    @property
    def row_off(self) -> int:
        return int(self.window.row_off)

    def owns(self, col: float, row: float) -> bool:
        """True if this tile's core contains the GLOBAL pixel point."""
        c0, r0, c1, r1 = self.core
        return c0 <= col < c1 and r0 <= row < r1


def _starts(length: int, size: int, overlap: int) -> List[int]:
    """Tile start offsets along one axis. The last tile is pulled back to end
    exactly at the raster edge (so no tile hangs off the map), which makes the
    last stride shorter; cores are computed from actual neighbours, so that is
    fine."""
    if size <= overlap:
        raise ValueError("tile size must be larger than overlap")
    if length <= size:
        return [0]
    stride = size - overlap
    starts = list(range(0, length - size + 1, stride))
    if starts[-1] + size < length:
        starts.append(length - size)
    return starts


def _cores(starts: List[int], size: int, length: int) -> List[Tuple[float, float]]:
    """Owned interval per tile: boundaries at the middle of each overlap."""
    out = []
    for i, s in enumerate(starts):
        lo = 0.0 if i == 0 else (s + starts[i - 1] + size) / 2.0
        hi = float(length) if i == len(starts) - 1 else (starts[i + 1] + s + size) / 2.0
        out.append((lo, hi))
    return out


def tile_grid(width: int, height: int, size: int = 640, overlap: int = 128) -> List[Tile]:
    """All tiles (without pixel data) covering a width x height raster."""
    cs, rs = _starts(width, size, overlap), _starts(height, size, overlap)
    ccore, rcore = _cores(cs, size, width), _cores(rs, size, height)
    tiles = []
    for ri, r in enumerate(rs):
        for ci, c in enumerate(cs):
            w, h = min(size, width - c), min(size, height - r)
            tiles.append(Tile(index=(ri, ci), window=Window(c, r, w, h),
                              core=(ccore[ci][0], rcore[ri][0], ccore[ci][1], rcore[ri][1])))
    return tiles


def iter_tiles(odm_map, size: int = 640, overlap: int = 128, with_chm: bool = True,
               min_valid_frac: float = 0.01) -> Iterator[Tile]:
    """Yield tiles with pixel data filled in. Tiles that are (almost) entirely
    outside the mosaic (alpha = 0) are skipped: ODM mosaics are irregular and
    the corners of the bounding box are usually empty."""
    for t in tile_grid(odm_map.ortho.width, odm_map.ortho.height, size, overlap):
        rgb, valid = odm_map.ortho_window(t.window)
        if valid.mean() < min_valid_frac:
            continue
        t.rgb, t.valid = rgb, valid
        t.chm = odm_map.chm_window(t.window) if with_chm else None
        yield t
