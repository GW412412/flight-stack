"""Tests for the experimental OpenIPC -> YOLO bridge.

Contributed by Maya (AI agent, Sesame), October 2026. Everything here runs
without a camera, GPU, network video or model download: frames are scripted
or drawn with numpy, YOLO results are stand-in objects, and the end-to-end
tests use the repo's fake vehicle.
"""
import json
import math
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

import openipc_yolo_bridge as ob
from openipc_yolo_bridge import (BridgeConfig, LatestDetectionBox, Observation, UdpJsonSink,
                                 VisionBridge, config_from_dict, load_config,
                                 timing_budget_report)
from vision_clock import (CaptureTimeEstimator, CaptureTimingConfig, GsClock,
                          MavNodeTelemetryFeed, OneWayOffsetEstimator, TelemetryAligner)
from vision_detect import (BoxDetection, CentroidTracker, ColorBlobDetector, TargetSelector,
                           boxes_from_ultralytics, UltralyticsYoloDetector)
from vision_ingest import (Frame, LatestFrameSlot, OpenCVStreamSource, ReplayEvent,
                           ReplayFrameSource, gst_rtp_pipeline, redact_url)
from visual_centering import (CenteringController, CenteringLimits, Detection,
                              SimulatedDownCamera, UdpJsonDetectionSource, body_to_ned)
from test_visual_centering import (CAM, M_PER_DEG, Clock, FakeTools, _free_udp_port,
                                   _wait_for, airborne)  # noqa: F401  (airborne = fixture)

W, H = 640, 480


# =============================================================== helpers
class Clocks:
    """One fake time base: GS_MONO and GS_WALL differ by a fixed offset, and the
    wall clock can be stepped."""

    def __init__(self, mono=100.0, wall_off=1.7e9):
        self.m, self.off = mono, wall_off

    def mono(self):
        return self.m

    def wall(self):
        return self.m + self.off

    def gs(self):
        return GsClock(mono=self.mono, wall=self.wall)


def red_square(cx=320, cy=240, half=12, w=W, h=H):
    img = np.full((h, w, 3), 90, dtype=np.uint8)
    x, y = int(round(cx)), int(round(cy))
    img[max(0, y - half):y + half, max(0, x - half):x + half] = (0, 0, 255)
    return img


def bridge(clocks, cfg=None, **kw):
    cfg = cfg or BridgeConfig()
    cfg.target.min_track_frames = kw.pop("min_track_frames", 1)
    box = LatestDetectionBox()
    b = VisionBridge(cfg, kw.pop("detector", ColorBlobDetector()), [box], clock=clocks.gs(), **kw)
    return b, box


def frame(clocks, seq, img=None, pts=None, epoch=0, cam=None, arrival=None):
    return Frame(seq, red_square() if img is None else img, W, H,
                 clocks.m if arrival is None else arrival, pts, cam, epoch, "cam0")


# =============================================================== clock domains
def test_arrival_method_brackets_capture_with_assumed_latency():
    e, _ = CaptureTimeEstimator(CaptureTimingConfig(min_latency_s=0.04, max_latency_s=0.2)) \
        .estimate(arrival_mono=50.0)
    assert (e.lo, e.hi) == pytest.approx((49.8, 49.96))
    assert e.lo < e.best < e.hi and e.assumed and e.method == "arrival-minus-assumed-latency"


def test_one_way_offset_floor_is_offset_plus_fastest_delay():
    est = OneWayOffsetEstimator(min_samples=5)
    rng = np.random.default_rng(1)
    for i in range(50):
        remote = i * 0.033
        est.add(remote, remote + 7.0 + 0.04 + float(rng.uniform(0, 0.08)))
    assert est.floor() == pytest.approx(7.04, abs=0.005)
    assert 0 <= est.lag_of_last() <= 0.08


