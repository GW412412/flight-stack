"""Pluggable target detectors for orthophoto tiles.

Contributed by Maya (AI agent, Sesame). Experimental.

Every detector takes one `map_tiles.Tile` and returns `PixelDetection`s in
TILE pixel coordinates. Nothing here knows about map coordinates, heights or
routes; map_georef.py does that. Swapping the detector never touches the rest
of the pipeline.

Three detectors:

  ColourBlobDetector     A plain colour-threshold + blob finder. It is a TEST
                         STAND-IN and a bench tool (bright green card on a
                         brown tarp), NOT a weed classifier: on real pasture
                         almost everything is green. Optional canopy-height
                         limits show how the height channel is meant to help.
  ScriptedDetector       Returns fixed detections. For tests.
  UltralyticsSegAdapter  Wraps a locally trained Ultralytics YOLO-seg model
                         (the family docs/LABELING-WORKFLOW.md already locks).
                         It REFUSES to run without a weights file that already
                         exists on disk, because Ultralytics downloads official
                         weights automatically when given a bare model name.
                         Supports 3-channel (RGB) or 4-channel (RGB + canopy
                         height) models; see docs/MAP-TO-ROUTE.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Protocol, Sequence, Tuple

import numpy as np


@dataclass
class PixelDetection:
    """One target found in a tile. Coordinates are TILE pixels, origin at the
    tile's top-left pixel corner, x = column, y = row."""
    cx: float
    cy: float
    confidence: float
    label: str = "weed"
    # Outline in tile pixels [(x, y), ...]. None for box-only detectors, in
    # which case `bbox` is used for area (and over-estimates it).
    polygon: Optional[List[Tuple[float, float]]] = None
    bbox: Optional[Tuple[float, float, float, float]] = None   # x0, y0, x1, y1
    area_px: Optional[float] = None
    extra: dict = field(default_factory=dict)


class Detector(Protocol):
    name: str

    def detect(self, tile) -> List[PixelDetection]:
        ...


# =============================================================================
# Geometry helpers (no OpenCV dependency)
# =============================================================================

def polygon_area_centroid(poly: Sequence[Tuple[float, float]]) -> Tuple[float, float, float]:
    """Shoelace area and centroid of a simple polygon (pixel units)."""
    pts = np.asarray(poly, dtype=float)
    if len(pts) < 3:
        c = pts.mean(axis=0) if len(pts) else np.zeros(2)
        return 0.0, float(c[0]), float(c[1])
    x, y = pts[:, 0], pts[:, 1]
    xn, yn = np.roll(x, -1), np.roll(y, -1)
    cross = x * yn - xn * y
    a = cross.sum() / 2.0
    if abs(a) < 1e-12:
        return 0.0, float(x.mean()), float(y.mean())
    cx = ((x + xn) * cross).sum() / (6.0 * a)
    cy = ((y + yn) * cross).sum() / (6.0 * a)
    return float(abs(a)), float(cx), float(cy)


# =============================================================================
# Stand-in: colour blobs
# =============================================================================

