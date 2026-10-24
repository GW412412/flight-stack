# Written by Maya, an AI agent from Sesame, at the direction of G Wichita (GW412412),
# the project owner. Experimental and not flight-validated. See draft PR #1.
"""Visual centering: nudge the copter until a detected target sits under the crosshair.

This is the flight-and-vision proof of concept that comes BEFORE any spraying:

    camera frame -> detector (YOLO, ...) -> Detection(pixel centre, confidence,
    target id, capture time, optional nudge chosen by the vision model)
        -> CenteringController (this file)
            -> DroneTools.goto_relative(north_m, east_m)   [existing tool]
            -> wait until the copter has settled at the new spot
            -> DroneTools.hold()                          [existing tool]
            -> wait for a FRESH frame taken after the move, repeat

Nothing here talks MAVLink directly. Every move goes through the existing
DroneTools -> FlightGate -> MavlinkHub path, so the gate still decides
(actuation switch, stale link, pilot override, RC inhibit switch, rate limit,
geofence, altitude window, GUIDED-only). The controller never arms, never takes
off, never calls set_mode. If the flight mode is not GUIDED (the pilot flicked
the switch, a failsafe fired, ...) it stops and does NOT put GUIDED back.

Nudge size
----------
The vision model may choose the nudge itself, every frame: Detection.nudge_forward_m
and Detection.nudge_right_m. The controller treats that as a request, not an
order: it is clamped to CenteringLimits.max_step_m, dropped below min_step_m,
and (when the camera geometry is known) refused if it points away from where
the target actually is in the picture. With no nudge from the model, the
controller steps a fixed CenteringLimits.default_step_m (0.5 m, "forward point
five") toward the target, or less when the target is closer than that.

Frames: camera, body, earth
---------------------------
* Camera frame: what the picture shows. "forward" = towards the TOP of the image,
  "right" = towards the right edge. Detector pixels and the model's nudge are
  both in this frame.
* Body frame: forward = the copter's nose, right = its right side.
  CameraMapping says how the camera is mounted (the yaw between image-top and
  the nose, and whether the image is mirrored). This cannot be guessed: it must
  be measured on the real airframe.
* Earth frame: DroneTools.goto_relative takes NORTH/EAST metres, not
  forward/right. Body -> north/east uses the copter heading at the moment the
  frame was captured (Detection.heading_deg, or the heading telemetry sample
  nearest in time). If the heading has changed too much since the frame, or no
  heading sample is close enough in time, no move is sent.

This module is plain Python with no dependency beyond the middleware itself.
Run `python visual_centering.py --demo` for a self-contained run against the
in-process fake vehicle (no real aircraft, no network beyond 127.0.0.1).
"""
from __future__ import annotations

import argparse
import json
import math
import socket
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Callable, Deque, List, Optional, Protocol, Tuple

from mav_node import haversine_m


# =============================================================== inputs
@dataclass
class Detection:
    """One detector result for one frame.

    t            capture time of the frame, epoch seconds (time.time() clock of
                 the ground station; keep the camera and this box NTP-synced).
    target_id    identity of the tracked target (tracker id or class+id).
    confidence   0..1 from the detector.
    cx, cy       target centre in pixels, origin top-left (as YOLO gives it).
    image_w/h    frame size in pixels.
    nudge_forward_m / nudge_right_m
                 OPTIONAL move chosen by the vision model, in the CAMERA frame
                 (forward = image top). None = let the controller choose.
    heading_deg  OPTIONAL vehicle heading stamped on the frame (0 = north,
                 clockwise). None = look it up from telemetry history.
    frame_id     OPTIONAL, for logs.
    """
    t: float
    target_id: str
    confidence: float
    cx: float
    cy: float
    image_w: int
    image_h: int
    nudge_forward_m: Optional[float] = None
    nudge_right_m: Optional[float] = None
    heading_deg: Optional[float] = None
    frame_id: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Detection":
        return cls(t=float(d["t"]), target_id=str(d["target_id"]),
                   confidence=float(d["confidence"]), cx=float(d["cx"]), cy=float(d["cy"]),
                   image_w=int(d["image_w"]), image_h=int(d["image_h"]),
                   nudge_forward_m=_opt_float(d.get("nudge_forward_m")),
                   nudge_right_m=_opt_float(d.get("nudge_right_m")),
                   heading_deg=_opt_float(d.get("heading_deg")),
                   frame_id=None if d.get("frame_id") is None else str(d["frame_id"]))