def test_offset_estimator_detects_backwards_forward_and_sustained_jumps():
    est = OneWayOffsetEstimator(min_samples=3, jump_s=0.25, confirm_jump_n=3)
    for i in range(5):
        est.add(i * 0.1, i * 0.1 + 1.0)
    assert est.add(0.05, 0.6).startswith("reset")              # remote went backwards
    for i in range(5):
        est.add(10 + i * 0.1, 11 + i * 0.1)
    assert est.add(30.0, 11.6).startswith("reset")             # arrived "before sent"
    for i in range(5):
        est.add(40 + i * 0.1, 41 + i * 0.1)
    s = [est.add(41 + i * 0.1, 43 + i * 0.1) for i in range(3)]
    assert s[0] == "ok" and s[1] == "ok" and s[2].startswith("reset")   # confirmed jump
    assert est.resets == 3


def test_stream_pts_removes_arrival_jitter_and_never_exceeds_arrival():
    cfg = CaptureTimingConfig(method="stream_pts", min_latency_s=0.05,
                              min_latency_uncertainty_s=0.02, pts_min_samples=10)
    ce = CaptureTimeEstimator(cfg)
    rng = np.random.default_rng(2)
    errs = []
    for i in range(200):
        capture = 10.0 + i / 30.0                     # true capture on GS_MONO
        arrival = capture + 0.05 + float(rng.exponential(0.03))
        e, why = ce.estimate(arrival, pts_s=123.0 + i / 30.0)
        if i < 9:
            assert e is None and "warming" in why
            continue
        assert e.hi <= arrival and e.lo <= capture + 0.02
        if i > 60:
            errs.append(abs(e.best - capture))
    assert max(errs) < 0.02            # jitter (mean 30 ms) removed to within the window


def test_stream_pts_reconnect_epoch_resets_and_rewarms():
    ce = CaptureTimeEstimator(CaptureTimingConfig(method="stream_pts", pts_min_samples=3))
    for i in range(5):
        assert ce.estimate(1 + i * 0.033, pts_s=i * 0.033, epoch=0)[0] is not None or i < 2
    e, why = ce.estimate(5.0, pts_s=0.0, epoch=1)
    assert e is None and "warming" in why and ce.pts.resets == 1
    assert ce.estimate(5.0, pts_s=None, epoch=1)[1].startswith("no stream pts")


def test_camera_wall_clock_checked_against_arrival():
    cfg = CaptureTimingConfig(method="camera_wall", camera_to_gs_offset_s=0.5,
                              camera_clock_uncertainty_s=0.02)
    ce = CaptureTimeEstimator(cfg)
    off = 1000.0                                   # GS_WALL - GS_MONO
    e, _ = ce.estimate(arrival_mono=10.0, camera_wall_s=1009.4, wall_minus_mono=off)
    assert e.best == pytest.approx(9.9) and e.hi <= 10.0
    e, why = ce.estimate(arrival_mono=10.0, camera_wall_s=1010.0, wall_minus_mono=off)
    assert e is None and "not in sync" in why       # captured 0.5 s after it arrived
    assert ce.estimate(10.0, wall_minus_mono=off)[1] == "frame carries no camera capture time"


def test_gs_wall_clock_step_detected():
    c = Clocks()
    g = c.gs()
    g.wall_minus_mono()
    c.m += 1.0
    assert not g.stepped_since(100.5)
    c.off += 2.0                                     # NTP steps the wall clock
    assert g.stepped_since(100.5) and g.steps == 1


def test_bad_timing_config_rejected():
    with pytest.raises(ValueError):
        CaptureTimeEstimator(CaptureTimingConfig(min_latency_s=0.3, max_latency_s=0.1))
    with pytest.raises(ValueError):
        CaptureTimeEstimator(CaptureTimingConfig(method="guess"))


# =============================================================== telemetry
def _aligner_with(samples, **kw):
    a = TelemetryAligner(min_samples=3, **kw)
    for boot, hdg in samples:
        a.add_sample(boot, boot + 500.0, hdg)          # FC_BOOT + 500 = GS_MONO, no delay
    return a


