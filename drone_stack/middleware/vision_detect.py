"""Detector adapters and target identity for the OpenIPC -> YOLO bridge.

Contributed by Maya (AI agent, Sesame), an AI assistant made by Sesame,
drafted at G's request in October 2026. EXPERIMENTAL. The Ultralytics adapter has NOT
been run with a real model here (no model was downloaded); its result parsing
is tested against a stand-in object shaped like an Ultralytics result. The
colour-blob detector is a numpy-only bench / test tool, not a weed detector.

Interface
---------
    BoxDetection     one box in pixel coordinates, with confidence, class and
                     (if the detector tracks) a track id.
    Detector         anything with .name and .detect(image) -> [BoxDetection].
                     Swap in a TensorRT engine, an OpenCV DNN net or a remote
                     inference call by implementing those two members.
    CentroidTracker  gives boxes a track id when the detector does not.
    TargetSelector   picks ONE target and keeps it. Its id becomes
                     Detection.target_id, which CenteringController locks onto:
                     if the id changes mid-run the controller ignores the new
                     target and, after its frame timeout, stops. Identity
                     therefore has to be stable, and a target is only offered
                     after it has been seen in `min_track_frames` frames, so a
                     single-frame false positive cannot become the target.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple


@dataclass
class BoxDetection:
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    class_id: int = 0
    class_name: str = "object"
    track_id: Optional[int] = None

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)


class Detector(Protocol):
    name: str

    def detect(self, image: Any) -> List[BoxDetection]: ...


# =============================================================== Ultralytics
def boxes_from_ultralytics(result: Any, names: Optional[Dict[int, str]] = None) -> List[BoxDetection]:
    """Convert one Ultralytics `Results` object to BoxDetections.

    Uses result.boxes.xyxy, .conf, .cls and (when tracking) .id, each of which
    Ultralytics returns as a tensor; .tolist() works on tensors and numpy
    arrays alike, so no torch import is needed here.
    """
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []
    names = names or getattr(result, "names", None) or {}

    def _list(v):
        if v is None:
            return None
        return v.tolist() if hasattr(v, "tolist") else list(v)

    xyxy = _list(getattr(boxes, "xyxy", None)) or []
    conf = _list(getattr(boxes, "conf", None)) or [1.0] * len(xyxy)
    cls = _list(getattr(boxes, "cls", None)) or [0] * len(xyxy)
    ids = _list(getattr(boxes, "id", None))
    out = []
    for i, b in enumerate(xyxy):
        c = int(cls[i])
        out.append(BoxDetection(float(b[0]), float(b[1]), float(b[2]), float(b[3]),
                                float(conf[i]), c, str(names.get(c, c)),
                                None if ids is None else int(ids[i])))
    return out


class UltralyticsYoloDetector:
    """YOLO through the `ultralytics` package (optional dependency).

    * Refuses to download weights unless allow_download=True: a missing model
      file is an error, so a field laptop never stalls fetching from the
      internet and you always know which weights produced a detection.
    * tracker="bytetrack.yaml" (or "botsort.yaml") runs Ultralytics' tracker
      with persist=True so ids survive between frames; tracker=None runs plain
      prediction and the bridge's CentroidTracker assigns ids.
    * Speed is NOT measured here. "About 30 fps" depends on the GPU, model
      size and imgsz; benchmark on the ground station itself
      (openipc_yolo_bridge.py --benchmark with --detector yolo).
    """

    def __init__(self, model_path: str, *, imgsz: int = 640, conf: float = 0.25,
                 iou: float = 0.45, device: Optional[str] = None,
                 classes: Optional[Sequence[int]] = None, half: bool = False,
                 tracker: Optional[str] = "bytetrack.yaml", allow_download: bool = False):
        if not allow_download and not os.path.exists(model_path):
            raise FileNotFoundError(
                f"model {model_path!r} not found and allow_download is false")
        try:
            from ultralytics import YOLO  # lazy: optional dependency
        except ImportError as e:
            raise RuntimeError("pip install ultralytics to use the YOLO detector") from e
        self.model = YOLO(model_path)
        self.name = f"ultralytics:{os.path.basename(model_path)}"
        self.kw = dict(imgsz=imgsz, conf=conf, iou=iou, half=half, verbose=False)
        if device:
            self.kw["device"] = device
        if classes:
            self.kw["classes"] = list(classes)
        self.tracker = tracker

    def detect(self, image: Any) -> List[BoxDetection]:
        if self.tracker:
            res = self.model.track(image, persist=True, tracker=self.tracker, **self.kw)
        else:
            res = self.model.predict(image, **self.kw)
        return boxes_from_ultralytics(res[0], getattr(self.model, "names", None)) if res else []


# =============================================================== colour blob
class ColorBlobDetector:
    """Finds ONE blob of a given BGR colour with numpy only.

    For bench tests (point the camera at a coloured card) and for the
    end-to-end tests, where frames are drawn with a coloured target. It reports
    the bounding box and centroid of all matching pixels, so it assumes a
    single target in view. Confidence is the matched fraction of the box.
    """

    def __init__(self, bgr: Tuple[int, int, int] = (0, 0, 255), tol: int = 60,
                 min_pixels: int = 20, class_name: str = "target"):
        self.bgr, self.tol, self.min_pixels = bgr, tol, min_pixels
        self.class_name = class_name
        self.name = f"colour-blob{bgr}"

    def detect(self, image: Any) -> List[BoxDetection]:
        import numpy as np
        if image is None:
            return []
        diff = np.abs(image.astype(np.int16) - np.array(self.bgr, dtype=np.int16))
        mask = np.all(diff <= self.tol, axis=2)
        n = int(mask.sum())
        if n < self.min_pixels:
            return []
        ys, xs = np.nonzero(mask)
        x1, x2, y1, y2 = float(xs.min()), float(xs.max() + 1), float(ys.min()), float(ys.max() + 1)
        fill = n / max(1.0, (x2 - x1) * (y2 - y1))
        # Report the centroid as the box centre so off-centre fill does not bias it.
        cx, cy = float(xs.mean()) + 0.5, float(ys.mean()) + 0.5
        hw, hh = (x2 - x1) / 2.0, (y2 - y1) / 2.0
        return [BoxDetection(cx - hw, cy - hh, cx + hw, cy + hh, min(1.0, 0.5 + fill / 2.0),
                             0, self.class_name, None)]


# =============================================================== tracking
class CentroidTracker:
    """Minimal id assignment for detectors without a tracker.

    Nearest-centre matching (closest pairs first) of same-class boxes between frames, within
    `max_jump_frac` of the image diagonal and `max_gap_s` of the last sighting.
    Deliberately simple and easy to audit; replace with ByteTrack for crowded
    scenes (many weeds in one frame). Times are GS_MONO capture estimates.
    """

    def __init__(self, max_jump_frac: float = 0.15, max_gap_s: float = 1.0):
        self.max_jump_frac, self.max_gap_s = max_jump_frac, max_gap_s
        self._tracks: Dict[int, Tuple[float, float, str, float]] = {}  # id -> cx, cy, cls, t
        self._next = 1

    def assign(self, boxes: List[BoxDetection], width: int, height: int, t: float) -> List[BoxDetection]:
        diag = math.hypot(width, height)
        self._tracks = {k: v for k, v in self._tracks.items() if t - v[3] <= self.max_gap_s}
        todo = [b for b in boxes if b.track_id is None]
        # Globally greedy: take the closest (box, track) pair first, so a new
        # box near an existing target cannot steal that target's id just
        # because it is more confident.
        pairs = []
        for bi, b in enumerate(todo):
            for k, (x, y, c, _) in self._tracks.items():
                d = math.hypot(b.cx - x, b.cy - y)
                if c == b.class_name and d <= self.max_jump_frac * diag:
                    pairs.append((d, bi, k))
        used_b, used_k = set(), set()
        for d, bi, k in sorted(pairs):
            if bi in used_b or k in used_k:
                continue
            todo[bi].track_id = k
            used_b.add(bi)
            used_k.add(k)
        for b in todo:
            if b.track_id is None:
                b.track_id = self._next
                self._next += 1
            self._tracks[b.track_id] = (b.cx, b.cy, b.class_name, t)
        return boxes


@dataclass
class SelectedTarget:
    box: BoxDetection
    target_id: str
    track_frames: int          # how many frames this identity has been seen in


class TargetSelector:
    """Choose one target, then stick to it.

    policy "centre"      start with the qualifying box nearest the image centre
                         (fewest moves to centre it).
    policy "confidence"  start with the most confident qualifying box.
    A box qualifies if confidence >= min_confidence and (if `classes` is set)
    its class name is listed. Once locked, only that track id is returned. If
    it is unseen for `lost_after_s`, the lock is released and a NEW id may be
    chosen; the controller then sees a different target_id and stops, which
    is the intended safe behaviour.
    """

    def __init__(self, policy: str = "centre", min_confidence: float = 0.5,
                 classes: Optional[Iterable[str]] = None, min_track_frames: int = 3,
                 lost_after_s: float = 1.0):
        if policy not in ("centre", "confidence"):
            raise ValueError("policy must be centre or confidence")
        self.policy, self.min_confidence = policy, min_confidence
        self.classes = set(classes) if classes else None
        self.min_track_frames, self.lost_after_s = min_track_frames, lost_after_s
        self._seen: Dict[Tuple[str, int], int] = {}
        self._last_seen_t: Dict[Tuple[str, int], float] = {}
        self.locked: Optional[Tuple[str, int]] = None
        self.relocks = 0

    def _qualifies(self, b: BoxDetection) -> bool:
        return b.confidence >= self.min_confidence and \
            (self.classes is None or b.class_name in self.classes) and b.track_id is not None

    def select(self, boxes: List[BoxDetection], width: int, height: int,
               t: float) -> Tuple[Optional[SelectedTarget], str]:
        quals = [b for b in boxes if self._qualifies(b)]
        for b in quals:
            k = (b.class_name, int(b.track_id))
            if t - self._last_seen_t.get(k, -1e18) > self.lost_after_s:
                self._seen[k] = 0            # reappearance after a gap restarts the count
            self._seen[k] = self._seen.get(k, 0) + 1
            self._last_seen_t[k] = t
        if self.locked is not None:
            for b in quals:
                if (b.class_name, int(b.track_id)) == self.locked:
                    return SelectedTarget(b, self._tid(self.locked), self._seen[self.locked]), ""
            if t - self._last_seen_t.get(self.locked, -1e18) <= self.lost_after_s:
                return None, "locked target not in this frame"
            self.locked = None
            self.relocks += 1
        ready = [b for b in quals if self._seen[(b.class_name, int(b.track_id))] >= self.min_track_frames]
        if not ready:
            return None, ("no qualifying target" if not quals else
                          f"candidate seen in fewer than {self.min_track_frames} frames")
        if self.policy == "centre":
            b = min(ready, key=lambda b: math.hypot(b.cx - width / 2.0, b.cy - height / 2.0))
        else:
            b = max(ready, key=lambda b: b.confidence)
        self.locked = (b.class_name, int(b.track_id))
        return SelectedTarget(b, self._tid(self.locked), self._seen[self.locked]), ""

    @staticmethod
    def _tid(k: Tuple[str, int]) -> str:
        return f"{k[0]}-{k[1]}"