def _opt_float(v) -> Optional[float]:
    return None if v is None else float(v)


def detection_from_xyxy(box: Tuple[float, float, float, float], confidence: float,
                        target_id: str, image_w: int, image_h: int, t: float,
                        **extra) -> Detection:
    """Adapter for a YOLO-style box (x1, y1, x2, y2 in pixels). No YOLO import:
    pass e.g. `result.boxes.xyxy[i].tolist()` and `float(result.boxes.conf[i])`."""
    x1, y1, x2, y2 = box
    return Detection(t=t, target_id=target_id, confidence=confidence,
                     cx=(x1 + x2) / 2.0, cy=(y1 + y2) / 2.0,
                     image_w=image_w, image_h=image_h, **extra)


class DetectionSource(Protocol):
    def latest(self) -> Optional[Detection]:
        """Most recent detection, or None. Must not block."""


class UdpJsonDetectionSource:
    """Detector process -> this controller, one JSON object per UDP datagram.

    The detector (on the ground station, reading the OpenIPC video) sends
    Detection fields as JSON to e.g. 127.0.0.1:5602. Only the newest packet is
    kept: perception may run at 30 fps, the controller still moves once per
    settled frame. Malformed packets are counted and ignored.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 5602):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.sock.settimeout(0.2)
        self._latest: Optional[Detection] = None
        self.bad_packets = 0
        self._stop = threading.Event()
        self._lock = threading.Lock()
        threading.Thread(target=self._rx, name="detections-udp", daemon=True).start()

    def _rx(self):
        while not self._stop.is_set():
            try:
                data, _ = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                det = Detection.from_dict(json.loads(data.decode()))
            except Exception:
                self.bad_packets += 1
                continue
            with self._lock:
                if self._latest is None or det.t >= self._latest.t:
                    self._latest = det

    def latest(self) -> Optional[Detection]:
        with self._lock:
            return self._latest

    def close(self):
        self._stop.set()
        self.sock.close()


# =============================================================== geometry
@dataclass
class CameraMapping:
    """How the camera is mounted. MEASURE THIS on the airframe; do not guess.

    yaw_offset_deg  angle from the copter's nose to the image-top direction,
                    clockwise seen from above. 0 = image top points at the nose;
                    90 = image top points out the right side.
    mirrored        True if the image is flipped left-right (some sensors).
    hfov_deg/vfov_deg  lens field of view. With altitude this turns pixels into
                    metres on the ground (pinhole model, camera pointing straight
                    down, flat ground). None = unknown: then the controller
                    needs the vision model to supply the nudge, and cannot
                    sanity-check it.
    calibrated      must be set True by whoever measured the values. The live
                    runner refuses an uncalibrated mapping.
    """
    yaw_offset_deg: float
    mirrored: bool = False
    hfov_deg: Optional[float] = None
    vfov_deg: Optional[float] = None
    calibrated: bool = False

    def camera_to_body(self, fwd: float, right: float) -> Tuple[float, float]:
        if self.mirrored:
            right = -right
        a = math.radians(self.yaw_offset_deg)
        return (fwd * math.cos(a) - right * math.sin(a),
                fwd * math.sin(a) + right * math.cos(a))

    def pixel_to_camera_m(self, det: Detection, height_m: float) -> Optional[Tuple[float, float]]:
        """Ground offset of the target from the crosshair, camera frame (fwd, right)."""
        if self.hfov_deg is None or self.vfov_deg is None or height_m <= 0:
            return None
        u = (det.cx - det.image_w / 2.0) / (det.image_w / 2.0)     # +1 = right edge
        v = (det.image_h / 2.0 - det.cy) / (det.image_h / 2.0)     # +1 = top edge
        return (v * height_m * math.tan(math.radians(self.vfov_deg) / 2.0),
                u * height_m * math.tan(math.radians(self.hfov_deg) / 2.0))


def body_to_ned(fwd: float, right: float, heading_deg: float) -> Tuple[float, float]:
    h = math.radians(heading_deg)
    return (fwd * math.cos(h) - right * math.sin(h),
            fwd * math.sin(h) + right * math.cos(h))


def angle_diff_deg(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


class HeadingHistory:
    """Recent (time, heading) samples so a frame can be matched to the heading
    the copter had when the frame was captured, not when it was processed."""

    def __init__(self, maxlen: int = 200):
        self._s: Deque[Tuple[float, float]] = deque(maxlen=maxlen)

    def add(self, t: float, heading_deg: Optional[float]):
        if heading_deg is not None:
            self._s.append((t, float(heading_deg)))

    def at(self, t: float, max_skew_s: float) -> Optional[float]:
        best = min(self._s, key=lambda s: abs(s[0] - t), default=None)
        if best is None or abs(best[0] - t) > max_skew_s:
            return None
        return best[1]


# =============================================================== controller
@dataclass
class CenteringLimits:
    default_step_m: float = 0.5        # fixed nudge when the model gives none
    max_step_m: float = 1.0            # clamp on ANY nudge, model-chosen or not
    min_step_m: float = 0.05           # smaller than this = not worth a move
    deadband_frac: float = 0.06        # target within 6 % of half-frame = centred
    min_confidence: float = 0.5
    max_detection_age_s: float = 0.5   # frame older than this at decision time = stale
    settle_frame_delay_s: float = 0.2  # frame must be captured this long after settling
    max_iterations: int = 12           # moves per run
    max_total_travel_m: float = 5.0    # never end up further than this from the start
    arrival_tol_m: float = 0.25        # "arrived" for the settle check
    settle_speed_ms: float = 0.15      # ...and slower than this (GLOBAL_POSITION_INT vx/vy)
    settle_timeout_s: float = 8.0
    frame_timeout_s: float = 3.0       # no usable frame / no plannable move this long -> stop
    max_heading_skew_s: float = 0.3    # heading sample must be this close to frame time
    max_heading_change_deg: float = 5.0  # yawed more than this since the frame -> skip
    min_height_m: float = 2.0
    crosscheck_model_nudge: bool = True  # refuse model nudges pointing away from target
    max_run_s: float = 120.0
    poll_s: float = 0.05


@dataclass
class StepLog:
    t: float
    event: str
    detail: dict = field(default_factory=dict)


class CenteringController:
    """One move at a time; a fresh post-move frame before every decision.

    `tools` is a DroneTools (or anything with get_status / goto_relative / hold
    and a `gate` with pilot_override() and rate.check(), and `hub.dry_run`).
    """

    def __init__(self, tools, source: DetectionSource, camera: CameraMapping,
                 limits: Optional[CenteringLimits] = None, target_id: Optional[str] = None,
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep,
                 on_event: Optional[Callable[[StepLog], None]] = None):
        self.tools, self.source, self.camera = tools, source, camera
        self.lim = limits or CenteringLimits()
        self.target_id = target_id
        self.clock, self.sleep = clock, sleep
        self.headings = HeadingHistory()
        self.log: List[StepLog] = []
        self._on_event = on_event

    # ------------------------------------------------------------ helpers
    def _event(self, event: str, **detail):
        e = StepLog(round(self.clock(), 3), event, detail)
        self.log.append(e)
        if self._on_event:
            self._on_event(e)

    def _status(self) -> dict:
        s = self.tools.get_status()
        self.headings.add(self.clock(), s.get("heading"))
        return s

    def _flight_ok(self, s: dict) -> Optional[str]:
        """Reasons to STOP the run (not just skip a frame)."""
        if not s.get("link_ok"):
            return "telemetry link stale"
        o = self.tools.gate.pilot_override()
        if o:
            return f"pilot override: {o.get('reason')}"
        if (s.get("mode") or "").upper() != "GUIDED":
            return (f"mode is {s.get('mode')}, not GUIDED (this controller never changes "
                    "modes: start it hovering in GUIDED; after a pilot switch it stops)")
        if not s.get("armed"):
            return "not armed"
        if (s.get("rel_alt") or 0.0) < self.lim.min_height_m:
            return f"height {s.get('rel_alt')} m below {self.lim.min_height_m} m"
        if s.get("lat") is None:
            return "no position"
        return None

    def _usable(self, det: Optional[Detection], not_before: float) -> Tuple[bool, str]:
        if det is None:
            return False, "no detection"
        age = self.clock() - det.t
        if age > self.lim.max_detection_age_s:
            return False, f"stale frame ({age:.2f} s old)"
        if det.t < not_before:
            return False, "frame older than the last move"
        if age < -0.5:
            return False, "frame time is in the future (clock sync?)"
        if det.confidence < self.lim.min_confidence:
            return False, f"low confidence {det.confidence:.2f}"
        if self.target_id is not None and det.target_id != self.target_id:
            return False, f"different target ({det.target_id} != {self.target_id})"
        if not (det.image_w > 0 and det.image_h > 0 and 0 <= det.cx <= det.image_w
                and 0 <= det.cy <= det.image_h):
            return False, "target outside the frame"
        return True, ""

    def _centred(self, det: Detection) -> bool:
        u = (det.cx - det.image_w / 2.0) / (det.image_w / 2.0)
        v = (det.image_h / 2.0 - det.cy) / (det.image_h / 2.0)
        return math.hypot(u, v) <= self.lim.deadband_frac

    def plan_nudge(self, det: Detection, height_m: float) -> Tuple[Optional[Tuple[float, float]], str]:
        """Camera-frame (fwd, right) nudge in metres, or (None, reason)."""
        geo = self.camera.pixel_to_camera_m(det, height_m)
        if det.nudge_forward_m is not None or det.nudge_right_m is not None:
            f, r = det.nudge_forward_m or 0.0, det.nudge_right_m or 0.0
            src = "model"
            if (self.lim.crosscheck_model_nudge and geo is not None
                    and math.hypot(*geo) > 1e-6 and (f * geo[0] + r * geo[1]) <= 0):
                return None, (f"model nudge ({f:+.2f} fwd, {r:+.2f} right) points away from the "
                              f"target ({geo[0]:+.2f}, {geo[1]:+.2f})")
        elif geo is not None:
            d = math.hypot(*geo)
            if d < 1e-6:
                return None, "target on crosshair"
            step = min(self.lim.default_step_m, d)
            f, r, src = geo[0] / d * step, geo[1] / d * step, "fixed"
        else:
            return None, "no model nudge and no lens FOV to compute one"
        mag = math.hypot(f, r)
        if mag < self.lim.min_step_m:
            return None, f"nudge {mag:.3f} m below min_step_m"
        if mag > self.lim.max_step_m:
            f, r = f / mag * self.lim.max_step_m, r / mag * self.lim.max_step_m
            src += "+clamped"
        return (f, r), src

    def plan_only(self, det: Detection, status: dict) -> dict:
        """What the controller WOULD command for this frame. Sends nothing and
        skips the flight checks: for checking the camera mapping on the bench
        (hold the copter, put a target in view, see which way it would go)."""
        height = float(status.get("rel_alt") or 0.0) or 3.0
        nudge, src = self.plan_nudge(det, height)
        out = {"frame_id": det.frame_id, "centred": self._centred(det), "source": src,
               "assumed_height_m": height}
        hdg = det.heading_deg if det.heading_deg is not None else status.get("heading")
        if nudge and hdg is not None:
            bf, br = self.camera.camera_to_body(*nudge)
            n, e = body_to_ned(bf, br, float(hdg))
            out.update(camera={"fwd": round(nudge[0], 3), "right": round(nudge[1], 3)},
                       body={"fwd": round(bf, 3), "right": round(br, 3)},
                       ned={"north": round(n, 3), "east": round(e, 3)}, heading_deg=hdg)
        return out

    def _wait_settled(self, lat: float, lon: float) -> Tuple[bool, str]:
        t0 = self.clock()
        while self.clock() - t0 < self.lim.settle_timeout_s:
            s = self._status()
            why = self._flight_ok(s)
            if why:
                return False, why
            if haversine_m(s["lat"], s["lon"], lat, lon) <= self.lim.arrival_tol_m \
                    and self._ground_speed() <= self.lim.settle_speed_ms:
                return True, ""
            self.sleep(self.lim.poll_s)
        return False, "did not settle in time"

    def _ground_speed(self) -> float:
        get_t = getattr(self.tools, "get_telemetry", None)
        if get_t is None:
            return 0.0
        p = (get_t() or {}).get("position") or {}
        return math.hypot(p.get("vx_ms") or 0.0, p.get("vy_ms") or 0.0)

    def _safe_hold(self, s: Optional[dict] = None):
        """hold() only while we still own the copter in GUIDED. DroneTools.hold
        falls back to set_mode(LOITER) outside GUIDED; after a pilot mode change
        we must not send ANY mode, so we skip it then."""
        s = s or self._status()
        if (s.get("mode") or "").upper() == "GUIDED" and not self.tools.gate.pilot_override():
            r = self.tools.hold()
            self._event("hold", ok=bool(r.get("ok")), held_at=r.get("held_at"),
                        dry_run=bool(r.get("dry_run")))

    # ------------------------------------------------------------ main loop
    def run(self) -> dict:
        lim = self.lim
        dry = bool(getattr(self.tools.hub, "dry_run", False))
        s = self._status()
        why = self._flight_ok(s)
        if why:
            self._event("refused_start", reason=why)
            return self._result(False, why)
        start = (s["lat"], s["lon"])
        t_start = self.clock()
        not_before = t_start - lim.max_detection_age_s   # first frame may predate the call
        moves = 0
        last_reason, progress_t, last_block = "", self.clock(), ""
        self._event("start", dry_run=dry, target_id=self.target_id, start=start)

        while True:
            if self.clock() - t_start > lim.max_run_s:
                return self._stop("max_run_s reached")
            s = self._status()
            why = self._flight_ok(s)
            if why:
                return self._stop(why, hold=False)

            if self.clock() - progress_t > lim.frame_timeout_s:
                return self._stop(f"no usable frame for {lim.frame_timeout_s} s "
                                  f"({last_block or last_reason})", moves=moves)
            det = self.source.latest()
            ok, reason = self._usable(det, not_before)
            if not ok:
                if reason != last_reason:
                    self._event("skip_frame", reason=reason)
                    last_reason = reason
                self.sleep(lim.poll_s)
                continue
            last_reason = ""
            if self.target_id is None:
                self.target_id = det.target_id
                self._event("locked_target", target_id=det.target_id)

            if self._centred(det):
                self._event("centred", frame_id=det.frame_id, cx=det.cx, cy=det.cy, moves=moves)
                return self._result(True, "target centred", moves=moves)
            if moves >= lim.max_iterations:
                return self._stop(f"max_iterations ({lim.max_iterations}) reached", moves=moves)

            height = float(s.get("rel_alt") or 0.0)
            nudge, src = self.plan_nudge(det, height)
            if nudge is None:
                # A bad model suggestion or an unplannable frame is not a reason
                # to move; wait for the next fresh frame instead.
                if src != last_block:
                    self._event("no_move", reason=src, frame_id=det.frame_id)
                last_block = src
                not_before = det.t + 1e-6
                continue

            hdg_frame = det.heading_deg if det.heading_deg is not None else \
                self.headings.at(det.t, lim.max_heading_skew_s)
            if hdg_frame is None:
                if last_block != "no heading sample near the frame time":
                    self._event("no_move", reason="no heading sample near the frame time")
                last_block = "no heading sample near the frame time"
                not_before = det.t + 1e-6
                continue
            if s.get("heading") is not None and \
                    angle_diff_deg(float(s["heading"]), hdg_frame) > lim.max_heading_change_deg:
                self._event("no_move", reason="copter yawed since the frame was taken",
                            heading_frame=hdg_frame, heading_now=s["heading"])
                last_block = "copter yawed since the frame was taken"
                not_before = det.t + 1e-6
                continue

            bf, br = self.camera.camera_to_body(*nudge)
            north, east = body_to_ned(bf, br, hdg_frame)
            # Travel bound measured from where the run started.
            dlat = north / 111_320.0
            dlon = east / (111_320.0 * max(math.cos(math.radians(s["lat"])), 1e-6))
            if haversine_m(start[0], start[1], s["lat"] + dlat, s["lon"] + dlon) > lim.max_total_travel_m:
                return self._stop(f"move would exceed max_total_travel_m ({lim.max_total_travel_m} m)",
                                  moves=moves)

            rate = self.tools.gate.rate.check()
            if rate:
                self._event("wait_rate_limit", reason=rate)
                self.sleep(max(lim.poll_s, getattr(self.tools.gate.rate, "min_interval_s", 1.0)))
                progress_t = self.clock()     # waiting on the gate is not a lost target
                not_before = det.t + 1e-6     # re-decide on a newer frame
                continue

            r = self.tools.goto_relative(north_m=round(north, 3), east_m=round(east, 3))
            moves += 1
            self._event("move", n=moves, source=src,
                        camera={"fwd": round(nudge[0], 3), "right": round(nudge[1], 3)},
                        body={"fwd": round(bf, 3), "right": round(br, 3)},
                        ned={"north": round(north, 3), "east": round(east, 3)},
                        heading_deg=hdg_frame, frame_id=det.frame_id,
                        ok=bool(r.get("ok")), reasons=r.get("reasons"), error=r.get("error"),
                        dry_run=bool(r.get("dry_run")))
            if not r.get("ok"):
                return self._stop("move refused: " + "; ".join(r.get("reasons") or
                                                               [str(r.get("error"))]), moves=moves)
            if dry:
                # Nothing moved, so the next frame would show the same offset.
                return self._result(False, "dry run: first move planned and gated, "
                                    "nothing transmitted", moves=moves)
            tgt = r.get("target") or {}
            settled, why = self._wait_settled(tgt["lat"], tgt["lon"])
            if not settled:
                return self._stop(why, moves=moves)
            self._safe_hold()
            # Next decision only from a frame captured after we stopped moving.
            not_before = self.clock() + lim.settle_frame_delay_s
            progress_t, last_block = not_before, ""

    def _stop(self, reason: str, hold: bool = True, moves: Optional[int] = None) -> dict:
        if hold:
            self._safe_hold()
        self._event("stop", reason=reason)
        return self._result(False, reason, moves=moves)

    def _result(self, centred: bool, reason: str, moves: Optional[int] = None) -> dict:
        if moves is None:
            moves = sum(1 for e in self.log if e.event == "move")
        return {"centred": centred, "reason": reason, "moves": moves,
                "log": [asdict(e) for e in self.log]}


# =============================================================== simulation
class SimulatedDownCamera:
    """Pretend downward camera for tests and the demo.

    Reads the copter position/heading from `status_fn` and draws a target that
    sits at a fixed lat/lon on the ground. Optional `vision_nudge(fwd_m, right_m)`
    stands in for a vision model choosing the nudge (camera frame).
    """

    def __init__(self, status_fn: Callable[[], dict], target_latlon: Tuple[float, float],
                 camera: CameraMapping, image_wh: Tuple[int, int] = (640, 480),
                 target_id: str = "weed-1", confidence: float = 0.9,
                 vision_nudge: Optional[Callable[[float, float], Tuple[float, float]]] = None,
                 clock: Callable[[], float] = time.time):
        self.status_fn, self.target = status_fn, target_latlon
        self.camera, (self.w, self.h) = camera, image_wh
        self.target_id, self.confidence = target_id, confidence
        self.vision_nudge, self.clock = vision_nudge, clock
        self.frames = 0

    def latest(self) -> Optional[Detection]:
        s = self.status_fn()
        if s.get("lat") is None:
            return None
        lat, lon, hdg = s["lat"], s["lon"], float(s.get("heading") or 0.0)
        height = float(s.get("rel_alt") or 0.0)
        north = (self.target[0] - lat) * 111_320.0
        east = (self.target[1] - lon) * 111_320.0 * math.cos(math.radians(lat))
        h = math.radians(hdg)                     # earth -> body
        bf = north * math.cos(h) + east * math.sin(h)
        br = -north * math.sin(h) + east * math.cos(h)
        a = math.radians(self.camera.yaw_offset_deg)   # body -> camera
        cf = bf * math.cos(a) + br * math.sin(a)
        cr = -bf * math.sin(a) + br * math.cos(a)
        if self.camera.mirrored:
            cr = -cr
        half_w = height * math.tan(math.radians(self.camera.hfov_deg) / 2.0)
        half_h = height * math.tan(math.radians(self.camera.vfov_deg) / 2.0)
        if half_w <= 0 or abs(cr) > half_w or abs(cf) > half_h:
            return None                           # out of view
        self.frames += 1
        det = Detection(t=self.clock(), target_id=self.target_id, confidence=self.confidence,
                        cx=self.w / 2.0 + cr / half_w * self.w / 2.0,
                        cy=self.h / 2.0 - cf / half_h * self.h / 2.0,
                        image_w=self.w, image_h=self.h, frame_id=f"sim-{self.frames}")
        if self.vision_nudge:
            det.nudge_forward_m, det.nudge_right_m = self.vision_nudge(cf, cr)
        return det


def proportional_model(gain: float = 0.8) -> Callable[[float, float], Tuple[float, float]]:
    """Stand-in 'vision model' that picks a different nudge each frame:
    gain x the offset it sees. Real models plug in through Detection fields."""
    return lambda f, r: (gain * f, gain * r)


# =============================================================== CLI
def _print_event(e: StepLog):
    print(json.dumps(asdict(e), default=str), flush=True)


def run_demo(offset_fwd_m: float = 1.4, offset_right_m: float = -0.9,
             heading_deg: float = 0.0, model: bool = True) -> dict:
    """Self-contained: in-process FakeVehicle on 127.0.0.1, never real hardware."""
    from drone_tools import DroneTools
    from fake_vehicle import FakeVehicle
    from mav_node import MavNode
    from mavlink_hub import MavlinkHub
    from safety_gate import FlightGate
    from stack_config import SafetyConfig

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    node = MavNode(f"udpin:127.0.0.1:{port}", record_dir=None, send_heartbeat=True)
    node.start(wait_heartbeat=False)
    fv = FakeVehicle(out=f"127.0.0.1:{port}").start()
    cfg = SafetyConfig(actuation_enabled=True, dry_run=False, require_rc_link=True,
                       geofence_radius_m=60, max_alt_m=20)
    gate = FlightGate(cfg)
    hub = MavlinkHub(node, flight_gate=gate, dry_run=False, log_dir=None)
    tools = DroneTools(node, hub, gate, log_dir=None, home_poll_s=0.5)
    try:
        t0 = time.time()
        while not (node.link_ok() and node.home()) and time.time() - t0 < 8:
            time.sleep(0.1)
        # Demo setup ONLY (fake vehicle): GUIDED, arm, take off to 6 m.
        tools.set_mode("GUIDED")
        time.sleep(1.2)
        r = tools.arm()
        tools.arm(confirm_token=r.get("confirm_token"))
        time.sleep(1.2)
        r = tools.takeoff(alt_m=6)
        tools.takeoff(alt_m=6, confirm_token=r.get("confirm_token"))
        while (node.get_status().get("rel_alt") or 0) < 5.8 and time.time() - t0 < 30:
            time.sleep(0.1)
        time.sleep(1.2)
        st = node.get_status()
        cam = CameraMapping(yaw_offset_deg=0.0, hfov_deg=80.0, vfov_deg=64.0, calibrated=True)
        bn, be = body_to_ned(offset_fwd_m, offset_right_m, heading_deg)
        tlat = st["lat"] + bn / 111_320.0
        tlon = st["lon"] + be / (111_320.0 * math.cos(math.radians(st["lat"])))
        src = SimulatedDownCamera(node.get_status, (tlat, tlon), cam,
                                  vision_nudge=proportional_model() if model else None)
        ctl = CenteringController(tools, src, cam, on_event=_print_event)
        return ctl.run()
    finally:
        tools.close()
        fv.stop()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Visual centering with measured GUIDED nudges.")
    ap.add_argument("--demo", action="store_true",
                    help="run against the in-process fake vehicle (no real aircraft)")
    ap.add_argument("--fixed-step", action="store_true",
                    help="demo: ignore the model's nudge, use the fixed 0.5 m step")
    ap.add_argument("--config", help="stack TOML (e.g. ../config/llm_stack.sitl.toml)")
    ap.add_argument("--mavlink-url", help="override mavlink.url (use its own MAVProxy --out)")
    ap.add_argument("--udp", default="127.0.0.1:5602", help="detector JSON input host:port")
    ap.add_argument("--camera-yaw-deg", type=float, help="image-top vs nose, clockwise")
    ap.add_argument("--mirrored", action="store_true")
    ap.add_argument("--hfov-deg", type=float)
    ap.add_argument("--vfov-deg", type=float)
    ap.add_argument("--camera-calibrated", action="store_true",
                    help="I measured the camera yaw/mirroring on this airframe")
    ap.add_argument("--target-id")
    ap.add_argument("--max-step-m", type=float, default=1.0)
    ap.add_argument("--max-iterations", type=int, default=12)
    ap.add_argument("--sim-camera", metavar="FWD,RIGHT",
                    help="SITL only: no detector; pretend camera sees a target FWD/RIGHT "
                         "metres (body frame) from where the run starts. Needs FOVs.")
    ap.add_argument("--model-gain", type=float,
                    help="with --sim-camera: stand-in model picks nudge = gain x offset")
    ap.add_argument("--plan-only", action="store_true",
                    help="print the planned nudge for each new detection; never moves")
    ap.add_argument("--live", action="store_true",
                    help="actually transmit (otherwise dry_run is forced on)")
    a = ap.parse_args(argv)

    if a.demo:
        res = run_demo(model=not a.fixed_step)
        print(json.dumps({k: v for k, v in res.items() if k != "log"}))
        return 0 if res["centred"] else 1

    if a.camera_yaw_deg is None or not a.camera_calibrated:
        ap.error("live/dry-run use needs --camera-yaw-deg and --camera-calibrated "
                 "(measure the mount first)")
    from drone_tools import DroneTools
    from stack_config import load_config
    cfg = load_config(a.config)
    if a.mavlink_url:
        cfg.mavlink.url = a.mavlink_url
    if not a.live:
        cfg.safety.dry_run = True
    cam = CameraMapping(a.camera_yaw_deg, a.mirrored, a.hfov_deg, a.vfov_deg, True)
    if a.sim_camera and cfg.profile != "sitl":
        ap.error("--sim-camera is for SITL only (config profile must be 'sitl')")
    if a.sim_camera and (a.hfov_deg is None or a.vfov_deg is None):
        ap.error("--sim-camera needs --hfov-deg and --vfov-deg")
    src = None
    if not a.sim_camera:
        host, port = a.udp.rsplit(":", 1)
        src = UdpJsonDetectionSource(host, int(port))
    tools = DroneTools.from_config(cfg)
    try:
        t0 = time.time()
        while not tools.node.link_ok() and time.time() - t0 < 10:
            time.sleep(0.2)
        if a.sim_camera:
            fwd, right = (float(x) for x in a.sim_camera.split(","))
            st = tools.node.get_status()
            if st.get("lat") is None:
                ap.error("no position from the vehicle yet")
            n, e = body_to_ned(fwd, right, float(st.get("heading") or 0.0))
            tgt = (st["lat"] + n / 111_320.0,
                   st["lon"] + e / (111_320.0 * math.cos(math.radians(st["lat"]))))
            src = SimulatedDownCamera(tools.node.get_status, tgt, cam,
                                      vision_nudge=proportional_model(a.model_gain)
                                      if a.model_gain else None)
        lim = CenteringLimits(max_step_m=a.max_step_m, max_iterations=a.max_iterations)
        if a.plan_only:
            ctl = CenteringController(tools, src, cam, lim, target_id=a.target_id)
            last = None
            print("plan-only: Ctrl-C to quit; nothing is sent", file=sys.stderr)
            while True:
                det = src.latest()
                if det is not None and (last is None or det.t > last):
                    last = det.t
                    print(json.dumps(ctl.plan_only(det, tools.get_status())), flush=True)
                time.sleep(0.1)
        res = CenteringController(tools, src, cam, lim, target_id=a.target_id,
                                  on_event=_print_event).run()
        print(json.dumps({k: v for k, v in res.items() if k != "log"}))
        return 0 if res["centred"] else 1
    finally:
        if hasattr(src, "close"):
            src.close()
        tools.close()


if __name__ == "__main__":
    sys.exit(main())
