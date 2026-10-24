"""Clock domains and capture-time estimation for the OpenIPC -> YOLO bridge.

Contributed by Maya (AI agent, Sesame), an AI assistant made by Sesame,
drafted at G's request in October 2026. EXPERIMENTAL: tested only with mock and replayed
data on a development machine, never with a real camera, radio link or
aircraft. Please critique it before relying on it.

Why this module exists
----------------------
The centering controller (visual_centering.py) decides whether a detection is
fresh enough to act on by comparing Detection.t with time.time(). That only
works if Detection.t really is the moment the picture was taken, expressed on
the ground station's time.time() clock. Nothing in the documented OpenIPC
RTSP / RTP video path hands the ground station that number directly (see
"What OpenIPC documents" below), so the bridge has to ESTIMATE it, and it must
say how good the estimate is. Everything here is about doing that honestly.

Clock domains (every timestamp in the bridge belongs to exactly one)
--------------------------------------------------------------------
  GS_MONO      time.monotonic() on the ground station. Never steps. All of the
               bridge's own bookkeeping (ages, timeouts, ordering) uses it.
  GS_WALL      time.time() on the ground station. This is the clock
               CenteringController uses for Detection.t and for "now". It CAN
               STEP (NTP / chrony correction, manual date change).
  STREAM       the decoder's presentation timestamp for a frame, in seconds
               (OpenCV CAP_PROP_POS_MSEC / FFmpeg pts). Arbitrary origin,
               restarts on reconnect; assumed to advance at 1 s per second of
               camera time.
  CAMERA_WALL  the camera's own epoch clock. Meaningful only if the camera is
               time-synced to something; OpenIPC cameras generally have no
               battery-backed clock.
  FC_BOOT      flight controller time_boot_ms / 1000 from MAVLink. Restarts
               when the flight controller reboots.

The bridge converts everything to GS_MONO internally and to GS_WALL only at
the moment it publishes, using the GS wall-minus-monotonic offset measured at
that moment (GsClock). If the GS wall clock stepped while a frame was in the
pipeline, that frame is dropped rather than published with a wrong time.

What OpenIPC documents (checked 25 Oct 2026, not tested on hardware)
--------------------------------------------------------------------
* RTSP (port 554, stream=0 / stream=1) and RTP-over-UDP push
  (outgoing.servers, e.g. udp://GS:5600) carry RTP media timestamps. Per RFC
  3550 section 5.1 the initial RTP timestamp SHOULD be random, so on its own
  it says nothing about wall-clock capture time. The Majestic configuration
  and streamer pages do not document RTCP sender reports or a per-frame
  wall-clock field on these outputs, so this code does not assume them.
* Majestic's analytics events (motion / face detectors, /api/v1/analytics and
  /ws/analytics) DO carry `t`, "when that frame was captured, microseconds since
  the epoch", graded by `q` (`k` = anchored to the kernel's frame timing,
  `e` = estimate), plus `pts`, "the same presentation timestamp the video
  carries". That is a documented capture-time anchor on the CAMERA_WALL
  clock, but it is only published for the camera's own detectors, its pts
  units and relation to the RTP timestamps a ground-station decoder sees are
  not documented, and whether the FPV build includes it is not stated. It is
  therefore listed as a future option, not used.
* An OpenIPC camera with no battery-backed clock has no trustworthy wall time
  until it hears a time server.
  Sources: https://github.com/OpenIPC/wiki/blob/master/en/analytics-metadata.md
           https://github.com/OpenIPC/wiki/blob/master/en/majestic-config.md
           https://github.com/OpenIPC/wiki/blob/master/en/majestic-streamer.md
           https://www.rfc-editor.org/rfc/rfc3550

Capture-time methods offered (CaptureTimeEstimator.method)
----------------------------------------------------------
  "arrival"      capture = arrival - latency, with latency in
                 [min_latency_s, max_latency_s]. Both numbers are ASSUMPTIONS
                 until someone measures glass-to-glass latency on the real
                 link. Works with any transport.
  "stream_pts"   uses the frame pts to remove arrival jitter: a windowed minimum
                 of (arrival - pts) tracks offset + fastest delivery, so
                 capture = pts + floor - min_latency_s. The fastest delivery
                 time itself cannot be observed from one-way data, so
                 min_latency_s is still an assumption; only the jitter part
                 is measured. Needs a decoder that reports real pts.
  "camera_wall"  the frame carries a CAMERA_WALL capture time from some source
                 (a replay file today; a future SEI / analytics reader).
                 capture = camera_wall + camera_to_gs_offset_s, +- the
                 configured camera clock uncertainty. Sanity-checked: a
                 capture time later than the frame's arrival means the clocks
                 are not in sync and the frame is rejected.

Every estimate is a TimeEstimate with lo <= best <= hi and a flag saying
whether it rests on an unmeasured assumption. The bridge publishes `lo`, the
EARLIEST plausible capture time, as Detection.t (see openipc_yolo_bridge.py,
timing issue T6): that makes the controller's "too old" and "taken before the
move settled" checks err on the safe side.

Numbered timing issues (T1..T14) are explained in docs/OPENIPC-YOLO-BRIDGE.md
and tagged in comments where they bite.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, List, Optional, Tuple

CONTRIBUTOR = "Maya (AI agent, Sesame)"

GS_MONO = "GS_MONO"
GS_WALL = "GS_WALL"
STREAM = "STREAM"
CAMERA_WALL = "CAMERA_WALL"
FC_BOOT = "FC_BOOT"


# =============================================================== value types
@dataclass(frozen=True)
class TimeEstimate:
    """An instant that is only known to lie within [lo, hi].

    domain   which clock lo/best/hi are on (GS_MONO inside the bridge).
    method   how it was obtained, for logs and for the published JSON.
    assumed  True if the interval rests on a number nobody has measured yet
             (e.g. the assumed video latency). Reviewers: treat any estimate
             with assumed=True as a placeholder until the latency is measured.
    """
    lo: float
    best: float
    hi: float
    domain: str
    method: str
    assumed: bool

    @property
    def width(self) -> float:
        return self.hi - self.lo

    def shifted(self, dt: float, domain: str) -> "TimeEstimate":
        return TimeEstimate(self.lo + dt, self.best + dt, self.hi + dt, domain,
                            self.method, self.assumed)


# =============================================================== GS clock
class GsClock:
    """Ground-station clock pair (GS_MONO, GS_WALL) with wall-clock step detection.

    Timing issue T3: time.time() can step. CenteringController measures
    detection age with time.time(), so a step of more than a fraction of a
    second makes every in-flight detection look stale (step forward) or from
    the future (step back). The bridge keeps its own times on the monotonic
    clock, converts to wall time only when publishing, and refuses to publish a
    frame that was in the pipeline while the offset jumped.

    `mono` and `wall` are injectable so tests can drive both clocks.
    """

    def __init__(self, mono: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time, step_threshold_s: float = 0.05):
        self._mono, self._wall = mono, wall
        self.step_threshold_s = step_threshold_s
        self._offset: Optional[float] = None
        self.steps = 0
        self.last_step_mono: Optional[float] = None

    def mono(self) -> float:
        return self._mono()

    def wall_minus_mono(self) -> float:
        """Current GS_WALL - GS_MONO. Records a step if it moved."""
        m, w = self._mono(), self._wall()
        off = w - m
        if self._offset is not None and abs(off - self._offset) > self.step_threshold_s:
            self.steps += 1
            self.last_step_mono = m
        self._offset = off
        return off

    def stepped_since(self, mono_t: float) -> bool:
        """True if a GS wall-clock step was detected after GS_MONO time mono_t."""
        self.wall_minus_mono()
        return self.last_step_mono is not None and self.last_step_mono >= mono_t


# =============================================================== one-way offset
class OneWayOffsetEstimator:
    """Estimate the offset between a remote clock and GS_MONO from one-way
    timestamps (remote stamp, local arrival), with no round trip available.

        arrival - remote = offset + delay,   delay >= 0 and varies.

    The minimum of (arrival - remote) over a sliding window is
    offset + d_min. d_min, the fastest any sample is ever delivered, is NOT
    observable from one-way data: callers add it as a stated assumption.
    What this does measure is the jitter: how much later than the fastest
    sample a given sample arrived.

    Discontinuities (timing issue T10): a reconnect, a camera or flight
    controller reboot, or a clock step changes the offset abruptly.
      * remote time going backwards -> reset immediately;
      * a sample implying a delay more than `jump_s` BELOW the floor (it
        arrived "before it was sent") -> remote clock jumped forward -> reset;
      * `confirm_jump_n` samples in a row more than `jump_s` ABOVE the floor
        -> the remote clock jumped back, or the link latency rose sharply.
        From one-way data those look identical; either way the old floor is
        wrong, so reset.
    After a reset the estimator needs `min_samples` samples before ready().
    """

    def __init__(self, window_s: float = 10.0, min_samples: int = 5,
                 jump_s: float = 0.25, confirm_jump_n: int = 3, backwards_tol_s: float = 1e-6):
        self.window_s, self.min_samples = window_s, min_samples
        self.jump_s, self.confirm_jump_n = jump_s, confirm_jump_n
        self.backwards_tol_s = backwards_tol_s
        self._s: Deque[Tuple[float, float]] = deque()   # (local_t, diff)
        self._last_remote: Optional[float] = None
        self._high_run = 0
        self.resets = 0
        self.last_reset_reason = ""

    def reset(self, reason: str):
        self._s.clear()
        self._last_remote = None
        self._high_run = 0
        self.resets += 1
        self.last_reset_reason = reason

    def _floor_raw(self) -> Optional[float]:
        return min((d for _, d in self._s), default=None)

    def add(self, remote_t: float, local_t: float) -> str:
        """Feed one sample. Returns 'ok', 'warming', or 'reset: <reason>'."""
        status = "ok"
        diff = local_t - remote_t
        if self._last_remote is not None and remote_t < self._last_remote - self.backwards_tol_s:
            self.reset("remote clock went backwards")
            status = "reset: remote clock went backwards"
        else:
            floor = self._floor_raw()
            if floor is not None and diff < floor - self.jump_s:
                self.reset("sample earlier than the delay floor (remote clock jumped forward)")
                status = "reset: remote clock jumped forward"
            elif floor is not None and diff > floor + self.jump_s:
                self._high_run += 1
                if self._high_run >= self.confirm_jump_n:
                    self.reset("sustained jump above the delay floor (clock jumped back "
                               "or latency rose)")
                    status = "reset: sustained jump above floor"
                    # replay the confirming samples is not possible; start fresh
            else:
                self._high_run = 0
        self._last_remote = remote_t
        self._s.append((local_t, diff))
        while self._s and self._s[0][0] < local_t - self.window_s:
            self._s.popleft()
        if status == "ok" and not self.ready():
            status = "warming"
        return status

    def ready(self) -> bool:
        return len(self._s) >= self.min_samples

    def floor(self) -> Optional[float]:
        """min(arrival - remote) in the window = offset + d_min, or None if not ready."""
        return self._floor_raw() if self.ready() else None

    def lag_of_last(self) -> Optional[float]:
        """How much later than the fastest sample the newest one arrived (jitter,
        or a decoder / buffer falling behind if it keeps growing)."""
        if not self._s or not self.ready():
            return None
        return self._s[-1][1] - self._floor_raw()


# =============================================================== capture time
@dataclass
class CaptureTimingConfig:
    """Timing assumptions. DEFAULTS ARE PLACEHOLDERS, NOT MEASUREMENTS.

    min_latency_s / max_latency_s
        capture-to-arrival latency range on the ground station (arrival is when
        the decoder hands over the frame, so it includes decode: timing issue
        T2). The defaults (0.035 to 0.200 s) bracket the 35-70 ms the OpenIPC
        APFPV guide quotes for its link plus room for decode and buffering;
        they are not measured on this airframe.
    min_latency_uncertainty_s
        stream_pts method: how wrong min_latency_s might be.
    camera_to_gs_offset_s / camera_clock_uncertainty_s
        camera_wall method: GS_WALL - CAMERA_WALL and how well it is known.
    arrival_slack_s
        camera_wall method: tolerance before "captured after it arrived" is
        treated as a clock-sync failure.
    """
    method: str = "arrival"                 # arrival | stream_pts | camera_wall
    min_latency_s: float = 0.035
    max_latency_s: float = 0.200
    min_latency_uncertainty_s: float = 0.030
    camera_to_gs_offset_s: float = 0.0
    camera_clock_uncertainty_s: float = 0.050
    arrival_slack_s: float = 0.010
    pts_window_s: float = 10.0
    pts_min_samples: int = 10
    pts_jump_s: float = 0.25
    measured: bool = False                 # set True only after measuring latency

    def validate(self) -> List[str]:
        errs = []
        if self.method not in ("arrival", "stream_pts", "camera_wall"):
            errs.append(f"unknown capture time method {self.method!r}")
        if not 0 <= self.min_latency_s <= self.max_latency_s:
            errs.append("need 0 <= min_latency_s <= max_latency_s")
        if self.min_latency_uncertainty_s < 0 or self.camera_clock_uncertainty_s < 0:
            errs.append("uncertainties must be >= 0")
        return errs


class CaptureTimeEstimator:
    """Turn what is known about a frame into a GS_MONO TimeEstimate of its capture.

    Inputs per frame (all optional except arrival):
        arrival_mono   GS_MONO when the decoder handed the frame over
        pts_s          STREAM time of the frame (decoder pts), or None
        epoch          increments on every reconnect; pts from different epochs
                       are never compared
        camera_wall_s  CAMERA_WALL capture time, or None
        wall_minus_mono  current GS_WALL - GS_MONO (camera_wall method only)

    Returns (estimate or None, reason). None means "do not use this frame".
    """

    def __init__(self, cfg: CaptureTimingConfig):
        errs = cfg.validate()
        if errs:
            raise ValueError("; ".join(errs))
        self.cfg = cfg
        self.pts = OneWayOffsetEstimator(window_s=cfg.pts_window_s,
                                         min_samples=cfg.pts_min_samples,
                                         jump_s=cfg.pts_jump_s)
        self._epoch: Optional[int] = None

    def estimate(self, arrival_mono: float, pts_s: Optional[float] = None, epoch: int = 0,
                 camera_wall_s: Optional[float] = None,
                 wall_minus_mono: Optional[float] = None) -> Tuple[Optional[TimeEstimate], str]:
        c = self.cfg
        if c.method == "arrival":
            # T1/T2: arrival is NOT capture. Bracket it with the assumed latency.
            return TimeEstimate(arrival_mono - c.max_latency_s,
                                arrival_mono - (c.min_latency_s + c.max_latency_s) / 2.0,
                                arrival_mono - c.min_latency_s, GS_MONO,
                                "arrival-minus-assumed-latency", not c.measured), ""

        if c.method == "stream_pts":
            if pts_s is None:
                return None, "no stream pts on this frame (decoder did not report one)"
            if self._epoch is None or epoch != self._epoch:
                if self._epoch is not None:
                    self.pts.reset("new stream epoch (reconnect)")
                self._epoch = epoch
            status = self.pts.add(pts_s, arrival_mono)
            floor = self.pts.floor()
            if floor is None:
                return None, f"stream clock warming up ({status})"
            best = pts_s + floor - c.min_latency_s
            lo = best - c.min_latency_uncertainty_s
            hi = min(best + c.min_latency_uncertainty_s, arrival_mono)  # cannot be after arrival
            best = min(best, hi)
            return TimeEstimate(lo, best, hi, GS_MONO, "stream-pts-min-filter",
                                not c.measured), ("" if status == "ok" else status)

        # camera_wall
        if camera_wall_s is None:
            return None, "frame carries no camera capture time"
        if wall_minus_mono is None:
            return None, "no GS wall/mono offset supplied"
        best = camera_wall_s + c.camera_to_gs_offset_s - wall_minus_mono   # -> GS_MONO
        u = c.camera_clock_uncertainty_s
        if best - u > arrival_mono + c.arrival_slack_s:
            # T11: the camera says it took the picture after we received it.
            return None, (f"camera capture time is {best - arrival_mono:+.3f} s after arrival: "
                          "camera and ground-station clocks are not in sync")
        if arrival_mono - best > c.max_latency_s + u + 1.0:
            return None, (f"camera capture time is {arrival_mono - best:.3f} s before arrival: "
                          "clock offset wrong or stream badly delayed")
        hi = min(best + u, arrival_mono)
        return TimeEstimate(best - u, min(best, hi), hi, GS_MONO, "camera-wall-clock",
                            not c.measured), ""


# =============================================================== telemetry
@dataclass
class HeadingWindow:
    heading_deg: float          # circular mean over the capture window
    spread_deg: float           # largest deviation from that mean in the window
    samples: int
    extrapolated_s: float = 0.0


class TelemetryAligner:
    """Heading history on GS_MONO, aligned using the flight controller's own clock.

    Timing issue T4: CenteringController's HeadingHistory stamps each heading
    with the time it POLLED get_status(), not when the autopilot measured it.
    MavNode requests streams at 4 Hz by default, so a heading can be about
    250 ms plus link latency older than its stamp. Here every sample carries
    FC_BOOT time (time_boot_ms) from the message itself; FC_BOOT is mapped to
    GS_MONO with a one-way minimum filter, minus an assumed minimum telemetry
    latency (`telem_min_latency_s`, unmeasured placeholder).

    heading_over(lo, hi) answers: what was the heading while the picture could
    have been taken, and how much did it change in that window? The bridge only
    stamps heading_deg on a detection if the spread is small.
    """

    def __init__(self, window_s: float = 10.0, min_samples: int = 3,
                 telem_min_latency_s: float = 0.0, max_gap_s: float = 0.6,
                 max_extrapolate_s: float = 0.15, max_yaw_rate_dps: float = 10.0,
                 history_s: float = 20.0):
        self.off = OneWayOffsetEstimator(window_s=window_s, min_samples=min_samples,
                                         jump_s=0.5)
        self.telem_min_latency_s = telem_min_latency_s
        self.max_gap_s, self.max_extrapolate_s = max_gap_s, max_extrapolate_s
        self.max_yaw_rate_dps, self.history_s = max_yaw_rate_dps, history_s
        self._raw: Deque[Tuple[float, float]] = deque()   # (fc_boot_s, heading)
        self._last_boot: Optional[float] = None
        self.ignored_unknown_heading = 0
        self.fc_reboots = 0

    def add_sample(self, fc_boot_s: float, arrival_mono: float, heading_deg: Optional[float]):
        # T9: GLOBAL_POSITION_INT.hdg = 65535 means "unknown"; MavNode divides by
        # 100 and reports 655.35. Never treat that as a heading.
        if heading_deg is None or not (0.0 <= heading_deg <= 360.0):
            self.ignored_unknown_heading += 1
            return
        if self._last_boot is not None and fc_boot_s < self._last_boot - 1.0:
            self.fc_reboots += 1          # flight controller rebooted: old history is void
            self._raw.clear()
        if self._last_boot is not None and fc_boot_s <= self._last_boot and self._raw:
            return                        # duplicate or out-of-order telemetry
        self._last_boot = fc_boot_s
        self.off.add(fc_boot_s, arrival_mono)
        self._raw.append((fc_boot_s, float(heading_deg) % 360.0))
        while self._raw and self._raw[0][0] < fc_boot_s - self.history_s:
            self._raw.popleft()

    def _samples_mono(self) -> Optional[List[Tuple[float, float]]]:
        floor = self.off.floor()
        if floor is None:
            return None
        shift = floor - self.telem_min_latency_s
        return [(b + shift, h) for b, h in self._raw]

    @staticmethod
    def _interp(a: Tuple[float, float], b: Tuple[float, float], t: float) -> float:
        if b[0] == a[0]:
            return a[1]
        d = (b[1] - a[1] + 180.0) % 360.0 - 180.0
        return (a[1] + d * (t - a[0]) / (b[0] - a[0])) % 360.0

    def heading_over(self, lo: float, hi: float) -> Tuple[Optional[HeadingWindow], str]:
        s = self._samples_mono()
        if s is None:
            return None, "telemetry clock not aligned yet"
        if not s:
            return None, "no heading telemetry"
        before = [x for x in s if x[0] <= lo]
        if before and before[-1] is s[-1] and hi - s[-1][0] > self.max_extrapolate_s:
            return None, f"newest heading is {hi - s[-1][0]:.2f} s older than the frame"
        if not before or lo - before[-1][0] > self.max_gap_s:
            return None, "telemetry does not reach back to the capture window"
        inside = [x for x in s if lo < x[0] < hi]
        after = [x for x in s if x[0] >= hi]
        pts = [self._interp(before[-1], (inside or after or [before[-1]])[0], lo)]
        pts += [h for _, h in inside]
        extrap = 0.0
        if after:
            prev = inside[-1] if inside else before[-1]
            if after[0][0] - prev[0] > self.max_gap_s:
                return None, "gap in heading telemetry across the capture window"
            pts.append(self._interp(prev, after[0], hi))
        else:
            # T14: frame newer than the newest telemetry. Allow a short hold of
            # the last value only if the copter was not yawing.
            last = s[-1]
            extrap = hi - last[0]
            if extrap > self.max_extrapolate_s:
                return None, f"newest heading is {extrap:.2f} s older than the frame"
            recent = [x for x in s if x[0] >= last[0] - 1.0]
            rate = 0.0
            if len(recent) >= 2 and recent[-1][0] > recent[0][0]:
                d = (recent[-1][1] - recent[0][1] + 180.0) % 360.0 - 180.0
                rate = abs(d) / (recent[-1][0] - recent[0][0])
            if rate > self.max_yaw_rate_dps:
                return None, f"yawing at {rate:.1f} deg/s and telemetry is behind the frame"
            pts.append(last[1])
            pts.append((last[1] + math.copysign(rate * extrap, 1.0)) % 360.0)
        mean = circular_mean_deg(pts)
        spread = max(abs((p - mean + 180.0) % 360.0 - 180.0) for p in pts)
        return HeadingWindow(mean, spread, len(pts), extrap), ""


def circular_mean_deg(values: List[float]) -> float:
    x = sum(math.cos(math.radians(v)) for v in values)
    y = sum(math.sin(math.radians(v)) for v in values)
    return math.degrees(math.atan2(y, x)) % 360.0


class MavNodeTelemetryFeed:
    """Feeds a TelemetryAligner from an existing MavNode without touching it.

    Reads MavNode.recent_by_type["GLOBAL_POSITION_INT"] (the same message the
    controller's get_status()["heading"] comes from, so both sides use the same
    heading source). Each record already holds the GS arrival time MavNode
    stamped in its reader thread.

    Timing issue T8: MavNode stamps arrival with time.time() (GS_WALL, can
    step) rounded to 1 ms. It is converted to GS_MONO with the offset measured
    at poll time; records received before a detected GS wall-clock step are
    skipped.
    """

    def __init__(self, node, aligner: TelemetryAligner, clock: GsClock,
                 msg_type: str = "GLOBAL_POSITION_INT"):
        self.node, self.aligner, self.clock, self.msg_type = node, aligner, clock, msg_type
        self._last_boot_ms: Optional[int] = None

    def poll(self) -> int:
        recs = list(self.node.recent_by_type.get(self.msg_type, ()))
        off = self.clock.wall_minus_mono()
        n = 0
        for r in recs:
            d = r.get("data") or {}
            boot_ms = d.get("time_boot_ms")
            if boot_ms is None:
                continue
            if self._last_boot_ms is not None and boot_ms <= self._last_boot_ms \
                    and self._last_boot_ms - boot_ms < 1000:
                continue                    # already consumed
            arrival_mono = float(r["t"]) - off
            if self.clock.last_step_mono is not None and arrival_mono < self.clock.last_step_mono:
                continue
            hdg = d.get("hdg")
            self.aligner.add_sample(boot_ms / 1000.0, arrival_mono,
                                    None if hdg is None or hdg == 65535 else hdg / 100.0)
            self._last_boot_ms = boot_ms
            n += 1
        return n