def test_heading_over_capture_window_interpolates_and_measures_spread():
    a = _aligner_with([(i * 0.1, 10.0 + i * 1.0) for i in range(30)])   # 10 deg/s yaw
    hw, _ = a.heading_over(501.0, 501.2)
    assert hw.heading_deg == pytest.approx(21.0, abs=0.2)
    assert hw.spread_deg == pytest.approx(1.0, abs=0.2)


def test_heading_wraps_through_north():
    a = _aligner_with([(i * 0.1, (355.0 + i) % 360) for i in range(20)])
    hw, _ = a.heading_over(500.4, 500.6)
    assert min(hw.heading_deg, 360 - hw.heading_deg) < 1.0 and hw.spread_deg < 1.5


def test_heading_unknown_reboot_and_coverage_gaps():
    a = _aligner_with([(i * 0.1, 655.35) for i in range(5)])
    assert a.ignored_unknown_heading == 5
    a = _aligner_with([(i * 0.1, 90.0) for i in range(20)])
    assert a.heading_over(499.0, 499.1)[1] == "telemetry does not reach back to the capture window"
    assert "older than the frame" in a.heading_over(502.5, 502.6)[1]
    hw, _ = a.heading_over(501.9, 502.0)               # short hold, not yawing
    assert hw.heading_deg == pytest.approx(90.0) and hw.extrapolated_s > 0
    a.add_sample(0.2, 600.0, 90.0)                      # FC rebooted
    assert a.fc_reboots == 1


def test_mavnode_feed_reads_global_position_int_records():
    c = Clocks()
    recs = [{"t": c.wall() - 1 + i * 0.1, "data": {"time_boot_ms": 5000 + i * 100,
                                                   "hdg": 4500 if i != 3 else 65535}}
            for i in range(8)]
    node = SimpleNamespace(recent_by_type={"GLOBAL_POSITION_INT": recs})
    a = TelemetryAligner(min_samples=3)
    feed = MavNodeTelemetryFeed(node, a, c.gs())
    assert feed.poll() == 8 and feed.poll() == 0          # second poll: nothing new
    assert a.ignored_unknown_heading == 1
    hw, why = a.heading_over(c.m - 0.8, c.m - 0.5)
    assert hw is not None and hw.heading_deg == pytest.approx(45.0), why


# =============================================================== ingest
def test_latest_frame_slot_is_bounded_and_returns_newest():
    s = LatestFrameSlot(1)
    for i in range(10):
        s.put(Frame(i, None, W, H, float(i)))
    assert len(s) == 1 and s.dropped == 9 and s.get(0).seq == 9 and s.get(0.01) is None
    s3 = LatestFrameSlot(3)
    for i in range(5):
        s3.put(Frame(i, None, W, H, float(i)))
    assert len(s3) == 3 and s3.get(0).seq == 4 and s3.discarded_on_get == 2


def test_latest_frame_slot_under_threads_never_exceeds_capacity():
    s = LatestFrameSlot(2)
    seen, sizes = [], []
    done = threading.Event()

    def consume():
        while not done.is_set() or len(s):
            f = s.get(0.01)
            sizes.append(len(s))
            if f:
                seen.append(f.seq)

    th = threading.Thread(target=consume)
    th.start()
    for i in range(2000):
        s.put(Frame(i, None, W, H, float(i)))
    done.set()
    th.join()
    assert max(sizes) <= 2 and seen == sorted(seen) and seen[-1] == 1999
    assert s.put_count == 2000 == len(seen) + s.dropped + s.discarded_on_get


class FakeCap:
    def __init__(self, n_ok, pts_ms=33.0):
        self.n, self.i, self.pts_ms, self.released = n_ok, 0, pts_ms, False

    def isOpened(self):
        return True

    def read(self):
        self.i += 1
        return (True, red_square()) if self.i <= self.n else (False, None)

    def get(self, prop):
        return self.i * self.pts_ms

    def release(self):
        self.released = True


