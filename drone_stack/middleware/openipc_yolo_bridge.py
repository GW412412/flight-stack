"""OpenIPC video -> YOLO detections -> CenteringController observations.

Contributed by Maya (AI agent, Sesame), drafted at G's request in October
2026. EXPERIMENTAL and UNREVIEWED: tested only with mock and replayed frames
on a development machine. It has never seen a real OpenIPC camera, a real
YOLO model, a radio link or the aircraft. Please critique it before merging.

What it does
------------
    camera --(RTSP / RTP-UDP / file)--> vision_ingest reader thread
           --> LatestFrameSlot (newest frame only, older ones dropped)
           --> VisionBridge.process_frame():
                 1. reject frames that waited too long or predate a reconnect
                 2. estimate WHEN the frame was captured, with an uncertainty
                    window, on GS_MONO (vision_clock.CaptureTimeEstimator)
                 3. run the detector, give boxes track ids, pick one target
                 4. optionally look up the heading over the capture window
                    from flight-controller-timestamped telemetry
                 5. convert to GS_WALL and publish a visual_centering.Detection
           --> sink: UDP JSON to 127.0.0.1:5602 (the existing
               UdpJsonDetectionSource), or LatestDetectionBox in-process.

What it deliberately does NOT do
--------------------------------
* It never sends MAVLink and never commands the aircraft. It only publishes
  observations. CenteringController, through DroneTools and the safety gate,
  stays the single flight-command path.
* No sprayer control.
* It does not invent capture times. Every published time is an estimate with
  a stated method, an uncertainty and an `assumed` flag (vision_clock.py).

Interface to the existing backbone (unchanged files)
----------------------------------------------------
    visual_centering.Detection        the published record; unknown extra
                                      JSON keys are ignored by from_dict, so
                                      the extras below are safe to add.
    visual_centering.DetectionSource  LatestDetectionBox implements latest().
    UdpJsonDetectionSource            keeps the newest datagram by `t`.
    mav_node.MavNode                  read-only, via MavNodeTelemetryFeed.

Detection.t is the EARLIEST plausible capture time (TimeEstimate.lo), on the
ground station's time.time() clock. Timing issue T6: this is deliberate. The
controller rejects frames older than max_detection_age_s and frames captured
before "settle + settle_frame_delay_s"; publishing the earliest plausible time
makes both checks err towards rejecting a frame rather than acting on one that
might predate the last move. The cost is that the uncertainty eats into the
controller's age budget; timing_budget_report() prints that budget at start.

Timing issue T12: the bridge and the controller must share a time.time()
clock (same machine, or machines synced well inside the age budget). Over
127.0.0.1 they do.

Timing issues are numbered T1..T14 and explained in
docs/OPENIPC-YOLO-BRIDGE.md.
"""
from __future__ import annotations

import argparse
import json
import math
import socket
import sys
import threading
import time
from collections import Counter, deque
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Protocol, Tuple

from vision_clock import (CaptureTimeEstimator, CaptureTimingConfig, GsClock,
                          HeadingWindow, MavNodeTelemetryFeed, TelemetryAligner, TimeEstimate)
from vision_detect import (CentroidTracker, ColorBlobDetector, Detector,
                           SelectedTarget, TargetSelector, UltralyticsYoloDetector)
from vision_ingest import (Frame, LatestFrameSlot, OpenCVStreamSource, ReplayFrameSource,
                           gst_rtp_pipeline)
from visual_centering import Detection

BRIDGE_VERSION = "openipc_yolo_bridge 0.1 (experimental)"


# =============================================================== config
@dataclass
class VideoConfig:
    url: str = "rtsp://root:${OPENIPC_PASSWORD}@192.168.1.10:554/stream=1"
    backend: str = "ffmpeg"          # ffmpeg | gstreamer | any
    rtp_port: int = 5600             # used when url = "rtp" (GStreamer pipeline)
    rtp_codec: str = "h265"
    rtsp_transport: str = "udp"
    open_timeout_s: float = 5.0
    read_timeout_s: float = 2.0
    reconnect_backoff_s: float = 0.5
    stall_s: float = 1.0
    source_id: str = "cam0"