class ColourBlobDetector:
    """Excess-green threshold + connected blobs, with optional height limits.

    ExG = 2g - r - b on chromatic coordinates (r = R/(R+G+B) etc.), a classic
    vegetation index. Blobs come from rasterio.features.shapes (GDAL
    polygonize), so no OpenCV/SciPy is needed.

    Height limits (only when the tile has a canopy-height channel):
      max_canopy_h_m  drop blobs whose tallest pixel is above this (shrubs,
                      trees, cattle, the ute)
      min_canopy_h_m  drop blobs lower than this (use for bolted weeds)

    Confidence is a made-up monotone function of ExG margin. It only exists so
    the downstream conf >= 0.50 gate has something to act on in tests.
    """

    name = "colour_blob"

    def __init__(self, exg_min: float = 0.12, min_area_px: int = 20,
                 max_area_px: int = 200_000, max_canopy_h_m: Optional[float] = None,
                 min_canopy_h_m: Optional[float] = None, label: str = "weed"):
        self.exg_min = exg_min
        self.min_area_px = min_area_px
        self.max_area_px = max_area_px
        self.max_canopy_h_m = max_canopy_h_m
        self.min_canopy_h_m = min_canopy_h_m
        self.label = label

    def exg(self, rgb: np.ndarray) -> np.ndarray:
        f = rgb.astype(np.float32)
        s = f.sum(axis=-1) + 1e-6
        r, g, b = f[..., 0] / s, f[..., 1] / s, f[..., 2] / s
        return 2 * g - r - b

    def detect(self, tile) -> List[PixelDetection]:
        from rasterio import features          # local: keeps import light
        from rasterio.transform import Affine

        exg = self.exg(tile.rgb)
        mask = exg > self.exg_min
        if tile.valid is not None:
            mask &= tile.valid
        out: List[PixelDetection] = []
        if not mask.any():
            return out
        for geom, _ in features.shapes(mask.astype(np.uint8), mask=mask,
                                       transform=Affine.identity(), connectivity=8):
            ring = geom["coordinates"][0]
            area, cx, cy = polygon_area_centroid(ring)
            if not (self.min_area_px <= area <= self.max_area_px):
                continue
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            x0, x1 = int(max(min(xs), 0)), int(min(max(xs), mask.shape[1]))
            y0, y1 = int(max(min(ys), 0)), int(min(max(ys), mask.shape[0]))
            canopy = None
            if tile.chm is not None:
                sub = tile.chm[y0:y1, x0:x1][mask[y0:y1, x0:x1]]
                sub = sub[~np.isnan(sub)]
                canopy = float(sub.max()) if sub.size else None
                if canopy is not None:
                    if self.max_canopy_h_m is not None and canopy > self.max_canopy_h_m:
                        continue
                    if self.min_canopy_h_m is not None and canopy < self.min_canopy_h_m:
                        continue
            margin = float(exg[y0:y1, x0:x1][mask[y0:y1, x0:x1]].mean() - self.exg_min)
            conf = float(min(0.99, 0.5 + 2.0 * margin))
            out.append(PixelDetection(cx=cx, cy=cy, confidence=conf, label=self.label,
                                      polygon=[(float(a), float(b)) for a, b in ring],
                                      bbox=(x0, y0, x1, y1), area_px=area,
                                      extra={"canopy_h_m": canopy}))
        return out


class ScriptedDetector:
    """Returns detections from a callable(tile) -> list. For tests."""
    name = "scripted"

    def __init__(self, fn: Callable[[object], List[PixelDetection]]):
        self.fn = fn

    def detect(self, tile) -> List[PixelDetection]:
        return list(self.fn(tile))


# =============================================================================
# Real model: Ultralytics YOLO-seg adapter
# =============================================================================

WEIGHT_SUFFIXES = {".pt", ".onnx", ".engine", ".torchscript", ".mlpackage", ".tflite"}


def _to_np(x):
    """torch tensor / numpy / list -> numpy, without importing torch."""
    if x is None:
        return None
    if hasattr(x, "detach"):
        x = x.detach()
    if hasattr(x, "cpu"):
        x = x.cpu()
    if hasattr(x, "numpy"):
        return x.numpy()
    return np.asarray(x)