def test_opencv_source_reads_reconnects_with_new_epoch_and_redacts_password():
    caps = [FakeCap(3), FakeCap(2, pts_ms=0.0)]
    slot = LatestFrameSlot(1)
    t = [0.0]

    def mono():
        t[0] += 0.01
        return t[0]

    src = OpenCVStreamSource("rtsp://root:secret@10.0.0.5:554/stream=1", slot,
                             capture_factory=lambda url, be: caps.pop(0), mono=mono)
    assert src.run_once() == 3
    f = slot.get(0)
    assert (f.seq, f.epoch, f.stream_pts_s) == (3, 0, pytest.approx(0.099))
    assert src.run_once() == 2
    f = slot.get(0)
    assert f.epoch == 1 and f.stream_pts_s is None and src.pts_missing == 2
    st = src.status()
    assert "secret" not in json.dumps(st) and st["read_failures"] == 2 and not st["connected"]


def test_opencv_source_open_failure_reported():
    class Closed(FakeCap):
        def isOpened(self):
            return False
    src = OpenCVStreamSource("rtsp://u:pw@cam/stream=0", LatestFrameSlot(),
                             capture_factory=lambda u, b: Closed(0))
    assert src.run_once() == 0 and "could not open" in src.last_error and "pw" not in src.last_error


def test_replay_disconnect_starts_new_epoch_and_jsonl(tmp_path):
    slot = LatestFrameSlot()
    r = ReplayFrameSource([ReplayEvent(0.0), ReplayEvent(0.033), ReplayEvent(0.05, "disconnect"),
                           ReplayEvent(1.0)], slot, t0_mono=10.0)
    assert [r.step().epoch for _ in range(3)] == [0, 0, 1] and r.step() is None
    p = tmp_path / "t.jsonl"
    p.write_text('{"at_s": 0.1, "pts_s": 5.0}\n# comment\n{"at_s": 0.2, "kind": "disconnect"}\n')
    r2 = ReplayFrameSource.load_jsonl(str(p), slot)
    assert r2.step().stream_pts_s == 5.0 and r2.step() is None


def test_url_helpers():
    assert redact_url("rtsp://root:12345@192.168.1.10:554/stream=0") == \
        "rtsp://root:***@192.168.1.10:554/stream=0"
    assert redact_url("udp://0.0.0.0:5600") == "udp://0.0.0.0:5600"
    g = gst_rtp_pipeline(5600, "h265")
    assert "udpsrc port=5600" in g and "rtph265depay" in g and "drop=true max-buffers=1" in g
    with pytest.raises(ValueError):
        gst_rtp_pipeline(codec="mjpeg")


# =============================================================== detection
def test_ultralytics_result_parsing_without_ultralytics():
    boxes = SimpleNamespace(xyxy=np.array([[10, 20, 30, 60], [100, 100, 120, 110]]),
                            conf=np.array([0.9, 0.4]), cls=np.array([1, 0]),
                            id=np.array([7, 8]))
    out = boxes_from_ultralytics(SimpleNamespace(boxes=boxes, names={0: "grass", 1: "thistle"}))
    assert [(b.class_name, b.track_id, b.cx, b.cy) for b in out] == \
        [("thistle", 7, 20.0, 40.0), ("grass", 8, 110.0, 105.0)]
    no_ids = SimpleNamespace(boxes=SimpleNamespace(xyxy=[[0, 0, 2, 2]], conf=[0.5], cls=[0], id=None))
    assert boxes_from_ultralytics(no_ids)[0].track_id is None


def test_yolo_adapter_refuses_to_download_missing_model(tmp_path):
    with pytest.raises(FileNotFoundError):
        UltralyticsYoloDetector(str(tmp_path / "nope.pt"))