@dataclass
class DetectorConfig:
    kind: str = "yolo"               # yolo | colour | none
    model_path: str = "models/weeds.pt"
    imgsz: int = 640
    conf: float = 0.25
    iou: float = 0.45
    device: str = ""
    classes: List[int] = field(default_factory=list)
    tracker: str = "bytetrack.yaml"  # "" = bridge CentroidTracker
    allow_download: bool = False
    half: bool = False
    colour_bgr: List[int] = field(default_factory=lambda: [0, 0, 255])
    colour_tol: int = 60


@dataclass
class TargetConfig:
    policy: str = "centre"
    min_confidence: float = 0.5
    class_names: List[str] = field(default_factory=list)
    min_track_frames: int = 3
    lost_after_s: float = 1.0


@dataclass
class PipelineTimingConfig:
    """Limits applied by the bridge itself. Placeholders; tune after measuring.

    max_frame_age_s            frame waited this long for the detector -> drop
    max_capture_uncertainty_s  capture window wider than this -> drop
    max_publish_age_s          (now - best capture estimate) at publish -> drop
    controller_max_detection_age_s / controller_settle_frame_delay_s
        copies of the CenteringConfig values the bridge is feeding, used only
        for the startup budget report (the controller enforces its own).
    expected_detect_s / expected_observer_s
        rough per-frame times for the budget report. UNMEASURED defaults.
    """
    max_frame_age_s: float = 0.15
    max_capture_uncertainty_s: float = 0.25
    max_publish_age_s: float = 0.45
    controller_max_detection_age_s: float = 0.5
    controller_settle_frame_delay_s: float = 0.2
    expected_detect_s: float = 0.033
    expected_observer_s: float = 0.2


@dataclass
class ObserverConfig:
    """Optional slow vision model (e.g. ~5 fps) that proposes a nudge.

    Timing issue T5: its answer describes a frame that is older than the fast
    detector's newest, and UdpJsonDetectionSource keeps only the newest
    datagram by `t`, so interleaving both would make the slow answers lose
    every time. When an observer is enabled the bridge therefore publishes
    ONLY observer-annotated frames (effective rate = observer rate) and each
    one is re-checked for age when the observer finishes.
    """
    enabled: bool = False
    max_rate_hz: float = 5.0


@dataclass
class TelemetryConfig:
    enabled: bool = False
    require_heading: bool = True
    max_heading_spread_deg: float = 3.0
    telem_min_latency_s: float = 0.0
    max_extrapolate_s: float = 0.15
    max_yaw_rate_dps: float = 10.0


@dataclass
class OutputConfig:
    udp: str = "127.0.0.1:5602"      # "" = no UDP output
    print_json: bool = False