class UltralyticsSegAdapter:
    """Run a LOCAL Ultralytics YOLO-seg (or detect) model on a tile.

    weights_path  existing file only. A bare name such as "yolo26n-seg.pt"
                  that is not on disk is refused: Ultralytics would download
                  it, and a COCO model knows no weeds anyway.
    channels      3 = RGB model, 4 = RGB + canopy height model (trained with
                  `channels: 4` in the dataset YAML).
    input_order   channel order the model was TRAINED with. Ultralytics treats
                  numpy input as BGR (OpenCV convention), so the default is
                  "BGR" / "BGRH". For a 4-channel model the order must match
                  however the training TIFFs were read: this is an open
                  question in docs/MAP-TO-ROUTE.md, check it before trusting
                  a 4-channel model.
    chm_full_scale_m  canopy height mapped to 255 in the 4th channel. Must
                  match training. Placeholder 2.0 m.
    yolo_factory  for tests: callable(path) -> model with .predict(). Leave
                  None to import ultralytics lazily.
    """

    name = "ultralytics_seg"

    def __init__(self, weights_path, channels: int = 3, conf: float = 0.25,
                 imgsz: Optional[int] = None, device: Optional[str] = None,
                 input_order: Optional[str] = None, chm_full_scale_m: float = 2.0,
                 label_map: Optional[dict] = None, yolo_factory=None):
        p = Path(weights_path) if weights_path else None
        if p is None or not p.is_file():
            raise FileNotFoundError(
                f"weights file not found: {weights_path!r}. This adapter never "
                "downloads weights; train or copy a model to disk first "
                "(docs/LABELING-WORKFLOW.md).")
        if p.suffix.lower() not in WEIGHT_SUFFIXES:
            raise ValueError(f"unrecognised weights format {p.suffix!r}")
        if channels not in (3, 4):
            raise ValueError("channels must be 3 (RGB) or 4 (RGB + canopy height)")
        self.weights_path = p
        self.channels = channels
        self.conf = conf
        self.imgsz = imgsz
        self.device = device
        self.input_order = (input_order or ("BGR" if channels == 3 else "BGRH")).upper()
        if sorted(self.input_order) != sorted("RGB" if channels == 3 else "RGBH"):
            raise ValueError(f"input_order {self.input_order!r} does not match "
                             f"{channels} channels")
        self.chm_full_scale_m = chm_full_scale_m
        self.label_map = label_map
        if yolo_factory is None:
            from ultralytics import YOLO      # lazy, optional dependency (AGPL-3.0)
            yolo_factory = YOLO
        self.model = yolo_factory(str(p))

    # -- input -------------------------------------------------------------
    def build_input(self, tile) -> np.ndarray:
        """HxWxC uint8 in the model's trained channel order."""
        planes = {"R": tile.rgb[..., 0], "G": tile.rgb[..., 1], "B": tile.rgb[..., 2]}
        if self.channels == 4:
            if tile.chm is None:
                raise ValueError("4-channel model needs DSM + DTM (canopy height)")
            chm = np.nan_to_num(tile.chm, nan=0.0)
            planes["H"] = (np.clip(chm / self.chm_full_scale_m, 0, 1) * 255).astype(np.uint8)
        img = np.stack([planes[c] for c in self.input_order], axis=-1)
        if tile.valid is not None:
            img = img * tile.valid[..., None].astype(np.uint8)
        return np.ascontiguousarray(img)

    # -- inference ---------------------------------------------------------
    def detect(self, tile) -> List[PixelDetection]:
        img = self.build_input(tile)
        kw = {"conf": self.conf, "verbose": False}
        if self.imgsz:
            kw["imgsz"] = self.imgsz
        if self.device:
            kw["device"] = self.device
        results = self.model.predict(img, **kw)
        return self.parse(results[0] if isinstance(results, (list, tuple)) else results)

    def parse(self, r) -> List[PixelDetection]:
        """Ultralytics Results -> PixelDetections (tile pixels).

        Uses r.boxes.{xyxy, conf, cls} and, for -seg models, r.masks.xy (one
        polygon per instance, already in input-image pixels)."""
        boxes = getattr(r, "boxes", None)
        if boxes is None:
            return []
        xyxy = _to_np(boxes.xyxy)
        confs = _to_np(boxes.conf)
        clss = _to_np(boxes.cls)
        if xyxy is None or len(xyxy) == 0:
            return []
        names = self.label_map or getattr(r, "names", None) or {}
        masks = getattr(r, "masks", None)
        polys = list(masks.xy) if masks is not None and getattr(masks, "xy", None) is not None else None
        out = []
        for i in range(len(xyxy)):
            x0, y0, x1, y1 = (float(v) for v in xyxy[i])
            label = str(names.get(int(clss[i]), int(clss[i]))) if clss is not None else "weed"
            poly = None
            if polys is not None and i < len(polys) and len(polys[i]) >= 3:
                poly = [(float(a), float(b)) for a, b in _to_np(polys[i])]
                area, cx, cy = polygon_area_centroid(poly)
            else:
                area = (x1 - x0) * (y1 - y0)       # box area over-estimates canopy
                cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            out.append(PixelDetection(cx=cx, cy=cy, confidence=float(confs[i]), label=label,
                                      polygon=poly, bbox=(x0, y0, x1, y1), area_px=area,
                                      extra={"area_from": "mask" if poly else "bbox"}))
        return out