def test_colour_blob_detector_finds_target_centre():
    b = ColorBlobDetector().detect(red_square(400, 100))
    assert len(b) == 1 and (b[0].cx, b[0].cy) == pytest.approx((400, 100), abs=0.6)
    assert ColorBlobDetector().detect(np.full((H, W, 3), 90, np.uint8)) == []


def test_tracker_and_selector_keep_identity_and_gate_new_targets():
    tr = CentroidTracker()
    sel = TargetSelector("centre", 0.5, min_track_frames=3, lost_after_s=0.5)
    outs = []
    for i in range(4):
        boxes = [BoxDetection(300 + i, 230, 320 + i, 250, 0.9, 0, "weed")]
        if i >= 2:                                   # a more central target appears later
            boxes.append(BoxDetection(315, 235, 325, 245, 0.95, 0, "weed"))
        tr.assign(boxes, W, H, i * 0.033)
        outs.append(sel.select(boxes, W, H, i * 0.033))
    assert outs[0][0] is None and "fewer than 3" in outs[0][1]
    assert outs[2][0].target_id == "weed-1" and outs[3][0].target_id == "weed-1"   # stays locked
    # Locked target disappears: briefly tolerated, then a new lock (controller will stop).
    other = [BoxDetection(315, 235, 325, 245, 0.95, 0, "weed", track_id=2)]
    assert sel.select(other, W, H, 0.3)[1] == "locked target not in this frame"
    assert sel.select(other, W, H, 0.45)[1] == "locked target not in this frame"
    got, _ = sel.select(other, W, H, 0.7)          # lock released; weed-2 seen 3 times
    assert got.target_id == "weed-2" and sel.relocks == 1


# =============================================================== bridge core
def test_bridge_publishes_detection_with_conservative_wall_time_and_extras():
    c = Clocks()
    b, box = bridge(c)
    out = b.process_frame(frame(c, 1, red_square(400, 300)))
    assert out.published, out.reason
    d = box.latest()
    assert d.t == pytest.approx(c.wall() - 0.2)              # earliest plausible capture
    assert out.extras["t_latest"] == pytest.approx(c.wall() - 0.035)
    assert out.extras["t_assumed"] is True and out.extras["producer"].startswith("openipc_yolo")
    assert (d.cx, d.cy, d.image_w, d.frame_id) == (pytest.approx(400, abs=1), pytest.approx(300, abs=1),
                                                   W, "cam0-e0-1")
    back = Detection.from_dict(json.loads(ob.detection_json(d, out.extras)))
    assert back == d                                        # extras ignored by the controller


def test_bridge_drops_stale_out_of_order_and_pre_reconnect_frames():
    c = Clocks()
    b, box = bridge(c)
    c.m = 100.3
    assert b.process_frame(frame(c, 1, arrival=100.0)).reason.startswith("waited_too_long")
    assert b.process_frame(frame(c, 2, epoch=1)).published
    assert b.process_frame(frame(c, 3, arrival=c.m - 0.05, epoch=1)).reason.startswith("out_of_order")
    c.m += 0.033
    assert b.process_frame(frame(c, 4, epoch=0)).reason.startswith("old_epoch")
    assert b.stats()["drops"] == {"waited_too_long": 1, "out_of_order": 1, "old_epoch": 1}


def test_bridge_drops_frame_when_gs_wall_clock_steps():
    c = Clocks()
    b, box = bridge(c)
    assert b.process_frame(frame(c, 1)).published
    c.m += 0.033
    f = frame(c, 2)
    c.off += 3.0                                             # step between arrival and publish
    assert b.process_frame(f).reason.startswith("gs_clock_stepped")
    c.m += 0.5
    out = b.process_frame(frame(c, 3))
    assert out.published and box.latest().t == pytest.approx(c.wall() - 0.2)