@dataclass
class BridgeConfig:
    video: VideoConfig = field(default_factory=VideoConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    target: TargetConfig = field(default_factory=TargetConfig)
    capture: CaptureTimingConfig = field(default_factory=CaptureTimingConfig)
    pipeline: PipelineTimingConfig = field(default_factory=PipelineTimingConfig)
    observer: ObserverConfig = field(default_factory=ObserverConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    output: OutputConfig = field(default_factory=OutputConfig)


def _fill(dc, d: Dict[str, Any], where: str):
    names = {f.name: f for f in fields(dc)}
    for k, v in d.items():
        if k not in names:
            raise ValueError(f"unknown setting [{where}] {k}")   # catch typos
        cur = getattr(dc, k)
        if is_dataclass(cur):
            if not isinstance(v, dict):
                raise ValueError(f"[{where}.{k}] must be a table")
            _fill(cur, v, f"{where}.{k}")
        else:
            setattr(dc, k, v)
    return dc


def config_from_dict(d: Dict[str, Any]) -> BridgeConfig:
    cfg = BridgeConfig()
    for k, v in d.items():
        if not hasattr(cfg, k):
            raise ValueError(f"unknown section [{k}]")
        _fill(getattr(cfg, k), v, k)
    errs = cfg.capture.validate()
    if errs:
        raise ValueError("; ".join(errs))
    return cfg


def load_config(path: str) -> BridgeConfig:
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        import tomli as tomllib  # type: ignore
    with open(path, "rb") as fh:
        return config_from_dict(tomllib.load(fh))


# =============================================================== budget
def timing_budget_report(cfg: BridgeConfig) -> List[str]:
    """Explain, before running, whether the configured numbers can work.

    Lines starting with "ERROR" mean every frame would be dropped or rejected
    by the controller; "WARN" lines are timing issues a reviewer should see.
    """
    c, p = cfg.capture, cfg.pipeline
    out = []
    if c.method == "arrival":
        width = c.max_latency_s - c.min_latency_s
        worst_latency = c.max_latency_s
    elif c.method == "stream_pts":
        width = 2 * c.min_latency_uncertainty_s
        worst_latency = c.min_latency_s + c.min_latency_uncertainty_s
    else:
        width = 2 * c.camera_clock_uncertainty_s
        worst_latency = c.max_latency_s + c.camera_clock_uncertainty_s
    proc = p.expected_detect_s + (p.expected_observer_s if cfg.observer.enabled else 0.0)
    # Age the controller sees = now - lo. In arrival mode lo = arrival - max_latency,
    # so age = max_latency + processing. In the other modes lo sits half a window
    # below the best estimate, which adds width / 2.
    pad = 0.0 if c.method == "arrival" else width / 2
    worst_age = worst_latency + proc + pad
    out.append(f"INFO capture time method {c.method}: uncertainty window {width * 1000:.0f} ms, "
               f"worst-case age at the controller about {worst_age * 1000:.0f} ms "
               f"(latency {worst_latency * 1000:.0f} + processing {proc * 1000:.0f} ms) vs "
               f"max_detection_age_s {p.controller_max_detection_age_s * 1000:.0f} ms")
    if not c.measured:
        out.append("WARN T1: video latency numbers are ASSUMED, not measured on this link")
    if width > p.max_capture_uncertainty_s:
        out.append(f"ERROR capture uncertainty {width:.3f} s exceeds max_capture_uncertainty_s "
                   f"{p.max_capture_uncertainty_s:.3f} s: every frame would be dropped")
    if worst_age > p.controller_max_detection_age_s:
        out.append("WARN T6: worst-case age exceeds the controller's max_detection_age_s; "
                   "slow frames will be rejected as stale")
    if p.max_publish_age_s > p.controller_max_detection_age_s:
        out.append("WARN max_publish_age_s is above the controller's limit; the controller "
                   "will reject some frames the bridge publishes")
    if cfg.observer.enabled:
        out.append(f"WARN T5: observer on, so only observer frames are published "
                   f"(at most {cfg.observer.max_rate_hz:g} per second)")
    if not cfg.telemetry.enabled:
        out.append("WARN T4: no telemetry alignment; the controller will use its own heading "
                   "lookup, stamped at poll time rather than measurement time")
    # T7: the controller needs lo >= settle + settle_frame_delay_s, so the first
    # acceptable frame ARRIVES about delay + worst latency + pad after settling.
    out.append(f"INFO T7: after each move the first acceptable frame arrives about "
               f"{(p.controller_settle_frame_delay_s + worst_latency + pad) * 1000:.0f} ms "
               "after the copter settles")
    return out


# =============================================================== sinks
class DetectionSink(Protocol):
    def publish(self, det: Detection, extras: Dict[str, Any]) -> None: ...


class LatestDetectionBox:
    """In-process DetectionSource for CenteringController (same newest-by-t rule
    as UdpJsonDetectionSource). Use when the bridge and controller share a
    process; no socket involved."""

    def __init__(self):
        self._lock = threading.Lock()
        self._latest: Optional[Detection] = None
        self.extras: Dict[str, Any] = {}
        self.published = 0

    def publish(self, det: Detection, extras: Dict[str, Any]) -> None:
        with self._lock:
            self.published += 1
            if self._latest is None or det.t >= self._latest.t:
                self._latest, self.extras = det, extras

    def latest(self) -> Optional[Detection]:
        with self._lock:
            return self._latest


def detection_json(det: Detection, extras: Dict[str, Any]) -> bytes:
    d = asdict(det)
    d.update(extras)
    return json.dumps(d, separators=(",", ":")).encode()


class UdpJsonSink:
    """Sends each Detection as one JSON datagram (format of UdpJsonDetectionSource)."""

    def __init__(self, target: str = "127.0.0.1:5602"):
        host, port = target.rsplit(":", 1)
        self.addr = (host, int(port))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sent = 0
        self.errors = 0

    def publish(self, det: Detection, extras: Dict[str, Any]) -> None:
        try:
            self.sock.sendto(detection_json(det, extras), self.addr)
            self.sent += 1
        except OSError:
            self.errors += 1

    def close(self):
        self.sock.close()


class PrintSink:
    def __init__(self, stream=None):
        self.stream = stream or sys.stdout

    def publish(self, det: Detection, extras: Dict[str, Any]) -> None:
        self.stream.write(detection_json(det, extras).decode() + "\n")
        self.stream.flush()


# =============================================================== observer
@dataclass
class Observation:
    """Slow vision model's answer for one frame. Nudges are in the CAMERA frame
    (forward = image top, right = image right), exactly as Detection expects;
    the controller still caps and sanity-checks them."""
    nudge_forward_m: Optional[float] = None
    nudge_right_m: Optional[float] = None
    agrees_with_target: bool = True
    note: str = ""


class VisionObserver(Protocol):
    def observe(self, image: Any, det: Detection) -> Optional[Observation]: ...


# =============================================================== bridge
@dataclass
class Outcome:
    published: bool
    reason: str = ""
    detection: Optional[Detection] = None
    extras: Dict[str, Any] = field(default_factory=dict)


class _Stats:
    def __init__(self, keep: int = 300):
        self.drops: Counter = Counter()
        self.frames = 0
        self.published = 0
        self.detect_s: Deque[float] = deque(maxlen=keep)
        self.observer_s: Deque[float] = deque(maxlen=keep)
        self.age_at_publish_s: Deque[float] = deque(maxlen=keep)

    @staticmethod
    def _summ(xs) -> Optional[Dict[str, float]]:
        if not xs:
            return None
        s = sorted(xs)
        return {"n": len(s), "mean_ms": round(1000 * sum(s) / len(s), 2),
                "p50_ms": round(1000 * s[len(s) // 2], 2),
                "p95_ms": round(1000 * s[min(len(s) - 1, int(0.95 * len(s)))], 2),
                "max_ms": round(1000 * s[-1], 2)}

    def as_dict(self) -> Dict[str, Any]:
        return {"frames": self.frames, "published": self.published,
                "drops": dict(self.drops), "detect": self._summ(self.detect_s),
                "observer": self._summ(self.observer_s),
                "age_at_publish": self._summ(self.age_at_publish_s)}


class VisionBridge:
    """The pipeline core. process_frame() is synchronous and deterministic
    given its clock, so tests drive it frame by frame; run() wraps it in a
    loop that takes frames from a LatestFrameSlot.

    Drop reasons are counted in stats().drops under short stable keys so a
    reviewer can see exactly why frames were not published.
    """

    def __init__(self, cfg: BridgeConfig, detector: Detector, sinks: List[DetectionSink],
                 *, clock: Optional[GsClock] = None, observer: Optional[VisionObserver] = None,
                 telemetry: Optional[TelemetryAligner] = None,
                 telemetry_feed: Optional[MavNodeTelemetryFeed] = None,
                 observer_inline: bool = False):
        self.cfg = cfg
        self.detector = detector
        self.sinks = list(sinks)
        self.clock = clock or GsClock()
        self.capture = CaptureTimeEstimator(cfg.capture)
        self.tracker = CentroidTracker()
        t = cfg.target
        self.selector = TargetSelector(t.policy, t.min_confidence, t.class_names or None,
                                       t.min_track_frames, t.lost_after_s)
        self.telemetry = telemetry
        self.telemetry_feed = telemetry_feed
        if cfg.telemetry.enabled and telemetry is None:
            raise ValueError("telemetry.enabled needs a TelemetryAligner")
        if cfg.observer.enabled and observer is None:
            raise ValueError("observer.enabled needs a VisionObserver")
        self.observer = observer if cfg.observer.enabled else None
        self.observer_inline = observer_inline
        self._obs_slot: List[Tuple[Frame, Detection, Dict[str, Any], TimeEstimate, float]] = []
        self._obs_cv = threading.Condition()
        self._obs_last_start = -1e18
        self._obs_busy = False
        self.stats_ = _Stats()
        self._max_epoch = -1
        self._last_best: Optional[float] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()        # one frame at a time through _process
        self._emit_lock = threading.Lock()   # publishing from frame loop + observer

    # ---------------------------------------------------------- helpers
    def _drop(self, key: str, detail: str = "") -> Outcome:
        self.stats_.drops[key] += 1
        return Outcome(False, f"{key}: {detail}" if detail else key)

    def stats(self) -> Dict[str, Any]:
        d = self.stats_.as_dict()
        d.update({"gs_wall_clock_steps": self.clock.steps,
                  "stream_clock_resets": self.capture.pts.resets,
                  "target_relocks": self.selector.relocks})
        if self.telemetry is not None:
            d["telemetry_fc_reboots"] = self.telemetry.fc_reboots
        return d

    # ---------------------------------------------------------- core
    def process_frame(self, frame: Frame) -> Outcome:
        with self._lock:
            return self._process(frame)

    def _process(self, frame: Frame) -> Outcome:
        st, cfg = self.stats_, self.cfg
        st.frames += 1
        now = self.clock.mono()

        # 1. Frame bookkeeping: stale in the mailbox, or from before a reconnect.
        #    (T13: a slow detector means frames wait or are overwritten.)
        if now - frame.arrival_mono > cfg.pipeline.max_frame_age_s:
            return self._drop("waited_too_long", f"{now - frame.arrival_mono:.3f} s since arrival")
        if frame.epoch < self._max_epoch:
            return self._drop("old_epoch", "frame from before the last reconnect")
        self._max_epoch = max(self._max_epoch, frame.epoch)

        # 2. When was it captured? (vision_clock.py; T1, T2, T10, T11)
        est, why = self.capture.estimate(frame.arrival_mono, frame.stream_pts_s, frame.epoch,
                                         frame.camera_wall_s, self.clock.wall_minus_mono())
        if est is None:
            return self._drop("no_capture_time", why)
        if est.width > cfg.pipeline.max_capture_uncertainty_s:
            return self._drop("capture_too_uncertain", f"window {est.width:.3f} s")
        if self._last_best is not None and est.best <= self._last_best:
            return self._drop("out_of_order", "captured no later than an earlier frame")
        self._last_best = est.best

        # 3. Detect, identify, select.
        if frame.image is None:
            return self._drop("no_image")
        t0 = time.perf_counter()
        try:
            boxes = self.detector.detect(frame.image)
        except Exception as e:     # a detector crash must not kill the bridge
            return self._drop("detector_error", f"{type(e).__name__}: {e}")
        st.detect_s.append(time.perf_counter() - t0)
        self.tracker.assign(boxes, frame.width, frame.height, est.best)
        sel, why = self.selector.select(boxes, frame.width, frame.height, est.best)
        if sel is None:
            return self._drop("no_target", why)

        # 4. Heading over the capture window (T4, T8, T9, T14).
        hw: Optional[HeadingWindow] = None
        if self.telemetry is not None:
            if self.telemetry_feed is not None:
                self.telemetry_feed.poll()
            hw, why = self.telemetry.heading_over(est.lo, est.hi)
            if hw is None and cfg.telemetry.require_heading:
                return self._drop("no_heading", why)
            if hw is not None and hw.spread_deg > cfg.telemetry.max_heading_spread_deg:
                return self._drop("heading_changing",
                                  f"heading varied {hw.spread_deg:.1f} deg during capture window")

        det, extras = self._build(frame, est, sel, hw)
        if det is None:
            return self._drop(*extras["_drop"])

        # 5. Fast path, or hand to the slow observer (T5).
        if self.observer is None:
            return self._emit(det, extras, est)
        return self._offer_observer(frame, det, extras, est)

    def _build(self, frame: Frame, est: TimeEstimate, sel: SelectedTarget,
               hw: Optional[HeadingWindow]):
        # GS_MONO -> GS_WALL at this moment; refuse if the wall clock stepped (T3).
        if self.clock.stepped_since(est.lo):
            return None, {"_drop": ("gs_clock_stepped", "GS wall clock stepped mid-frame")}
        off = self.clock.wall_minus_mono()
        b = sel.box
        det = Detection(t=est.lo + off, target_id=sel.target_id, confidence=b.confidence,
                        cx=b.cx, cy=b.cy, image_w=frame.width, image_h=frame.height,
                        heading_deg=None if hw is None else round(hw.heading_deg, 3),
                        frame_id=frame.frame_id)
        extras = {
            "t_best": est.best + off, "t_latest": est.hi + off,
            "t_method": est.method, "t_assumed": est.assumed,
            "t_uncertainty_s": round(est.width, 4),
            "heading_spread_deg": None if hw is None else round(hw.spread_deg, 2),
            "class_name": b.class_name, "track_frames": sel.track_frames,
            "detector": getattr(self.detector, "name", type(self.detector).__name__),
            "producer": BRIDGE_VERSION,
        }
        return det, extras

    def _emit(self, det: Detection, extras: Dict[str, Any], est: TimeEstimate) -> Outcome:
        # Called from the frame loop and from the observer thread.
        with self._emit_lock:
            return self._emit_locked(det, extras, est)

    def _emit_locked(self, det: Detection, extras: Dict[str, Any], est: TimeEstimate) -> Outcome:
        age = self.clock.mono() - est.best
        if age > self.cfg.pipeline.max_publish_age_s:
            return self._drop("too_old_to_publish", f"{age:.3f} s after best capture estimate")
        self.stats_.age_at_publish_s.append(age)
        self.stats_.published += 1
        for s in self.sinks:
            s.publish(det, extras)
        return Outcome(True, "", det, extras)

    # ---------------------------------------------------------- observer
    def _offer_observer(self, frame, det, extras, est) -> Outcome:
        now = self.clock.mono()
        if now - self._obs_last_start < 1.0 / max(1e-6, self.cfg.observer.max_rate_hz):
            return self._drop("observer_rate_limit")
        if self.observer_inline:
            self._obs_last_start = now
            return self._run_observer(frame, det, extras, est)
        with self._obs_cv:
            if self._obs_busy:
                return self._drop("observer_busy")
            self._obs_last_start = now
            self._obs_slot = [(frame, det, extras, est, now)]   # newest only
            self._obs_cv.notify_all()
        return Outcome(False, "handed_to_observer", det, extras)

    def _run_observer(self, frame, det, extras, est) -> Outcome:
        t0 = time.perf_counter()
        try:
            obs = self.observer.observe(frame.image, det)
        except Exception as e:
            return self._drop("observer_error", f"{type(e).__name__}: {e}")
        self.stats_.observer_s.append(time.perf_counter() - t0)
        if obs is None or not obs.agrees_with_target:
            return self._drop("observer_rejected", obs.note if obs else "no answer")
        det.nudge_forward_m, det.nudge_right_m = obs.nudge_forward_m, obs.nudge_right_m
        extras = dict(extras, observer_note=obs.note)
        # The observer took time: the wall clock may have stepped meanwhile (T3).
        if self.clock.stepped_since(est.lo):
            return self._drop("gs_clock_stepped", "during observer")
        return self._emit(det, extras, est)

    def _observer_loop(self):
        while not self._stop.is_set():
            with self._obs_cv:
                if not self._obs_slot:
                    self._obs_cv.wait(0.1)
                if not self._obs_slot:
                    continue
                job = self._obs_slot.pop()
                self._obs_busy = True
            try:
                frame, det, extras, est, _ = job
                self._run_observer(frame, det, extras, est)
            finally:
                with self._obs_cv:
                    self._obs_busy = False

    # ---------------------------------------------------------- loop
    def run(self, slot: LatestFrameSlot, on_outcome: Optional[Callable[[Outcome], None]] = None):
        if self.observer is not None and not self.observer_inline:
            threading.Thread(target=self._observer_loop, name="vision-observer",
                             daemon=True).start()
        while not self._stop.is_set():
            f = slot.get(timeout=0.1)
            if f is None:
                if self.telemetry_feed is not None:
                    self.telemetry_feed.poll()
                continue
            out = self.process_frame(f)
            if on_outcome:
                on_outcome(out)

    def stop(self):
        self._stop.set()


# =============================================================== building
def make_detector(cfg: DetectorConfig) -> Optional[Detector]:
    if cfg.kind == "yolo":
        return UltralyticsYoloDetector(cfg.model_path, imgsz=cfg.imgsz, conf=cfg.conf,
                                       iou=cfg.iou, device=cfg.device or None,
                                       classes=cfg.classes or None, half=cfg.half,
                                       tracker=cfg.tracker or None,
                                       allow_download=cfg.allow_download)
    if cfg.kind == "colour":
        return ColorBlobDetector(tuple(cfg.colour_bgr), cfg.colour_tol)
    if cfg.kind == "none":
        return None
    raise ValueError(f"unknown detector kind {cfg.kind!r}")


def make_video_source(cfg: VideoConfig, slot: LatestFrameSlot) -> OpenCVStreamSource:
    url, backend = cfg.url, cfg.backend
    if url == "rtp":
        url, backend = gst_rtp_pipeline(cfg.rtp_port, cfg.rtp_codec), "gstreamer"
    return OpenCVStreamSource(url, slot, backend=backend, source_id=cfg.source_id,
                              rtsp_transport=cfg.rtsp_transport,
                              open_timeout_s=cfg.open_timeout_s,
                              read_timeout_s=cfg.read_timeout_s,
                              reconnect_backoff_s=cfg.reconnect_backoff_s,
                              stall_s=cfg.stall_s)


# =============================================================== benchmark
def synthetic_frames(n: int, w: int = 640, h: int = 480, fps: float = 30.0):
    """Frames with a red square drifting across a grey field (numpy only).

    The square bounces back and forth (triangle wave) rather than wrapping.
    An earlier version wrapped from the right edge back to the left every 90
    frames; the tracker then saw a new object, and TargetSelector correctly
    held its lock on the vanished one for lost_after_s, so about half the
    benchmark frames were dropped as no_target. That measured the sticky-target
    rule, not throughput.
    """
    import numpy as np
    base = np.full((h, w, 3), 90, dtype=np.uint8)
    for i in range(n):
        img = base.copy()
        phase = (i % 180) / 90.0                      # 0..2
        tri = phase if phase <= 1.0 else 2.0 - phase  # 0..1..0
        x = int(w * 0.2 + (w * 0.6) * tri)
        y = int(h * 0.5 + 40 * math.sin(i / 15.0))
        img[y - 12:y + 12, x - 12:x + 12] = (0, 0, 255)
        yield i, img


def run_benchmark(cfg: BridgeConfig, detector: Detector, n: int = 300, w: int = 640,
                  h: int = 480) -> Dict[str, Any]:
    """Measure the bridge's own per-frame cost on THIS machine with synthetic
    frames: no camera, no network, no decode. The figures say nothing about
    camera latency or ground-station GPU speed."""
    box = LatestDetectionBox()
    bridge = VisionBridge(cfg, detector, [box])
    t_start = time.perf_counter()
    per_frame = []
    for i, img in synthetic_frames(n, w, h):
        f = Frame(i + 1, img, w, h, bridge.clock.mono(), None, None, 0, "bench")
        t0 = time.perf_counter()
        bridge.process_frame(f)
        per_frame.append(time.perf_counter() - t0)
    total = time.perf_counter() - t_start
    s = sorted(per_frame)
    return {"machine_note": "measured on the machine running this command, synthetic frames, "
                            "no camera / network / video decode",
            "detector": getattr(detector, "name", "?"), "frames": n, "size": f"{w}x{h}",
            "per_frame_mean_ms": round(1000 * sum(s) / n, 3),
            "per_frame_p95_ms": round(1000 * s[int(0.95 * (n - 1))], 3),
            "throughput_fps": round(n / total, 1), "published": bridge.stats_.published,
            "stats": bridge.stats()}


# =============================================================== CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="OpenIPC -> YOLO -> visual centering observations "
                                 "(experimental, by Maya, AI agent, Sesame)")
    ap.add_argument("--config", help="TOML file (see config/vision_bridge.example.toml)")
    ap.add_argument("--source", help="override video url (rtsp://..., file path, or 'rtp')")
    ap.add_argument("--replay-jsonl", help="replay a recorded timing script instead of a camera")
    ap.add_argument("--detector", choices=["yolo", "colour", "none"])
    ap.add_argument("--no-udp", action="store_true", help="do not send to the controller port")
    ap.add_argument("--print", action="store_true", help="print published detections as JSON")
    ap.add_argument("--benchmark", type=int, metavar="N",
                    help="time N synthetic frames through the bridge and exit")
    ap.add_argument("--budget", action="store_true", help="print the timing budget and exit")
    ap.add_argument("--status-every", type=float, default=5.0)
    a = ap.parse_args(argv)

    cfg = load_config(a.config) if a.config else BridgeConfig()
    if a.source:
        cfg.video.url = a.source
    if a.detector:
        cfg.detector.kind = a.detector
    if a.no_udp:
        cfg.output.udp = ""
    if a.print:
        cfg.output.print_json = True

    for line in timing_budget_report(cfg):
        print(line, file=sys.stderr)
    if a.budget:
        return 0
    if cfg.telemetry.enabled:
        print("telemetry alignment needs an in-process MavNode; wire MavNodeTelemetryFeed "
              "in your launcher (see docs/OPENIPC-YOLO-BRIDGE.md). Disabled for this CLI run.",
              file=sys.stderr)
        cfg.telemetry.enabled = False
    if cfg.observer.enabled:
        print("no observer model is wired into the CLI; observer disabled for this run",
              file=sys.stderr)
        cfg.observer.enabled = False

    det = make_detector(cfg.detector)
    if a.benchmark:
        if det is None:
            det = ColorBlobDetector()
        print(json.dumps(run_benchmark(cfg, det, a.benchmark), indent=2))
        return 0
    if det is None:
        print("detector kind 'none': nothing to publish", file=sys.stderr)
        return 2

    sinks: List[DetectionSink] = []
    if cfg.output.udp:
        sinks.append(UdpJsonSink(cfg.output.udp))
    if cfg.output.print_json:
        sinks.append(PrintSink())
    slot = LatestFrameSlot(1)
    if a.replay_jsonl:
        src = ReplayFrameSource.load_jsonl(a.replay_jsonl, slot)
    else:
        src = make_video_source(cfg.video, slot)
    bridge = VisionBridge(cfg, det, sinks)
    src.start()
    th = threading.Thread(target=bridge.run, args=(slot,), daemon=True)
    th.start()
    try:
        while True:
            time.sleep(a.status_every)
            print(json.dumps({"source": src.status(), "bridge": bridge.stats()}), file=sys.stderr)
            if a.replay_jsonl and not src.status()["remaining"]:
                time.sleep(0.5)
                break
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
        src.stop()
    print(json.dumps({"final": bridge.stats()}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