def test_bridge_uncertainty_and_publish_age_limits():
    c = Clocks()
    cfg = BridgeConfig()
    cfg.capture.max_latency_s = 0.6
    b, _ = bridge(c, cfg)
    assert b.process_frame(frame(c, 1)).reason.startswith("capture_too_uncertain")
    assert any(l.startswith("ERROR") for l in timing_budget_report(cfg))

    class Slow(ColorBlobDetector):
        def detect(self, image):
            c.m += 0.4                                      # detector took 400 ms
            return super().detect(image)
    b2, _ = bridge(c, detector=Slow())
    assert b2.process_frame(frame(c, 1)).reason.startswith("too_old_to_publish")


def test_bridge_survives_detector_errors_and_empty_frames():
    c = Clocks()

    class Boom:
        name = "boom"

        def detect(self, image):
            raise RuntimeError("cuda out of memory")
    b, _ = bridge(c, detector=Boom())
    assert b.process_frame(frame(c, 1)).reason.startswith("detector_error")
    c.m += 0.04
    b2, _ = bridge(c)
    assert b2.process_frame(frame(c, 1, img=np.full((H, W, 3), 90, np.uint8))).reason \
        .startswith("no_target")


def test_bridge_stamps_heading_only_when_steady_over_capture_window():
    c = Clocks()
    cfg = BridgeConfig()
    cfg.telemetry.enabled = True
    tel = TelemetryAligner(min_samples=3)
    b, box = bridge(c, cfg, telemetry=tel)
    assert b.process_frame(frame(c, 1)).reason.startswith("no_heading")
    base_boot, base_mono = 0.0, c.m - 1.0
    for i in range(40):                                      # 1 s of steady heading 30 deg
        tel.add_sample(base_boot + i * 0.025, base_mono + i * 0.025, 30.0)
    c.m += 0.01
    out = b.process_frame(frame(c, 2))
    assert out.published and box.latest().heading_deg == pytest.approx(30.0)
    for i in range(40, 80):                                  # then yawing 40 deg/s
        tel.add_sample(base_boot + i * 0.025, base_mono + i * 0.025, 30.0 + (i - 39) * 1.0)
    c.m = base_mono + 1.9                                     # telemetry covers the window
    assert b.process_frame(frame(c, 3)).reason.startswith("heading_changing")


def test_slow_observer_nudges_only_on_its_own_frames_and_is_rate_limited():
    c = Clocks()
    cfg = BridgeConfig()
    cfg.observer.enabled, cfg.observer.max_rate_hz = True, 5.0
    calls = []

    class Model:
        def observe(self, image, det):
            calls.append(det.frame_id)
            c.m += 0.1                                        # 100 ms of thinking
            if len(calls) == 2:
                return Observation(agrees_with_target=False, note="not a weed")
            return Observation(0.3, -0.2, note="step toward target")
    b, box = bridge(c, cfg, observer=Model(), observer_inline=True)
    out = b.process_frame(frame(c, 1))
    assert out.published and (box.latest().nudge_forward_m, box.latest().nudge_right_m) == (0.3, -0.2)
    c.m += 0.033
    assert b.process_frame(frame(c, 2)).reason == "observer_rate_limit"   # within 200 ms
    c.m += 0.2
    assert b.process_frame(frame(c, 3)).reason.startswith("observer_rejected")
    assert calls == ["cam0-e0-1", "cam0-e0-3"] and box.published == 1


def test_threaded_run_with_replay_publishes_newest_frames():
    slot = LatestFrameSlot(1)
    cfg = BridgeConfig()
    cfg.target.min_track_frames = 1
    box = LatestDetectionBox()
    b = VisionBridge(cfg, ColorBlobDetector(), [box])
    evs = [ReplayEvent(i / 30.0, image=red_square(200 + i, 240)) for i in range(30)]
    src = ReplayFrameSource(evs, slot)
    th = threading.Thread(target=b.run, args=(slot,), daemon=True)
    th.start()
    src.run_realtime()
    assert _wait_for(lambda: box.latest() is not None and box.latest().cx > 225, 3.0)
    b.stop()
    th.join(1.0)
    st = b.stats()
    assert st["published"] >= 5 and box.latest().t < time.time()


# =============================================================== config / CLI
def test_example_config_parses_and_typos_are_errors():
    import os
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    cfg = load_config(os.path.join(root, "config", "vision_bridge.example.toml"))
    assert cfg.capture.method == "arrival" and cfg.output.udp == "127.0.0.1:5602"
    assert cfg.detector.allow_download is False
    with pytest.raises(ValueError):
        config_from_dict({"capture": {"max_latncy_s": 0.1}})
    with pytest.raises(ValueError):
        config_from_dict({"cameraa": {}})


def test_cli_budget_and_benchmark(capsys):
    assert ob.main(["--budget"]) == 0
    err = capsys.readouterr().err
    assert "WARN T1" in err and "INFO T7" in err
    assert ob.main(["--benchmark", "20", "--detector", "colour"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["frames"] == 20 and rep["published"] >= 15 and "no camera" in rep["machine_note"]


# =============================================================== integration
def test_udp_sink_feeds_existing_controller_input():
    port = _free_udp_port()
    rx = UdpJsonDetectionSource(port=port)
    try:
        c = Clocks(wall_off=0.0)
        c.m = time.time()
        b = VisionBridge(BridgeConfig(), ColorBlobDetector(), [UdpJsonSink(f"127.0.0.1:{port}")],
                         clock=c.gs())
        b.selector.min_track_frames = 1
        out = b.process_frame(frame(c, 1, red_square(100, 50)))
        assert out.published
        assert _wait_for(lambda: rx.latest() is not None, 3.0)
        assert rx.latest() == out.detection and rx.bad_packets == 0
    finally:
        rx.close()


class RenderedBridgeFeed:
    """DetectionSource for the controller: each poll draws the pretend camera's
    view as an image, pushes it through the bridge, and returns the newest
    published Detection (same newest-by-t rule as the UDP input)."""

    def __init__(self, cam: SimulatedDownCamera, bridge_: VisionBridge, box, clocks, now_fn):
        self.cam, self.bridge, self.box, self.clocks, self.now = cam, bridge_, box, clocks, now_fn
        self.seq = 0

    def latest(self):
        d = self.cam.latest()
        if d is not None:
            self.seq += 1
            self.bridge.process_frame(Frame(self.seq, red_square(d.cx, d.cy), W, H,
                                            self.clocks.mono(), None, None, 0, "sim"))
        return self.box.latest()


def test_controller_centres_target_from_bridge_frames_fake_clock():
    clock = Clock()
    tools = FakeTools(heading=30.0)
    c = SimpleNamespace(mono=lambda: clock() - 900.0)       # GS_MONO != GS_WALL
    gs = GsClock(mono=c.mono, wall=clock)
    box = LatestDetectionBox()
    b = VisionBridge(BridgeConfig(), ColorBlobDetector(), [box], clock=gs)
    s = tools.get_status()
    n, e = body_to_ned(1.2, -0.7, s["heading"])
    tgt = (s["lat"] + n / M_PER_DEG, s["lon"] + e / (M_PER_DEG * math.cos(math.radians(s["lat"]))))
    cam = SimulatedDownCamera(tools.get_status, tgt, CAM, clock=clock)
    feed = RenderedBridgeFeed(cam, b, box, c, clock)
    res = CenteringController(tools, feed, CAM, CenteringLimits(poll_s=0.05),
                              clock=clock, sleep=clock.sleep).run()
    assert res["centred"], res["reason"]
    assert tools.moves() and all(cl[0] in ("goto_relative", "hold") for cl in tools.calls)
    assert b.stats()["published"] > 0 and box.extras["t_method"] == "arrival-minus-assumed-latency"


def test_e2e_bridge_over_udp_with_telemetry_heading_through_real_stack(airborne):
    """Fake vehicle -> MavNode -> (telemetry alignment) ; pretend camera images
    -> slot -> bridge -> UDP -> UdpJsonDetectionSource -> CenteringController ->
    DroneTools -> safety gate -> fake vehicle. Real clocks and threads."""
    t, fv = airborne
    s = t.node.get_status()
    tgt = (s["lat"] + 1.1 / M_PER_DEG,
           s["lon"] + 0.8 / (M_PER_DEG * math.cos(math.radians(s["lat"]))))
    cam = SimulatedDownCamera(t.node.get_status, tgt, CAM)
    port = _free_udp_port()
    rx = UdpJsonDetectionSource(port=port)
    cfg = BridgeConfig()
    cfg.telemetry.enabled = True
    gs = GsClock()
    tel = TelemetryAligner(min_samples=3)
    published = []

    class Recorder:
        def publish(self, det, extras):
            published.append((det, extras))

    b = VisionBridge(cfg, ColorBlobDetector(), [UdpJsonSink(f"127.0.0.1:{port}"), Recorder()],
                     clock=gs, telemetry=tel, telemetry_feed=MavNodeTelemetryFeed(t.node, tel, gs))
    slot = LatestFrameSlot(1)
    stop = threading.Event()

    def camera_thread():
        seq = 0
        while not stop.is_set():
            d = cam.latest()
            if d is not None:
                seq += 1
                slot.put(Frame(seq, red_square(d.cx, d.cy), W, H, gs.mono(), None, None, 0, "sim"))
            time.sleep(1 / 30.0)

    threads = [threading.Thread(target=camera_thread, daemon=True),
               threading.Thread(target=b.run, args=(slot,), daemon=True)]
    for th in threads:
        th.start()
    seen_before = list(fv.commands_seen)
    try:
        assert _wait_for(lambda: rx.latest() is not None, 10.0), b.stats()
        res = CenteringController(t, rx, CAM).run()
    finally:
        stop.set()
        b.stop()
        rx.close()
    assert res["centred"], (res["reason"], b.stats())
    from mav_node import haversine_m
    s = t.node.get_status()
    assert haversine_m(s["lat"], s["lon"], *tgt) < 0.35
    assert published and all(d.heading_deg is not None and abs(d.heading_deg) < 2.0 or
                             abs(d.heading_deg - 360) < 2.0 for d, _ in published)
    new = fv.commands_seen[len(seen_before):]
    assert "MAV_CMD_DO_SET_MODE" not in new and "MAV_CMD_COMPONENT_ARM_DISARM" not in new
    assert fv.mode == "GUIDED"


def test_opencv_source_decodes_a_real_video_file(tmp_path):
    """Real OpenCV decode of a small file (skipped where OpenCV cannot load).
    This checks the reader against the actual library, not a camera."""
    # exc_type=ImportError: pytest 9 only skips on ModuleNotFoundError by default,
    # but OpenCV can be installed and still fail to load (e.g. missing libxcb).
    cv2 = pytest.importorskip("cv2", exc_type=ImportError)
    path = str(tmp_path / "clip.avi")
    wr = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), 30.0, (W, H))
    for i in range(15):
        wr.write(red_square(200 + 10 * i, 240))
    wr.release()
    got = []

    class Spy(LatestFrameSlot):
        def put(self, f):
            got.append(f)
            super().put(f)

    src = OpenCVStreamSource(path, Spy(1), backend="any")
    assert src.run_once() == 15 and src.epoch == 0
    assert all(g.image.shape == (H, W, 3) for g in got)
    b = ColorBlobDetector().detect(got[-1].image)
    assert b and b[0].cx == pytest.approx(340, abs=3)
    pts = [g.stream_pts_s for g in got if g.stream_pts_s is not None]
    assert pts == sorted(pts)                       # file pts advance monotonically
